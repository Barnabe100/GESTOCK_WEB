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
- Lot 3-G (ADR-0045) : un mouvement portant un lot (``lot_id`` : réception, annulation de
  réception) fait varier du même montant le solde du lot sur le site — ventilation du stock
  (site, article), verrouillée après les niveaux dans l'ordre global (site, article, lot),
  jamais négative. Le CMUP reste celui du site (aucun coût par lot, C1).
- Lot 3-H-A (ADR-0045, H-D1 à H-D18, O-1 à O-6) : ``consume`` est le moteur UNIQUE de
  consommation des lots (ventes, POS, sorties) — FEFO / FIFO automatique ou répartition
  manuelle, un mouvement par lot (M1), tout ou rien. Garde-fou serveur (H-D14, O-6) : tout
  mouvement d'un article suivi par lot porte un lot ; aucun mouvement d'un article non suivi
  n'en porte.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.api import (
    QUANTITY_STEP,
    ArticleRef,
    LotFlags,
    get_article_refs,
    is_whole,
    lock_lot_flags,
    lock_stock_managed,
)
from app.modules.stock.models import (
    MovementType,
    StockLevel,
    StockLot,
    StockLotLevel,
    StockMovement,
)
from app.shared.ids import new_id

Key = tuple[uuid.UUID, uuid.UUID]  # (site, article)
LotKey = tuple[uuid.UUID, uuid.UUID, uuid.UUID]  # (site, article, lot)

# Mouvements porteurs d'un coût d'acquisition : seuls à recalculer le CMUP du site (STK-05).
# Un transfert entrant est une entrée pour le site destination, au coût du site source.
COST_ENTRY_TYPES = frozenset({MovementType.ENTRY, MovementType.TRANSFER_IN})

COST_PRECISION = Decimal("0.0001")
MONEY_PRECISION = Decimal("0.01")


def round_cost(value: Decimal) -> Decimal:
    return value.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


def round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


def cost_per_base(unit_cost: Decimal, conversion: Decimal | None) -> Decimal:
    """Coût d'une UNITÉ DE BASE à partir d'un coût par présentation (Lot 3-C) : 12 000 le
    carton de 24 → 500 ; 4 décimales comme le CMUP (Q6). Unité de base : coût inchangé."""
    return unit_cost if conversion is None else round_cost(unit_cost / conversion)


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
class PackagingSnapshot:
    """Présentation saisie d'une opération (Lot 3-C, ADR-0041) : ``quantity`` conditionnements
    de ``conversion`` unités de base — conservée sur le mouvement pour l'historique
    (« 3 Carton 24 » pour −72)."""

    packaging_id: uuid.UUID
    name: str
    conversion: Decimal
    quantity: Decimal


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
    # Lot 3-C : présentation saisie ; ``quantity`` reste TOUJOURS en unité de base.
    packaging: PackagingSnapshot | None = None
    # Lot 3-G : lot de l'article (même article garanti par FK composite) ; son solde sur le site
    # varie de ``quantity``.
    lot_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TransferItem:
    """Ligne d'un transfert inter-sites : article, quantité en unité de base (> 0), ligne du
    document source et présentation saisie (Lot 3-C)."""

    line_id: uuid.UUID
    article_id: uuid.UUID
    quantity: Decimal
    packaging: PackagingSnapshot | None = None


@dataclass(frozen=True)
class MovementRef:
    """Mouvement d'origine d'une ligne (pour une annulation par mouvement inverse). Lot 3-H
    (M1) : une ligne peut avoir PLUSIEURS mouvements (un par lot) — chacun est inversé à
    l'identique (même quantité, même coût, même lot, même présentation)."""

    id: uuid.UUID
    unit_cost: Decimal | None
    quantity: Decimal = Decimal("0")
    lot_id: uuid.UUID | None = None
    packaging: "PackagingSnapshot | None" = None


@dataclass(frozen=True)
class LotPick:
    """Quantité (unité de base, > 0) prise sur un lot désigné explicitement : choix manuel
    d'une sortie (H-D1), ou lot périmé désigné par une dérogation de vente (O-1)."""

    lot_id: uuid.UUID
    quantity: Decimal


@dataclass(frozen=True)
class ConsumptionRequest:
    """Sortie de stock d'une ligne de document (quantité POSITIVE, en unité de base).

    - Article non suivi par lot : un mouvement, comme avant le Lot 3-H (``picks`` interdit).
    - Article suivi, ``manual=False`` (ventes, POS) : les ``picks`` éventuels (dérogation
      autorisée par l'appelant) d'abord, puis FEFO / FIFO automatique sur les lots non périmés.
    - Article suivi, ``manual=True`` (sorties) : les ``picks`` seuls, dont la somme doit égaler
      exactement la quantité (O-4).
    ``packaging`` : présentation de la ligne ; un mouvement réparti ne la porte que si sa
    quantité y est exactement représentable (O-3)."""

    article_id: uuid.UUID
    movement_type: MovementType
    quantity: Decimal
    source_type: str
    source_id: uuid.UUID
    source_line_id: uuid.UUID
    source_number: str | None = None
    comment: str | None = None
    packaging: "PackagingSnapshot | None" = None
    picks: tuple[LotPick, ...] = ()
    manual: bool = False


@dataclass
class _LotCandidate:
    level: StockLotLevel
    lot: StockLot
    remaining: Decimal = field(default=Decimal("0"))


def _packaging_columns(packaging: PackagingSnapshot | None) -> dict[str, object]:
    if packaging is None:
        return {}
    return {
        "packaging_id": packaging.packaging_id,
        "packaging_name": packaging.name,
        "packaging_conversion": packaging.conversion,
        "packaging_quantity": packaging.quantity,
    }


def split_packaging(
    packaging: PackagingSnapshot | None, quantity: Decimal, decimal_allowed: bool
) -> PackagingSnapshot | None:
    """Présentation d'un mouvement réparti (O-3, H-D16) : conservée seulement si ``quantity``
    (unité de base, > 0) est EXACTEMENT ``n`` conditionnements, ``n`` à 3 décimales au plus et
    entier pour un article en quantités entières (3-B / 3-C). Sinon aucune présentation : le
    mouvement garde sa seule quantité de base (jamais de faux arrondi) ; la ligne du document
    conserve la présentation saisie. Ex. carton de 24 : 48 → 2 cartons ; 30 → aucune."""
    if packaging is None:
        return None
    if quantity == packaging.quantity * packaging.conversion:
        return packaging
    count = (quantity / packaging.conversion).quantize(QUANTITY_STEP)
    if count <= 0 or count * packaging.conversion != quantity:
        return None
    if not decimal_allowed and not is_whole(count):
        return None
    return replace(packaging, quantity=count)


def fefo_key(lot: StockLot, expiry_tracked: bool) -> tuple[object, ...]:
    """Ordre de consommation automatique, déterministe : FEFO (H-D3 : péremption croissante,
    lots sans date après les lots datés) puis date de création du lot et numéro ; FIFO pour un
    article sans suivi de péremption (H-D4 : création puis numéro)."""
    created = (lot.created_at, lot.number.lower(), lot.number, lot.id)
    if not expiry_tracked:
        return created
    return (lot.expiry_date is None, lot.expiry_date or date.max, *created)


def is_expired(lot: StockLot, flags: LotFlags, today: date) -> bool:
    """Lot périmé (H-D5) : article suivi en péremption et date antérieure à aujourd'hui
    (fuseau du tenant) — même règle que l'état de péremption affiché (3-G, D17)."""
    return flags.expiry_tracked and lot.expiry_date is not None and lot.expiry_date < today


def refuse_unmanaged(db: Session, article_ids: list[uuid.UUID]) -> None:
    """Refus explicite d'une opération de stock sur des articles non gérés en stock."""
    if not article_ids:
        return
    refs = get_article_refs(db, set(article_ids))
    raise BusinessRuleError(
        "Article non géré en stock : aucune opération de stock possible",
        code="article_not_stock_managed",
        extra={"articles": sorted(refs[a].reference for a in article_ids if a in refs)},
    )


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
        self._ensure_stock_managed({article_id for _, article_id in ordered})
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

    def _ensure_stock_managed(self, article_ids: set[uuid.UUID]) -> None:
        """Garde centrale (Lot 3-A, ADR-0039) : aucun niveau ni mouvement pour un article non
        géré en stock. Lecture sous verrou partagé de l'article (``lock_stock_managed``) : le
        passage « géré » → « non géré » ne peut pas s'intercaler avant la fin de l'opération."""
        flags = lock_stock_managed(self.db, article_ids)
        refuse_unmanaged(self.db, [a for a in sorted(article_ids) if not flags.get(a, True)])

    def apply(self, site_id: uuid.UUID, requests: list[MovementRequest]) -> list[StockMovement]:
        """Applique les mouvements d'un site (tout ou rien) et renvoie les mouvements créés, dans
        l'ordre des demandes. Pour une SORTIE, ``unit_cost`` du mouvement = CMUP du site."""
        return self.apply_many([(site_id, request) for request in requests])

    def apply_many(self, requests: list[tuple[uuid.UUID, MovementRequest]]) -> list[StockMovement]:
        """Applique des mouvements sur un ou plusieurs sites, tout ou rien : verrouillage de tous
        les niveaux concernés, contrôle global du stock, puis écritures."""
        levels = self._lock({(site_id, r.article_id) for site_id, r in requests})
        requests = self._restore_without_lot(requests)
        lots = self._lock_lots(
            {(site_id, r.article_id, r.lot_id) for site_id, r in requests if r.lot_id is not None}
        )
        return self._write(
            [(site_id, r, levels[(site_id, r.article_id)]) for site_id, r in requests], lots
        )

    def _restore_without_lot(
        self, requests: list[tuple[uuid.UUID, MovementRequest]]
    ) -> list[tuple[uuid.UUID, MovementRequest]]:
        """Annulation d'un mouvement par lot d'un article qui n'est PLUS suivi (suivi retiré à
        stock nul sur tous les sites, 3-G D7) : remise en stock sans lot — les soldes de ses
        lots sont restés nuls, l'article n'en tient plus."""
        cancelled = {
            r.article_id
            for _, r in requests
            if r.movement_type is MovementType.CANCELLATION and r.lot_id is not None
        }
        if not cancelled:
            return requests
        flags = lock_lot_flags(self.db, cancelled)
        return [
            (
                site_id,
                replace(r, lot_id=None)
                if r.article_id in cancelled
                and r.movement_type is MovementType.CANCELLATION
                and not flags[r.article_id].lot_tracked
                else r,
            )
            for site_id, r in requests
        ]

    def _lock_lots(self, keys: set[LotKey]) -> dict[LotKey, StockLotLevel]:
        """Soldes des lots (site, article, lot), créés au besoin puis verrouillés APRÈS les
        niveaux, dans l'ordre global (site, article, lot) : sans interblocage."""
        ordered = sorted(keys)
        if not ordered:
            return {}
        self.db.execute(
            insert(StockLotLevel)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": self.tenant_id,
                        "site_id": site_id,
                        "article_id": article_id,
                        "lot_id": lot_id,
                        "quantity": Decimal("0"),
                    }
                    for site_id, article_id, lot_id in ordered
                ]
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "lot_id"])
        )
        rows = self.db.scalars(
            select(StockLotLevel)
            .where(
                tuple_(StockLotLevel.site_id, StockLotLevel.lot_id).in_(
                    [(site_id, lot_id) for site_id, _, lot_id in ordered]
                )
            )
            .order_by(StockLotLevel.site_id, StockLotLevel.article_id, StockLotLevel.lot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        return {(r.site_id, r.article_id, r.lot_id): r for r in rows}

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
                packaging=item.packaging,
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
        self,
        planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]],
        lots: dict[LotKey, StockLotLevel] | None = None,
    ) -> list[StockMovement]:
        lots = lots or {}
        self._ensure_lot_guard(planned)
        self._ensure_non_negative(planned)
        self._ensure_lots_non_negative(planned, lots)
        movements: list[StockMovement] = []
        previous_id: uuid.UUID | None = None
        for site_id, request, level in planned:
            # Identifiants strictement croissants dans une même écriture : l'ordre du journal
            # (``occurred_at``, ``id``) est celui des mouvements (lots d'une ligne, M1).
            movement_id = new_id()
            if previous_id is not None and movement_id <= previous_id:
                movement_id = uuid.UUID(int=previous_id.int + 1)
            previous_id = movement_id
            if request.lot_id is not None:
                lot_level = lots[(site_id, request.article_id, request.lot_id)]
                lot_level.quantity = lot_level.quantity + request.quantity
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
                id=movement_id,
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
                lot_id=request.lot_id,
                **_packaging_columns(request.packaging),
            )
            self.db.add(movement)
            movements.append(movement)
        self.db.flush()
        return movements

    def movements_of(
        self, source_id: uuid.UUID, movement_type: MovementType
    ) -> dict[uuid.UUID, list[MovementRef]]:
        """Mouvements d'un type donné d'un document source, par ligne source (lecture seule) —
        plusieurs par ligne pour une ligne répartie sur plusieurs lots (M1), dans l'ordre de
        création."""
        rows = self.db.scalars(
            select(StockMovement)
            .where(
                StockMovement.source_id == source_id,
                StockMovement.movement_type == movement_type,
            )
            .order_by(StockMovement.occurred_at, StockMovement.id)
        )
        result: dict[uuid.UUID, list[MovementRef]] = {}
        for m in rows:
            result.setdefault(m.source_line_id, []).append(movement_ref(m))
        return result

    # --- Consommation des lots (Lot 3-H-A) -------------------------------------------------------

    def consume(
        self, site_id: uuid.UUID, requests: Sequence[ConsumptionRequest], *, today: date
    ) -> list[StockMovement]:
        """Sorties de stock d'un site (ventes, POS, sorties), tout ou rien, dans la transaction
        de l'appelant — SEUL moteur de consommation des lots :

        1. verrou partagé des articles (géré en stock, suivi par lot) puis verrou des niveaux
           (site, article) ;
        2. contrôle global du stock (``insufficient_stock``) ;
        3. verrou des soldes de lots des articles suivis, ordre global (site, article, lot) ;
        4. répartition : lots désignés (``picks``) puis, en automatique, FEFO / FIFO sur les
           lots NON périmés — jamais un lot périmé automatiquement (H-D3, O-1) ; stock non
           périmé insuffisant : ``insufficient_unexpired_stock`` ;
        5. un mouvement par lot (M1), niveaux et soldes de lots mis à jour ensemble.

        Deux ventes simultanées du même article s'exécutent l'une après l'autre (verrou du
        niveau) : la seconde voit les soldes laissés par la première, aucune double
        consommation. ``today`` : date du jour du tenant (péremption)."""
        if not requests:
            return []
        article_ids = {r.article_id for r in requests}
        levels = self.lock_levels(site_id, article_ids)
        flags = lock_lot_flags(self.db, article_ids)
        refs = get_article_refs(self.db, article_ids)
        for r in requests:
            if r.quantity <= 0:
                raise ValueError("une consommation exige une quantité positive")
            if r.picks and not flags[r.article_id].lot_tracked:
                raise BusinessRuleError(
                    "Cet article n'est pas suivi par lot",
                    code="article_not_lot_tracked",
                    extra={"articles": [refs[r.article_id].reference]},
                )
        # Stock total insuffisant (lots périmés compris) : même refus qu'avant le Lot 3-H.
        self._ensure_non_negative([(site_id, _plain(r), levels[r.article_id]) for r in requests])
        tracked = {a for a in article_ids if flags[a].lot_tracked}
        candidates = self._lock_article_lots(site_id, tracked)
        planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]] = []
        unexpired_shortages: dict[uuid.UUID, Decimal] = {}
        for r in requests:
            ref = refs[r.article_id]
            if r.article_id not in tracked:
                planned.append((site_id, _plain(r), levels[r.article_id]))
                continue
            allocations, missing = self._allocate(
                r, ref, flags[r.article_id], candidates.get(r.article_id, {}), today
            )
            if missing > 0:
                unexpired_shortages[r.article_id] = (
                    unexpired_shortages.get(r.article_id, Decimal("0")) + missing
                )
                continue
            for lot_id, quantity in allocations:
                planned.append(
                    (
                        site_id,
                        MovementRequest(
                            article_id=r.article_id,
                            movement_type=r.movement_type,
                            quantity=-quantity,
                            source_type=r.source_type,
                            source_id=r.source_id,
                            source_line_id=r.source_line_id,
                            source_number=r.source_number,
                            comment=r.comment,
                            packaging=split_packaging(
                                r.packaging, quantity, ref.decimal_quantity_allowed
                            ),
                            lot_id=lot_id,
                        ),
                        levels[r.article_id],
                    )
                )
        if unexpired_shortages:
            self._refuse_unexpired(unexpired_shortages, refs, flags, candidates, today)
        lot_levels = {
            (site_id, article_id, lot_id): candidate.level
            for article_id, by_lot in candidates.items()
            for lot_id, candidate in by_lot.items()
        }
        return self._write(planned, lot_levels)

    def _lock_article_lots(
        self, site_id: uuid.UUID, article_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, dict[uuid.UUID, _LotCandidate]]:
        """Soldes des lots des articles sur le site, verrouillés (``FOR UPDATE``) APRÈS les
        niveaux, dans l'ordre global (site, article, lot)."""
        if not article_ids:
            return {}
        rows = self.db.execute(
            select(StockLotLevel, StockLot)
            .join(
                StockLot,
                (StockLot.id == StockLotLevel.lot_id)
                & (StockLot.tenant_id == StockLotLevel.tenant_id),
            )
            .where(StockLotLevel.site_id == site_id, StockLotLevel.article_id.in_(article_ids))
            .order_by(StockLotLevel.site_id, StockLotLevel.article_id, StockLotLevel.lot_id)
            .with_for_update(of=StockLotLevel)
            .execution_options(populate_existing=True)
        ).all()
        result: dict[uuid.UUID, dict[uuid.UUID, _LotCandidate]] = {}
        for level, lot in rows:
            result.setdefault(level.article_id, {})[lot.id] = _LotCandidate(
                level=level, lot=lot, remaining=level.quantity
            )
        return result

    def _allocate(
        self,
        request: ConsumptionRequest,
        ref: ArticleRef,
        flags: LotFlags,
        candidates: dict[uuid.UUID, _LotCandidate],
        today: date,
    ) -> tuple[list[tuple[uuid.UUID, Decimal]], Decimal]:
        """Répartition d'une ligne sur les lots (soldes de travail décrémentés au fil des lignes
        d'une même opération). Renvoie les allocations (lot, quantité) et la quantité restée
        sans lot non périmé (automatique seulement)."""
        allocations: list[tuple[uuid.UUID, Decimal]] = []
        seen: set[uuid.UUID] = set()
        picked = Decimal("0")
        for pick in request.picks:
            candidate = candidates.get(pick.lot_id)
            if candidate is None:
                # Lot d'un autre article, d'un autre tenant (RLS) ou sans solde sur ce site.
                raise BusinessRuleError(
                    "Ce lot n'est pas disponible pour cet article sur ce site",
                    code="lot_not_available",
                    extra={"articles": [ref.reference], "lot_id": str(pick.lot_id)},
                )
            if pick.lot_id in seen:
                raise BusinessRuleError(
                    "Un même lot est choisi plusieurs fois sur la ligne",
                    code="duplicate_lot_allocation",
                    extra={"articles": [ref.reference], "lots": [candidate.lot.number]},
                )
            seen.add(pick.lot_id)
            if pick.quantity <= 0:
                raise ValueError("une allocation exige une quantité positive")
            if pick.quantity > candidate.remaining:
                raise BusinessRuleError(
                    "Solde de lot insuffisant : le solde du lot deviendrait négatif",
                    code="insufficient_lot_stock",
                    extra={
                        "lots": [
                            {
                                "article_id": str(ref.id),
                                "lot_id": str(pick.lot_id),
                                "reference": ref.reference,
                                "lot_number": candidate.lot.number,
                                "available": format(candidate.remaining, "f"),
                            }
                        ]
                    },
                )
            candidate.remaining -= pick.quantity
            picked += pick.quantity
            allocations.append((pick.lot_id, pick.quantity))
        rest = request.quantity - picked
        if request.manual or rest < 0:
            if rest != 0:
                raise BusinessRuleError(
                    "La répartition par lot ne correspond pas à la quantité de la ligne",
                    code="lot_allocation_incomplete",
                    extra={
                        "articles": [ref.reference],
                        "requested": format(request.quantity, "f"),
                        "allocated": format(picked, "f"),
                    },
                )
            return allocations, Decimal("0")
        ordered = sorted(
            (
                c
                for c in candidates.values()
                if c.remaining > 0 and c.lot.id not in seen and not is_expired(c.lot, flags, today)
            ),
            key=lambda c: fefo_key(c.lot, flags.expiry_tracked),
        )
        for candidate in ordered:
            if rest == 0:
                break
            taken = min(rest, candidate.remaining)
            candidate.remaining -= taken
            rest -= taken
            allocations.append((candidate.lot.id, taken))
        return allocations, rest

    def _refuse_unexpired(
        self,
        shortages: dict[uuid.UUID, Decimal],
        refs: dict[uuid.UUID, ArticleRef],
        flags: dict[uuid.UUID, LotFlags],
        candidates: dict[uuid.UUID, dict[uuid.UUID, _LotCandidate]],
        today: date,
    ) -> None:
        """O-1 : le stock du site suffit, mais pas ses lots NON périmés — refus par défaut,
        jamais de bascule automatique vers un lot périmé. Le détail permet à l'interface de
        proposer la dérogation explicite (permission, lot, motif)."""
        details = []
        for article_id, missing in shortages.items():
            lots = candidates.get(article_id, {}).values()
            expired = [
                c
                for c in lots
                if c.level.quantity > 0 and is_expired(c.lot, flags[article_id], today)
            ]
            details.append(
                {
                    "article_id": str(article_id),
                    "reference": refs[article_id].reference,
                    "missing": format(missing, "f"),
                    "expired_available": format(
                        sum((c.level.quantity for c in expired), Decimal("0")), "f"
                    ),
                    "expired_lots": [
                        {
                            "lot_id": str(c.lot.id),
                            "lot_number": c.lot.number,
                            "expiry_date": c.lot.expiry_date.isoformat()
                            if c.lot.expiry_date
                            else None,
                            "available": format(c.level.quantity, "f"),
                        }
                        for c in sorted(expired, key=lambda c: fefo_key(c.lot, True))
                    ],
                }
            )
        raise BusinessRuleError(
            "Stock non périmé insuffisant : un lot périmé ne peut pas être vendu "
            "sans dérogation explicite",
            code="insufficient_unexpired_stock",
            extra={"articles": details},
        )

    def _ensure_lot_guard(
        self, planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]]
    ) -> None:
        """Garde-fou serveur (H-D14, O-6), pour TOUT mouvement : un article suivi par lot exige
        un lot (sinon ``lot_required``) ; un article non suivi n'en porte aucun. Réglages lus
        sous verrou partagé de l'article : un changement concurrent attend la fin de
        l'opération."""
        article_ids = {request.article_id for _, request, _ in planned}
        flags = lock_lot_flags(self.db, article_ids)
        missing = sorted(
            {
                r.article_id
                for _, r, _ in planned
                if r.lot_id is None and flags.get(r.article_id, _UNTRACKED).lot_tracked
            }
        )
        unexpected = sorted(
            {
                r.article_id
                for _, r, _ in planned
                if r.lot_id is not None and not flags.get(r.article_id, _UNTRACKED).lot_tracked
            }
        )
        if not missing and not unexpected:
            return
        refs = get_article_refs(self.db, set(missing) | set(unexpected))
        if missing:
            raise BusinessRuleError(
                "Article suivi par lot : tout mouvement de stock doit porter un lot",
                code="lot_required",
                extra={"articles": [refs[a].reference for a in missing if a in refs]},
            )
        raise BusinessRuleError(
            "Cet article n'est pas suivi par lot",
            code="article_not_lot_tracked",
            extra={"articles": [refs[a].reference for a in unexpected if a in refs]},
        )

    def _ensure_lots_non_negative(
        self,
        planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]],
        lots: dict[LotKey, StockLotLevel],
    ) -> None:
        """Lot 3-G (D10) : aucun solde de lot négatif, contrôlé avant toute écriture — refus de
        l'opération entière (ex. annulation d'une réception dont le lot a déjà été consommé)."""
        projected: dict[LotKey, Decimal] = {}
        shortages: list[LotKey] = []
        for site_id, request, _ in planned:
            if request.lot_id is None:
                continue
            key = (site_id, request.article_id, request.lot_id)
            projected[key] = projected.get(key, lots[key].quantity) + request.quantity
            if projected[key] < 0 and key not in shortages:
                shortages.append(key)
        if not shortages:
            return
        refs = get_article_refs(self.db, {article_id for _, article_id, _ in shortages})
        numbers = {
            row[0]: row[1]
            for row in self.db.execute(
                select(StockLot.id, StockLot.number).where(
                    StockLot.id.in_({lot_id for _, _, lot_id in shortages})
                )
            )
        }
        raise BusinessRuleError(
            "Solde de lot insuffisant : le solde du lot deviendrait négatif",
            code="insufficient_lot_stock",
            extra={
                "lots": [
                    {
                        "article_id": str(article_id),
                        "site_id": str(site_id),
                        "lot_id": str(lot_id),
                        "reference": refs[article_id].reference if article_id in refs else None,
                        "lot_number": numbers.get(lot_id),
                        "available": format(lots[(site_id, article_id, lot_id)].quantity, "f"),
                    }
                    for site_id, article_id, lot_id in shortages
                ]
            },
        )

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


_UNTRACKED = LotFlags(lot_tracked=False, expiry_tracked=False)


def _plain(request: ConsumptionRequest) -> MovementRequest:
    """Mouvement unique d'une ligne (article non suivi par lot) — comportement d'avant 3-H."""
    return MovementRequest(
        article_id=request.article_id,
        movement_type=request.movement_type,
        quantity=-request.quantity,
        source_type=request.source_type,
        source_id=request.source_id,
        source_line_id=request.source_line_id,
        source_number=request.source_number,
        comment=request.comment,
        packaging=request.packaging,
    )


def movement_ref(movement: StockMovement) -> MovementRef:
    """Référence d'un mouvement d'origine, avec sa présentation propre (pour son inverse)."""
    packaging = (
        PackagingSnapshot(
            packaging_id=movement.packaging_id,
            name=movement.packaging_name or "",
            conversion=movement.packaging_conversion or Decimal("1"),
            quantity=movement.packaging_quantity or Decimal("0"),
        )
        if movement.packaging_id is not None
        else None
    )
    return MovementRef(
        id=movement.id,
        unit_cost=movement.unit_cost,
        quantity=movement.quantity,
        lot_id=movement.lot_id,
        packaging=packaging,
    )


def inverse_packaging(
    origin: MovementRef, line_packaging: PackagingSnapshot | None, single: bool
) -> PackagingSnapshot | None:
    """Présentation de l'inverse d'un mouvement : la sienne ; pour un mouvement unique sans
    présentation enregistrée (historique antérieur au 3-C), celle de la ligne (comportement
    inchangé)."""
    if origin.packaging is not None:
        return origin.packaging
    return line_packaging if single else None
