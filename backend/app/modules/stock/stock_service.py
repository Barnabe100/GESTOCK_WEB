"""Moteur central du stock (STK-01 à STK-09) : SEUL code autorisé à modifier la quantité et le
CMUP d'un niveau de stock.

- S'exécute dans la transaction de l'appelant (jamais de commit ici) : document + mouvements +
  niveaux réussissent ou échouent ensemble.
- Verrouille les niveaux concernés (``SELECT … FOR UPDATE``) dans un ordre global
  (site, article) : deux opérations simultanées sur un même niveau s'exécutent l'une après
  l'autre, sans interblocage, y compris lorsqu'elles touchent plusieurs sites (transferts).
- Refuse tout stock négatif avant la moindre écriture (et la base le refuse aussi).
- Recalcule le CMUP uniquement sur une ENTRÉE (achat, stock initial, transfert entrant), avec
  4 décimales (Q6).
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.api import get_article_refs
from app.modules.stock.models import MovementType, StockLevel, StockMovement

Key = tuple[uuid.UUID, uuid.UUID]  # (site, article)

# Mouvements porteurs d'un coût d'acquisition : seuls à recalculer le CMUP du site (STK-05).
# Un transfert entrant est une entrée pour le site destination, au coût du site source.
COST_ENTRY_TYPES = frozenset({MovementType.ENTRY, MovementType.TRANSFER_IN})

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
class TransferItem:
    """Ligne d'un transfert inter-sites : article, quantité (> 0) et ligne du document source."""

    line_id: uuid.UUID
    article_id: uuid.UUID
    quantity: Decimal


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
        locked = self._lock({(site_id, article_id) for article_id in article_ids})
        return {article_id: level for (_, article_id), level in locked.items()}

    def _lock(self, keys: set[tuple[uuid.UUID, uuid.UUID]]) -> dict[Key, StockLevel]:
        """Crée au besoin puis verrouille les niveaux (site, article) dans UN ordre global
        (site, article) : deux opérations touchant les mêmes niveaux, même sur plusieurs sites
        (transferts A → B et B → A), les verrouillent dans le même ordre, sans interblocage."""
        ordered = sorted(keys)
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
                    for site_id, article_id in ordered
                ]
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "article_id"])
        )
        levels = self.db.scalars(
            select(StockLevel)
            .where(tuple_(StockLevel.site_id, StockLevel.article_id).in_(ordered))
            .order_by(StockLevel.site_id, StockLevel.article_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        return {(level.site_id, level.article_id): level for level in levels}

    def apply(self, site_id: uuid.UUID, requests: list[MovementRequest]) -> list[StockMovement]:
        """Applique les mouvements d'un site (tout ou rien) et renvoie les mouvements créés, dans
        l'ordre des demandes. Pour une SORTIE, ``unit_cost`` du mouvement = CMUP du site."""
        return self.apply_many([(site_id, request) for request in requests])

    def apply_many(self, requests: list[tuple[uuid.UUID, MovementRequest]]) -> list[StockMovement]:
        """Applique des mouvements sur un ou plusieurs sites, tout ou rien : verrouillage de tous
        les niveaux concernés, contrôle global du stock, puis écritures."""
        levels = self._lock({(site_id, r.article_id) for site_id, r in requests})
        return self._write(
            [(site_id, r, levels[(site_id, r.article_id)]) for site_id, r in requests]
        )

    def transfer(
        self,
        source_site_id: uuid.UUID,
        destination_site_id: uuid.UUID,
        items: list[TransferItem],
        *,
        source_type: str,
        source_id: uuid.UUID,
        source_number: str,
    ) -> list[tuple[StockMovement, StockMovement]]:
        """Transfert inter-sites dans la transaction de l'appelant : par article, une SORTIE
        ``TRANSFER_OUT`` au CMUP du site source (inchangé) et une ENTRÉE ``TRANSFER_IN`` du même
        coût sur le site destination (CMUP destination recalculé, STK-05). Les niveaux des deux
        sites sont verrouillés ensemble et tout le stock source contrôlé avant la moindre
        écriture : aucune sortie sans son entrée, aucun état intermédiaire visible."""
        if source_site_id == destination_site_id:
            raise ValueError("un transfert exige deux sites distincts")
        levels = self._lock(
            {
                (site, item.article_id)
                for item in items
                for site in (source_site_id, destination_site_id)
            }
        )
        planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]] = []
        for item in items:
            cost = levels[(source_site_id, item.article_id)].average_cost
            outgoing = MovementRequest(
                article_id=item.article_id,
                movement_type=MovementType.TRANSFER_OUT,
                quantity=-item.quantity,
                unit_cost=cost,
                source_type=source_type,
                source_id=source_id,
                source_line_id=item.line_id,
                source_number=source_number,
                comment=source_number,
            )
            incoming = replace(
                outgoing, movement_type=MovementType.TRANSFER_IN, quantity=item.quantity
            )
            planned.append((source_site_id, outgoing, levels[(source_site_id, item.article_id)]))
            planned.append(
                (destination_site_id, incoming, levels[(destination_site_id, item.article_id)])
            )
        movements = self._write(planned)
        return [(movements[i], movements[i + 1]) for i in range(0, len(movements), 2)]

    def _write(
        self, planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]]
    ) -> list[StockMovement]:
        self._ensure_non_negative(planned)
        movements: list[StockMovement] = []
        for site_id, request, level in planned:
            quantity_before, cost_before = level.quantity, level.average_cost
            quantity_after = quantity_before + request.quantity
            unit_cost = request.unit_cost
            if request.movement_type in COST_ENTRY_TYPES:
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

    def _ensure_non_negative(
        self, planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]]
    ) -> None:
        """Contrôle global avant toute écriture, par (site, article) : refus de l'opération
        entière (STK-03)."""
        projected: dict[Key, Decimal] = {}
        shortages: list[Key] = []
        levels: dict[Key, StockLevel] = {}
        for site_id, request, level in planned:
            key = (site_id, request.article_id)
            levels[key] = level
            current = projected.get(key, level.quantity)
            projected[key] = current + request.quantity
            if projected[key] < 0 and key not in shortages:
                shortages.append(key)
        if not shortages:
            return
        refs = get_article_refs(self.db, {article_id for _, article_id in shortages})
        details = [
            {
                "article_id": str(article_id),
                "site_id": str(site_id),
                "reference": refs[article_id].reference if article_id in refs else None,
                "available": format(levels[(site_id, article_id)].quantity, "f"),
            }
            for site_id, article_id in shortages
        ]
        raise BusinessRuleError(
            "Stock insuffisant : le stock deviendrait négatif",
            code="insufficient_stock",
            extra={"articles": details},
        )
