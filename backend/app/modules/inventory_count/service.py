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
from app.modules.catalog.api import articles_view, get_article_refs
from app.modules.inventory_count.models import (
    Inventory,
    InventoryLine,
    InventoryStatus,
    InventoryType,
)
from app.modules.inventory_count.schemas import (
    CandidateOut,
    CountInput,
    InventoryCreate,
    InventoryLineOut,
    InventoryOut,
    InventorySummary,
    InventoryUpdate,
    LineState,
)
from app.modules.stock.api import (
    MovementRequest,
    MovementType,
    StockService,
    ensure_document_site,
    levels_view,
    operation_site,
    round_money,
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
            .where(line.inventory_id == inventory.id)
        )
        return stmt, {"articles": articles, "variance": variance, "value": value, "line": line}

    def lines(
        self,
        inventory: Inventory,
        params: PageParams,
        *,
        search: str | None = None,
        state: LineState = LineState.ALL,
        line_ids: Iterable[uuid.UUID] | None = None,
    ) -> tuple[list[InventoryLineOut], int]:
        stmt, cols = self._base_lines(inventory)
        articles, variance, line = cols["articles"], cols["variance"], cols["line"]
        conditions: list[ColumnElement[bool] | None] = [
            search_filter(search, articles.c.reference, articles.c.designation, articles.c.barcode),
            line.id.in_(list(line_ids)) if line_ids is not None else None,
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
        }
        stmt = apply_sort(stmt, params.sort, sortable, "reference", line.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return self._lines_out(inventory, rows), total

    def _lines_out(self, inventory: Inventory, rows: Sequence[Any]) -> list[InventoryLineOut]:
        final = inventory.status is S.VALIDATED
        open_ = inventory.status in OPEN_STATUSES
        users = _names(self.db, User, {r.counted_by for r in rows if r.counted_by}, User.full_name)
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
                )
            )
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
            .where(articles.c.tenant_id == self.ctx.tenant_id, articles.c.is_active.is_(True))
        )
        by_text = search_filter(
            search, articles.c.reference, articles.c.designation, articles.c.barcode
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
        if not article_ids:
            raise BusinessRuleError("Aucun article à compter", code="inventory_empty")
        # Instantané du stock théorique au début du comptage (information seulement).
        quantities = self._quantities(inventory.site_id, article_ids)
        line_ids = self._line_articles(inventory)
        self.db.execute(
            update(InventoryLine),
            [
                {"id": line_ids[article_id], "stock_theoretical_initial": quantity}
                for article_id, quantity in quantities.items()
            ],
        )
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
            },
        )
        return inventory

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
        changes: list[tuple[uuid.UUID, Decimal | None, Decimal | None]] = []
        for count in counts:
            line = lines[count.line_id]
            quantity = (
                None
                if count.quantity_physical is None
                else count.quantity_physical.quantize(QUANTITY_STEP)
            )
            if quantity == line.quantity_physical:
                continue
            changes.append((line.article_id, line.quantity_physical, quantity))
            line.quantity_physical = quantity
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

    def validate(self, inventory_id: uuid.UUID) -> Inventory:
        """Applique les écarts au stock, en une transaction (tout ou rien) :

        1. verrou de l'inventaire et contrôle du statut (PRÊT À VALIDER) ;
        2. contrôle du comptage complet ;
        3. verrou des niveaux (ordre global de ``StockService``) et relecture du stock courant ;
        4. écart = physique − stock courant ; valeur = écart × CMUP courant ;
        5. mouvements ``ADJUSTMENT`` pour les écarts non nuls, via ``StockService.apply``
           (contrôle du stock négatif, CMUP inchangé) ;
        6. lignes figées, statut VALIDÉ, audit. Le commit est fait par l'endpoint."""
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
        stock = self._stock()
        levels = stock.lock_levels(inventory.site_id, {line.article_id for line in lines})
        requests: list[MovementRequest] = []
        surplus = shortage = 0
        surplus_value = shortage_value = ZERO
        for line in lines:
            physical = line.quantity_physical
            if physical is None or physical < 0:  # garde : contrôlé ci-dessus et en base
                raise BusinessRuleError("Quantité physique invalide", code="invalid_quantity")
            level = levels[line.article_id]
            variance = physical - level.quantity
            value = round_money(variance * level.average_cost)
            line.stock_theoretical_at_validation = level.quantity
            line.quantity_variance = variance
            line.unit_cost = level.average_cost
            line.adjustment_value = value
            if variance == 0:
                continue  # aucun mouvement sans écart
            if variance > 0:
                surplus, surplus_value = surplus + 1, surplus_value + value
            else:
                shortage, shortage_value = shortage + 1, shortage_value - value
            requests.append(
                MovementRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.ADJUSTMENT,
                    quantity=variance,
                    source_type=SOURCE_TYPE,
                    source_id=inventory.id,
                    source_line_id=line.id,
                    source_number=inventory.number,
                    comment=f"Inventaire {inventory.number}",
                )
            )
        # Moteur central : sortie au CMUP courant, excédent valorisé au CMUP courant (le type
        # ADJUSTMENT ne recalcule jamais le CMUP), stock jamais négatif, tout ou rien.
        movements = stock.apply(inventory.site_id, requests)
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
            },
        )
        return inventory

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

    def _counts(self, inventories: Sequence[Inventory]) -> dict[uuid.UUID, tuple[int, int, int]]:
        """(lignes, comptées, écarts non nuls) par inventaire, en une requête groupée."""
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
        return {row[0]: (int(row[1]), int(row[2]), int(row[3])) for row in rows}

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
                line_count=counts.get(i.id, (0, 0, 0))[0],
                counted_count=counts.get(i.id, (0, 0, 0))[1],
                variance_count=counts.get(i.id, (0, 0, 0))[2],
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


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _names(db: Session, model: Any, ids: set[Any], column: Any) -> dict[Any, str]:
    if not ids:
        return {}
    return {row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(ids)))}
