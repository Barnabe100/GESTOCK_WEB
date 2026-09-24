"""Ventes comptant : brouillon → validée (sortie de stock) → annulée.

La validation s'exécute dans UNE transaction (celle de la requête) : verrou de la vente,
contrôles, verrou du client et contrôle de sa limite de crédit (ADR-0021),
``StockService.apply`` (verrou des niveaux, contrôle global du stock, mouvements ``SALE``),
changement de statut, audit, encaissements immédiats éventuels. Toute erreur annule tout : ni
stock, ni mouvement, ni statut, ni paiement, ni audit. Le service ne valide jamais la
transaction (ADR-0008).
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import ArticleRef, get_article_refs
from app.modules.customers.api import (
    CustomerRef,
    customers_view,
    get_customer_credit,
    get_customer_refs,
)
from app.modules.sales.credit import customer_exposure
from app.modules.sales.models import (
    Payment,
    PaymentStatus,
    Sale,
    SaleLine,
    SalePaymentStatus,
    SaleStatus,
)
from app.modules.sales.payment_service import ZERO as MONEY_ZERO
from app.modules.sales.payment_service import paid_amounts, paid_subquery, payment_status
from app.modules.sales.schemas import (
    PaymentCreate,
    SaleCreate,
    SaleInput,
    SaleLineOut,
    SaleOut,
)
from app.modules.stock.api import (
    MovementRequest,
    MovementType,
    StockService,
    ensure_document_site,
    operation_site,
    round_money,
    sees_all_sites,
    tenant_today,
    visible_site_ids,
)
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.sequences.service import next_number
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter

SOURCE_TYPE = "sale"
SEQUENCE_KEY = "sale"
PREFIX = "VTE"
ZERO = Decimal("0")
QUANTITY_STEP = Decimal("0.001")  # NUMERIC(18,3) : même valeur en réponse, en audit et en base

SORTABLE = {
    "number": Sale.number,
    "sale_date": Sale.sale_date,
    "total": Sale.total,
    "created_at": Sale.created_at,
}


class SaleService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Lecture ------------------------------------------------------------------------------

    def search(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        status: SaleStatus | None = None,
        site_id: uuid.UUID | None = None,
        customer_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        payment: SalePaymentStatus | None = None,
    ) -> tuple[list[Sale], int]:
        # Périmètre : ventes des sites visibles par le membre (site sélectionné ou ses sites).
        stmt: Select[tuple[Sale]] = select(Sale).where(Sale.site_id.in_(visible_site_ids(self.ctx)))
        by_number = search_filter(search, Sale.number)
        if by_number is not None:
            # Numéro de vente, ou code / nom / téléphone du client.
            customers = customers_view()
            matching = select(customers.c.id).where(customers.c.tenant_id == self.ctx.tenant_id)
            by_customer = search_filter(
                search, customers.c.code, customers.c.name, customers.c.phone
            )
            if by_customer is not None:
                matching = matching.where(by_customer)
            stmt = stmt.where(or_(by_number, Sale.customer_id.in_(matching)))
        conditions = [
            Sale.status == status if status else None,
            Sale.site_id == site_id if site_id else None,
            Sale.customer_id == customer_id if customer_id else None,
            Sale.sale_date >= date_from if date_from else None,
            Sale.sale_date <= date_to if date_to else None,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        if payment is not None:
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
                }[payment]
            )
        stmt = apply_sort(stmt, params.sort, SORTABLE, "-number", Sale.id)
        return paginate(self.db, stmt, params)

    def get(self, sale_id: uuid.UUID, *, lock: bool = False) -> Sale:
        stmt = select(Sale).where(Sale.id == sale_id)
        if lock:
            # Verrou du document : deux validations simultanées s'exécutent l'une après l'autre ;
            # la seconde trouve la vente validée et échoue proprement (idempotence).
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        sale = self.db.scalars(stmt).one_or_none()
        if sale is None:
            raise NotFoundError("Vente introuvable", code="sale_not_found")
        ensure_document_site(self.ctx, sale.site_id, "sale_not_found")
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

    def _articles(self, article_ids: list[uuid.UUID]) -> dict[uuid.UUID, ArticleRef]:
        """Articles du tenant (RLS), existants, actifs, chacun sur une seule ligne."""
        if len(set(article_ids)) != len(article_ids):
            raise BusinessRuleError(
                "Un article apparaît sur plusieurs lignes", code="duplicate_article_line"
            )
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

    def _apply_input(self, sale: Sale, data: SaleInput) -> None:
        """Lignes au prix du catalogue (jamais au prix du client) ; montants recalculés."""
        refs = self._articles([line.article_id for line in data.lines])
        self._customer(data.customer_id)
        sale.customer_id = data.customer_id
        sale.sale_date = self._sale_date(data.sale_date)
        sale.notes = data.notes
        lines = []
        for index, line in enumerate(data.lines, start=1):
            quantity = line.quantity.quantize(QUANTITY_STEP)
            price = refs[line.article_id].sale_price
            lines.append(
                SaleLine(
                    tenant_id=self.ctx.tenant_id,
                    line_no=index,
                    article_id=line.article_id,
                    quantity=quantity,
                    unit_price=price,
                    line_total=round_money(quantity * price),
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

    def _check_credit(self, sale: Sale, prepaid: Decimal) -> None:
        """Limite de crédit (ADR-0021) : la validation crée une exposition égale au reste dû de
        la vente (total − encaissements immédiats). Refusée si l'exposition projetée du client
        (restes dus de ses ventes validées, tous sites) dépasse sa limite. Limite nulle = non
        configurée : aucun contrôle ; vente entièrement payée : aucune exposition, aucun
        contrôle. Verrou du client (après celui de la vente, ordre constant) : deux validations
        simultanées pour le même client s'exécutent l'une après l'autre et la seconde voit
        l'exposition créée par la première."""
        if sale.customer_id is None:
            return  # vente sans client : aucune exposition client
        credit = get_customer_credit(self.db, sale.customer_id, lock=True)
        if credit is None or credit.credit_limit is None:
            return
        new_exposure = max(sale.total - prepaid, MONEY_ZERO)
        if new_exposure <= 0:
            return
        current, _ = customer_exposure(self.db, sale.customer_id)
        if current + new_exposure <= credit.credit_limit:
            return
        extra: dict[str, Any] = {
            "credit_limit": format(credit.credit_limit, "f"),
            "sale_exposure": format(new_exposure, "f"),
        }
        # Exposition consolidée (tous sites) : seulement pour qui voit tous les sites.
        if sees_all_sites(self.ctx):
            extra["current_exposure"] = format(current, "f")
            extra["available_credit"] = format(max(credit.credit_limit - current, MONEY_ZERO), "f")
        raise BusinessRuleError(
            "La limite de crédit du client serait dépassée",
            code="credit_limit_exceeded",
            extra=extra,
        )

    def _stock(self) -> StockService:
        return StockService(self.db, self.ctx.tenant_id, self.ctx.user.id, self.now)

    # --- Écritures ----------------------------------------------------------------------------

    def create(self, data: SaleCreate) -> Sale:
        site_id = operation_site(self.ctx, data.site_id)
        sale = Sale(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            number=next_number(self.db, self.ctx.tenant_id, SEQUENCE_KEY, PREFIX),
            status=SaleStatus.DRAFT,
            created_by=self.ctx.user.id,
        )
        self._apply_input(sale, data)
        self.db.add(sale)
        self.db.flush()
        self._audit("created", sale, {"status": sale.status.value, **self._snapshot(sale)})
        return sale

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

    def validate(self, sale_id: uuid.UUID, payments: Sequence[PaymentCreate] = ()) -> Sale:
        """Validation (sortie de stock) et, facultativement, encaissements immédiats dans la
        même transaction : une vente payée comptant à la validation ne crée aucune exposition
        de crédit. Les paiements passent par ``PaymentService`` (mêmes règles qu'en 2.7)."""
        sale = self.get(sale_id, lock=True)
        self._require_status(sale, SaleStatus.DRAFT, "sale_not_draft")
        if not sale.lines:
            raise BusinessRuleError("Aucune ligne à valider", code="sale_empty")
        refs = self._articles([line.article_id for line in sale.lines])
        self._customer(sale.customer_id)
        # Le total annoncé au client doit rester exact : un prix catalogue modifié depuis
        # l'enregistrement du brouillon impose de le réenregistrer (prix relus).
        changed = [
            refs[line.article_id].reference
            for line in sale.lines
            if refs[line.article_id].sale_price != line.unit_price
        ]
        if changed:
            raise ConflictError(
                "Des prix ont changé depuis l'enregistrement de la vente",
                code="sale_prices_changed",
                extra={"articles": changed},
            )
        self._check_credit(sale, sum((p.amount for p in payments), MONEY_ZERO))
        # Sortie de stock : exclusivement via le moteur central (verrous, tout ou rien).
        self._stock().apply(
            sale.site_id,
            [
                MovementRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.SALE,
                    quantity=-line.quantity,
                    source_type=SOURCE_TYPE,
                    source_id=sale.id,
                    source_line_id=line.id,
                    source_number=sale.number,
                )
                for line in sale.lines
            ],
        )
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
            },
        )
        if payments:
            from app.modules.sales.payment_service import PaymentService

            payment_service = PaymentService(self.db, self.ctx, self.now)
            for payment in payments:
                payment_service.create(sale.id, payment)
        return sale

    def cancel(self, sale_id: uuid.UUID, reason: str) -> Sale:
        """Brouillon : abandon sans effet sur le stock. Vente validée : mouvements inverses
        ``CANCELLATION`` (remise en stock au coût de la sortie, CMUP inchangé, STK-06), liés
        aux mouvements ``SALE`` d'origine ; les mouvements historiques ne sont jamais modifiés."""
        sale = self.get(sale_id, lock=True)
        previous = sale.status
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
            self._stock().apply(
                sale.site_id,
                [
                    MovementRequest(
                        article_id=line.article_id,
                        movement_type=MovementType.CANCELLATION,
                        quantity=line.quantity,
                        unit_cost=origins[line.id].unit_cost,
                        source_type=SOURCE_TYPE,
                        source_id=sale.id,
                        source_line_id=line.id,
                        source_number=sale.number,
                        origin_movement_id=origins[line.id].id,
                        comment=f"Annulation {sale.number}",
                    )
                    for line in sale.lines
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
            },
        )
        return sale

    # --- Sortie API ---------------------------------------------------------------------------

    def to_out(self, sales: Sequence[Sale], *, with_lines: bool = False) -> list[SaleOut]:
        users = {u for s in sales for u in (s.created_by, s.validated_by, s.cancelled_by) if u}
        user_names = _names(self.db, User, users, User.full_name)
        site_names = _names(self.db, Site, {s.site_id for s in sales}, Site.name)
        customers = get_customer_refs(self.db, {s.customer_id for s in sales if s.customer_id})
        refs = (
            get_article_refs(self.db, {line.article_id for s in sales for line in s.lines})
            if with_lines
            else {}
        )
        # Montants payés des ventes validées : une seule agrégation pour toute la page.
        paid = paid_amounts(self.db, {s.id for s in sales if s.status is SaleStatus.VALIDATED})
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
                    lines=[_line_out(line, refs) for line in sale.lines] if with_lines else [],
                )
            )
        return result


def _names(db: Session, model: Any, ids: set[Any], column: Any) -> dict[Any, str]:
    if not ids:
        return {}
    return {row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(ids)))}


def _line_out(line: SaleLine, refs: dict[uuid.UUID, ArticleRef]) -> SaleLineOut:
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
    )
