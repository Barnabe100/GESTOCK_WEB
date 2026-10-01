"""Réceptions d'un fournisseur vues du stock (Lot 3-E, ADR-0043) — LECTURE SEULE.

Le stock possède les entrées : la synthèse et les articles reçus d'un fournisseur sont calculés
ici, à partir des seules réceptions (``PURCHASE``) **VALIDÉES** des sites visibles du membre
(portée de ``stock.entry.view``). Une réception brouillon ou annulée n'entre dans aucun agrégat,
ni dans le « dernier coût ». Le fournisseur n'est lu que par l'API publique de ``suppliers``.

Coûts (``received_total``, ``last_unit_cost``) : retirés des réponses sans
``catalog.article.cost_view`` par le routeur du stock (``STOCK_COST_FIELDS``). Aucune écriture :
ni stock, ni CMUP, ni prix d'achat de référence du catalogue (décision D2).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, distinct, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.modules.catalog.api import articles_view
from app.modules.stock.models import DocumentStatus, EntryKind, StockEntry, StockEntryLine
from app.modules.stock.sites import filter_site_ids
from app.modules.stock.stock_service import cost_per_base, round_cost, round_money
from app.modules.suppliers.api import get_supplier_ref
from app.platform.context import RequestContext
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort


@dataclass(frozen=True)
class SupplierSummary:
    supplier_id: uuid.UUID
    validated_count: int
    last_received_on: date | None
    received_total: Decimal


@dataclass(frozen=True)
class ReceivedArticle:
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    article_active: bool
    receipt_count: int
    received_base_quantity: Decimal
    last_received_on: date
    last_entry_id: uuid.UUID
    last_entry_number: str
    # Coût par UNITÉ DE BASE de la dernière réception validée de l'article.
    last_unit_cost: Decimal


def _join() -> Any:
    return (StockEntry.tenant_id == StockEntryLine.tenant_id) & (
        StockEntry.id == StockEntryLine.entry_id
    )


class SupplierReceptions:
    def __init__(self, db: Session, ctx: RequestContext, supplier_id: uuid.UUID) -> None:
        if get_supplier_ref(db, supplier_id) is None:
            raise NotFoundError("Fournisseur introuvable", code="supplier_not_found")
        self.db = db
        self.ctx = ctx
        self.supplier_id = supplier_id

    def _validated(self, site_id: uuid.UUID | None) -> list[ColumnElement[bool]]:
        """Réceptions VALIDÉES de ce fournisseur sur les sites visibles (filtre de site inclus)."""
        return [
            StockEntry.tenant_id == self.ctx.tenant_id,
            StockEntry.supplier_id == self.supplier_id,
            StockEntry.kind == EntryKind.PURCHASE,
            StockEntry.status == DocumentStatus.VALIDATED,
            StockEntry.site_id.in_(filter_site_ids(self.ctx, site_id)),
        ]

    def summary(self, site_id: uuid.UUID | None) -> SupplierSummary:
        row = self.db.execute(
            select(
                func.count(distinct(StockEntry.id)),
                func.max(StockEntry.operation_date),
                func.coalesce(func.sum(StockEntryLine.amount), Decimal("0")),
            )
            .select_from(StockEntry)
            .join(StockEntryLine, _join())
            .where(*self._validated(site_id))
        ).one()
        return SupplierSummary(
            supplier_id=self.supplier_id,
            validated_count=int(row[0]),
            last_received_on=row[1],
            received_total=round_money(Decimal(row[2])),
        )

    def articles(
        self, params: PageParams, search: str | None, site_id: uuid.UUID | None
    ) -> tuple[list[ReceivedArticle], int]:
        conditions = self._validated(site_id)
        received = (
            select(
                StockEntryLine.article_id,
                func.count(distinct(StockEntry.id)).label("receipt_count"),
                func.max(StockEntry.operation_date).label("last_received_on"),
                func.sum(StockEntryLine.base_quantity).label("received_base_quantity"),
            )
            .join(StockEntry, _join())
            .where(*conditions)
            .group_by(StockEntryLine.article_id)
            .subquery("received")
        )
        articles = articles_view()
        stmt = select(
            received,
            articles.c.reference,
            articles.c.designation,
            articles.c.unit,
            articles.c.is_active,
        ).join(articles, articles.c.id == received.c.article_id)
        condition = search_filter(search, articles.c.reference, articles.c.designation)
        if condition is not None:
            stmt = stmt.where(condition)
        sortable = {
            "designation": text_sort(articles.c.designation),
            "reference": text_sort(articles.c.reference),
            "last_received_on": received.c.last_received_on,
            "received_base_quantity": received.c.received_base_quantity,
            "receipt_count": received.c.receipt_count,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "-last_received_on", articles.c.id)
        rows, total = paginate_rows(self.db, stmt, params)
        last = self._last_receptions({row.article_id for row in rows}, conditions)
        return [
            ReceivedArticle(
                article_id=row.article_id,
                article_reference=row.reference,
                article_designation=row.designation,
                unit=row.unit,
                article_active=row.is_active,
                receipt_count=int(row.receipt_count),
                received_base_quantity=row.received_base_quantity,
                last_received_on=row.last_received_on,
                last_entry_id=last[row.article_id][0],
                last_entry_number=last[row.article_id][1],
                last_unit_cost=last[row.article_id][2],
            )
            for row in rows
        ], total

    def _last_receptions(
        self, article_ids: set[uuid.UUID], conditions: list[ColumnElement[bool]]
    ) -> dict[uuid.UUID, tuple[uuid.UUID, str, Decimal]]:
        """Dernière réception VALIDÉE de chaque article (date d'opération, puis validation) et
        son coût par unité de base. Plusieurs lignes du même article dans cette réception (une
        par présentation, Lot 3-C) : coût moyen pondéré par les quantités de base."""
        if not article_ids:
            return {}
        rank = (
            func.dense_rank()
            .over(
                partition_by=StockEntryLine.article_id,
                order_by=(
                    StockEntry.operation_date.desc(),
                    StockEntry.validated_at.desc(),
                    StockEntry.id.desc(),
                ),
            )
            .label("rank")
        )
        ranked = (
            select(
                StockEntryLine.article_id,
                StockEntryLine.unit_cost,
                StockEntryLine.packaging_conversion,
                StockEntryLine.base_quantity,
                StockEntry.id.label("entry_id"),
                StockEntry.number,
                rank,
            )
            .join(StockEntry, _join())
            .where(*conditions, StockEntryLine.article_id.in_(article_ids))
            .subquery("ranked")
        )
        totals: dict[uuid.UUID, list[Any]] = {}
        for row in self.db.execute(select(ranked).where(ranked.c.rank == 1)):
            entry = totals.setdefault(
                row.article_id, [row.entry_id, row.number, Decimal("0"), Decimal("0")]
            )
            base_cost = cost_per_base(row.unit_cost, row.packaging_conversion)
            entry[2] += base_cost * row.base_quantity
            entry[3] += row.base_quantity
        return {
            article_id: (entry_id, number, round_cost(value / quantity))
            for article_id, (entry_id, number, value, quantity) in totals.items()
        }
