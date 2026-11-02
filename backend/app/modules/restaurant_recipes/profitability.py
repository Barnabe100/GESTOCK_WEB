"""Rentabilité des recettes (palier R10, ADR-0055) : ce qu'ont RÉELLEMENT coûté les
ingrédients des plats vendus, sur une période.

- portions vendues = Σ quantité en unité de base des lignes de vente VALIDÉES du plat qui ont
  déduit des ingrédients (consommations matérialisées, ADR-0053 O10) ;
- chiffre d'affaires = Σ montants de ces lignes (avant remise globale, ADR-0054) ;
- coût des ingrédients = Σ coûts des mouvements de sortie des consommations de ces lignes
  (CMUP du site au moment de la vente) ; coût par portion = coût / portions ;
- marge = CA − coût ; taux = marge / CA × 100.

Une ligne vendue sans consommation (recette inactive ou module désactivé à ce moment) n'entre
pas dans ce rapport. Coûts et marges masqués sans ``catalog.article.cost_view``.
"""

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalog.api import articles_view
from app.modules.restaurant_recipes.models import RecipeConsumption
from app.modules.sales.api import SALE_SOURCE_TYPE, report_lines_query
from app.modules.stock.api import outflow_costs_query
from app.shared.pagination import PageParams, apply_sort, paginate_rows, text_sort

CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def profitability_rows(
    db: Session,
    tenant_id: uuid.UUID,
    site_ids: list[uuid.UUID],
    date_from: date,
    date_to: date,
    params: PageParams,
    *,
    costs_visible: bool,
) -> tuple[list[dict[str, Any]], int]:
    lines = report_lines_query(tenant_id).subquery()
    movements = outflow_costs_query(tenant_id, SALE_SOURCE_TYPE).subquery()
    # Coût des ingrédients par ligne de vente (une consommation par ingrédient).
    per_line = (
        select(
            RecipeConsumption.sale_line_id.label("line_id"),
            func.coalesce(func.sum(movements.c.cost), 0).label("cost"),
        )
        .outerjoin(
            movements,
            (movements.c.source_id == RecipeConsumption.sale_id)
            & (movements.c.source_line_id == RecipeConsumption.id),
        )
        .where(RecipeConsumption.tenant_id == tenant_id)
        .group_by(RecipeConsumption.sale_line_id)
        .subquery()
    )
    articles = articles_view()
    revenue = func.sum(lines.c.line_total).label("revenue")
    cost = func.sum(per_line.c.cost).label("ingredient_cost")
    portions = func.sum(lines.c.base_quantity).label("portions")
    label = func.max(articles.c.designation).label("designation")
    stmt = (
        select(
            lines.c.article_id.label("article_id"),
            func.max(articles.c.reference).label("reference"),
            label,
            func.max(articles.c.unit).label("unit"),
            func.count(func.distinct(lines.c.sale_id)).label("sales_count"),
            portions,
            revenue,
            cost,
        )
        .join(per_line, per_line.c.line_id == lines.c.line_id)
        .join(articles, articles.c.id == lines.c.article_id)
        .where(
            lines.c.site_id.in_(site_ids),
            lines.c.sale_date >= date_from,
            lines.c.sale_date <= date_to,
        )
        .group_by(lines.c.article_id)
    )
    sortable: dict[str, Any] = {
        "revenue": revenue,
        "portions": portions,
        "designation": text_sort(label),
    }
    if costs_visible:
        sortable |= {"ingredient_cost": cost, "margin": revenue - cost}
    stmt = apply_sort(stmt, params.sort, sortable, "-revenue", lines.c.article_id)
    rows, total = paginate_rows(db, stmt, params)
    out = []
    for r in rows:
        rev, ing = _money(r.revenue), _money(r.ingredient_cost)
        qty = Decimal(r.portions or 0)
        out.append(
            {
                "article_id": r.article_id,
                "reference": r.reference,
                "designation": r.designation,
                "unit": r.unit,
                "sales_count": int(r.sales_count),
                "portions": qty,
                "revenue": rev,
                "ingredient_cost": ing,
                "portion_cost": _money(ing / qty) if qty else None,
                "margin": rev - ing,
                "margin_rate": _money((rev - ing) * 100 / rev) if rev else None,
            }
        )
    return out, total
