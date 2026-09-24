"""Moteur central du stock (STK-01 à STK-09) : SEUL code autorisé à modifier la quantité et le
CMUP d'un niveau de stock.

- S'exécute dans la transaction de l'appelant (jamais de commit ici) : document + mouvements +
  niveaux réussissent ou échouent ensemble.
- Verrouille les niveaux concernés (``SELECT … FOR UPDATE``) dans l'ordre des identifiants
  d'article : deux opérations simultanées sur un même article s'exécutent l'une après l'autre,
  sans interblocage.
- Refuse tout stock négatif avant la moindre écriture (et la base le refuse aussi).
- Recalcule le CMUP uniquement sur une ENTRÉE, avec 4 décimales (Q6).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.api import get_article_refs
from app.modules.stock.models import MovementType, StockLevel, StockMovement

COST_PRECISION = Decimal("0.0001")
MONEY_PRECISION = Decimal("0.01")


def round_cost(value: Decimal) -> Decimal:
    return value.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


def round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


def compute_average_cost(
    quantity_before: Decimal, cost_before: Decimal, quantity_in: Decimal, unit_cost: Decimal
) -> Decimal:
    """CMUP = ((stock_avant × CMUP_avant) + (q × coût)) / (stock_avant + q), 4 décimales.
    Avec un stock initial nul, le CMUP vaut le coût d'entrée (STK-05)."""
    total = quantity_before + quantity_in
    if total <= 0:
        raise ValueError("une entrée doit augmenter le stock")
    return round_cost((quantity_before * cost_before + quantity_in * unit_cost) / total)


@dataclass(frozen=True)
class MovementRequest:
    article_id: uuid.UUID
    movement_type: MovementType
    quantity: Decimal  # signée : > 0 augmente le stock, < 0 le diminue
    source_type: str
    source_id: uuid.UUID
    source_line_id: uuid.UUID
    unit_cost: Decimal | None = None  # obligatoire pour une ENTRÉE
    origin_movement_id: uuid.UUID | None = None
    comment: str | None = None
    source_number: str | None = None


@dataclass(frozen=True)
class MovementRef:
    """Mouvement d'origine d'une ligne (pour une annulation par mouvement inverse)."""

    id: uuid.UUID
    unit_cost: Decimal | None


class StockService:
    def __init__(
        self, db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID | None, now: datetime
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.now = now

    def lock_levels(
        self, site_id: uuid.UUID, article_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, StockLevel]:
        """Crée au besoin puis verrouille les niveaux (site, articles), ordre déterministe."""
        ordered = sorted(article_ids)
        if not ordered:
            return {}
        self.db.execute(
            insert(StockLevel)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": self.tenant_id,
                        "site_id": site_id,
                        "article_id": article_id,
                        "quantity": Decimal("0"),
                        "average_cost": Decimal("0"),
                    }
                    for article_id in ordered
                ]
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "article_id"])
        )
        levels = self.db.scalars(
            select(StockLevel)
            .where(StockLevel.site_id == site_id, StockLevel.article_id.in_(ordered))
            .order_by(StockLevel.article_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        return {level.article_id: level for level in levels}

    def apply(self, site_id: uuid.UUID, requests: list[MovementRequest]) -> list[StockMovement]:
        """Applique les mouvements (tout ou rien) et renvoie les mouvements créés, dans l'ordre
        des demandes. Pour une SORTIE, ``unit_cost`` du mouvement = CMUP du site (figé)."""
        levels = self.lock_levels(site_id, {r.article_id for r in requests})
        planned: list[tuple[MovementRequest, StockLevel]] = [
            (r, levels[r.article_id]) for r in requests
        ]
        self._ensure_non_negative(planned)

        movements: list[StockMovement] = []
        for request, level in planned:
            quantity_before, cost_before = level.quantity, level.average_cost
            quantity_after = quantity_before + request.quantity
            unit_cost = request.unit_cost
            if request.movement_type is MovementType.ENTRY:
                if request.quantity <= 0 or unit_cost is None:
                    raise ValueError("une ENTRÉE exige une quantité positive et un coût")
                level.average_cost = compute_average_cost(
                    quantity_before, cost_before, request.quantity, unit_cost
                )
            elif unit_cost is None:
                # Sortie (ou annulation d'une sortie sans coût fourni) : CMUP courant du site.
                unit_cost = cost_before
            level.quantity = quantity_after
            movement = StockMovement(
                tenant_id=self.tenant_id,
                site_id=site_id,
                article_id=request.article_id,
                movement_type=request.movement_type,
                quantity=request.quantity,
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                unit_cost=unit_cost,
                average_cost_before=cost_before,
                average_cost_after=level.average_cost,
                source_type=request.source_type,
                source_id=request.source_id,
                source_line_id=request.source_line_id,
                source_number=request.source_number,
                origin_movement_id=request.origin_movement_id,
                user_id=self.user_id,
                comment=request.comment,
                occurred_at=self.now,
            )
            self.db.add(movement)
            movements.append(movement)
        self.db.flush()
        return movements

    def movements_of(
        self, source_id: uuid.UUID, movement_type: MovementType
    ) -> dict[uuid.UUID, MovementRef]:
        """Mouvements d'un type donné d'un document source, par ligne source (lecture seule)."""
        rows = self.db.scalars(
            select(StockMovement).where(
                StockMovement.source_id == source_id,
                StockMovement.movement_type == movement_type,
            )
        )
        return {m.source_line_id: MovementRef(id=m.id, unit_cost=m.unit_cost) for m in rows}

    def _ensure_non_negative(self, planned: list[tuple[MovementRequest, StockLevel]]) -> None:
        """Contrôle global avant toute écriture : refus de l'opération entière (STK-03)."""
        projected: dict[uuid.UUID, Decimal] = {}
        shortages: list[uuid.UUID] = []
        for request, level in planned:
            current = projected.get(request.article_id, level.quantity)
            projected[request.article_id] = current + request.quantity
            if projected[request.article_id] < 0 and request.article_id not in shortages:
                shortages.append(request.article_id)
        if not shortages:
            return
        refs = get_article_refs(self.db, set(shortages))
        levels = {level.article_id: level for _, level in planned}
        details = [
            {
                "article_id": str(article_id),
                "reference": refs[article_id].reference if article_id in refs else None,
                "available": format(levels[article_id].quantity, "f"),
            }
            for article_id in shortages
        ]
        raise BusinessRuleError(
            "Stock insuffisant : le stock deviendrait négatif",
            code="insufficient_stock",
            extra={"articles": details},
        )
