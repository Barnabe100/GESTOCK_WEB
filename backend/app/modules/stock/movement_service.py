"""Journal des mouvements de stock (lecture seule, STK-05) : filtres par site, article, type,
utilisateur, période et recherche (article, numéro de document)."""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.orm import Session

from app.modules.catalog.api import articles_view
from app.modules.stock.models import MovementType, StockEntry, StockExit, StockMovement
from app.platform.identity.models import User
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter


def list_movements(
    db: Session,
    tenant_id: uuid.UUID,
    timezone: str,
    site_ids: set[uuid.UUID],
    params: PageParams,
    *,
    search: str | None = None,
    article_id: uuid.UUID | None = None,
    movement_type: MovementType | None = None,
    user_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[Any], int]:
    articles = articles_view()
    movement = StockMovement
    document_number = func.coalesce(StockEntry.number, StockExit.number)
    local_date = cast(func.timezone(timezone, movement.occurred_at), Date)
    stmt = (
        select(
            movement,
            Site.name.label("site_name"),
            articles.c.reference.label("article_reference"),
            articles.c.designation.label("article_designation"),
            articles.c.unit.label("unit"),
            User.full_name.label("user_name"),
            document_number.label("document_number"),
        )
        .join(Site, and_(Site.id == movement.site_id, Site.tenant_id == movement.tenant_id))
        .join(
            articles,
            and_(
                articles.c.id == movement.article_id,
                articles.c.tenant_id == movement.tenant_id,
            ),
        )
        .outerjoin(User, User.id == movement.user_id)
        .outerjoin(
            StockEntry,
            and_(
                movement.source_type == "stock_entry",
                StockEntry.id == movement.source_id,
                StockEntry.tenant_id == movement.tenant_id,
            ),
        )
        .outerjoin(
            StockExit,
            and_(
                movement.source_type == "stock_exit",
                StockExit.id == movement.source_id,
                StockExit.tenant_id == movement.tenant_id,
            ),
        )
        .where(movement.tenant_id == tenant_id, movement.site_id.in_(site_ids))
    )
    conditions = [
        search_filter(search, articles.c.reference, articles.c.designation, document_number),
        movement.article_id == article_id if article_id else None,
        movement.movement_type == movement_type if movement_type else None,
        movement.user_id == user_id if user_id else None,
        local_date >= date_from if date_from else None,
        local_date <= date_to if date_to else None,
    ]
    for condition in conditions:
        if condition is not None:
            stmt = stmt.where(condition)
    sortable = {"occurred_at": movement.occurred_at}
    stmt = apply_sort(stmt, params.sort, sortable, "-occurred_at", movement.id)
    return paginate_rows(db, stmt, params)
