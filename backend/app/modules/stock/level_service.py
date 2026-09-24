"""Consultation du stock par site, seuils effectifs, surcharges par site (Q2) et alertes
(ALR-01, ALR-02).

Seuils effectifs d'un article sur un site : surcharge du site si elle existe, sinon seuil par
défaut de l'article. États :
- ``out``  : article géré sur le site (niveau existant) et stock nul (rupture) ;
- ``low``  : 0 < stock ≤ minimum effectif (stock faible) ;
- ``ok``   : au-dessus du minimum ;
- ``not_stocked`` : jamais géré sur ce site (aucun niveau) — jamais une alerte.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, NotFoundError
from app.modules.catalog.api import articles_view, get_article_refs
from app.modules.stock.models import StockLevel
from app.modules.stock.stock_service import StockService, round_money
from app.platform.audit.service import audit_action, changes
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort

QUANTITY_STEP = Decimal("0.001")


class LevelState(StrEnum):
    OUT = "out"
    LOW = "low"
    OK = "ok"
    NOT_STOCKED = "not_stocked"


class StateFilter(StrEnum):
    ALL = "all"
    ALERTS = "alerts"  # out + low
    OUT = "out"
    LOW = "low"
    OK = "ok"
    NOT_STOCKED = "not_stocked"


@dataclass(frozen=True)
class LevelRow:
    site_id: uuid.UUID
    site_name: str
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    category_name: str
    article_active: bool
    quantity: Decimal
    average_cost: Decimal
    stock_value: Decimal
    min_stock: Decimal
    max_stock: Decimal | None
    min_override: Decimal | None
    max_override: Decimal | None
    state: LevelState


def levels_view() -> Any:
    """Vue en lecture des niveaux de stock (quantité, CMUP par site et article) pour les
    jointures d'autres modules (inventaires). Filtrage par tenant : RLS et condition de
    l'appelant. Aucune écriture : seul ``StockService`` modifie les niveaux."""
    level = StockLevel.__table__
    return select(
        level.c.tenant_id,
        level.c.site_id,
        level.c.article_id,
        level.c.quantity,
        level.c.average_cost,
    ).subquery("stock_levels_view")


def _levels_query(site_ids: set[uuid.UUID], tenant_id: uuid.UUID) -> tuple[Any, dict[str, Any]]:
    articles = articles_view()
    sites = select(Site.id, Site.name).where(Site.id.in_(site_ids)).subquery("s")
    level = StockLevel.__table__
    quantity = func.coalesce(level.c.quantity, literal(Decimal("0")))
    min_eff = func.coalesce(level.c.min_stock, articles.c.min_stock)
    max_eff = func.coalesce(level.c.max_stock, articles.c.max_stock)
    state = case(
        (level.c.id.is_(None), literal(LevelState.NOT_STOCKED.value)),
        (quantity <= 0, literal(LevelState.OUT.value)),
        (quantity <= min_eff, literal(LevelState.LOW.value)),
        else_=literal(LevelState.OK.value),
    )
    stmt = (
        select(
            sites.c.id.label("site_id"),
            sites.c.name.label("site_name"),
            articles.c.id.label("article_id"),
            articles.c.reference,
            articles.c.designation,
            articles.c.unit,
            articles.c.category_name,
            articles.c.is_active.label("article_active"),
            quantity.label("quantity"),
            func.coalesce(level.c.average_cost, literal(Decimal("0"))).label("average_cost"),
            min_eff.label("min_stock"),
            max_eff.label("max_stock"),
            level.c.min_stock.label("min_override"),
            level.c.max_stock.label("max_override"),
            state.label("state"),
        )
        .select_from(articles.join(sites, literal(True)))
        .outerjoin(
            level,
            and_(
                level.c.tenant_id == articles.c.tenant_id,
                level.c.article_id == articles.c.id,
                level.c.site_id == sites.c.id,
            ),
        )
        .where(articles.c.tenant_id == tenant_id)
    )
    columns = {
        "articles": articles,
        "quantity": quantity,
        "state": state,
        "category_id": articles.c.category_id,
    }
    return stmt, columns


def _state_condition(state: ColumnElement[Any], wanted: StateFilter) -> ColumnElement[bool] | None:
    if wanted is StateFilter.ALL:
        return None
    if wanted is StateFilter.ALERTS:
        return or_(state == LevelState.OUT.value, state == LevelState.LOW.value)
    return state == wanted.value


def _to_row(row: Any) -> LevelRow:
    return LevelRow(
        site_id=row.site_id,
        site_name=row.site_name,
        article_id=row.article_id,
        reference=row.reference,
        designation=row.designation,
        unit=row.unit,
        category_name=row.category_name,
        article_active=row.article_active,
        quantity=row.quantity,
        average_cost=row.average_cost,
        stock_value=round_money(row.quantity * row.average_cost),
        min_stock=row.min_stock,
        max_stock=row.max_stock,
        min_override=row.min_override,
        max_override=row.max_override,
        state=LevelState(row.state),
    )


def list_levels(
    db: Session,
    tenant_id: uuid.UUID,
    site_ids: set[uuid.UUID],
    params: PageParams,
    *,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    state: StateFilter = StateFilter.ALL,
    include_inactive: bool = False,
    article_ids: set[uuid.UUID] | None = None,
) -> tuple[list[LevelRow], int]:
    stmt, cols = _levels_query(site_ids, tenant_id)
    articles = cols["articles"]
    conditions = [
        search_filter(search, articles.c.reference, articles.c.designation, articles.c.barcode),
        # Articles précis (ex. stock disponible des lignes d'un transfert en saisie).
        articles.c.id.in_(article_ids) if article_ids else None,
        articles.c.category_id == category_id if category_id else None,
        None if include_inactive else articles.c.is_active.is_(True),
        _state_condition(cols["state"], state),
    ]
    for condition in conditions:
        if condition is not None:
            stmt = stmt.where(condition)
    sortable = {
        "reference": text_sort(articles.c.reference),
        "designation": text_sort(articles.c.designation),
        "category": text_sort(articles.c.category_name),
        "quantity": cols["quantity"],
        "site": text_sort(stmt.selected_columns.site_name),
    }
    stmt = apply_sort(stmt, params.sort, sortable, "reference", articles.c.id)
    rows, total = paginate_rows(db, stmt, params)
    return [_to_row(r) for r in rows], total


def get_level(
    db: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, article_id: uuid.UUID
) -> LevelRow:
    stmt, cols = _levels_query({site_id}, tenant_id)
    row = db.execute(stmt.where(cols["articles"].c.id == article_id)).one_or_none()
    if row is None:
        raise NotFoundError("Article introuvable", code="article_not_found")
    return _to_row(row)


def count_alerts(db: Session, tenant_id: uuid.UUID, site_ids: set[uuid.UUID]) -> dict[str, int]:
    stmt, cols = _levels_query(site_ids, tenant_id)
    stmt = stmt.where(cols["articles"].c.is_active.is_(True))
    sub = stmt.subquery()
    counts: dict[str, int] = {
        state: count
        for state, count in db.execute(
            select(sub.c.state, func.count())
            .where(sub.c.state.in_([LevelState.OUT.value, LevelState.LOW.value]))
            .group_by(sub.c.state)
        ).tuples()
    }
    return {"out": int(counts.get("out", 0)), "low": int(counts.get("low", 0))}


def _qty(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(QUANTITY_STEP)


class ThresholdService:
    """Surcharges de seuils par site (Q2). Ne touche jamais la quantité ni le CMUP."""

    def __init__(self, db: Session, ctx: RequestContext, stock: StockService) -> None:
        self.db = db
        self.ctx = ctx
        self.stock = stock

    def set_thresholds(
        self,
        site_id: uuid.UUID,
        article_id: uuid.UUID,
        min_stock: Decimal | None,
        max_stock: Decimal | None,
    ) -> StockLevel:
        min_stock = _qty(min_stock)
        max_stock = _qty(max_stock)
        ref = get_article_refs(self.db, {article_id}).get(article_id)
        if ref is None:
            raise NotFoundError("Article introuvable", code="article_not_found")
        effective_min = min_stock if min_stock is not None else ref.min_stock
        effective_max = max_stock if max_stock is not None else ref.max_stock
        if effective_max is not None and effective_max < effective_min:
            raise BusinessRuleError(
                "Le stock maximum doit être supérieur ou égal au stock minimum",
                code="invalid_stock_thresholds",
            )
        level = self.stock.lock_levels(site_id, {article_id})[article_id]
        before = {"min_stock": level.min_stock, "max_stock": level.max_stock}
        level.min_stock = min_stock
        level.max_stock = max_stock
        self.db.flush()
        diff = changes(before, {"min_stock": min_stock, "max_stock": max_stock})
        if diff:
            audit_action(
                self.db,
                self.ctx,
                "stock_threshold.updated",
                entity_type="article",
                entity_id=article_id,
                site_id=site_id,
                data={"reference": ref.reference, **diff},
            )
        return level
