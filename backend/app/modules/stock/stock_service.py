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
- Lot 3-H-B1 : ``transfer_lots`` est le moteur UNIQUE des transferts d'articles suivis par
  lot — une paire ``TRANSFER_OUT`` / ``TRANSFER_IN`` par lot, sous le MÊME lot des deux côtés,
  au CMUP source lu une fois ; CMUP destination calculé une seule fois par ligne (identique à un
  transfert non réparti, T-3).
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.api import (
    QUANTITY_STEP,
    ArticleRef,
    LotFlags,
    ensure_in_assortment,
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
    # Lot 3-H-B1 : répartition MANUELLE d'un article suivi par lot (somme exacte exigée).
    picks: tuple["LotPick", ...] = ()


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

    def _lock(
        self,
        keys: set[tuple[uuid.UUID, uuid.UUID]],
        assorted: set[tuple[uuid.UUID, uuid.UUID]] | None = None,
    ) -> dict[Key, StockLevel]:
        """Crée au besoin puis verrouille les niveaux (site, article) dans UN ordre global
        (site, article) : deux opérations touchant les mêmes niveaux, même sur plusieurs sites
        (transferts A → B et B → A), les verrouillent dans le même ordre, sans interblocage.

        ``assorted`` : couples (site, article) devant figurer dans l'assortiment ACTIF du site
        (ADR-0046) — par défaut tous ; une annulation n'en exige aucun (D3-7)."""
        ordered = sorted(keys)
        if not ordered:
            return {}
        self._ensure_stock_managed({article_id for _, article_id in ordered})
        self._ensure_in_assortment(set(ordered) if assorted is None else assorted)
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

    def _ensure_in_assortment(self, keys: set[tuple[uuid.UUID, uuid.UUID]]) -> None:
        """Garde centrale de l'assortiment (Recette, étape 1, ADR-0046, D3) : tout mouvement sur
        (site, article) — réception, sortie, vente, transfert (source ET destination),
        ajustement d'inventaire — exige l'article ACTIF dans l'assortiment du site, sinon
        ``422 article_not_in_site_assortment`` ; aucun ajout automatique. Lignes d'assortiment
        lues sous verrou PARTAGÉ, après les articles et avant les niveaux (ordre global article
        → assortiment → niveaux → lots, D4) : un retrait concurrent attend la fin de
        l'opération, ou l'opération voit le retrait. Les annulations ne passent pas ici."""
        by_site: dict[uuid.UUID, set[uuid.UUID]] = {}
        for site_id, article_id in keys:
            by_site.setdefault(site_id, set()).add(article_id)
        for site_id in sorted(by_site):
            ensure_in_assortment(self.db, site_id, by_site[site_id], lock=True)

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
        levels = self._lock(
            {(site_id, r.article_id) for site_id, r in requests},
            # Une annulation restaure l'historique même hors assortiment (D3-7, D4).
            {
                (site_id, r.article_id)
                for site_id, r in requests
                if r.movement_type is not MovementType.CANCELLATION
            },
        )
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
            planned.extend(
                _transfer_pair(
                    source_site_id,
                    destination_site_id,
                    item,
                    levels,
                    item.quantity,
                    None,
                    item.packaging,
                    source_type=source_type,
                    source_id=source_id,
                    source_number=source_number,
                )
            )
        movements = self._write(planned)
        return [(movements[i], movements[i + 1]) for i in range(0, len(movements), 2)]

    def transfer_lots(
        self,
        source_site_id: uuid.UUID,
        destination_site_id: uuid.UUID,
        items: Sequence[TransferItem],
        *,
        source_type: str,
        source_id: uuid.UUID,
        source_number: str,
        today: date,
    ) -> list[StockMovement]:
        """Transfert inter-sites d'articles suivis ou non par lot (Lot 3-H-B1), tout ou rien,
        dans la transaction de l'appelant — SEUL moteur des transferts par lot :

        1. verrou partagé des articles puis verrou des niveaux des DEUX sites, ordre global
           (site, article) ;
        2. contrôle global du stock source (``insufficient_stock``) ;
        3. lots désignés relus (même tenant par RLS, même article : ``lot_not_available``) ;
           soldes des lots des deux sites verrouillés APRÈS les niveaux, ordre global
           (site, article, lot) — les soldes destination manquants créés à zéro ;
        4. invariant Σ lots = stock contrôlé sur les deux sites (``lot_invariant_broken``) ;
        5. répartition MANUELLE revérifiée : lot présent au site source, non périmé
           (``lot_expired_not_transferable``, D-1, sans dérogation), solde suffisant, somme
           exacte (``lot_allocation_exceeds`` / ``lot_allocation_incomplete``) ;
        6. par (ligne, lot) : ``TRANSFER_OUT`` (source, −q) et ``TRANSFER_IN`` (destination,
           +q), MÊME lot, MÊME coût = CMUP source lu une fois (aucun arrondi intermédiaire :
           valeur sortie = valeur entrée) ; CMUP destination calculé UNE fois par ligne sur la
           quantité totale (identique à un transfert non réparti, T-3).

        Article non suivi : une paire par ligne, exactement comme ``transfer``. Renvoie les
        mouvements créés, paire par paire, dans l'ordre des lignes puis des lots."""
        if source_site_id == destination_site_id:
            raise ValueError("un transfert exige deux sites distincts")
        if not items:
            return []
        article_ids = {item.article_id for item in items}
        sites = (source_site_id, destination_site_id)
        levels = self._lock({(site, a) for a in article_ids for site in sites})
        flags = lock_lot_flags(self.db, article_ids)
        refs = get_article_refs(self.db, article_ids)
        for item in items:
            if item.quantity <= 0:
                raise ValueError("un transfert exige une quantité positive")
            if item.picks and not flags[item.article_id].lot_tracked:
                raise BusinessRuleError(
                    "Cet article n'est pas suivi par lot",
                    code="article_not_lot_tracked",
                    extra={"articles": [refs[item.article_id].reference]},
                )
        # Stock total du site source insuffisant : même refus qu'un transfert non suivi.
        self._ensure_non_negative(
            [
                (
                    source_site_id,
                    MovementRequest(
                        article_id=item.article_id,
                        movement_type=MovementType.TRANSFER_OUT,
                        quantity=-item.quantity,
                        source_type=source_type,
                        source_id=source_id,
                        source_line_id=item.line_id,
                    ),
                    levels[(source_site_id, item.article_id)],
                )
                for item in items
            ]
        )
        tracked = {a for a in article_ids if flags[a].lot_tracked}
        lot_levels = self._lock_transfer_lots(source_site_id, destination_site_id, items, tracked)
        self._ensure_lot_invariant(levels, lot_levels, tracked, sites, refs)
        candidates: dict[uuid.UUID, dict[uuid.UUID, _LotCandidate]] = {}
        for (site_id, article_id, lot_id), (level, lot) in lot_levels.items():
            if site_id == source_site_id:
                candidates.setdefault(article_id, {})[lot_id] = _LotCandidate(
                    level=level, lot=lot, remaining=level.quantity
                )
        planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]] = []
        # Index (dans ``planned``) d'une ENTRÉE → quantité pesée dans le CMUP destination :
        # la quantité TOTALE de la ligne pour sa première entrée, 0 pour les suivantes.
        entry_weights: dict[int, Decimal] = {}

        def pair(
            item: TransferItem,
            quantity: Decimal,
            lot_id: uuid.UUID | None,
            packaging: PackagingSnapshot | None,
        ) -> tuple[
            tuple[uuid.UUID, MovementRequest, StockLevel],
            tuple[uuid.UUID, MovementRequest, StockLevel],
        ]:
            return _transfer_pair(
                *sites,
                item,
                levels,
                quantity,
                lot_id,
                packaging,
                source_type=source_type,
                source_id=source_id,
                source_number=source_number,
            )

        for item in items:
            ref = refs[item.article_id]
            if item.article_id not in tracked:
                planned.extend(pair(item, item.quantity, None, item.packaging))
                continue
            allocated = sum((pick.quantity for pick in item.picks), Decimal("0"))
            if allocated > item.quantity:
                raise BusinessRuleError(
                    "La répartition par lot dépasse la quantité de la ligne",
                    code="lot_allocation_exceeds",
                    extra={
                        "articles": [ref.reference],
                        "requested": format(item.quantity, "f"),
                        "allocated": format(allocated, "f"),
                    },
                )
            allocations, _ = self._allocate(
                ConsumptionRequest(
                    article_id=item.article_id,
                    movement_type=MovementType.TRANSFER_OUT,
                    quantity=item.quantity,
                    source_type=source_type,
                    source_id=source_id,
                    source_line_id=item.line_id,
                    picks=item.picks,
                    manual=True,
                ),
                ref,
                flags[item.article_id],
                candidates.get(item.article_id, {}),
                today,
                refuse_expired=True,
            )
            for position, (lot_id, quantity) in enumerate(allocations):
                packaging = split_packaging(item.packaging, quantity, ref.decimal_quantity_allowed)
                outgoing, incoming = pair(item, quantity, lot_id, packaging)
                planned.append(outgoing)
                entry_weights[len(planned)] = item.quantity if position == 0 else Decimal("0")
                planned.append(incoming)
        return self._write(
            planned,
            {key: level for key, (level, _) in lot_levels.items()},
            entry_weights=entry_weights,
        )

    def _lock_transfer_lots(
        self,
        source_site_id: uuid.UUID,
        destination_site_id: uuid.UUID,
        items: Sequence[TransferItem],
        tracked: set[uuid.UUID],
    ) -> dict[LotKey, tuple[StockLotLevel, StockLot]]:
        """Soldes des lots des articles suivis sur les DEUX sites, verrouillés APRÈS les niveaux
        dans l'ordre global (site, article, lot). Les lots désignés sont d'abord relus (même
        tenant par RLS, même article) ; leur solde destination est créé à zéro au besoin — le
        MÊME lot, jamais un nouveau ``stock_lot`` (aucun lot n'est créé par un transfert)."""
        if not tracked:
            return {}
        picks = [(item.article_id, pick) for item in items for pick in item.picks]
        lots = {
            lot.id: lot
            for lot in self.db.scalars(
                select(StockLot).where(StockLot.id.in_({pick.lot_id for _, pick in picks}))
            )
        }
        for article_id, pick in picks:
            lot = lots.get(pick.lot_id)
            if lot is None or lot.article_id != article_id:
                # Lot inconnu, d'un autre tenant (RLS) ou d'un autre article.
                refs = get_article_refs(self.db, {article_id})
                raise BusinessRuleError(
                    "Ce lot n'est pas disponible pour cet article sur ce site",
                    code="lot_not_available",
                    extra={
                        "articles": [refs[article_id].reference] if article_id in refs else [],
                        "lot_id": str(pick.lot_id),
                    },
                )
        wanted = sorted({(destination_site_id, a, pick.lot_id) for a, pick in picks})
        if wanted:
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
                        for site_id, article_id, lot_id in wanted
                    ]
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "lot_id"])
            )
        rows = self.db.execute(
            select(StockLotLevel, StockLot)
            .join(
                StockLot,
                (StockLot.id == StockLotLevel.lot_id)
                & (StockLot.tenant_id == StockLotLevel.tenant_id),
            )
            .where(
                StockLotLevel.site_id.in_((source_site_id, destination_site_id)),
                StockLotLevel.article_id.in_(tracked),
            )
            .order_by(StockLotLevel.site_id, StockLotLevel.article_id, StockLotLevel.lot_id)
            .with_for_update(of=StockLotLevel)
            .execution_options(populate_existing=True)
        ).all()
        return {
            (level.site_id, level.article_id, level.lot_id): (level, lot) for level, lot in rows
        }

    def lock_site_lots(
        self,
        site_id: uuid.UUID,
        article_ids: set[uuid.UUID],
        ensure: Iterable[tuple[uuid.UUID, uuid.UUID]] = (),
    ) -> dict[tuple[uuid.UUID, uuid.UUID], StockLotLevel]:
        """Inventaires par lot (Lot 3-H) : verrous dans l'ordre global — articles (partagé),
        niveaux (site, article), puis TOUS les soldes de lots des articles sur le site (``FOR
        UPDATE``, ordre site → article → lot) ; soldes manquants des lots ``ensure`` (article,
        lot) créés à zéro (lot découvert, lot sans solde sur le site). Invariant Σ lots = stock
        contrôlé avant toute écriture (``lot_invariant_broken``). Renvoie (article, lot) →
        solde verrouillé. Aucune écriture de stock : l'appelant applique ensuite ses
        mouvements via ``apply`` (même transaction, verrous déjà détenus)."""
        if not article_ids:
            return {}
        levels = self._lock({(site_id, article_id) for article_id in article_ids})
        wanted = sorted({(site_id, article_id, lot_id) for article_id, lot_id in ensure})
        if wanted:
            self.db.execute(
                insert(StockLotLevel)
                .values(
                    [
                        {
                            "id": uuid.uuid4(),
                            "tenant_id": self.tenant_id,
                            "site_id": site,
                            "article_id": article_id,
                            "lot_id": lot_id,
                            "quantity": Decimal("0"),
                        }
                        for site, article_id, lot_id in wanted
                    ]
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "lot_id"])
            )
        rows = self.db.scalars(
            select(StockLotLevel)
            .where(StockLotLevel.site_id == site_id, StockLotLevel.article_id.in_(article_ids))
            .order_by(StockLotLevel.site_id, StockLotLevel.article_id, StockLotLevel.lot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        locked = {(row.site_id, row.article_id, row.lot_id): row for row in rows}
        refs = get_article_refs(self.db, article_ids)
        self._ensure_lot_invariant(levels, locked, article_ids, (site_id,), refs)
        return {(article_id, lot_id): row for (_, article_id, lot_id), row in locked.items()}

    def verify_lot_invariant(self, site_id: uuid.UUID, article_ids: set[uuid.UUID]) -> None:
        """Défense en profondeur APRÈS les écritures (Lot 3-H) : Σ soldes des lots = stock du
        site pour chaque article suivi, relus en base dans la transaction. Une rupture est une
        erreur du moteur (jamais une règle métier) : l'opération entière est annulée."""
        if not article_ids:
            return
        self.db.flush()
        lots: dict[uuid.UUID, Decimal] = {
            row[0]: row[1]
            for row in self.db.execute(
                select(StockLotLevel.article_id, func.sum(StockLotLevel.quantity))
                .where(StockLotLevel.site_id == site_id, StockLotLevel.article_id.in_(article_ids))
                .group_by(StockLotLevel.article_id)
            )
        }
        stock: dict[uuid.UUID, Decimal] = {
            row[0]: row[1]
            for row in self.db.execute(
                select(StockLevel.article_id, StockLevel.quantity).where(
                    StockLevel.site_id == site_id, StockLevel.article_id.in_(article_ids)
                )
            )
        }
        broken = [
            a
            for a in sorted(article_ids)
            if lots.get(a, Decimal("0")) != stock.get(a, Decimal("0"))
        ]
        if broken:
            raise RuntimeError(f"invariant Σ lots = stock rompu après écriture : {broken}")

    def _ensure_lot_invariant(
        self,
        levels: dict[Key, StockLevel],
        lot_levels: Mapping[LotKey, tuple[StockLotLevel, StockLot] | StockLotLevel],
        tracked: set[uuid.UUID],
        sites: Sequence[uuid.UUID],
        refs: dict[uuid.UUID, ArticleRef],
    ) -> None:
        """Invariant Σ soldes des lots = stock du site (3-G), contrôlé sous verrou AVANT toute
        écriture sur chaque site concerné : s'il est déjà rompu, l'opération est refusée."""
        totals: dict[Key, Decimal] = {}
        for (site_id, article_id, _), entry in lot_levels.items():
            level = entry[0] if isinstance(entry, tuple) else entry
            totals[(site_id, article_id)] = totals.get((site_id, article_id), Decimal("0")) + (
                level.quantity
            )
        broken = [
            (site_id, article_id)
            for article_id in sorted(tracked)
            for site_id in sites
            if totals.get((site_id, article_id), Decimal("0"))
            != levels[(site_id, article_id)].quantity
        ]
        if broken:
            raise BusinessRuleError(
                "Les soldes des lots ne correspondent pas au stock de l'article",
                code="lot_invariant_broken",
                extra={
                    "articles": sorted({refs[article_id].reference for _, article_id in broken}),
                    "site_ids": sorted({str(site_id) for site_id, _ in broken}),
                },
            )

    def _write(
        self,
        planned: list[tuple[uuid.UUID, MovementRequest, StockLevel]],
        lots: dict[LotKey, StockLotLevel] | None = None,
        *,
        entry_weights: dict[int, Decimal] | None = None,
    ) -> list[StockMovement]:
        """Écritures (après les contrôles globaux). ``entry_weights`` (interne, transferts par
        lot) : pour l'ENTRÉE d'index donné, quantité pesée dans le CMUP — la quantité totale de
        la ligne pour sa première entrée, 0 pour les suivantes (CMUP déjà calculé) ; absent :
        la quantité du mouvement (comportement de toujours)."""
        lots = lots or {}
        entry_weights = entry_weights or {}
        self._ensure_lot_guard(planned)
        # Soldes des lots d'abord (Lot 3-H-B1) : pour un article suivi, Σ lots = stock — le refus
        # nomme le lot en cause (``insufficient_lot_stock``) ; puis le stock (site, article).
        self._ensure_lots_non_negative(planned, lots)
        self._ensure_non_negative(planned)
        movements: list[StockMovement] = []
        previous_id: uuid.UUID | None = None
        for index, (site_id, request, level) in enumerate(planned):
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
                weight = entry_weights.get(index, request.quantity)
                if weight > 0:
                    level.average_cost = compute_average_cost(
                        quantity_before, cost_before, weight, unit_cost
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
        *,
        refuse_expired: bool = False,
    ) -> tuple[list[tuple[uuid.UUID, Decimal]], Decimal]:
        """Répartition d'une ligne sur les lots (soldes de travail décrémentés au fil des lignes
        d'une même opération). Renvoie les allocations (lot, quantité) et la quantité restée
        sans lot non périmé (automatique seulement). ``refuse_expired`` (transferts, D-1) : un
        lot périmé désigné est refusé (``lot_expired_not_transferable``)."""
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
            if refuse_expired and is_expired(candidate.lot, flags, today):
                raise BusinessRuleError(
                    "Un lot périmé ne peut pas être transféré",
                    code="lot_expired_not_transferable",
                    extra={
                        "articles": [ref.reference],
                        "lots": [candidate.lot.number],
                        "lot_id": str(pick.lot_id),
                    },
                )
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


def _transfer_pair(
    source_site_id: uuid.UUID,
    destination_site_id: uuid.UUID,
    item: TransferItem,
    levels: dict[Key, StockLevel],
    quantity: Decimal,
    lot_id: uuid.UUID | None,
    packaging: PackagingSnapshot | None,
    *,
    source_type: str,
    source_id: uuid.UUID,
    source_number: str,
) -> tuple[
    tuple[uuid.UUID, MovementRequest, StockLevel], tuple[uuid.UUID, MovementRequest, StockLevel]
]:
    """Paire ``TRANSFER_OUT`` (source, −q) / ``TRANSFER_IN`` (destination, +q) : même coût =
    CMUP du site source (inchangé par une sortie), même lot, même présentation."""
    outgoing = MovementRequest(
        article_id=item.article_id,
        movement_type=MovementType.TRANSFER_OUT,
        quantity=-quantity,
        unit_cost=levels[(source_site_id, item.article_id)].average_cost,
        source_type=source_type,
        source_id=source_id,
        source_line_id=item.line_id,
        source_number=source_number,
        comment=source_number,
        packaging=packaging,
        lot_id=lot_id,
    )
    incoming = replace(outgoing, movement_type=MovementType.TRANSFER_IN, quantity=quantity)
    return (
        (source_site_id, outgoing, levels[(source_site_id, item.article_id)]),
        (destination_site_id, incoming, levels[(destination_site_id, item.article_id)]),
    )


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
