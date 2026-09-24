"""Pagination, tri et recherche côté serveur (listes paginées de l'API)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from fastapi import Query
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import SQLColumnExpression

from app.core.errors import AppError

T = TypeVar("T")

MAX_LIMIT = 200

Column = SQLColumnExpression[Any]


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int
    sort: str | None


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[
        str | None,
        Query(max_length=50, description="Champ de tri ; préfixe « - » pour l'ordre décroissant"),
    ] = None,
) -> PageParams:
    return PageParams(limit=limit, offset=offset, sort=sort)


def apply_sort(
    stmt: Select[Any],
    sort: str | None,
    allowed: Mapping[str, Column],
    default: str,
    tiebreaker: Column,
) -> Select[Any]:
    """Tri sur une liste blanche de champs (jamais de nom de colonne venant du client)."""
    key = sort or default
    descending = key.startswith("-")
    column = allowed.get(key.lstrip("-"))
    if column is None:
        raise AppError(
            "Champ de tri invalide", code="invalid_sort", extra={"allowed": sorted(allowed)}
        )
    return stmt.order_by(column.desc() if descending else column.asc(), tiebreaker)


# Tri alphabétique linguistique (accents, casse) indépendant de la collation de la base :
# « Électricité » avant « Outillage ».
TEXT_COLLATION = "und-x-icu"


def text_sort(column: Column) -> Column:
    return column.collate(TEXT_COLLATION)


def paginate(db: Session, stmt: Select[Any], params: PageParams) -> tuple[list[Any], int]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(db.scalars(stmt.limit(params.limit).offset(params.offset)).unique())
    return rows, total


def paginate_rows(db: Session, stmt: Select[Any], params: PageParams) -> tuple[list[Any], int]:
    """Comme ``paginate`` pour une requête multi-colonnes (lignes, pas entités)."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(db.execute(stmt.limit(params.limit).offset(params.offset)).all())
    return rows, total


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_filter(term: str | None, *columns: Column) -> ColumnElement[bool] | None:
    """Recherche « contient », insensible à la casse, sur plusieurs colonnes."""
    cleaned = (term or "").strip()
    if not cleaned:
        return None
    pattern = f"%{escape_like(cleaned)}%"
    return or_(*(column.ilike(pattern, escape="\\") for column in columns))
