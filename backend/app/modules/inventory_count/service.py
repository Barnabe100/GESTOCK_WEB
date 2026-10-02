"""Inventaires (Phase 2.6, ADR-0019) : BROUILLON → COMPTAGE → PRÊT À VALIDER → VALIDÉ, ou
ANNULÉ avant validation.

Règle centrale : l'écart appliqué est **quantité physique − stock courant au moment de la
validation** (et non le stock capturé au début) : les entrées, sorties, ventes et transferts
intervenus pendant le comptage sont pris en compte. Le stock théorique initial n'est qu'une
information de traçabilité.

- Le stock ne change qu'à la validation, exclusivement via ``StockService`` : verrouillage des
  niveaux dans l'ordre global (site, article), relecture du stock courant, mouvements
  ``ADJUSTMENT`` (+ excédent, − manquant) valorisés au CMUP courant, CMUP inchangé, contrôle
  du stock négatif — le tout dans la transaction de la requête (aucune écriture partielle).
- L'inventaire est verrouillé (``SELECT … FOR UPDATE``) avant tout changement : une double
  validation, même simultanée, ne s'applique qu'une fois (la seconde reçoit un 409).
- Un article ne figure qu'une fois par inventaire (contrainte en base) et dans un seul
  inventaire en cours par site (verrou consultatif par site pendant la vérification).
- Aucune requête par article : lignes créées, relues et mises à jour par lots.
- Lot 3-H (ADR-0045) : article suivi par lot — mode FIGÉ au démarrage (``lot_tracked`` de la
  ligne, relu sous verrou à la validation : ``409 inventory_lot_mode_changed``), comptage PAR
  LOT (``inventory_line_lots`` : lots attendus = solde non nul au démarrage, non saisis = 0 ;
  lots découverts, créés à la validation seulement via ``resolve_lots``) ; à la validation,
  écart par lot = physique − solde COURANT du lot relu sous verrou, un ``ADJUSTMENT`` par lot
  avec écart — même si l'écart de l'article est nul ; lot apparu pendant le comptage :
  ``409 inventory_lots_changed`` (action « Actualiser les lots ») ; invariant Σ lots = stock
  contrôlé avant et après les écritures.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    delete,
    func,
    insert,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import (
    QUANTITY_STEP as BASE_STEP,
)
from app.modules.catalog.api import (
    ArticleRef,
    BlockerKind,
    LotFlagsBlocker,
    PackagingRef,
    active_packagings,
    articles_view,
    barcode_search,
    check_packagings,
    ensure_conversion_unchanged,
    ensure_whole,
    get_article_refs,
    lock_lot_flags,
)
from app.modules.inventory_count.models import (
    Inventory,
    InventoryLine,
    InventoryLineLot,
    InventoryStatus,
    InventoryType,
)
from app.modules.inventory_count.schemas import (
    CandidateOut,
    CountFields,
    CountInput,
    CountPackagingOut,
    DiscoveredLotInput,
    InventoryCreate,
    InventoryLineOut,
    InventoryLotOut,
    InventoryOut,
    InventorySummary,
    InventoryUpdate,
    LineState,
    LotCountInput,
)
from app.modules.stock.api import (
    LotInput,
    MovementRequest,
    MovementType,
    StockService,
    check_known_lots,
    check_lot_inputs,
    ensure_document_site,
    existing_lot_infos,
    expiry_context,
    levels_view,
    locations_view,
    lot_infos,
    lot_key,
    operation_site,
    refuse_unmanaged,
    resolve_lots,
    round_money,
    site_lot_balances,
    visible_site_ids,
)
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.sequences.service import next_number
from app.platform.tenancy.models import Site
from app.shared.pagination import (
    PageParams,
    apply_sort,
    paginate,
    paginate_rows,
    search_filter,
    text_sort,
)

SOURCE_TYPE = "inventory_count"  # source des mouvements d'ajustement (ADR-0014)
SEQUENCE_KEY = "inventory"
PREFIX = "INV"
NOT_FOUND = "inventory_not_found"
ZERO = Decimal("0")
ZERO_QTY = Decimal("0.000")  # échelle des quantités : « 0.000 » en réponse, comme le stock
ZERO_COST = Decimal("0.0000")
QUANTITY_STEP = Decimal("0.001")  # NUMERIC(18,3) : même valeur en réponse, en audit et en base

S = InventoryStatus
OPEN_STATUSES = (S.DRAFT, S.COUNTING, S.READY_TO_VALIDATE)

# Transitions autorisées, centralisées : action → (statuts de départ, statut d'arrivée).
# Jamais DRAFT → VALIDATED, ni retour depuis VALIDATED ou CANCELLED.
TRANSITIONS: dict[str, tuple[frozenset[InventoryStatus], InventoryStatus]] = {
    "start": (frozenset({S.DRAFT}), S.COUNTING),
    "complete_counting": (frozenset({S.COUNTING}), S.READY_TO_VALIDATE),
    "reopen_counting": (frozenset({S.READY_TO_VALIDATE}), S.COUNTING),
    "validate": (frozenset({S.READY_TO_VALIDATE}), S.VALIDATED),
    "cancel": (frozenset(OPEN_STATUSES), S.CANCELLED),
}
# Opérations sans changement de statut : statut exigé.
EDITABLE = {"update": S.DRAFT, "count": S.COUNTING}

SORTABLE = {
    "number": Inventory.number,
    "created_at": Inventory.created_at,
    "status": Inventory.status,
}


def next_status(current: InventoryStatus, action: str) -> InventoryStatus:
    """Statut d'arrivée d'une action, ou ``ConflictError`` si la transition est interdite."""
    sources, target = TRANSITIONS[action]
    if current not in sources:
        raise ConflictError(
            "Opération impossible dans l'état actuel de l'inventaire",
            code="inventory_invalid_transition",
            extra={"status": current.value, "action": action},
        )
    return target


@dataclass(frozen=True)
class _Totals:
    lines: int
    counted: int
    surplus: int
    shortage: int
    no_variance: int
    surplus_value: Decimal
    shortage_value: Decimal

    @property
    def adjustment_value(self) -> Decimal:
        return self.surplus_value - self.shortage_value


class InventoryService:
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
        status: InventoryStatus | None = None,
        inventory_type: InventoryType | None = None,
        site_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[Inventory], int]:
        # Périmètre : inventaires des sites visibles (site sélectionné, sinon sites du membre).
        stmt: Select[tuple[Inventory]] = select(Inventory).where(
            Inventory.site_id.in_(visible_site_ids(self.ctx))
        )
        zone = ZoneInfo(self.ctx.tenant.timezone)
        conditions = [
            search_filter(search, Inventory.number),
            Inventory.status == status if status else None,
            Inventory.inventory_type == inventory_type if inventory_type else None,
            Inventory.site_id == site_id if site_id else None,
            # Dates exprimées dans le fuseau du tenant (jour civil local).
            Inventory.created_at >= datetime.combine(date_from, time.min, zone)
            if date_from
            else None,
            Inventory.created_at < datetime.combine(date_to + timedelta(days=1), time.min, zone)
            if date_to
            else None,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        stmt = apply_sort(stmt, params.sort, SORTABLE, "-number", Inventory.id)
        return paginate(self.db, stmt, params)

    def get(self, inventory_id: uuid.UUID, *, lock: bool = False) -> Inventory:
        stmt = select(Inventory).where(Inventory.id == inventory_id)
        if lock:
            # Verrou du document : deux opérations simultanées s'exécutent l'une après l'autre ;
            # la seconde voit le nouveau statut (idempotence de la validation).
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        inventory = self.db.scalars(stmt).one_or_none()
        if inventory is None:
            raise NotFoundError("Inventaire introuvable", code=NOT_FOUND)
        ensure_document_site(self.ctx, inventory.site_id, NOT_FOUND)
        return inventory

    def _base_lines(self, inventory: Inventory) -> tuple[Select[Any], dict[str, Any]]:
        """Lignes + article + niveau courant du site, en une requête (aucun N+1)."""
        articles = articles_view()
        levels = levels_view()
        locations = locations_view()
        line = InventoryLine.__table__.c
        current = func.coalesce(levels.c.quantity, literal(ZERO_QTY))
        current_cost = func.coalesce(levels.c.average_cost, literal(ZERO_COST))
        final = inventory.status is S.VALIDATED
        if final:
            variance: Any = line.quantity_variance
            value: Any = line.adjustment_value
        else:
            variance = line.quantity_physical - current
            value = func.round(variance * current_cost, 2)
        stmt = (
            select(
                line.id,
                line.article_id,
                articles.c.reference,
                articles.c.designation,
                articles.c.unit,
                articles.c.category_name,
                articles.c.is_active.label("article_active"),
                line.stock_theoretical_initial,
                current.label("stock_current"),
                line.stock_theoretical_at_validation,
                line.quantity_physical,
                variance.label("variance"),
                line.unit_cost,
                value.label("value"),
                line.counted_at,
                line.counted_by,
                line.count_packaging_id,
                line.count_packaging_name,
                line.count_packaging_conversion,
                line.count_packaging_quantity,
                line.count_unit_quantity,
                line.lot_tracked,
                locations.c.location_name,
            )
            .select_from(InventoryLine.__table__)
            .join(
                articles,
                and_(
                    articles.c.id == line.article_id,
                    articles.c.tenant_id == line.tenant_id,
                ),
            )
            .outerjoin(
                levels,
                and_(
                    levels.c.tenant_id == line.tenant_id,
                    levels.c.article_id == line.article_id,
                    levels.c.site_id == inventory.site_id,
                ),
            )
            # Lot 3-F : emplacement COURANT de l'article sur le site (parcours de comptage).
            .outerjoin(
                locations,
                and_(
                    locations.c.tenant_id == line.tenant_id,
                    locations.c.article_id == line.article_id,
                    locations.c.site_id == inventory.site_id,
                ),
            )
            .where(line.inventory_id == inventory.id)
        )
        return stmt, {
            "articles": articles,
            "variance": variance,
            "value": value,
            "line": line,
            "locations": locations,
        }

    def lines(
        self,
        inventory: Inventory,
        params: PageParams,
        *,
        search: str | None = None,
        state: LineState = LineState.ALL,
        line_ids: Iterable[uuid.UUID] | None = None,
        article_id: uuid.UUID | None = None,
    ) -> tuple[list[InventoryLineOut], int]:
        stmt, cols = self._base_lines(inventory)
        articles, variance, line = cols["articles"], cols["variance"], cols["line"]
        conditions: list[ColumnElement[bool] | None] = [
            barcode_search(
                search,
                search_filter(
                    search, articles.c.reference, articles.c.designation, articles.c.barcode
                ),
                articles.c.id,
            ),
            line.id.in_(list(line_ids)) if line_ids is not None else None,
            line.article_id == article_id if article_id is not None else None,
            _state_condition(state, line.quantity_physical, variance),
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        sortable = {
            "reference": text_sort(articles.c.reference),
            "designation": text_sort(articles.c.designation),
            "category": text_sort(articles.c.category_name),
            "variance": variance,
            "counted_at": line.counted_at,
            # Lot 3-F : parcours de comptage par emplacement (non rangés en dernier en ordre
            # croissant).
            "location": text_sort(cols["locations"].c.location_name),
        }
        stmt = apply_sort(stmt, params.sort, sortable, "reference", line.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return self._lines_out(inventory, rows), total

    def _lines_out(self, inventory: Inventory, rows: Sequence[Any]) -> list[InventoryLineOut]:
        final = inventory.status is S.VALIDATED
        open_ = inventory.status in OPEN_STATUSES
        users = _names(self.db, User, {r.counted_by for r in rows if r.counted_by}, User.full_name)
        # Lot 3-C : conditionnements actifs proposés à la saisie (une requête pour la page).
        packagings = (
            active_packagings(self.db, {r.article_id for r in rows}, priced_only=False)
            if open_
            else {}
        )
        lots = self._lots_out(inventory, [r for r in rows if r.lot_tracked])
        result = []
        for r in rows:
            counted = r.quantity_physical is not None
            result.append(
                InventoryLineOut(
                    id=r.id,
                    article_id=r.article_id,
                    reference=r.reference,
                    designation=r.designation,
                    unit=r.unit,
                    category_name=r.category_name,
                    article_active=r.article_active,
                    stock_theoretical_initial=r.stock_theoretical_initial,
                    stock_current=r.stock_current if open_ else None,
                    stock_theoretical_at_validation=r.stock_theoretical_at_validation,
                    quantity_physical=r.quantity_physical,
                    indicative_variance=(
                        r.quantity_physical - r.stock_theoretical_initial if counted else None
                    ),
                    quantity_variance=r.variance if counted and (final or open_) else None,
                    unit_cost=r.unit_cost if final else None,
                    adjustment_value=r.value if counted and (final or open_) else None,
                    counted_at=r.counted_at,
                    counted_by_name=users.get(r.counted_by) if r.counted_by else None,
                    count_packaging_id=r.count_packaging_id,
                    count_packaging_name=r.count_packaging_name,
                    count_packaging_conversion=r.count_packaging_conversion,
                    count_packaging_quantity=r.count_packaging_quantity,
                    count_unit_quantity=r.count_unit_quantity,
                    location_name=r.location_name,
                    packagings=[
                        CountPackagingOut(id=p.id, name=p.name, conversion=p.conversion)
                        for p in packagings.get(r.article_id, [])
                    ],
                    lot_tracked=r.lot_tracked,
                    lots=lots.get(r.id, []),
                )
            )
        return result

    def _lots_out(
        self, inventory: Inventory, rows: Sequence[Any]
    ) -> dict[uuid.UUID, list[InventoryLotOut]]:
        """Lots des lignes suivies d'une page (Lot 3-H), en trois requêtes : lots des lignes,
        référentiel des lots, soldes COURANTS du site (inventaire ouvert). Aucun coût."""
        if not rows:
            return {}
        open_ = inventory.status in OPEN_STATUSES
        lot_rows = self._rows_by_line([r.id for r in rows])
        all_rows = [row for group in lot_rows.values() for row in group]
        infos = lot_infos(self.db, {row.lot_id for row in all_rows if row.lot_id})
        balances = (
            site_lot_balances(self.db, inventory.site_id, {r.article_id for r in rows})
            if open_
            else {}
        )
        users = _names(
            self.db, User, {row.counted_by for row in all_rows if row.counted_by}, User.full_name
        )
        expiry = expiry_context(self.db, self.ctx, self.now)
        result: dict[uuid.UUID, list[InventoryLotOut]] = {}
        for line_id, group in lot_rows.items():
            out = []
            for row in group:
                info = infos.get(row.lot_id) if row.lot_id else None
                expiry_date = info.expiry_date if info else row.expiry_date
                current = (
                    balances.get(row.article_id, {}).get(row.lot_id, ZERO_QTY)
                    if open_ and row.lot_id
                    else (ZERO_QTY if open_ else None)
                )
                variance: Decimal | None = row.quantity_variance
                if open_ and row.quantity_physical is not None and current is not None:
                    variance = row.quantity_physical - current
                out.append(
                    InventoryLotOut(
                        id=row.id,
                        lot_id=row.lot_id,
                        lot_number=info.number if info else (row.lot_number or "?"),
                        expiry_date=expiry_date,
                        manufacturing_date=row.manufacturing_date,
                        state=expiry.state(expiry_date),
                        discovered=row.discovered,
                        stock_theoretical_initial=row.stock_theoretical_initial,
                        stock_current=current,
                        stock_theoretical_at_validation=row.stock_theoretical_at_validation,
                        quantity_physical=row.quantity_physical,
                        quantity_variance=variance,
                        counted_at=row.counted_at,
                        counted_by_name=users.get(row.counted_by) if row.counted_by else None,
                        count_packaging_id=row.count_packaging_id,
                        count_packaging_name=row.count_packaging_name,
                        count_packaging_conversion=row.count_packaging_conversion,
                        count_packaging_quantity=row.count_packaging_quantity,
                        count_unit_quantity=row.count_unit_quantity,
                    )
                )
            result[line_id] = out
        return result

    def _rows_by_line(
        self, line_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, list[InventoryLineLot]]:
        """Lots des lignes : attendus d'abord, puis découverts, dans l'ordre de saisie."""
        ids = list(line_ids)
        result: dict[uuid.UUID, list[InventoryLineLot]] = {line_id: [] for line_id in ids}
        if not ids:
            return result
        for row in self.db.scalars(
            select(InventoryLineLot)
            .where(InventoryLineLot.inventory_line_id.in_(ids))
            .order_by(
                InventoryLineLot.inventory_line_id,
                InventoryLineLot.discovered,
                InventoryLineLot.created_at,
                InventoryLineLot.id,
            )
        ):
            result[row.inventory_line_id].append(row)
        return result

    def summary(self, inventory: Inventory) -> InventorySummary:
        """Avant validation : écarts calculés sur le stock et le CMUP courants (ce que la
        validation appliquerait maintenant) ; après : valeurs figées à la validation."""
        stmt, _ = self._base_lines(inventory)
        sub = stmt.subquery()
        counted = sub.c.quantity_physical.is_not(None)
        row = self.db.execute(
            select(
                func.count(),
                func.count(sub.c.quantity_physical),
                func.count().filter(and_(counted, sub.c.variance > 0)),
                func.count().filter(and_(counted, sub.c.variance < 0)),
                func.count().filter(and_(counted, sub.c.variance == 0)),
                func.coalesce(func.sum(sub.c.value).filter(sub.c.variance > 0), ZERO),
                func.coalesce(func.sum(-sub.c.value).filter(sub.c.variance < 0), ZERO),
            )
        ).one()
        totals = _Totals(*row)
        return InventorySummary(
            lines=totals.lines,
            counted=totals.counted,
            surplus=totals.surplus,
            shortage=totals.shortage,
            no_variance=totals.no_variance,
            surplus_value=round_money(totals.surplus_value),
            shortage_value=round_money(totals.shortage_value),
            adjustment_value=round_money(totals.adjustment_value),
            final=inventory.status is S.VALIDATED,
        )

    def candidates(
        self,
        site_id: uuid.UUID | None,
        params: PageParams,
        *,
        search: str | None = None,
        stocked_only: bool = False,
    ) -> tuple[list[CandidateOut], int]:
        """Articles actifs proposables pour un inventaire du site, avec leur stock courant.
        Recherche et pagination serveur : jamais tout le catalogue d'un coup."""
        site = operation_site(self.ctx, site_id)
        articles = articles_view()
        levels = levels_view()
        stmt = (
            select(
                articles.c.id,
                articles.c.reference,
                articles.c.designation,
                articles.c.unit,
                articles.c.category_name,
                levels.c.article_id.is_not(None).label("stocked"),
                func.coalesce(levels.c.quantity, literal(ZERO_QTY)).label("quantity"),
            )
            .select_from(articles)
            .outerjoin(
                levels,
                and_(
                    levels.c.tenant_id == articles.c.tenant_id,
                    levels.c.article_id == articles.c.id,
                    levels.c.site_id == site,
                ),
            )
            .where(
                articles.c.tenant_id == self.ctx.tenant_id,
                articles.c.is_active.is_(True),
                articles.c.stock_managed.is_(True),
            )
        )
        by_text = barcode_search(
            search,
            search_filter(search, articles.c.reference, articles.c.designation, articles.c.barcode),
            articles.c.id,
        )
        if by_text is not None:
            stmt = stmt.where(by_text)
        if stocked_only:
            stmt = stmt.where(levels.c.article_id.is_not(None))
        sortable = {
            "reference": text_sort(articles.c.reference),
            "designation": text_sort(articles.c.designation),
        }
        stmt = apply_sort(stmt, params.sort, sortable, "reference", articles.c.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [
            CandidateOut(
                article_id=r.id,
                reference=r.reference,
                designation=r.designation,
                unit=r.unit,
                category_name=r.category_name,
                stocked=r.stocked,
                quantity=r.quantity,
            )
            for r in rows
        ], total

    # --- Règles -------------------------------------------------------------------------------

    @staticmethod
    def _require(inventory: Inventory, operation: str) -> None:
        if inventory.status is not EDITABLE[operation]:
            raise ConflictError(
                "Opération impossible dans l'état actuel de l'inventaire",
                code="inventory_invalid_transition",
                extra={"status": inventory.status.value, "action": operation},
            )

    def _lock_site(self, site_id: uuid.UUID) -> None:
        """Sérialise, par site, la vérification « un article dans un seul inventaire en
        cours » (verrou consultatif de transaction, libéré au commit ou à l'annulation)."""
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"inventory:{self.ctx.tenant_id}:{site_id}"},
        )

    def _ensure_not_in_open_inventory(
        self, site_id: uuid.UUID, article_ids: set[uuid.UUID], exclude: uuid.UUID | None
    ) -> None:
        if not article_ids:
            return
        stmt = (
            select(InventoryLine.article_id, Inventory.number)
            .join(Inventory, Inventory.id == InventoryLine.inventory_id)
            .where(
                Inventory.site_id == site_id,
                Inventory.status.in_(OPEN_STATUSES),
                InventoryLine.article_id.in_(article_ids),
            )
        )
        if exclude is not None:
            stmt = stmt.where(Inventory.id != exclude)
        clashes = self.db.execute(stmt.limit(20)).all()
        if clashes:
            refs = get_article_refs(self.db, {row[0] for row in clashes})
            raise ConflictError(
                "Des articles figurent déjà dans un inventaire en cours sur ce site",
                code="article_in_open_inventory",
                extra={
                    "articles": sorted(
                        {refs[row[0]].reference for row in clashes if row[0] in refs}
                    ),
                    "inventories": sorted({row[1] for row in clashes}),
                },
            )

    def _active_articles(self, article_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Articles du tenant (RLS), existants, actifs, chacun une seule fois."""
        if not article_ids:
            raise BusinessRuleError("Choisissez au moins un article", code="inventory_empty")
        if len(set(article_ids)) != len(article_ids):
            raise BusinessRuleError(
                "Un article apparaît plusieurs fois", code="duplicate_article_line"
            )
        refs = get_article_refs(self.db, set(article_ids))
        missing = [str(a) for a in article_ids if a not in refs]
        if missing:
            raise BusinessRuleError(
                "Article introuvable", code="article_not_found", extra={"articles": missing}
            )
        inactive = sorted(refs[a].reference for a in article_ids if not refs[a].is_active)
        if inactive:
            raise BusinessRuleError(
                "Article inactif", code="article_inactive", extra={"articles": inactive}
            )
        # Lot 3-A : un article non géré en stock ne s'inventorie pas.
        refuse_unmanaged(self.db, [a for a in article_ids if not refs[a].stock_managed])
        return set(article_ids)

    def _stocked_articles(self, site_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
        """Inventaire complet : articles actifs gérés sur le site (niveau de stock existant),
        avec leur stock courant. Une seule requête."""
        articles = articles_view()
        levels = levels_view()
        rows = self.db.execute(
            select(levels.c.article_id, levels.c.quantity)
            .join(
                articles,
                and_(
                    articles.c.id == levels.c.article_id,
                    articles.c.tenant_id == levels.c.tenant_id,
                ),
            )
            .where(
                levels.c.tenant_id == self.ctx.tenant_id,
                levels.c.site_id == site_id,
                articles.c.is_active.is_(True),
                articles.c.stock_managed.is_(True),
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    def _quantities(self, site_id: uuid.UUID, article_ids: set[uuid.UUID]) -> dict[uuid.UUID, Any]:
        """Stock courant (0 si l'article n'a jamais été géré sur le site). Lecture seule."""
        if not article_ids:
            return {}
        levels = levels_view()
        found = {
            row[0]: row[1]
            for row in self.db.execute(
                select(levels.c.article_id, levels.c.quantity).where(
                    levels.c.tenant_id == self.ctx.tenant_id,
                    levels.c.site_id == site_id,
                    levels.c.article_id.in_(article_ids),
                )
            )
        }
        return {article_id: found.get(article_id, ZERO_QTY) for article_id in article_ids}

    def _line_articles(self, inventory: Inventory) -> dict[uuid.UUID, uuid.UUID]:
        """article → ligne."""
        return {
            row[0]: row[1]
            for row in self.db.execute(
                select(InventoryLine.article_id, InventoryLine.id).where(
                    InventoryLine.inventory_id == inventory.id
                )
            )
        }

    def _add_lines(self, inventory: Inventory, quantities: dict[uuid.UUID, Decimal]) -> None:
        """Insertion groupée ; stock théorique initial = stock courant (instantané)."""
        if not quantities:
            return
        self.db.execute(
            insert(InventoryLine),
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": self.ctx.tenant_id,
                    "inventory_id": inventory.id,
                    "article_id": article_id,
                    "stock_theoretical_initial": quantity,
                }
                for article_id, quantity in sorted(quantities.items())
            ],
        )

    def _remove_lines(self, inventory: Inventory, article_ids: set[uuid.UUID]) -> None:
        if article_ids:
            self.db.execute(
                delete(InventoryLine).where(
                    InventoryLine.inventory_id == inventory.id,
                    InventoryLine.article_id.in_(article_ids),
                )
            )

    def _audit(self, action: str, inventory: Inventory, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"inventory.{action}",
            entity_type="inventory",
            entity_id=inventory.id,
            site_id=inventory.site_id,
            data={
                "number": inventory.number,
                "site_id": str(inventory.site_id),
                "inventory_type": inventory.inventory_type.value,
                **data,
            },
        )

    def _stock(self) -> StockService:
        return StockService(self.db, self.ctx.tenant_id, self.ctx.user.id, self.now)

    def _references(self, article_ids: Iterable[uuid.UUID]) -> list[str]:
        refs = get_article_refs(self.db, set(article_ids))
        return sorted(ref.reference for ref in refs.values())

    # --- Cycle de vie -------------------------------------------------------------------------

    def create(self, data: InventoryCreate) -> Inventory:
        site_id = operation_site(self.ctx, data.site_id)
        if data.inventory_type is InventoryType.FULL:
            if data.article_ids:
                raise BusinessRuleError(
                    "Un inventaire complet reprend tous les articles du site",
                    code="inventory_full_articles_fixed",
                )
            self._lock_site(site_id)
            quantities = self._stocked_articles(site_id)
            if not quantities:
                raise BusinessRuleError("Aucun article géré sur ce site", code="inventory_empty")
        else:
            wanted = self._active_articles(data.article_ids)
            self._lock_site(site_id)
            quantities = self._quantities(site_id, wanted)
        self._ensure_not_in_open_inventory(site_id, set(quantities), None)
        inventory = Inventory(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            number=next_number(self.db, self.ctx.tenant_id, SEQUENCE_KEY, PREFIX),
            status=S.DRAFT,
            inventory_type=data.inventory_type,
            comment=data.comment,
            created_by=self.ctx.user.id,
        )
        self.db.add(inventory)
        self.db.flush()
        self._add_lines(inventory, quantities)
        self._audit(
            "created", inventory, {"status": inventory.status.value, "lines": len(quantities)}
        )
        return inventory

    def update(self, inventory_id: uuid.UUID, data: InventoryUpdate) -> Inventory:
        """Brouillon seulement : commentaire ; articles ajoutés / retirés d'un inventaire
        ciblé (les lignes conservées gardent leur instantané)."""
        inventory = self.get(inventory_id, lock=True)
        self._require(inventory, "update")
        before: dict[str, Any] = {"comment": inventory.comment}
        after: dict[str, Any] = {"comment": data.comment}
        inventory.comment = data.comment
        if data.add_article_ids or data.remove_article_ids:
            if inventory.inventory_type is InventoryType.FULL:
                raise BusinessRuleError(
                    "Un inventaire complet reprend tous les articles du site",
                    code="inventory_full_articles_fixed",
                )
            self._lock_site(inventory.site_id)
            current = set(self._line_articles(inventory))
            removed = set(data.remove_article_ids) & current
            added = (
                self._active_articles(data.add_article_ids) - current
                if data.add_article_ids
                else set()
            )
            if not (current - removed) | added:
                raise BusinessRuleError("Choisissez au moins un article", code="inventory_empty")
            self._ensure_not_in_open_inventory(inventory.site_id, added, inventory.id)
            self._remove_lines(inventory, removed)
            self._add_lines(inventory, self._quantities(inventory.site_id, added))
            if added or removed:
                before["articles_removed"] = self._references(removed)
                after["articles_added"] = self._references(added)
                after["lines"] = len((current - removed) | added)
        self.db.flush()
        if before != after:
            self._audit("updated", inventory, {"before": before, "after": after})
        return inventory

    def start(self, inventory_id: uuid.UUID) -> Inventory:
        """Début du comptage. Inventaire complet : liste recalée sur les articles actifs gérés
        sur le site à cet instant. Stock théorique initial de chaque ligne = stock courant."""
        inventory = self.get(inventory_id, lock=True)
        target = next_status(inventory.status, "start")
        self._lock_site(inventory.site_id)
        lines = self._line_articles(inventory)
        if inventory.inventory_type is InventoryType.FULL:
            stocked = self._stocked_articles(inventory.site_id)
            self._remove_lines(inventory, set(lines) - set(stocked))
            added = {a: q for a, q in stocked.items() if a not in lines}
            self._ensure_not_in_open_inventory(inventory.site_id, set(added), inventory.id)
            self._add_lines(inventory, added)
            article_ids = set(stocked)
        else:
            article_ids = set(lines)
            refs = get_article_refs(self.db, article_ids)
            inactive = sorted(r.reference for r in refs.values() if not r.is_active)
            if inactive:
                raise BusinessRuleError(
                    "Article inactif", code="article_inactive", extra={"articles": inactive}
                )
            refuse_unmanaged(self.db, sorted(a for a, r in refs.items() if not r.stock_managed))
        if not article_ids:
            raise BusinessRuleError("Aucun article à compter", code="inventory_empty")
        # Instantané du stock théorique au début du comptage (information seulement).
        quantities = self._quantities(inventory.site_id, article_ids)
        line_ids = self._line_articles(inventory)
        # Lot 3-H : mode de suivi par lot FIGÉ (réglage lu sous verrou partagé de l'article).
        flags = lock_lot_flags(self.db, article_ids)
        self.db.execute(
            update(InventoryLine),
            [
                {
                    "id": line_ids[article_id],
                    "stock_theoretical_initial": quantity,
                    "lot_tracked": flags[article_id].lot_tracked,
                }
                for article_id, quantity in quantities.items()
            ],
        )
        tracked = {a for a in article_ids if flags[a].lot_tracked}
        self._add_expected_lots(inventory, {a: line_ids[a] for a in tracked}, {})
        previous = inventory.status
        inventory.status = target
        inventory.started_at = self.now
        inventory.started_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "started",
            inventory,
            {
                "previous_status": previous.value,
                "status": target.value,
                "lines": len(article_ids),
                **({"lot_tracked_lines": len(tracked)} if tracked else {}),
            },
        )
        return inventory

    # --- Lots (Lot 3-H) -----------------------------------------------------------------------

    def _add_expected_lots(
        self,
        inventory: Inventory,
        lines: dict[uuid.UUID, uuid.UUID],
        known: dict[uuid.UUID, set[uuid.UUID]],
    ) -> list[tuple[uuid.UUID, uuid.UUID, Decimal]]:
        """Lots ATTENDUS des lignes suivies (article → ligne) : solde non nul sur le site,
        absents des lots déjà connus de la ligne (``known`` : article → lots). Théorique initial
        = solde courant du lot. Renvoie (article, lot, solde) ajoutés."""
        balances = site_lot_balances(self.db, inventory.site_id, set(lines))
        added = [
            (article_id, lot_id, quantity)
            for article_id in sorted(lines)
            for lot_id, quantity in sorted(balances.get(article_id, {}).items())
            if lot_id not in known.get(article_id, set())
        ]
        if added:
            self.db.execute(
                insert(InventoryLineLot),
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": self.ctx.tenant_id,
                        "inventory_line_id": lines[article_id],
                        "article_id": article_id,
                        "lot_id": lot_id,
                        "discovered": False,
                        "stock_theoretical_initial": quantity,
                    }
                    for article_id, lot_id, quantity in added
                ],
            )
        return added

    def _tracked_line(self, inventory: Inventory, line_id: uuid.UUID) -> InventoryLine:
        line = self.db.scalars(
            select(InventoryLine).where(
                InventoryLine.inventory_id == inventory.id, InventoryLine.id == line_id
            )
        ).one_or_none()
        if line is None:
            raise NotFoundError("Ligne d'inventaire introuvable", code="inventory_line_not_found")
        if not line.lot_tracked:
            raise BusinessRuleError(
                "Cette ligne n'est pas suivie par lot", code="inventory_line_not_lot_tracked"
            )
        return line

    def _recount_line(self, line: InventoryLine, rows: Sequence[InventoryLineLot]) -> None:
        """Ligne suivie : quantité physique = Σ des lots (lot non saisi = 0, O-5) ; la ligne est
        comptée dès qu'un comptage par lot a été enregistré."""
        line.quantity_physical = sum(
            (row.quantity_physical or ZERO for row in rows), ZERO
        ).quantize(QUANTITY_STEP)
        line.counted_at = self.now
        line.counted_by = self.ctx.user.id

    def _apply_count(
        self,
        row: InventoryLineLot,
        ref: ArticleRef,
        count: CountFields | None,
        packagings: dict[uuid.UUID, PackagingRef],
    ) -> bool:
        """Comptage d'un lot (mécanisme commun 3-C : unité de base, ou conditionnement + vrac).
        Renvoie vrai si la saisie change."""
        if count is None:
            quantity, presentation = None, NO_PRESENTATION
        else:
            packaging = packagings[count.packaging_id] if count.packaging_id else None
            quantity, presentation = _count_quantity(ref, count, packaging)
        if quantity == row.quantity_physical and presentation == _presentation_of(row):
            return False
        row.quantity_physical = quantity
        (
            row.count_packaging_id,
            row.count_packaging_name,
            row.count_packaging_conversion,
            row.count_packaging_quantity,
            row.count_unit_quantity,
        ) = presentation
        row.counted_at = self.now if quantity is not None else None
        row.counted_by = self.ctx.user.id if quantity is not None else None
        return True

    def _lot_label(self, rows: Sequence[InventoryLineLot]) -> dict[uuid.UUID, str]:
        infos = lot_infos(self.db, {row.lot_id for row in rows if row.lot_id})
        return {
            row.id: infos[row.lot_id].number if row.lot_id in infos else (row.lot_number or "?")
            for row in rows
        }

    def save_lot_counts(
        self, inventory_id: uuid.UUID, line_id: uuid.UUID, counts: list[LotCountInput]
    ) -> InventoryLine:
        """Comptage COMPLET d'une ligne suivie par lot (remplacement) : lots listés comptés,
        autres lots de la ligne effacés (= 0 à la validation, O-5)."""
        inventory = self.get(inventory_id, lock=True)
        self._require(inventory, "count")
        line = self._tracked_line(inventory, line_id)
        rows = self._rows_by_line([line.id])[line.id]
        by_id = {row.id: row for row in rows}
        ids = [c.lot_row_id for c in counts]
        if len(set(ids)) != len(ids):
            raise BusinessRuleError("Un lot apparaît plusieurs fois", code="duplicate_count_line")
        missing = [str(i) for i in ids if i not in by_id]
        if missing:
            raise BusinessRuleError(
                "Lot d'inventaire introuvable sur cette ligne",
                code="inventory_lot_not_found",
                extra={"lots": missing},
            )
        ref = get_article_refs(self.db, {line.article_id})[line.article_id]
        packagings = check_packagings(
            self.db, [(line.article_id, c.packaging_id) for c in counts if c.packaging_id]
        )
        wanted = {c.lot_row_id: c for c in counts}
        labels = self._lot_label(rows)
        changes = []
        for row in rows:
            before = row.quantity_physical
            if self._apply_count(row, ref, wanted.get(row.id), packagings):
                changes.append(
                    {
                        "reference": ref.reference,
                        "lot_number": labels[row.id],
                        "before": _fmt(before),
                        "after": _fmt(row.quantity_physical),
                    }
                )
        before_line = line.quantity_physical
        self._recount_line(line, rows)
        self.db.flush()
        if changes or before_line != line.quantity_physical:
            self._audit("counted", inventory, {"changes": changes, "lots": True})
        return line

    def add_discovered_lot(
        self, inventory_id: uuid.UUID, line_id: uuid.UUID, data: DiscoveredLotInput
    ) -> InventoryLine:
        """Lot trouvé physiquement (T-4) : règles de saisie 3-G ; rattaché au lot EXISTANT de
        l'article (jamais de doublon), sinon créé à la validation seulement ; refus s'il figure
        déjà sur la ligne (``409 duplicate_lot_in_inventory``)."""
        inventory = self.get(inventory_id, lock=True)
        self._require(inventory, "count")
        line = self._tracked_line(inventory, line_id)
        refs = get_article_refs(self.db, {line.article_id})
        ref = refs[line.article_id]
        lot_input = LotInput(
            line.article_id, data.lot_number, data.expiry_date, data.manufacturing_date
        )
        check_lot_inputs([lot_input], refs)
        check_known_lots(self.db, [lot_input], refs)
        rows = self._rows_by_line([line.id])[line.id]
        key = lot_key(line.article_id, data.lot_number)
        existing = existing_lot_infos(self.db, {key}).get(key)
        duplicate = any(
            (existing is not None and row.lot_id == existing.id)
            or (
                row.lot_id is None
                and row.lot_number is not None
                and row.lot_number.lower() == data.lot_number.lower()
            )
            for row in rows
        )
        if duplicate:
            raise ConflictError(
                "Ce lot figure déjà sur la ligne",
                code="duplicate_lot_in_inventory",
                extra={"articles": [ref.reference], "lots": [data.lot_number]},
            )
        initial = ZERO_QTY
        if existing is not None:
            initial = (
                site_lot_balances(self.db, inventory.site_id, {line.article_id})
                .get(line.article_id, {})
                .get(existing.id, ZERO_QTY)
            )
        row = InventoryLineLot(
            id=uuid.uuid4(),
            tenant_id=self.ctx.tenant_id,
            inventory_line_id=line.id,
            article_id=line.article_id,
            lot_id=existing.id if existing else None,
            discovered=True,
            lot_number=data.lot_number,
            expiry_date=data.expiry_date,
            manufacturing_date=data.manufacturing_date,
            stock_theoretical_initial=initial,
        )
        self.db.add(row)
        counted = data.quantity_physical is not None or data.packaging_id is not None
        if counted:
            packagings = check_packagings(
                self.db, [(line.article_id, data.packaging_id)] if data.packaging_id else []
            )
            self._apply_count(row, ref, data, packagings)
        self.db.flush()
        self._recount_line(line, [*rows, row])
        self.db.flush()
        self._audit(
            "lot_discovered",
            inventory,
            {
                "reference": ref.reference,
                "lot_number": data.lot_number,
                "known_lot": existing is not None,
                "expiry_date": data.expiry_date.isoformat() if data.expiry_date else None,
                "quantity": _fmt(row.quantity_physical),
            },
        )
        return line

    def remove_discovered_lot(
        self, inventory_id: uuid.UUID, line_id: uuid.UUID, row_id: uuid.UUID
    ) -> InventoryLine:
        """Retrait d'un lot DÉCOUVERT (saisie erronée) ; un lot attendu ne se retire jamais :
        non trouvé, il est compté 0."""
        inventory = self.get(inventory_id, lock=True)
        self._require(inventory, "count")
        line = self._tracked_line(inventory, line_id)
        rows = self._rows_by_line([line.id])[line.id]
        row = next((r for r in rows if r.id == row_id), None)
        if row is None:
            raise NotFoundError("Lot d'inventaire introuvable", code="inventory_lot_not_found")
        if not row.discovered:
            raise BusinessRuleError(
                "Un lot attendu ne se retire pas : non trouvé, il est compté 0",
                code="inventory_lot_not_discovered",
            )
        label = self._lot_label([row])[row.id]
        self.db.delete(row)
        self.db.flush()
        self._recount_line(line, [r for r in rows if r.id != row_id])
        self.db.flush()
        ref = get_article_refs(self.db, {line.article_id})[line.article_id]
        self._audit(
            "lot_discovery_removed",
            inventory,
            {"reference": ref.reference, "lot_number": label},
        )
        return line

    def _attach_discovered(
        self, rows: Sequence[InventoryLineLot], refs: dict[uuid.UUID, ArticleRef]
    ) -> None:
        """Lot découvert NOUVEAU dont le numéro est entre-temps connu (créé par une réception
        ou un autre inventaire) : rattaché au lot existant, sous réserve de la même péremption
        (``lot_expiry_mismatch``) — jamais de doublon."""
        pending = [row for row in rows if row.discovered and row.lot_id is None]
        if not pending:
            return
        inputs = [
            LotInput(row.article_id, row.lot_number, row.expiry_date, row.manufacturing_date)
            for row in pending
        ]
        check_known_lots(self.db, inputs, refs)
        known = existing_lot_infos(
            self.db, {lot_key(row.article_id, row.lot_number or "") for row in pending}
        )
        for row in pending:
            info = known.get(lot_key(row.article_id, row.lot_number or ""))
            if info is not None:
                row.lot_id = info.id

    def refresh_lots(self, inventory_id: uuid.UUID) -> int:
        """« Actualiser les lots » (D-3) : ajoute aux lignes suivies les lots apparus sur le site
        depuis le démarrage (réception, transfert entrant…), à compter — jamais comptés 0 sans
        avoir été montrés. Comptage terminé : retour au comptage s'il y a des lots ajoutés."""
        inventory = self.get(inventory_id, lock=True)
        if inventory.status not in (S.COUNTING, S.READY_TO_VALIDATE):
            raise ConflictError(
                "Opération impossible dans l'état actuel de l'inventaire",
                code="inventory_invalid_transition",
                extra={"status": inventory.status.value, "action": "refresh_lots"},
            )
        lines = list(
            self.db.scalars(
                select(InventoryLine).where(
                    InventoryLine.inventory_id == inventory.id, InventoryLine.lot_tracked
                )
            )
        )
        rows_by_line = self._rows_by_line([line.id for line in lines])
        refs = get_article_refs(self.db, {line.article_id for line in lines})
        all_rows = [row for group in rows_by_line.values() for row in group]
        self._attach_discovered(all_rows, refs)
        self.db.flush()
        known: dict[uuid.UUID, set[uuid.UUID]] = {}
        for row in all_rows:
            if row.lot_id is not None:
                known.setdefault(row.article_id, set()).add(row.lot_id)
        added = self._add_expected_lots(
            inventory, {line.article_id: line.id for line in lines}, known
        )
        if added and inventory.status is S.READY_TO_VALIDATE:
            inventory.status = S.COUNTING
            inventory.completed_at = None
            inventory.completed_by = None
        self.db.flush()
        if added:
            infos = lot_infos(self.db, {lot_id for _, lot_id, _ in added})
            self._audit(
                "lots_refreshed",
                inventory,
                {
                    "status": inventory.status.value,
                    "lots": [
                        {
                            "reference": refs[article_id].reference,
                            "lot_number": infos[lot_id].number if lot_id in infos else None,
                            "stock_theoretical_initial": format(quantity, "f"),
                        }
                        for article_id, lot_id, quantity in added
                    ],
                },
            )
        return len(added)

    def save_counts(
        self, inventory_id: uuid.UUID, counts: list[CountInput]
    ) -> tuple[Inventory, list[uuid.UUID]]:
        """Saisie progressive des quantités physiques (≥ 0, 3 décimales) ; ``None`` efface."""
        inventory = self.get(inventory_id, lock=True)
        self._require(inventory, "count")
        ids = [c.line_id for c in counts]
        if len(set(ids)) != len(ids):
            raise BusinessRuleError(
                "Une ligne apparaît plusieurs fois", code="duplicate_count_line"
            )
        lines = {
            line.id: line
            for line in self.db.scalars(
                select(InventoryLine).where(
                    InventoryLine.inventory_id == inventory.id, InventoryLine.id.in_(ids)
                )
            )
        }
        missing = [str(i) for i in ids if i not in lines]
        if missing:
            raise BusinessRuleError(
                "Ligne d'inventaire introuvable",
                code="inventory_line_not_found",
                extra={"lines": missing},
            )
        # Lot 3-H : une ligne suivie par lot se compte PAR LOT (la quantité de la ligne est la
        # somme des lots, calculée par le serveur).
        tracked = [line for line in lines.values() if line.lot_tracked]
        if tracked:
            refs = get_article_refs(self.db, {line.article_id for line in tracked})
            raise BusinessRuleError(
                "Article suivi par lot : comptez chaque lot",
                code="inventory_line_lot_tracked",
                extra={"articles": sorted(refs[line.article_id].reference for line in tracked)},
            )
        # Lot 3-C : articles (règle des quantités entières) et conditionnements des comptages
        # (de l'article de la ligne, actifs, sous verrou partagé) — mécanisme commun.
        refs = get_article_refs(self.db, {line.article_id for line in lines.values()})
        packagings = check_packagings(
            self.db,
            [
                (lines[c.line_id].article_id, c.packaging_id)
                for c in counts
                if c.packaging_id is not None
            ],
        )
        changes: list[tuple[uuid.UUID, Decimal | None, Decimal | None]] = []
        for count in counts:
            line = lines[count.line_id]
            ref = refs[line.article_id]
            packaging = packagings[count.packaging_id] if count.packaging_id else None
            quantity, presentation = _count_quantity(ref, count, packaging)
            if quantity == line.quantity_physical and presentation == _presentation_of(line):
                continue
            changes.append((line.article_id, line.quantity_physical, quantity))
            line.quantity_physical = quantity
            (
                line.count_packaging_id,
                line.count_packaging_name,
                line.count_packaging_conversion,
                line.count_packaging_quantity,
                line.count_unit_quantity,
            ) = presentation
            line.counted_at = self.now if quantity is not None else None
            line.counted_by = self.ctx.user.id if quantity is not None else None
        self.db.flush()
        if changes:
            refs = get_article_refs(self.db, {c[0] for c in changes})
            self._audit(
                "counted",
                inventory,
                {
                    "changes": [
                        {
                            "reference": refs[a].reference if a in refs else str(a),
                            "before": _fmt(before),
                            "after": _fmt(after),
                        }
                        for a, before, after in changes
                    ]
                },
            )
        return inventory, ids

    def complete_counting(self, inventory_id: uuid.UUID) -> Inventory:
        inventory = self.get(inventory_id, lock=True)
        target = next_status(inventory.status, "complete_counting")
        self._ensure_fully_counted(inventory)
        previous = inventory.status
        inventory.status = target
        inventory.completed_at = self.now
        inventory.completed_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "count_completed",
            inventory,
            {"previous_status": previous.value, "status": target.value},
        )
        return inventory

    def reopen_counting(self, inventory_id: uuid.UUID) -> Inventory:
        """Retour au comptage avant validation (correction d'une saisie)."""
        inventory = self.get(inventory_id, lock=True)
        target = next_status(inventory.status, "reopen_counting")
        previous = inventory.status
        inventory.status = target
        inventory.completed_at = None
        inventory.completed_by = None
        self.db.flush()
        self._audit(
            "counting_reopened",
            inventory,
            {"previous_status": previous.value, "status": target.value},
        )
        return inventory

    def _ensure_fully_counted(self, inventory: Inventory) -> None:
        remaining = self.db.scalar(
            select(func.count()).where(
                InventoryLine.inventory_id == inventory.id,
                InventoryLine.quantity_physical.is_(None),
            )
        )
        if remaining:
            raise BusinessRuleError(
                "Toutes les lignes doivent être comptées",
                code="inventory_not_fully_counted",
                extra={"remaining": int(remaining)},
            )

    def _revalidate_counts(self, lines: Sequence[InventoryLine | InventoryLineLot]) -> None:
        """Lot 3-C : à la validation, tout comptage est relu — règle des quantités entières de
        l'article, conditionnement (existant, actif, conversion inchangée) et quantité de base
        recalculée par le serveur. Lot 3-H : même contrôle pour chaque comptage PAR LOT."""
        refs = get_article_refs(self.db, {line.article_id for line in lines})
        counted = [line for line in lines if line.count_packaging_id is not None]
        packagings = check_packagings(
            self.db, [(line.article_id, line.count_packaging_id) for line in counted]
        )
        for line in lines:
            ref = refs[line.article_id]
            if line.count_packaging_id is None:
                if line.quantity_physical is not None:
                    ensure_whole(ref, line.quantity_physical)
                continue
            packaging = packagings[line.count_packaging_id]
            ensure_conversion_unchanged(ref, packaging, line.count_packaging_conversion)
            assert line.count_packaging_quantity is not None
            assert line.count_unit_quantity is not None
            physical = _base_count(
                ref, packaging, line.count_packaging_quantity, line.count_unit_quantity
            )
            if physical != line.quantity_physical:
                raise ConflictError(
                    "Le comptage en conditionnement ne correspond plus : saisissez-le à nouveau",
                    code="packaging_conversion_changed",
                    extra={"articles": [ref.reference]},
                )

    def validate(self, inventory_id: uuid.UUID) -> Inventory:
        """Applique les écarts au stock, en une transaction (tout ou rien) :

        1. verrou de l'inventaire et contrôle du statut (PRÊT À VALIDER) ;
        2. contrôle du comptage complet et revalidation des comptages (3-C) ;
        3. Lot 3-H : réglages de suivi relus sous verrou partagé des articles — mode changé
           depuis le démarrage : ``409 inventory_lot_mode_changed`` ; lots découverts revérifiés
           (règles 3-G) puis rattachés ou créés (``resolve_lots``, jamais de doublon) ;
        4. verrou des niveaux puis des soldes de lots du site (ordre global de
           ``StockService``), invariant Σ lots = stock contrôlé ; lot apparu depuis le démarrage
           et absent du comptage : ``409 inventory_lots_changed`` ;
        5. écart = physique − stock courant (par lot pour un article suivi : un lot non saisi
           compte 0) ; valeur = écart × CMUP courant ;
        6. mouvements ``ADJUSTMENT`` pour les écarts non nuls — un par LOT pour un article suivi,
           même si l'écart de l'article est nul — via ``StockService.apply`` (stock jamais
           négatif, CMUP inchangé), invariant revérifié après écriture ;
        7. lignes figées, statut VALIDÉ, audit. Le commit est fait par l'endpoint."""
        inventory = self.get(inventory_id, lock=True)
        target = next_status(inventory.status, "validate")
        self._ensure_fully_counted(inventory)
        lines = list(
            self.db.scalars(
                select(InventoryLine)
                .where(InventoryLine.inventory_id == inventory.id)
                .order_by(InventoryLine.article_id)
            )
        )
        if not lines:
            raise BusinessRuleError("Aucun article à valider", code="inventory_empty")
        tracked_lines = [line for line in lines if line.lot_tracked]
        rows_by_line = self._rows_by_line([line.id for line in tracked_lines])
        all_rows = [row for group in rows_by_line.values() for row in group]
        self._revalidate_counts([*(line for line in lines if not line.lot_tracked), *all_rows])
        article_ids = {line.article_id for line in lines}
        flags = lock_lot_flags(self.db, article_ids)
        refs = get_article_refs(self.db, article_ids)
        changed = sorted(
            refs[line.article_id].reference
            for line in lines
            if flags[line.article_id].lot_tracked != line.lot_tracked
        )
        if changed:
            raise ConflictError(
                "Le suivi par lot d'un article a changé depuis le début du comptage : "
                "annulez cet inventaire et recommencez",
                code="inventory_lot_mode_changed",
                extra={"articles": changed},
            )
        created = self._resolve_discovered(inventory, all_rows, refs)
        tracked_ids = {line.article_id for line in tracked_lines}
        stock = self._stock()
        levels = stock.lock_levels(inventory.site_id, article_ids)
        lot_levels = stock.lock_site_lots(
            inventory.site_id,
            tracked_ids,
            {(row.article_id, row.lot_id) for row in all_rows if row.lot_id is not None},
        )
        self._ensure_no_new_lots(tracked_lines, rows_by_line, lot_levels, refs)
        requests: list[MovementRequest] = []
        lot_changes: list[dict[str, str]] = []
        surplus = shortage = 0
        surplus_value = shortage_value = ZERO
        labels = self._lot_label(all_rows)
        for line in lines:
            level = levels[line.article_id]
            if line.lot_tracked:
                physical = ZERO_QTY
                for row in rows_by_line[line.id]:
                    assert row.lot_id is not None  # rattaché ou créé ci-dessus
                    current = lot_levels[(line.article_id, row.lot_id)].quantity
                    counted = row.quantity_physical if row.quantity_physical is not None else ZERO
                    row.stock_theoretical_at_validation = current
                    row.quantity_physical = counted.quantize(QUANTITY_STEP)
                    row.quantity_variance = row.quantity_physical - current
                    physical += row.quantity_physical
                    if row.quantity_variance == 0:
                        continue
                    lot_changes.append(
                        {
                            "reference": refs[line.article_id].reference,
                            "lot_number": labels[row.id],
                            "variance": format(row.quantity_variance, "f"),
                        }
                    )
                    requests.append(
                        self._adjustment(inventory, line, row.quantity_variance, row.lot_id)
                    )
                line.quantity_physical = physical
            physical_total = line.quantity_physical
            if physical_total is None or physical_total < 0:  # garde : contrôlé et en base
                raise BusinessRuleError("Quantité physique invalide", code="invalid_quantity")
            variance = physical_total - level.quantity
            value = round_money(variance * level.average_cost)
            line.stock_theoretical_at_validation = level.quantity
            line.quantity_variance = variance
            line.unit_cost = level.average_cost
            line.adjustment_value = value
            if variance > 0:
                surplus, surplus_value = surplus + 1, surplus_value + value
            elif variance < 0:
                shortage, shortage_value = shortage + 1, shortage_value - value
            if variance != 0 and not line.lot_tracked:
                requests.append(self._adjustment(inventory, line, variance, None))
        # Moteur central : sortie au CMUP courant, excédent valorisé au CMUP courant (le type
        # ADJUSTMENT ne recalcule jamais le CMUP), stock jamais négatif, tout ou rien.
        movements = stock.apply(inventory.site_id, requests)
        stock.verify_lot_invariant(inventory.site_id, tracked_ids)
        previous = inventory.status
        inventory.status = target
        inventory.validated_at = self.now
        inventory.validated_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "validated",
            inventory,
            {
                "previous_status": previous.value,
                "status": target.value,
                "lines": len(lines),
                "surplus": surplus,
                "shortage": shortage,
                "no_variance": len(lines) - surplus - shortage,
                "movements": len(movements),
                "surplus_value": format(surplus_value, "f"),
                "shortage_value": format(shortage_value, "f"),
                "adjustment_value": format(surplus_value - shortage_value, "f"),
                **({"lots": lot_changes} if lot_changes else {}),
                **({"lots_created": created} if created else {}),
            },
        )
        return inventory

    def _adjustment(
        self,
        inventory: Inventory,
        line: InventoryLine,
        variance: Decimal,
        lot_id: uuid.UUID | None,
    ) -> MovementRequest:
        return MovementRequest(
            article_id=line.article_id,
            movement_type=MovementType.ADJUSTMENT,
            quantity=variance,
            source_type=SOURCE_TYPE,
            source_id=inventory.id,
            source_line_id=line.id,
            source_number=inventory.number,
            comment=f"Inventaire {inventory.number}",
            lot_id=lot_id,
        )

    def _resolve_discovered(
        self,
        inventory: Inventory,
        rows: Sequence[InventoryLineLot],
        refs: dict[uuid.UUID, ArticleRef],
    ) -> list[str]:
        """Lots découverts (T-4, validation seulement) : règles 3-G revérifiées avec les
        réglages courants, lot existant rattaché (même péremption, sinon ``lot_expiry_mismatch``),
        lot nouveau créé par ``resolve_lots`` (``INSERT … ON CONFLICT``, audit
        ``stock_lot.created`` avec l'inventaire comme source) ; jamais deux fois le même lot sur
        une ligne. Renvoie les numéros des lots créés."""
        discovered = [row for row in rows if row.discovered]
        if not discovered:
            return []
        inputs = [
            LotInput(row.article_id, row.lot_number, row.expiry_date, row.manufacturing_date)
            for row in discovered
        ]
        check_lot_inputs(inputs, refs)
        check_known_lots(self.db, inputs, refs)
        pending = [row for row in discovered if row.lot_id is None]
        known = existing_lot_infos(
            self.db, {lot_key(row.article_id, row.lot_number or "") for row in pending}
        )
        resolved = resolve_lots(
            self.db,
            self.ctx,
            [
                LotInput(row.article_id, row.lot_number, row.expiry_date, row.manufacturing_date)
                for row in pending
            ],
            refs,
            source={"source_type": "inventory", "source_number": inventory.number},
        )
        created = []
        for row in pending:
            key = lot_key(row.article_id, row.lot_number or "")
            row.lot_id = resolved[key]
            if key not in known:
                created.append(row.lot_number or "")
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for row in rows:
            key_line = (row.inventory_line_id, row.lot_id or uuid.UUID(int=0))
            if key_line in seen:
                raise ConflictError(
                    "Un même lot figure deux fois sur une ligne",
                    code="duplicate_lot_in_inventory",
                    extra={
                        "articles": [refs[row.article_id].reference],
                        "lots": [row.lot_number or ""],
                    },
                )
            seen.add(key_line)
        self.db.flush()
        return sorted(created)

    def _ensure_no_new_lots(
        self,
        lines: Sequence[InventoryLine],
        rows_by_line: dict[uuid.UUID, list[InventoryLineLot]],
        lot_levels: dict[tuple[uuid.UUID, uuid.UUID], Any],
        refs: dict[uuid.UUID, ArticleRef],
    ) -> None:
        """D-3 : un lot ayant un solde sur le site mais absent du comptage (apparu après le
        démarrage : réception, transfert entrant…) n'est jamais compté 0 en silence —
        ``409 inventory_lots_changed`` ; « Actualiser les lots » l'ajoute au comptage."""
        counted = {(line.article_id, row.lot_id) for line in lines for row in rows_by_line[line.id]}
        appeared = sorted(
            (
                key
                for key, level in lot_levels.items()
                if level.quantity != 0 and key not in counted
            ),
            key=lambda k: (str(k[0]), str(k[1])),
        )
        if not appeared:
            return
        infos = lot_infos(self.db, {lot_id for _, lot_id in appeared})
        raise ConflictError(
            "Des lots sont apparus sur le site depuis le début du comptage : actualisez les lots",
            code="inventory_lots_changed",
            extra={
                "articles": sorted({refs[a].reference for a, _ in appeared}),
                "lots": [
                    {
                        "article_id": str(article_id),
                        "reference": refs[article_id].reference,
                        "lot_id": str(lot_id),
                        "lot_number": infos[lot_id].number if lot_id in infos else None,
                        "quantity": format(lot_levels[(article_id, lot_id)].quantity, "f"),
                    }
                    for article_id, lot_id in appeared
                ],
            },
        )

    def cancel(self, inventory_id: uuid.UUID, reason: str) -> Inventory:
        """Avant validation seulement, sans effet sur le stock. Un inventaire validé est
        immuable : une erreur se corrige par un nouvel inventaire."""
        inventory = self.get(inventory_id, lock=True)
        target = next_status(inventory.status, "cancel")
        previous = inventory.status
        inventory.status = target
        inventory.cancelled_at = self.now
        inventory.cancelled_by = self.ctx.user.id
        inventory.cancellation_reason = reason
        self.db.flush()
        self._audit(
            "cancelled",
            inventory,
            {"previous_status": previous.value, "status": target.value, "reason": reason},
        )
        return inventory

    # --- Sortie API ---------------------------------------------------------------------------

    def _counts(
        self, inventories: Sequence[Inventory]
    ) -> dict[uuid.UUID, tuple[int, int, int, int]]:
        """(lignes, comptées, écarts non nuls, suivies par lot) par inventaire, en une requête
        groupée."""
        if not inventories:
            return {}
        levels = levels_view()
        line = InventoryLine.__table__.c
        variance = case(
            (Inventory.status == S.VALIDATED, line.quantity_variance),
            else_=line.quantity_physical - func.coalesce(levels.c.quantity, literal(ZERO)),
        )
        rows = self.db.execute(
            select(
                line.inventory_id,
                func.count(),
                func.count(line.quantity_physical),
                func.count().filter(variance != 0),
                func.count().filter(line.lot_tracked),
            )
            .select_from(InventoryLine.__table__)
            .join(Inventory, Inventory.id == line.inventory_id)
            .outerjoin(
                levels,
                and_(
                    levels.c.tenant_id == line.tenant_id,
                    levels.c.article_id == line.article_id,
                    levels.c.site_id == Inventory.site_id,
                ),
            )
            .where(line.inventory_id.in_([i.id for i in inventories]))
            .group_by(line.inventory_id)
        ).all()
        return {row[0]: (int(row[1]), int(row[2]), int(row[3]), int(row[4])) for row in rows}

    def to_out(
        self, inventories: Sequence[Inventory], *, with_summary: bool = False
    ) -> list[InventoryOut]:
        user_ids = {
            u
            for i in inventories
            for u in (i.created_by, i.started_by, i.completed_by, i.validated_by, i.cancelled_by)
            if u
        }
        users = _names(self.db, User, user_ids, User.full_name)
        sites = _names(self.db, Site, {i.site_id for i in inventories}, Site.name)
        counts = self._counts(inventories)

        def name(user_id: uuid.UUID | None) -> str | None:
            return users.get(user_id) if user_id else None

        return [
            InventoryOut(
                id=i.id,
                number=i.number,
                site_id=i.site_id,
                site_name=sites.get(i.site_id, ""),
                status=i.status,
                inventory_type=i.inventory_type,
                comment=i.comment,
                line_count=counts.get(i.id, (0, 0, 0, 0))[0],
                counted_count=counts.get(i.id, (0, 0, 0, 0))[1],
                variance_count=counts.get(i.id, (0, 0, 0, 0))[2],
                lot_tracked_count=counts.get(i.id, (0, 0, 0, 0))[3],
                created_at=i.created_at,
                updated_at=i.updated_at,
                created_by_name=name(i.created_by),
                started_at=i.started_at,
                started_by_name=name(i.started_by),
                completed_at=i.completed_at,
                completed_by_name=name(i.completed_by),
                validated_at=i.validated_at,
                validated_by_name=name(i.validated_by),
                cancelled_at=i.cancelled_at,
                cancelled_by_name=name(i.cancelled_by),
                cancellation_reason=i.cancellation_reason,
                summary=self.summary(i) if with_summary else None,
            )
            for i in inventories
        ]


def _state_condition(state: LineState, physical: Any, variance: Any) -> ColumnElement[bool] | None:
    if state is LineState.ALL:
        return None
    if state is LineState.COUNTED:
        return physical.is_not(None)  # type: ignore[no-any-return]
    if state is LineState.UNCOUNTED:
        return physical.is_(None)  # type: ignore[no-any-return]
    if state is LineState.SURPLUS:
        return and_(physical.is_not(None), variance > 0)
    if state is LineState.SHORTAGE:
        return and_(physical.is_not(None), variance < 0)
    return and_(physical.is_not(None), variance == 0)


# (conditionnement, nom, conversion, quantité de conditionnements, unités en vrac)
Presentation = tuple[uuid.UUID | None, str | None, Decimal | None, Decimal | None, Decimal | None]
NO_PRESENTATION: Presentation = (None, None, None, None, None)


def _presentation_of(line: InventoryLine | InventoryLineLot) -> Presentation:
    return (
        line.count_packaging_id,
        line.count_packaging_name,
        line.count_packaging_conversion,
        line.count_packaging_quantity,
        line.count_unit_quantity,
    )


def _base_count(
    ref: ArticleRef, packaging: PackagingRef, packaging_quantity: Decimal, unit_quantity: Decimal
) -> Decimal:
    """Quantité physique en unité de base = conditionnements × conversion + unités en vrac,
    SANS arrondi (plus de 3 décimales : refus) ; article entier : tout entier."""
    physical = packaging_quantity * packaging.conversion + unit_quantity
    ensure_whole(ref, packaging_quantity, unit_quantity, physical)
    if physical != physical.quantize(BASE_STEP):
        raise BusinessRuleError(
            "La quantité en unité de base dépasse la précision autorisée (3 décimales)",
            code="base_quantity_precision",
            extra={"articles": [ref.reference]},
        )
    return physical.quantize(BASE_STEP)


def _count_quantity(
    ref: ArticleRef, count: CountFields, packaging: PackagingRef | None
) -> tuple[Decimal | None, Presentation]:
    """Quantité physique (unité de base) et présentation d'un comptage saisi (Lot 3-C)."""
    if packaging is None:
        if count.quantity_physical is None:
            return None, NO_PRESENTATION
        quantity = count.quantity_physical.quantize(QUANTITY_STEP)
        ensure_whole(ref, quantity)
        return quantity, NO_PRESENTATION
    assert count.packaging_quantity is not None
    packaging_quantity = count.packaging_quantity.quantize(QUANTITY_STEP)
    unit_quantity = (count.unit_quantity or Decimal("0")).quantize(QUANTITY_STEP)
    physical = _base_count(ref, packaging, packaging_quantity, unit_quantity)
    return physical, (
        packaging.id,
        packaging.name,
        packaging.conversion,
        packaging_quantity,
        unit_quantity,
    )


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _names(db: Session, model: Any, ids: set[Any], column: Any) -> dict[Any, str]:
    if not ids:
        return {}
    return {row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(ids)))}


def packagings_used(db: Session, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Port du catalogue (Lot 3-C) : conditionnements utilisés par un comptage d'inventaire
    (ligne ou lot, Lot 3-H)."""
    used: set[uuid.UUID] = set()
    for column in (InventoryLine.count_packaging_id, InventoryLineLot.count_packaging_id):
        used |= {
            packaging_id
            for packaging_id in db.scalars(select(column).where(column.in_(ids)).distinct())
            if packaging_id is not None
        }
    return used


def lot_flags_check(
    db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID, enabling: bool
) -> list[LotFlagsBlocker]:
    """Port du catalogue (Lot 3-H) : un article figurant dans un inventaire OUVERT ne change pas
    de suivi par lot ou de péremption (mode figé au démarrage, lots en cours de comptage)."""
    return [
        LotFlagsBlocker(BlockerKind.OPEN_DOCUMENT, number)
        for number in db.scalars(
            select(Inventory.number)
            .join(InventoryLine, InventoryLine.inventory_id == Inventory.id)
            .where(Inventory.status.in_(OPEN_STATUSES), InventoryLine.article_id == article_id)
            .distinct()
            .limit(20)
        )
    ]
