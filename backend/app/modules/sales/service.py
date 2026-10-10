"""Ventes : brouillon → validée (sortie de stock) → annulée.

Lot 1 (ADR-0037) : numéro ``VENT-{SITE}-{ANNÉE}-{SÉQUENCE}`` attribué à la **validation**
(compteur par tenant, site et année ; aucun numéro au brouillon) ; vente à **crédit** (reste
dû à la validation) : client obligatoire, permission ``sales.sale.credit_create``, dépassement
de limite seulement avec ``sales.sale.credit_override`` et justification ; portée :
``sales.sale.view`` = ses propres ventes, ``sales.sale.view_all`` = toutes les ventes du site
(permission évaluée site par site, jamais le nom d'un rôle).

Lot 2 : une seule requête filtrée et limitée au périmètre (``query``) sert la liste et l'export
(même périmètre quel que soit le format).

La validation s'exécute dans UNE transaction (celle de la requête) : verrou de la vente,
contrôles, verrou du client et contrôle de sa limite de crédit (ADR-0021),
``StockService.apply`` (verrou des niveaux, contrôle global du stock, mouvements ``SALE``),
changement de statut, audit, encaissements immédiats éventuels. Toute erreur annule tout : ni
stock, ni mouvement, ni statut, ni paiement, ni audit. Le service ne valide jamais la
transaction (ADR-0008).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.modules.catalog.api import (
    ArticleRef,
    PackagingRef,
    articles_view,
    barcode_search,
    base_quantity,
    check_packagings,
    ensure_in_assortment,
    ensure_whole,
    get_article_refs,
    lock_lot_flags,
    lock_stock_managed,
)
from app.modules.customers.api import (
    CustomerRef,
    customers_view,
    get_customer_credit,
    get_customer_refs,
)
from app.modules.sales.credit import customer_exposure
from app.modules.sales.filters import SaleFilters
from app.modules.sales.models import (
    ACTIVE_ORIGIN_INDEX,
    CreditStatus,
    Payment,
    PaymentStatus,
    Sale,
    SaleChannel,
    SaleLine,
    SalePaymentStatus,
    SaleStatus,
)
from app.modules.sales.origin_port import notify_before_cancel
from app.modules.sales.payment_service import ZERO as MONEY_ZERO
from app.modules.sales.payment_service import paid_amounts, paid_subquery, payment_status
from app.modules.sales.schemas import (
    CreditOverride,
    ExpiredLotOverride,
    PaymentCreate,
    SaleCheckout,
    SaleCreate,
    SaleInput,
    SaleLineInput,
    SaleLineLotOut,
    SaleLineOut,
    SaleOut,
)
from app.modules.sales.scope import site_can
from app.modules.stock.api import (
    ConsumptionRequest,
    LotAllocation,
    LotPick,
    MovementRequest,
    MovementType,
    PackagingSnapshot,
    StockService,
    ensure_document_site,
    inverse_packaging,
    is_expired_lot,
    lot_allocations,
    lot_infos,
    operation_site,
    round_money,
    sees_all_sites,
    tenant_today,
    visible_site_ids,
)
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.exports import check_row_limit
from app.platform.identity.models import User
from app.platform.sequences.service import next_number, site_sequence_key
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter

SOURCE_TYPE = "sale"
SEQUENCE_KIND = "sale"
PREFIX = "VENT"
VIEW_ALL = "sales.sale.view_all"
EXPORT = "sales.sale.export"
CREDIT_CREATE = "sales.sale.credit_create"
CREDIT_OVERRIDE = "sales.sale.credit_override"
EXPIRED_LOT_OVERRIDE = "sales.sale.expired_lot_override"
ZERO = Decimal("0")
QUANTITY_STEP = Decimal("0.001")  # NUMERIC(18,3) : même valeur en réponse, en audit et en base

DEFAULT_SORT = "-created_at"
SORTABLE = {
    "number": Sale.number,
    "sale_date": Sale.sale_date,
    "total": Sale.total,
    "created_at": Sale.created_at,
    "validated_at": Sale.validated_at,
}


@dataclass(frozen=True)
class OriginLine:
    """Ligne d'une vente issue d'un document d'origine (R2-D) : présentation et montants FIGÉS
    sur l'origine (prix unitaire, total de ligne), jamais relus dans le catalogue."""

    article_id: uuid.UUID
    packaging_id: uuid.UUID | None
    packaging_name: str | None
    packaging_conversion: Decimal | None
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


def credit_status(sale: Sale, paid: Decimal) -> CreditStatus | None:
    """Situation d'une vente à crédit, calculée à partir des paiements effectués."""
    if not sale.is_credit:
        return None
    if sale.status is SaleStatus.CANCELLED:
        return CreditStatus.CANCELLED
    if paid <= 0:
        return CreditStatus.OPEN
    return CreditStatus.PARTIAL if paid < sale.total else CreditStatus.PAID


class SaleService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Lecture ------------------------------------------------------------------------------

    def query(
        self, filters: SaleFilters, *, site_permission: str | None = None
    ) -> Select[tuple[Sale]]:
        """Ventes du périmètre (sites visibles ; sur un site sans ``view_all``, ses propres
        ventes) répondant aux filtres : base commune de la liste et de l'export (Lot 2).
        ``site_permission`` : permission exigée en plus sur chaque site (export)."""
        stmt: Select[tuple[Sale]] = select(Sale).where(self._scope(site_permission))
        by_number = search_filter(filters.search, Sale.number)
        if by_number is not None:
            # Numéro de vente, ou code / nom / téléphone du client.
            customers = customers_view()
            matching = select(customers.c.id).where(customers.c.tenant_id == self.ctx.tenant_id)
            by_customer = search_filter(
                filters.search, customers.c.code, customers.c.name, customers.c.phone
            )
            if by_customer is not None:
                matching = matching.where(by_customer)
            stmt = stmt.where(or_(by_number, Sale.customer_id.in_(matching)))
        conditions = [
            Sale.status == filters.status if filters.status else None,
            Sale.site_id == filters.site_id if filters.site_id else None,
            Sale.customer_id == filters.customer_id if filters.customer_id else None,
            Sale.sale_date >= filters.date_from if filters.date_from else None,
            Sale.sale_date <= filters.date_to if filters.date_to else None,
            Sale.channel == filters.channel if filters.channel else None,
            # Vendeur / opérateur : utilisateur qui a enregistré la vente.
            Sale.created_by == filters.seller_id if filters.seller_id else None,
            Sale.created_by == self.ctx.user.id if filters.mine else None,
            Sale.id.in_(select(SaleLine.sale_id).where(SaleLine.article_id == filters.article_id))
            if filters.article_id
            else None,
        ]
        articles = articles_view()
        by_article_reference = barcode_search(
            filters.article_reference,
            search_filter(filters.article_reference, articles.c.reference, articles.c.barcode),
            articles.c.id,
        )
        if by_article_reference is not None:
            conditions.append(
                Sale.id.in_(
                    select(SaleLine.sale_id)
                    .join(
                        articles,
                        and_(
                            articles.c.id == SaleLine.article_id,
                            articles.c.tenant_id == SaleLine.tenant_id,
                        ),
                    )
                    .where(by_article_reference)
                )
            )
        by_payment_reference = search_filter(filters.payment_reference, Payment.reference)
        if by_payment_reference is not None:
            conditions.append(Sale.id.in_(select(Payment.sale_id).where(by_payment_reference)))
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        if filters.payment_status is not None:
            # État d'encaissement calculé (ventes validées) : une agrégation jointe, pas de N+1.
            paid_sub = paid_subquery()
            paid = func.coalesce(paid_sub.c.paid, MONEY_ZERO)
            stmt = stmt.outerjoin(paid_sub, paid_sub.c.sale_id == Sale.id).where(
                Sale.status == SaleStatus.VALIDATED
            )
            stmt = stmt.where(
                {
                    SalePaymentStatus.UNPAID: and_(paid <= 0, Sale.total > 0),
                    SalePaymentStatus.PARTIALLY_PAID: and_(paid > 0, paid < Sale.total),
                    SalePaymentStatus.PAID: paid >= Sale.total,
                }[filters.payment_status]
            )
        return stmt

    def search(self, params: PageParams, filters: SaleFilters) -> tuple[list[Sale], int]:
        # Tri par défaut chronologique (création), jamais l'ordre alphabétique du numéro.
        stmt = apply_sort(self.query(filters), params.sort, SORTABLE, DEFAULT_SORT, Sale.id)
        return paginate(self.db, stmt, params)

    def export(self, filters: SaleFilters, sort: str | None, max_rows: int) -> list[Sale]:
        """Toutes les ventes de la liste filtrée (même requête, même tri), bornées ; un site
        où le membre ne détient pas ``sales.sale.export`` n'est jamais exporté."""
        base = self.query(filters, site_permission=EXPORT)
        total = self.db.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
        check_row_limit(int(total or 0), max_rows)
        stmt = apply_sort(base, sort, SORTABLE, DEFAULT_SORT, Sale.id)
        return list(self.db.scalars(stmt).unique())

    def sellers(self) -> list[tuple[uuid.UUID, str]]:
        """Vendeurs / opérateurs proposés au filtre : auteurs des ventes du périmètre de
        l'utilisateur (jamais la liste des membres de l'entreprise)."""
        authors = select(Sale.created_by).where(self._scope(), Sale.created_by.is_not(None))
        rows = self.db.execute(
            select(User.id, User.full_name).where(User.id.in_(authors)).order_by(User.full_name)
        ).all()
        return [(row[0], row[1]) for row in rows]

    def _scope(self, site_permission: str | None = None) -> Any:
        full: set[uuid.UUID] = set()
        own: set[uuid.UUID] = set()
        for site_id in visible_site_ids(self.ctx):
            if site_permission and not site_can(self.ctx, site_id, site_permission):
                continue
            (full if site_can(self.ctx, site_id, VIEW_ALL) else own).add(site_id)
        return or_(
            Sale.site_id.in_(full),
            and_(Sale.site_id.in_(own), Sale.created_by == self.ctx.user.id),
        )

    def get(self, sale_id: uuid.UUID, *, lock: bool = False, own_scope: bool = True) -> Sale:
        """Vente d'un site accessible. ``own_scope`` : portée de ``sales.sale.view`` (ses propres
        ventes sans ``view_all``) ; les créances (permission propre) la lèvent en lecture."""
        stmt = select(Sale).where(Sale.id == sale_id)
        if lock:
            # Verrou du document : deux validations simultanées s'exécutent l'une après l'autre ;
            # la seconde trouve la vente validée et échoue proprement (idempotence).
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        sale = self.db.scalars(stmt).one_or_none()
        if sale is None:
            raise NotFoundError("Vente introuvable", code="sale_not_found")
        ensure_document_site(self.ctx, sale.site_id, "sale_not_found")
        if (
            own_scope
            and sale.created_by != self.ctx.user.id
            and not site_can(self.ctx, sale.site_id, VIEW_ALL)
        ):
            # Portée « ses propres ventes » : la vente d'un autre utilisateur est introuvable.
            raise NotFoundError("Vente introuvable", code="sale_not_found")
        return sale

    # --- Règles -------------------------------------------------------------------------------

    def _sale_date(self, value: date | None) -> date:
        today = tenant_today(self.ctx, self.now)
        chosen = value or today
        if chosen > today:
            raise BusinessRuleError(
                "La date de la vente ne peut pas être postérieure à aujourd'hui",
                code="future_operation_date",
            )
        return chosen

    def _articles(
        self, lines: Sequence[tuple[uuid.UUID, uuid.UUID | None]]
    ) -> dict[uuid.UUID, ArticleRef]:
        """Articles du tenant (RLS), existants, actifs ; chaque présentation (article en unité
        de base, ou conditionnement) sur une seule ligne (Lot 3-B)."""
        if len(set(lines)) != len(lines):
            raise BusinessRuleError(
                "Un article apparaît plusieurs fois dans la même présentation",
                code="duplicate_article_line",
            )
        article_ids = [article_id for article_id, _ in lines]
        refs = get_article_refs(self.db, set(article_ids))
        missing = [str(a) for a in article_ids if a not in refs]
        if missing:
            raise BusinessRuleError(
                "Article introuvable", code="article_not_found", extra={"articles": missing}
            )
        inactive = [refs[a].reference for a in article_ids if not refs[a].is_active]
        if inactive:
            raise BusinessRuleError(
                "Article inactif", code="article_inactive", extra={"articles": inactive}
            )
        return refs

    def _references(self, article_ids: list[uuid.UUID]) -> list[str]:
        refs = get_article_refs(self.db, set(article_ids))
        return sorted(refs[a].reference for a in article_ids if a in refs)

    def _customer(self, customer_id: uuid.UUID | None) -> CustomerRef | None:
        """Client facultatif ; s'il est fourni : du tenant et actif (nouvelle opération)."""
        if customer_id is None:
            return None
        ref = get_customer_refs(self.db, {customer_id}).get(customer_id)
        if ref is None:
            raise BusinessRuleError("Client introuvable", code="customer_not_found")
        if not ref.is_active:
            raise BusinessRuleError(
                "Ce client est désactivé",
                code="customer_inactive",
                extra={"customer_code": ref.code},
            )
        return ref

    @staticmethod
    def _require_status(sale: Sale, expected: SaleStatus, code: str) -> None:
        if sale.status is not expected:
            raise ConflictError("Opération impossible dans l'état actuel de la vente", code=code)

    def _packagings(
        self, lines: Sequence[SaleLineInput | SaleLine]
    ) -> dict[uuid.UUID, PackagingRef]:
        """Conditionnements des lignes, relus par le mécanisme commun du catalogue (du tenant,
        de l'article de la ligne, actifs, sous verrou partagé) et, pour une vente, au prix
        configuré (Lot 3-B)."""
        packagings = check_packagings(
            self.db, [(line.article_id, line.packaging_id) for line in lines]
        )
        for line in lines:
            if line.packaging_id is None:
                continue
            packaging = packagings[line.packaging_id]
            # Prix non configuré (créé sans ``price_update``) : invendable, à l'enregistrement
            # comme à la validation — jamais vendu à un prix 0 implicite.
            if packaging.sale_price is None:
                raise BusinessRuleError(
                    "Le prix de ce conditionnement n'est pas encore configuré",
                    code="packaging_price_not_set",
                    extra={"packagings": [packaging.name]},
                )
        return packagings

    @staticmethod
    def _base_quantity(
        ref: ArticleRef, quantity: Decimal, packaging: PackagingRef | None
    ) -> Decimal:
        """Quantité en unité de base (mécanisme commun du catalogue, Lot 3-B / 3-C)."""
        return base_quantity(ref, quantity, packaging)

    def _apply_input(self, sale: Sale, data: SaleInput) -> None:
        """Lignes au prix du catalogue (jamais au prix du client) — prix de l'article ou du
        conditionnement vendu ; quantité de base et montants recalculés."""
        refs = self._articles([(line.article_id, line.packaging_id) for line in data.lines])
        # Recette, étape 1 (ADR-0046, D3) : contrôle de saisie — article de l'assortiment ACTIF
        # du site de la vente, sans ajout automatique ; la validation revérifie sous verrou.
        ensure_in_assortment(self.db, sale.site_id, {line.article_id for line in data.lines})
        packagings = self._packagings(data.lines)
        self._customer(data.customer_id)
        sale.customer_id = data.customer_id
        sale.sale_date = self._sale_date(data.sale_date)
        sale.notes = data.notes
        lines = []
        for index, line in enumerate(data.lines, start=1):
            quantity = line.quantity.quantize(QUANTITY_STEP)
            ref = refs[line.article_id]
            packaging = packagings[line.packaging_id] if line.packaging_id else None
            price = _packaging_price(packaging) if packaging else ref.sale_price
            lines.append(
                SaleLine(
                    tenant_id=self.ctx.tenant_id,
                    line_no=index,
                    article_id=line.article_id,
                    quantity=quantity,
                    unit_price=price,
                    line_total=round_money(quantity * price),
                    packaging_id=packaging.id if packaging else None,
                    packaging_name=packaging.name if packaging else None,
                    packaging_conversion=packaging.conversion if packaging else None,
                    base_quantity=self._base_quantity(ref, quantity, packaging),
                )
            )
        sale.lines = lines
        sale.subtotal = sum((line.line_total for line in sale.lines), ZERO)
        sale.total = sale.subtotal  # ni remise ni taxe dans cette phase

    def _snapshot(self, sale: Sale) -> dict[str, Any]:
        return {
            "customer_id": str(sale.customer_id) if sale.customer_id else None,
            "sale_date": sale.sale_date.isoformat(),
            "notes": sale.notes,
            "total": format(sale.total, "f"),
            "lines": [
                {
                    "article_id": str(line.article_id),
                    "quantity": format(line.quantity, "f"),
                    "unit_price": format(line.unit_price, "f"),
                    **(
                        {
                            "packaging_id": str(line.packaging_id),
                            "packaging_name": line.packaging_name,
                            "base_quantity": format(line.base_quantity, "f"),
                        }
                        if line.packaging_id
                        else {}
                    ),
                }
                for line in sale.lines
            ],
        }

    def _audit(self, action: str, sale: Sale, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"sale.{action}",
            entity_type="sale",
            entity_id=sale.id,
            site_id=sale.site_id,
            data={"number": sale.number, **data},
        )

    def _check_credit(self, sale: Sale, prepaid: Decimal, override: CreditOverride | None) -> None:
        """Vente à crédit (Lot 1) : reste dû à la validation (total − encaissements immédiats).
        Client obligatoire ; permission ``sales.sale.credit_create`` sur le site ; limite de
        crédit (ADR-0021) : l'exposition projetée du client (restes dus de ses ventes validées,
        tous sites) ne dépasse pas sa limite — sinon, dépassement exceptionnel seulement avec
        ``sales.sale.credit_override`` et une justification (autorisateur, date, montant et
        audit enregistrés). Limite nulle = non configurée. Verrou du client (après celui de la
        vente, ordre constant) : deux validations simultanées pour le même client s'exécutent
        l'une après l'autre et la seconde voit l'exposition créée par la première."""
        new_exposure = max(sale.total - prepaid, MONEY_ZERO)
        sale.is_credit = new_exposure > 0
        if not sale.is_credit:
            return
        if sale.customer_id is None:
            raise BusinessRuleError(
                "Une vente à crédit exige un client identifié",
                code="credit_customer_required",
                extra={"remaining": format(new_exposure, "f")},
            )
        if not site_can(self.ctx, sale.site_id, CREDIT_CREATE):
            raise ForbiddenError(
                "Vous n'êtes pas autorisé à vendre à crédit", code="credit_not_allowed"
            )
        credit = get_customer_credit(self.db, sale.customer_id, lock=True)
        if credit is None or credit.credit_limit is None:
            return
        current, _ = customer_exposure(self.db, sale.customer_id)
        excess = current + new_exposure - credit.credit_limit
        if excess <= 0:
            return
        can_override = site_can(self.ctx, sale.site_id, CREDIT_OVERRIDE)
        if override is None:
            extra: dict[str, Any] = {
                "credit_limit": format(credit.credit_limit, "f"),
                "sale_exposure": format(new_exposure, "f"),
                "override_allowed": can_override,
            }
            # Exposition consolidée (tous sites) : seulement pour qui voit tous les sites.
            if sees_all_sites(self.ctx):
                extra["current_exposure"] = format(current, "f")
                extra["available_credit"] = format(
                    max(credit.credit_limit - current, MONEY_ZERO), "f"
                )
            raise BusinessRuleError(
                "La limite de crédit du client serait dépassée",
                code="credit_limit_exceeded",
                extra=extra,
            )
        if not can_override:
            raise ForbiddenError(
                "Vous n'êtes pas autorisé à dépasser la limite de crédit",
                code="credit_override_not_allowed",
            )
        sale.credit_override_by = self.ctx.user.id
        sale.credit_override_at = self.now
        sale.credit_override_reason = override.reason
        sale.credit_override_amount = excess
        self._audit(
            "credit_limit_overridden",
            sale,
            {
                "customer_id": str(sale.customer_id),
                "credit_limit": format(credit.credit_limit, "f"),
                "current_exposure": format(current, "f"),
                "sale_exposure": format(new_exposure, "f"),
                "excess": format(excess, "f"),
                "reason": override.reason,
            },
        )

    def _assign_number(self, sale: Sale) -> None:
        """Numéro définitif ``VENT-{SITE}-{ANNÉE}-{SÉQUENCE}`` à la validation : compteur par
        tenant, site et année (BIGINT, ligne verrouillée jusqu'à la fin de la transaction) ; 6
        chiffres minimum, sans limite. Un brouillon n'a jamais de numéro."""
        site = self.db.get(Site, sale.site_id)
        assert site is not None
        year = tenant_today(self.ctx, self.now).year
        sale.number = next_number(
            self.db,
            self.ctx.tenant_id,
            site_sequence_key(site.id, SEQUENCE_KIND, year),
            f"{PREFIX}-{site.code.upper()}-{year}",
        )

    def _stock(self) -> StockService:
        return StockService(self.db, self.ctx.tenant_id, self.ctx.user.id, self.now)

    # --- Écritures ----------------------------------------------------------------------------

    def create(
        self,
        data: SaleCreate,
        *,
        channel: SaleChannel = SaleChannel.BACKOFFICE,
        idempotency_key: uuid.UUID | None = None,
    ) -> Sale:
        site_id = operation_site(self.ctx, data.site_id)
        sale = Sale(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            number=None,  # attribué à la validation
            status=SaleStatus.DRAFT,
            channel=channel,
            idempotency_key=idempotency_key,
            created_by=self.ctx.user.id,
        )
        self._apply_input(sale, data)
        self.db.add(sale)
        self.db.flush()
        self._audit(
            "created",
            sale,
            {"status": sale.status.value, "channel": channel.value, **self._snapshot(sale)},
        )
        return sale

    def checkout(
        self, data: SaleCheckout, channel: SaleChannel = SaleChannel.POS
    ) -> tuple[Sale, bool]:
        """Encaissement en une étape (POS, ADR-0023) : ``create`` puis ``validate`` (stock via
        StockService, limite de crédit, paiements immédiats via PaymentService — caisse pour
        les espèces) dans la transaction de la requête. Toute erreur annule tout : ni
        brouillon, ni stock, ni paiement, ni mouvement de caisse. Idempotent : la même clé
        renvoie la vente déjà enregistrée ``(vente, rejouée)``."""
        # Deux soumissions simultanées de la même clé s'exécutent l'une après l'autre.
        self.db.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(str(data.idempotency_key), 0)))
        )
        existing = self.db.scalars(
            select(Sale).where(Sale.idempotency_key == data.idempotency_key)
        ).one_or_none()
        if existing is not None:
            ensure_document_site(self.ctx, existing.site_id, "sale_not_found")
            return existing, True
        sale = self.create(data, channel=channel, idempotency_key=data.idempotency_key)
        return (
            self.validate(sale.id, data.payments, data.credit_override, data.expired_lot_override),
            False,
        )

    def create_from_order(
        self,
        *,
        site_id: uuid.UUID,
        origin_type: str,
        origin_id: uuid.UUID,
        customer_id: uuid.UUID | None,
        lines: Sequence[OriginLine],
        notes: str | None,
        idempotency_key: uuid.UUID,
        payments: Sequence[PaymentCreate] = (),
        credit_override: CreditOverride | None = None,
        expired_lot_override: ExpiredLotOverride | None = None,
    ) -> Sale:
        """Règlement d'un document d'origine (ADR-0049, R2-D) : vente du canal ``RESTAURANT``
        créée puis VALIDÉE dans la transaction de l'appelant — même ``validate`` que toute
        vente (articles actifs, assortiment, conditionnements, quantités, crédit et limite,
        stock via ``StockService.consume`` : FEFO, lots, CMUP, dérogation ``expired_lot_override``,
        paiements et caisse). Seule différence : les prix sont ceux FIGÉS sur l'origine (aucun
        ``sale_prices_changed``). Origine posée ici par le serveur, immuable ; au plus une vente
        active par origine (index ``uq_sales_active_origin``). Toute erreur annule tout."""
        site_id = operation_site(self.ctx, site_id)
        refs = self._articles([(line.article_id, line.packaging_id) for line in lines])
        ensure_in_assortment(self.db, site_id, {line.article_id for line in lines})
        self._packagings(
            [
                SaleLineInput(
                    article_id=line.article_id, packaging_id=line.packaging_id, quantity=Decimal(1)
                )
                for line in lines
            ]
        )
        self._customer(customer_id)
        sale = Sale(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            number=None,
            status=SaleStatus.DRAFT,
            channel=SaleChannel.RESTAURANT,
            idempotency_key=idempotency_key,
            created_by=self.ctx.user.id,
            customer_id=customer_id,
            sale_date=tenant_today(self.ctx, self.now),
            notes=notes,
            origin_type=origin_type,
            origin_id=origin_id,
        )
        sale.lines = [
            SaleLine(
                tenant_id=self.ctx.tenant_id,
                line_no=index,
                article_id=line.article_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_total=line.line_total,
                packaging_id=line.packaging_id,
                packaging_name=line.packaging_name,
                packaging_conversion=line.packaging_conversion,
                base_quantity=line.quantity * (line.packaging_conversion or Decimal(1)),
            )
            for index, line in enumerate(lines, start=1)
        ]
        # Article à quantités entières : contrôle commun du catalogue (ADR-0040).
        for line in lines:
            ensure_whole(refs[line.article_id], line.quantity)
        sale.subtotal = sum((line.line_total for line in sale.lines), ZERO)
        sale.total = sale.subtotal
        self.db.add(sale)
        try:
            self.db.flush()
        except IntegrityError as exc:
            # Défense en profondeur : l'appelant sérialise déjà sous le verrou de son document.
            if ACTIVE_ORIGIN_INDEX in str(exc.orig):
                raise ConflictError(
                    "Une vente active existe déjà pour ce document", code="sale_origin_active"
                ) from exc
            raise
        self._audit(
            "created",
            sale,
            {
                "status": sale.status.value,
                "channel": SaleChannel.RESTAURANT.value,
                "origin_type": origin_type,
                "origin_id": str(origin_id),
                **self._snapshot(sale),
            },
        )
        return self.validate(sale.id, payments, credit_override, expired_lot_override)

    def update(self, sale_id: uuid.UUID, data: SaleInput) -> Sale:
        """Brouillon seulement ; lignes remplacées et prix relus dans le catalogue."""
        sale = self.get(sale_id, lock=True)
        self._require_status(sale, SaleStatus.DRAFT, "sale_not_draft")
        before = self._snapshot(sale)
        sale.lines = []
        self.db.flush()
        self._apply_input(sale, data)
        self.db.flush()
        after = self._snapshot(sale)
        if before != after:
            self._audit("updated", sale, {"before": before, "after": after})
        return sale

    def validate(
        self,
        sale_id: uuid.UUID,
        payments: Sequence[PaymentCreate] = (),
        credit_override: CreditOverride | None = None,
        expired_lot_override: ExpiredLotOverride | None = None,
    ) -> Sale:
        """Validation (sortie de stock) et, facultativement, encaissements immédiats dans la
        même transaction : une vente payée comptant à la validation ne crée aucune exposition
        de crédit. Les paiements passent par ``PaymentService`` (mêmes règles qu'en 2.7).

        Lot 3-H-A (ADR-0045) : un article suivi par lot est consommé par le moteur central en
        FEFO / FIFO sur les lots non périmés (le vendeur ne choisit pas de lot) ; un lot périmé
        n'est vendu que par une dérogation explicite (``expired_lot_override``, O-1)."""
        sale = self.get(sale_id, lock=True)
        self._require_status(sale, SaleStatus.DRAFT, "sale_not_draft")
        if not sale.lines:
            raise BusinessRuleError("Aucune ligne à valider", code="sale_empty")
        refs = self._articles([(line.article_id, line.packaging_id) for line in sale.lines])
        self._customer(sale.customer_id)
        # Lot 3-B : conditionnements relus (existant, de l'article, actif) et règle des
        # quantités entières revérifiée — la conversion relue doit être celle de la ligne.
        packagings = self._packagings(sale.lines)
        for line in sale.lines:
            line_packaging = packagings.get(line.packaging_id) if line.packaging_id else None
            self._base_quantity(refs[line.article_id], line.quantity, line_packaging)
        # Le total annoncé au client doit rester exact : un prix catalogue (article ou
        # conditionnement) modifié depuis l'enregistrement du brouillon impose de le
        # réenregistrer (prix relus).
        # Vente issue d'une origine (R2-D) : prix FIGÉS sur l'origine, seule dispense du contrôle
        # des prix ; la conversion d'un conditionnement reste contrôlée.
        frozen_prices = sale.origin_type is not None
        changed = [
            refs[line.article_id].reference
            for line in sale.lines
            if (
                not frozen_prices
                and _current_price(line, refs[line.article_id], packagings) != line.unit_price
            )
            or (
                line.packaging_id is not None
                and packagings[line.packaging_id].conversion != line.packaging_conversion
            )
        ]
        if changed:
            raise ConflictError(
                "Des prix ont changé depuis l'enregistrement de la vente",
                code="sale_prices_changed",
                extra={"articles": changed},
            )
        from app.modules.sales.payment_service import PaymentService

        payment_service = PaymentService(self.db, self.ctx, self.now)
        planned = payment_service.plan(sale.site_id, sale.total, payments)
        self._check_credit(
            sale, sum((amount for _, amount in planned), MONEY_ZERO), credit_override
        )
        self._assign_number(sale)
        # Lot 3-A (ADR-0039) : « géré en stock » relu par le serveur, sous verrou partagé de
        # l'article ; un article non géré (service) se vend sans mouvement ni contrôle de stock.
        managed = lock_stock_managed(self.db, {line.article_id for line in sale.lines})
        # Recette, étape 1 (ADR-0046, D3) : contrôle FAISANT FOI, sous verrou partagé de
        # l'assortiment (article → assortiment → niveaux → lots) — y compris pour un article non
        # géré en stock, qui ne passe pas par ``StockService`` (point de vente compris).
        ensure_in_assortment(
            self.db, sale.site_id, {line.article_id for line in sale.lines}, lock=True
        )
        stocked = [line for line in sale.lines if managed.get(line.article_id, True)]
        picks = self._expired_lot_picks(sale, stocked, expired_lot_override)
        # Sortie de stock : exclusivement via le moteur central (verrous, lots, tout ou rien).
        movements = (
            self._stock().consume(
                sale.site_id,
                [
                    ConsumptionRequest(
                        article_id=line.article_id,
                        movement_type=MovementType.SALE,
                        # Toujours en unité de base (Lot 3-B) : 2 cartons de 24 → −48.
                        quantity=line.base_quantity,
                        source_type=SOURCE_TYPE,
                        source_id=sale.id,
                        source_line_id=line.id,
                        source_number=sale.number,
                        packaging=_snapshot_of(line),
                        picks=picks.get(line.id, ()),
                    )
                    for line in stocked
                ],
                today=tenant_today(self.ctx, self.now),
            )
            if stocked
            else []
        )
        consumed = self._consumed_lots(movements)
        if expired_lot_override is not None:
            self._record_expired_override(sale, expired_lot_override, picks, consumed)
        sale.status = SaleStatus.VALIDATED
        sale.validated_at = self.now
        sale.validated_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "validated",
            sale,
            {
                "previous_status": SaleStatus.DRAFT.value,
                "status": SaleStatus.VALIDATED.value,
                "total": format(sale.total, "f"),
                "lines": len(sale.lines),
                "customer_id": str(sale.customer_id) if sale.customer_id else None,
                "is_credit": sale.is_credit,
                **({"lots": list(consumed.values())} if consumed else {}),
            },
        )
        for payment, _ in planned:
            payment_service.create(sale.id, payment)
        return sale

    def _expired_lot_picks(
        self,
        sale: Sale,
        stocked: Sequence[SaleLine],
        override: ExpiredLotOverride | None,
    ) -> dict[uuid.UUID, tuple[LotPick, ...]]:
        """Dérogation (O-1) : permission sur le site de la vente, lots EXPLICITEMENT désignés,
        de l'article, effectivement périmés (jamais un lot valide choisi par le vendeur : le
        FEFO décide), quantités imputées sur les lignes de l'article dans leur ordre, au plus
        leur quantité. Sans dérogation : aucun choix (FEFO seul, lots périmés exclus)."""
        if override is None:
            return {}
        if not site_can(self.ctx, sale.site_id, EXPIRED_LOT_OVERRIDE):
            raise ForbiddenError(
                "Vous n'êtes pas autorisé à vendre un lot périmé",
                code="expired_lot_override_not_allowed",
            )
        lines_by_article: dict[uuid.UUID, list[SaleLine]] = {}
        for line in stocked:
            lines_by_article.setdefault(line.article_id, []).append(line)
        refs = get_article_refs(self.db, set(lines_by_article))
        flags = lock_lot_flags(self.db, set(lines_by_article))
        infos = lot_infos(self.db, {pick.lot_id for pick in override.lots})
        today = tenant_today(self.ctx, self.now)
        picks: dict[uuid.UUID, list[LotPick]] = {}
        seen: set[uuid.UUID] = set()
        for pick in override.lots:
            article_lines = lines_by_article.get(pick.article_id)
            info = infos.get(pick.lot_id)
            if not article_lines or info is None or info.article_id != pick.article_id:
                raise BusinessRuleError(
                    "Ce lot n'est pas disponible pour un article de la vente",
                    code="lot_not_available",
                    extra={"lot_id": str(pick.lot_id)},
                )
            reference = refs[pick.article_id].reference
            if pick.lot_id in seen:
                raise BusinessRuleError(
                    "Un même lot est désigné plusieurs fois",
                    code="duplicate_lot_allocation",
                    extra={"articles": [reference], "lots": [info.number]},
                )
            seen.add(pick.lot_id)
            if not is_expired_lot(info, flags[pick.article_id], today):
                raise BusinessRuleError(
                    "Seul un lot périmé peut être désigné par une dérogation : les autres lots "
                    "sont choisis automatiquement (FEFO)",
                    code="lot_not_expired",
                    extra={"articles": [reference], "lots": [info.number]},
                )
            quantity = pick.quantity.quantize(QUANTITY_STEP)
            ensure_whole(refs[pick.article_id], quantity)
            for line in article_lines:
                if quantity == 0:
                    break
                taken = min(
                    quantity,
                    line.base_quantity - sum((p.quantity for p in picks.get(line.id, [])), ZERO),
                )
                if taken > 0:
                    picks.setdefault(line.id, []).append(LotPick(pick.lot_id, taken))
                    quantity -= taken
            if quantity > 0:
                raise BusinessRuleError(
                    "La quantité désignée dépasse la quantité vendue de l'article",
                    code="lot_allocation_exceeds",
                    extra={"articles": [reference], "lots": [info.number]},
                )
        return {line_id: tuple(items) for line_id, items in picks.items()}

    def _consumed_lots(self, movements: Sequence[Any]) -> dict[uuid.UUID, dict[str, str | None]]:
        """Lots consommés (mouvement → détail d'audit) ; la traçabilité reste le journal."""
        with_lot = [m for m in movements if m.lot_id is not None]
        if not with_lot:
            return {}
        refs = get_article_refs(self.db, {m.article_id for m in with_lot})
        infos = lot_infos(self.db, {m.lot_id for m in with_lot})
        result: dict[uuid.UUID, dict[str, str | None]] = {}
        for m in with_lot:
            info = infos[m.lot_id]
            result[m.id] = {
                "article_id": str(m.article_id),
                "reference": refs[m.article_id].reference,
                "lot_id": str(m.lot_id),
                "lot_number": info.number,
                "expiry_date": info.expiry_date.isoformat() if info.expiry_date else None,
                "base_quantity": format(-m.quantity, "f"),
            }
        return result

    def _record_expired_override(
        self,
        sale: Sale,
        override: ExpiredLotOverride,
        picks: dict[uuid.UUID, tuple[LotPick, ...]],
        consumed: dict[uuid.UUID, dict[str, str | None]],
    ) -> None:
        """Dérogation tracée (O-1) : auteur, date et motif sur la vente ; audit dédié avec la
        vente, les articles, les lots périmés et leurs quantités."""
        expired_ids = {str(p.lot_id) for items in picks.values() for p in items}
        sale.expired_lot_override_by = self.ctx.user.id
        sale.expired_lot_override_at = self.now
        sale.expired_lot_override_reason = override.reason
        self._audit(
            "expired_lot_overridden",
            sale,
            {
                "reason": override.reason,
                "lots": [d for d in consumed.values() if d["lot_id"] in expired_ids],
            },
        )

    def cancel(self, sale_id: uuid.UUID, reason: str) -> Sale:
        """Brouillon : abandon sans effet sur le stock. Vente validée : mouvements inverses
        ``CANCELLATION`` (remise en stock au coût de la sortie, CMUP inchangé, STK-06), liés
        aux mouvements ``SALE`` d'origine ; les mouvements historiques ne sont jamais modifiés.

        Vente issue d'une origine (R2-D) : le gestionnaire du module d'origine verrouille son
        document AVANT le verrou de la vente (ordre du règlement) et peut refuser (ex.
        ``order_closed``) ; sans gestionnaire enregistré : ``409 sale_origin_unavailable``."""
        current = self.get(sale_id)
        if (
            current.origin_type is not None
            and current.origin_id is not None
            and not notify_before_cancel(
                self.db,
                self.ctx,
                self.now,
                origin_type=current.origin_type,
                origin_id=current.origin_id,
                sale_id=current.id,
            )
        ):
            raise ConflictError(
                "L'origine de cette vente n'est pas disponible : annulation impossible",
                code="sale_origin_unavailable",
                extra={"origin_type": current.origin_type},
            )
        sale = self.get(sale_id, lock=True)
        previous = sale.status
        skipped: list[uuid.UUID] = []
        restored: list[SaleLine] = []
        origins: dict[uuid.UUID, Any] = {}
        restored_lots = False
        if previous is SaleStatus.CANCELLED:
            raise ConflictError("Vente déjà annulée", code="sale_already_cancelled")
        if previous is SaleStatus.VALIDATED:
            # Une vente encaissée ne s'annule pas : annuler d'abord ses paiements (le
            # remboursement est hors périmètre). Verrou de la vente : aucun paiement concurrent.
            active = self.db.scalar(
                select(func.count()).where(
                    Payment.sale_id == sale.id,
                    Payment.status.in_((PaymentStatus.COMPLETED, PaymentStatus.PENDING)),
                )
            )
            if active:
                raise ConflictError(
                    "Annulez d'abord les paiements de cette vente",
                    code="sale_has_payments",
                    extra={"payments": int(active)},
                )
            origins = self._stock().movements_of(sale.id, MovementType.SALE)
            # Seules les lignes sorties du stock à la validation (mouvement ``SALE``) y
            # reviennent ; un article devenu non géré entre-temps (stock nul partout exigé) ne
            # reçoit aucun stock (Lot 3-A).
            managed = lock_stock_managed(self.db, {line.article_id for line in sale.lines})
            restored = [
                line
                for line in sale.lines
                if line.id in origins and managed.get(line.article_id, True)
            ]
            skipped = [
                line.article_id
                for line in sale.lines
                if line.id in origins and not managed.get(line.article_id, True)
            ]
            if restored:
                # Un inverse par mouvement d'origine : même quantité, même coût, MÊME lot —
                # restauration exacte, y compris sur un lot devenu périmé (H-D10) ; jamais sur
                # un autre lot. Double annulation : refusée (statut verrouillé + unicité).
                restored_lots = any(
                    origin.lot_id is not None for line in restored for origin in origins[line.id]
                )
                self._stock().apply(
                    sale.site_id,
                    [
                        MovementRequest(
                            article_id=line.article_id,
                            movement_type=MovementType.CANCELLATION,
                            quantity=-origin.quantity,
                            unit_cost=origin.unit_cost,
                            source_type=SOURCE_TYPE,
                            source_id=sale.id,
                            source_line_id=line.id,
                            source_number=sale.number,
                            origin_movement_id=origin.id,
                            comment=f"Annulation {sale.number}",
                            packaging=inverse_packaging(
                                origin, _snapshot_of(line), single=len(origins[line.id]) == 1
                            ),
                            lot_id=origin.lot_id,
                        )
                        for line in restored
                        for origin in origins[line.id]
                    ],
                )
        sale.status = SaleStatus.CANCELLED
        sale.cancelled_at = self.now
        sale.cancelled_by = self.ctx.user.id
        sale.cancellation_reason = reason
        self.db.flush()
        self._audit(
            "cancelled",
            sale,
            {
                "previous_status": previous.value,
                "status": SaleStatus.CANCELLED.value,
                "reason": reason,
                "stock_restored": previous is SaleStatus.VALIDATED,
                "total": format(sale.total, "f"),
                **({"lots": self._restored_lots(restored, origins)} if restored_lots else {}),
                **(
                    {"not_restored_unmanaged": self._references(skipped)}
                    if previous is SaleStatus.VALIDATED and skipped
                    else {}
                ),
            },
        )
        return sale

    def _restored_lots(
        self, restored: Sequence[SaleLine], origins: dict[uuid.UUID, Any]
    ) -> list[dict[str, str]]:
        """Lots restaurés par l'annulation (audit) : exactement ceux des mouvements d'origine."""
        pairs = [
            (line, origin)
            for line in restored
            for origin in origins[line.id]
            if origin.lot_id is not None
        ]
        refs = get_article_refs(self.db, {line.article_id for line, _ in pairs})
        infos = lot_infos(self.db, {origin.lot_id for _, origin in pairs})
        return [
            {
                "reference": refs[line.article_id].reference,
                "lot_id": str(origin.lot_id),
                "lot_number": infos[origin.lot_id].number,
                "base_quantity": format(-origin.quantity, "f"),
            }
            for line, origin in pairs
        ]

    # --- Sortie API ---------------------------------------------------------------------------

    def to_out(self, sales: Sequence[Sale], *, with_lines: bool = False) -> list[SaleOut]:
        users = {
            u
            for s in sales
            for u in (
                s.created_by,
                s.validated_by,
                s.cancelled_by,
                s.credit_override_by,
                s.expired_lot_override_by,
            )
            if u
        }
        user_names = _names(self.db, User, users, User.full_name)
        site_names = _names(self.db, Site, {s.site_id for s in sales}, Site.name)
        customers = get_customer_refs(self.db, {s.customer_id for s in sales if s.customer_id})
        refs = (
            get_article_refs(self.db, {line.article_id for s in sales for line in s.lines})
            if with_lines
            else {}
        )
        # Lot 3-H-A : répartition par lot des lignes (journal des mouvements ``SALE``).
        allocations = (
            lot_allocations(
                self.db,
                {s.id for s in sales if s.status is not SaleStatus.DRAFT},
                MovementType.SALE,
            )
            if with_lines
            else {}
        )
        # Montants payés des ventes validées : une seule agrégation pour toute la page.
        paid = paid_amounts(
            self.db, {s.id for s in sales if s.status is SaleStatus.VALIDATED or s.is_credit}
        )
        result = []
        for sale in sales:
            customer = customers.get(sale.customer_id) if sale.customer_id else None
            validated = sale.status is SaleStatus.VALIDATED
            sale_paid = paid.get(sale.id, MONEY_ZERO)
            result.append(
                SaleOut(
                    id=sale.id,
                    number=sale.number,
                    site_id=sale.site_id,
                    site_name=site_names.get(sale.site_id, ""),
                    customer_id=sale.customer_id,
                    customer_code=customer.code if customer else None,
                    customer_name=customer.name if customer else None,
                    status=sale.status,
                    channel=sale.channel,
                    sale_date=sale.sale_date,
                    notes=sale.notes,
                    subtotal=sale.subtotal,
                    total=sale.total,
                    line_count=len(sale.lines),
                    created_at=sale.created_at,
                    updated_at=sale.updated_at,
                    created_by_name=user_names.get(sale.created_by),
                    validated_at=sale.validated_at,
                    validated_by_name=user_names.get(sale.validated_by),
                    cancelled_at=sale.cancelled_at,
                    cancelled_by_name=user_names.get(sale.cancelled_by),
                    cancellation_reason=sale.cancellation_reason,
                    paid_amount=sale_paid if validated else None,
                    remaining_amount=max(sale.total - sale_paid, MONEY_ZERO) if validated else None,
                    payment_status=payment_status(sale.total, sale_paid) if validated else None,
                    is_credit=sale.is_credit,
                    credit_status=credit_status(sale, sale_paid),
                    credit_override_at=sale.credit_override_at,
                    credit_override_by_name=user_names.get(sale.credit_override_by),
                    credit_override_reason=sale.credit_override_reason,
                    credit_override_amount=sale.credit_override_amount,
                    expired_lot_override_at=sale.expired_lot_override_at,
                    expired_lot_override_by_name=user_names.get(sale.expired_lot_override_by),
                    expired_lot_override_reason=sale.expired_lot_override_reason,
                    origin_type=sale.origin_type,
                    origin_id=sale.origin_id,
                    lines=[
                        _line_out(line, refs, allocations.get(line.id, [])) for line in sale.lines
                    ]
                    if with_lines
                    else [],
                )
            )
        return result


def _names(db: Session, model: Any, ids: set[Any], column: Any) -> dict[Any, str]:
    if not ids:
        return {}
    return {row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(ids)))}


def _current_price(
    line: SaleLine, ref: ArticleRef, packagings: dict[uuid.UUID, PackagingRef]
) -> Decimal:
    """Prix catalogue actuel de la présentation de la ligne (article ou conditionnement)."""
    return _packaging_price(packagings[line.packaging_id]) if line.packaging_id else ref.sale_price


def _snapshot_of(line: SaleLine) -> PackagingSnapshot | None:
    """Présentation vendue, conservée sur le mouvement de stock (Lot 3-C : « 2 Carton 24 »)."""
    if line.packaging_id is None or line.packaging_name is None:
        return None
    assert line.packaging_conversion is not None
    return PackagingSnapshot(
        packaging_id=line.packaging_id,
        name=line.packaging_name,
        conversion=line.packaging_conversion,
        quantity=line.quantity,
    )


def _packaging_price(packaging: PackagingRef) -> Decimal:
    """Prix d'un conditionnement déjà contrôlé par ``_packagings`` (prix configuré)."""
    assert packaging.sale_price is not None
    return packaging.sale_price


def packagings_used(db: Session, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Port du catalogue (Lot 3-B) : conditionnements figurant sur au moins une ligne de vente
    du tenant (brouillons compris) — leur conversion est alors figée."""
    rows = db.scalars(
        select(SaleLine.packaging_id).where(SaleLine.packaging_id.in_(ids)).distinct()
    )
    return {packaging_id for packaging_id in rows if packaging_id is not None}


def _line_out(
    line: SaleLine, refs: dict[uuid.UUID, ArticleRef], lots: list[LotAllocation]
) -> SaleLineOut:
    ref = refs.get(line.article_id)
    return SaleLineOut(
        id=line.id,
        line_no=line.line_no,
        article_id=line.article_id,
        article_reference=ref.reference if ref else "?",
        article_designation=ref.designation if ref else "?",
        unit=ref.unit if ref else "",
        quantity=line.quantity,
        unit_price=line.unit_price,
        line_total=line.line_total,
        weighted_unit_price=line.line_total != round_money(line.quantity * line.unit_price),
        packaging_id=line.packaging_id,
        packaging_name=line.packaging_name,
        packaging_conversion=line.packaging_conversion,
        base_quantity=line.base_quantity,
        lots=[
            SaleLineLotOut(
                lot_id=a.lot_id,
                lot_number=a.lot_number,
                expiry_date=a.expiry_date,
                quantity=a.quantity,
            )
            for a in lots
        ],
    )
