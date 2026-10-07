"""Entrées et sorties de stock : cycle BROUILLON → VALIDÉE → ANNULÉE (ENT-*, SOR-*).

Le stock n'est modifié qu'à la validation et à l'annulation, exclusivement via
``StockService``, dans la transaction de la requête. Le document est verrouillé
(``SELECT … FOR UPDATE``) avant tout changement de statut : deux validations simultanées ne
peuvent pas s'appliquer deux fois.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import (
    QUANTITY_STEP,
    ArticleRef,
    PackagingRef,
    base_quantity,
    check_packagings,
    ensure_conversion_unchanged,
    ensure_in_assortment,
    ensure_whole,
    get_article_refs,
    lock_lot_flags,
)
from app.modules.stock.location_service import current_locations
from app.modules.stock.lot_service import (
    ExpiryContext,
    LotInput,
    check_known_lots,
    check_lot_inputs,
    expiry_context,
    lot_allocations,
    lot_infos,
    lot_key,
    resolve_lots,
)
from app.modules.stock.models import (
    DocumentStatus,
    EntryKind,
    ExitReason,
    MovementType,
    StockEntry,
    StockEntryLine,
    StockExit,
    StockExitLine,
    StockExitLineLot,
    StockMovement,
    StockTransferLine,
)
from app.modules.stock.schemas import (
    EntryCreate,
    EntryInput,
    EntryOut,
    ExitCreate,
    ExitInput,
    ExitOut,
    LineLotOut,
    LineOut,
)
from app.modules.stock.sites import (
    ensure_document_site,
    operation_site,
    tenant_today,
    visible_site_ids,
)
from app.modules.stock.stock_service import (
    ConsumptionRequest,
    LotPick,
    MovementRequest,
    PackagingSnapshot,
    StockService,
    cost_per_base,
    inverse_packaging,
    movement_ref,
    refuse_unmanaged,
    round_money,
)
from app.modules.suppliers.api import get_supplier_ref, supplier_names
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.sequences.service import next_number
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter

Doc = TypeVar("Doc", StockEntry, StockExit)


def _names(db: Session, model: Any, ids: set[uuid.UUID | None], column: Any) -> dict[Any, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {
        row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(wanted)))
    }


def check_articles(
    db: Session,
    lines: Sequence[tuple[uuid.UUID, uuid.UUID | None]],
    lots: Sequence[str | None] | None = None,
) -> dict[uuid.UUID, ArticleRef]:
    """Articles des lignes d'un document : existants, actifs (ART-13), gérés en stock (Lot 3-A),
    une ligne par présentation (article en unité de base, ou conditionnement — Lot 3-C) et, pour
    une réception (``lots``, Lot 3-G), par lot : un article peut figurer plusieurs fois dans la
    même présentation si les lots diffèrent (numéro sans distinction de casse)."""
    keyed: Sequence[Any] = (
        lines
        if lots is None
        else [(*key, (lot or "").lower()) for key, lot in zip(lines, lots, strict=True)]
    )
    if len(set(keyed)) != len(keyed):
        raise BusinessRuleError(
            "Un article apparaît plusieurs fois dans la même présentation",
            code="duplicate_article_line",
        )
    article_ids = [article_id for article_id, _ in lines]
    refs = get_article_refs(db, set(article_ids))
    missing = [str(a) for a in article_ids if a not in refs]
    if missing:
        raise BusinessRuleError(
            "Article introuvable", code="article_not_found", extra={"articles": missing}
        )
    inactive = [refs[a].reference for a in article_ids if not refs[a].is_active]
    if inactive:
        raise BusinessRuleError(
            "Article inactif", code="article_inactive", extra={"articles": inactive}
        )
    # Lot 3-A : entrées, sorties et transferts refusés pour un article non géré en stock.
    refuse_unmanaged(db, [a for a in article_ids if not refs[a].stock_managed])
    return refs


@dataclass(frozen=True)
class PresentedLine:
    """Ligne saisie dans une présentation (Lot 3-C, ADR-0041) : quantité dans la présentation,
    conditionnement relu (``None`` = unité de base) et quantité de base calculée PAR LE
    SERVEUR (mécanisme commun du catalogue)."""

    article_id: uuid.UUID
    packaging: PackagingRef | None
    quantity: Decimal
    base_quantity: Decimal

    def columns(self) -> dict[str, Any]:
        """Colonnes de présentation d'une ligne de document (instantané figé)."""
        return {
            "quantity": self.quantity,
            "base_quantity": self.base_quantity,
            "packaging_id": self.packaging.id if self.packaging else None,
            "packaging_name": self.packaging.name if self.packaging else None,
            "packaging_conversion": self.packaging.conversion if self.packaging else None,
        }


def _lot_numbers(lines: Sequence[Any], with_lots: bool) -> list[str | None] | None:
    return [line.lot_number for line in lines] if with_lots else None


def present_lines(
    db: Session, lines: Sequence[Any], *, with_lots: bool = False
) -> list[PresentedLine]:
    """Lignes saisies (``article_id``, ``packaging_id``, ``quantity``) : articles et
    conditionnements relus (du tenant, de l'article, actifs, sous verrou partagé), règle des
    quantités entières et quantité de base — UN mécanisme pour entrées, sorties, transferts.
    ``with_lots`` (réceptions, Lot 3-G) : une ligne par présentation ET par lot."""
    keys = [(line.article_id, line.packaging_id) for line in lines]
    refs = check_articles(db, keys, _lot_numbers(lines, with_lots))
    packagings = check_packagings(db, keys)
    presented = []
    for line in lines:
        quantity = line.quantity.quantize(QUANTITY_STEP)
        packaging = packagings[line.packaging_id] if line.packaging_id else None
        presented.append(
            PresentedLine(
                article_id=line.article_id,
                packaging=packaging,
                quantity=quantity,
                base_quantity=base_quantity(refs[line.article_id], quantity, packaging),
            )
        )
    return presented


def revalidate_lines(
    db: Session, lines: Sequence[Any], *, with_lots: bool = False
) -> dict[uuid.UUID, ArticleRef]:
    """Validation d'un document : tout est relu — article (actif, géré), conditionnement
    (existant, actif, conversion inchangée), règle des quantités entières — et la quantité de
    base recalculée doit être celle de la ligne (jamais celle du client)."""
    keys = [(line.article_id, line.packaging_id) for line in lines]
    refs = check_articles(db, keys, _lot_numbers(lines, with_lots))
    packagings = check_packagings(db, keys)
    for line in lines:
        ref = refs[line.article_id]
        packaging = packagings[line.packaging_id] if line.packaging_id else None
        ensure_conversion_unchanged(ref, packaging, line.packaging_conversion)
        if base_quantity(ref, line.quantity, packaging) != line.base_quantity:
            raise ConflictError(
                "La quantité en unité de base a changé : enregistrez à nouveau le document",
                code="packaging_conversion_changed",
                extra={"articles": [ref.reference]},
            )
    return refs


def packaging_snapshot(line: Any) -> PackagingSnapshot | None:
    """Présentation d'une ligne pour son mouvement (historique « 3 Carton 24 → 72 »)."""
    if line.packaging_id is None:
        return None
    return PackagingSnapshot(
        packaging_id=line.packaging_id,
        name=line.packaging_name,
        conversion=line.packaging_conversion,
        quantity=line.quantity,
    )


def packagings_used(db: Session, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Port du catalogue (Lot 3-C) : conditionnements figurant sur une ligne d'entrée, de
    sortie ou de transfert (brouillons compris) — leur conversion est alors figée."""
    used: set[uuid.UUID] = set()
    for model in (StockEntryLine, StockExitLine, StockTransferLine):
        used |= {
            packaging_id
            for packaging_id in db.scalars(
                select(model.packaging_id).where(model.packaging_id.in_(ids)).distinct()
            )
            if packaging_id is not None
        }
    return used


def check_lot_choices(db: Session, lines: Sequence[Any], presented: list[PresentedLine]) -> None:
    """Choix des lots d'un brouillon de sortie (Lot 3-H-A, H-D8, O-4) ou de transfert
    (Lot 3-H-B1) : article suivi par lot, lots de l'article (et du tenant), sans doublon,
    quantités en unité de base (entières pour un article en quantités entières), somme au plus
    égale à la quantité de la ligne. Une répartition INCOMPLÈTE est admise ; tout est revérifié
    à la validation."""
    refs = get_article_refs(db, {line.article_id for line in lines})
    lots = lot_infos(db, {c.lot_id for line in lines for c in line.lots})
    for line, shown in zip(lines, presented, strict=True):
        if not line.lots:
            continue
        ref = refs[line.article_id]
        if not ref.lot_tracked:
            raise BusinessRuleError(
                "Cet article n'est pas suivi par lot",
                code="article_not_lot_tracked",
                extra={"articles": [ref.reference]},
            )
        seen: set[uuid.UUID] = set()
        total = Decimal("0")
        for choice in line.lots:
            lot = lots.get(choice.lot_id)
            if lot is None or lot.article_id != line.article_id:
                raise BusinessRuleError(
                    "Ce lot n'est pas disponible pour cet article",
                    code="lot_not_available",
                    extra={"articles": [ref.reference], "lot_id": str(choice.lot_id)},
                )
            if choice.lot_id in seen:
                raise BusinessRuleError(
                    "Un même lot est choisi plusieurs fois sur la ligne",
                    code="duplicate_lot_allocation",
                    extra={"articles": [ref.reference], "lots": [lot.number]},
                )
            seen.add(choice.lot_id)
            quantity = choice.quantity.quantize(QUANTITY_STEP)
            if quantity != choice.quantity:
                raise BusinessRuleError(
                    "La quantité en unité de base dépasse la précision autorisée (3 décimales)",
                    code="base_quantity_precision",
                    extra={"articles": [ref.reference]},
                )
            ensure_whole(ref, quantity)
            total += quantity
        if total > shown.base_quantity:
            raise BusinessRuleError(
                "La répartition par lot dépasse la quantité de la ligne",
                code="lot_allocation_exceeds",
                extra={
                    "articles": [ref.reference],
                    "requested": format(shown.base_quantity, "f"),
                    "allocated": format(total, "f"),
                },
            )


class _DocumentService(Generic[Doc]):
    model: type[Doc]
    source_type: str
    sequence_key: str
    prefix: str
    audit_prefix: str
    not_found_code: str

    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Lecture ------------------------------------------------------------------------------

    def _base_query(self) -> Select[tuple[Doc]]:
        return select(self.model).where(self.model.site_id.in_(visible_site_ids(self.ctx)))

    def search(
        self,
        params: PageParams,
        search: str | None,
        status: DocumentStatus | None,
        site_id: uuid.UUID | None,
        date_from: date | None,
        date_to: date | None,
        extra: Sequence[Any] = (),
        search_columns: Sequence[Any] = (),
        search_also: Sequence[Any] = (),
    ) -> tuple[list[Doc], int]:
        """``search_also`` : conditions supplémentaires de la recherche texte, en OU avec les
        colonnes (ex. entrées : nom du fournisseur, Lot 3-E)."""
        stmt = self._base_query()
        text_search = search_filter(search, self.model.number, *search_columns)
        if text_search is not None and search_also:
            text_search = or_(text_search, *search_also)
        conditions: list[Any] = [
            text_search,
            self.model.status == status if status else None,
            self.model.site_id == site_id if site_id else None,
            self.model.operation_date >= date_from if date_from else None,
            self.model.operation_date <= date_to if date_to else None,
            *extra,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        sortable = {
            "number": self.model.number,
            "operation_date": self.model.operation_date,
            "created_at": self.model.created_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "-number", self.model.id)
        return paginate(self.db, stmt, params)

    def get(self, document_id: uuid.UUID, *, lock: bool = False) -> Doc:
        stmt = select(self.model).where(self.model.id == document_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        document = self.db.scalars(stmt).one_or_none()
        if document is None:
            raise NotFoundError("Document introuvable", code=self.not_found_code)
        ensure_document_site(self.ctx, document.site_id, self.not_found_code)
        return document

    # --- Règles communes -------------------------------------------------------------------------

    def _operation_date(self, value: date | None) -> date:
        today = tenant_today(self.ctx, self.now)
        chosen = value or today
        if chosen > today:
            raise BusinessRuleError(
                "La date de l'opération ne peut pas être postérieure à aujourd'hui",
                code="future_operation_date",
            )
        return chosen

    def _require_status(self, document: Doc, expected: DocumentStatus, code: str) -> None:
        if document.status is not expected:
            raise ConflictError("Opération impossible dans l'état actuel du document", code=code)

    def _audit(self, action: str, document: Doc, data: dict[str, Any] | None = None) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"{self.audit_prefix}.{action}",
            entity_type=self.audit_prefix,
            entity_id=document.id,
            site_id=document.site_id,
            data={"number": document.number, **(data or {})},
        )

    def _stock(self) -> StockService:
        return StockService(self.db, self.ctx.tenant_id, self.ctx.user.id, self.now)

    def _origin_movements(self, document: Doc) -> dict[uuid.UUID, list[StockMovement]]:
        """Mouvements d'origine par ligne — plusieurs pour une ligne répartie sur plusieurs
        lots (Lot 3-H, M1), dans l'ordre de création."""
        rows = self.db.scalars(
            select(StockMovement)
            .where(
                StockMovement.source_id == document.id,
                StockMovement.movement_type != MovementType.CANCELLATION,
            )
            .order_by(StockMovement.occurred_at, StockMovement.id)
        )
        result: dict[uuid.UUID, list[StockMovement]] = {}
        for movement in rows:
            result.setdefault(movement.source_line_id, []).append(movement)
        return result

    def _cancel(self, document_id: uuid.UUID, reason: str, sign: Decimal) -> Doc:
        """Un mouvement inverse par mouvement d'origine — même quantité, même coût, MÊME lot
        (3-G D10, 3-H H-D10 : restauration exacte, jamais sur un autre lot) ; refus total si un
        stock ou un solde de lot devenait négatif (ENT-08)."""
        document = self.get(document_id, lock=True)
        self._require_status(document, DocumentStatus.VALIDATED, "document_not_validated")
        origins = self._origin_movements(document)
        comment = f"Annulation {document.number}"
        requests: list[MovementRequest] = []
        for line in document.lines:
            line_packaging = packaging_snapshot(line)
            line_origins = origins.get(line.id, [])
            if not line_origins:
                # Historique sans mouvement rattaché : inverse calculé depuis la ligne.
                requests.append(
                    MovementRequest(
                        article_id=line.article_id,
                        movement_type=MovementType.CANCELLATION,
                        quantity=sign * line.base_quantity,
                        unit_cost=line.unit_cost,
                        source_type=self.source_type,
                        source_id=document.id,
                        source_line_id=line.id,
                        source_number=document.number,
                        comment=comment,
                        packaging=line_packaging,
                        lot_id=getattr(line, "lot_id", None),
                    )
                )
                continue
            for origin in line_origins:
                ref = movement_ref(origin)
                requests.append(
                    MovementRequest(
                        article_id=line.article_id,
                        movement_type=MovementType.CANCELLATION,
                        # Unité de base ; coût du mouvement d'origine (unité de base).
                        quantity=-origin.quantity,
                        unit_cost=origin.unit_cost,
                        source_type=self.source_type,
                        source_id=document.id,
                        source_line_id=line.id,
                        source_number=document.number,
                        origin_movement_id=origin.id,
                        comment=comment,
                        packaging=inverse_packaging(
                            ref, line_packaging, single=len(line_origins) == 1
                        ),
                        lot_id=origin.lot_id,
                    )
                )
        self._stock().apply(document.site_id, requests)
        document.status = DocumentStatus.CANCELLED
        document.cancelled_at = self.now
        document.cancelled_by = self.ctx.user.id
        document.cancellation_reason = reason
        self.db.flush()
        self._audit("cancelled", document, {"reason": reason})
        return document

    # --- Sortie API -----------------------------------------------------------------------------

    def _lines_out(
        self,
        document: Doc,
        refs: dict[uuid.UUID, ArticleRef],
        expiry: ExpiryContext | None = None,
    ) -> list[LineOut]:
        """Lignes d'un document, avec l'emplacement COURANT de chaque article sur le site du
        document (Lot 3-F : indicatif, jamais figé ni contrôlé) et, pour une réception, le lot
        saisi et son état de péremption (Lot 3-G)."""
        lines = document.lines
        locations = current_locations(
            self.db, document.site_id, {line.article_id for line in lines}
        )
        lots = self._line_lots(document, expiry)
        return [
            self._line_out(line, refs, locations.get(line.article_id), expiry, lots.get(line.id))
            for line in lines
        ]

    def _line_lots(
        self, document: Doc, expiry: ExpiryContext | None
    ) -> dict[uuid.UUID, list[LineLotOut]]:
        """Répartition par lot des lignes (sorties seulement, Lot 3-H-A)."""
        return {}

    def _line_out(
        self,
        line: Any,
        refs: dict[uuid.UUID, ArticleRef],
        location: tuple[str, bool] | None = None,
        expiry: ExpiryContext | None = None,
        lots: list[LineLotOut] | None = None,
    ) -> LineOut:
        ref = refs.get(line.article_id)
        lot_number = getattr(line, "lot_number", None)
        lot_expiry = getattr(line, "lot_expiry_date", None)
        return LineOut(
            id=line.id,
            line_no=line.line_no,
            article_id=line.article_id,
            article_reference=ref.reference if ref else "?",
            article_designation=ref.designation if ref else "?",
            unit=ref.unit if ref else "",
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            amount=line.amount,
            packaging_id=line.packaging_id,
            packaging_name=line.packaging_name,
            packaging_conversion=line.packaging_conversion,
            base_quantity=line.base_quantity,
            location_name=location[0] if location else None,
            lot_id=getattr(line, "lot_id", None),
            lot_number=lot_number,
            lot_expiry_date=lot_expiry,
            lot_manufacturing_date=getattr(line, "lot_manufacturing_date", None),
            lot_state=expiry.state(lot_expiry) if expiry and lot_number else None,
            lots=lots or [],
        )

    def _common(self, documents: Sequence[Doc]) -> dict[str, dict[Any, str]]:
        users = {u for d in documents for u in (d.created_by, d.validated_by, d.cancelled_by)}
        return {
            "users": _names(self.db, User, users, User.full_name),
            "sites": _names(self.db, Site, {d.site_id for d in documents}, Site.name),
        }

    def _base_out(self, document: Doc, names: dict[str, dict[Any, str]]) -> dict[str, Any]:
        amounts = [line.amount for line in document.lines]
        total = (
            round_money(sum((a for a in amounts if a is not None), Decimal("0")))
            if amounts and all(a is not None for a in amounts)
            else None
        )
        users = names["users"]
        return {
            "id": document.id,
            "number": document.number,
            "site_id": document.site_id,
            "site_name": names["sites"].get(document.site_id, ""),
            "status": document.status,
            "operation_date": document.operation_date,
            "comment": document.comment,
            "total_amount": total,
            "line_count": len(document.lines),
            "created_at": document.created_at,
            "created_by_name": users.get(document.created_by),
            "validated_at": document.validated_at,
            "validated_by_name": users.get(document.validated_by),
            "cancelled_at": document.cancelled_at,
            "cancelled_by_name": users.get(document.cancelled_by),
            "cancellation_reason": document.cancellation_reason,
        }


class EntryService(_DocumentService[StockEntry]):
    model = StockEntry
    source_type = "stock_entry"
    sequence_key = "stock_entry"
    prefix = "ENT"
    audit_prefix = "stock_entry"
    not_found_code = "stock_entry_not_found"

    def _check_supplier(self, kind: EntryKind, supplier_id: uuid.UUID | None) -> None:
        if supplier_id is None:
            if kind is EntryKind.PURCHASE:
                raise BusinessRuleError("Fournisseur obligatoire", code="supplier_required")
            return
        if "suppliers" not in self.ctx.capabilities.modules:
            raise BusinessRuleError(
                "Le module Fournisseurs n'est pas actif", code="module_unavailable"
            )
        supplier = get_supplier_ref(self.db, supplier_id)
        if supplier is None:
            raise BusinessRuleError("Fournisseur introuvable", code="supplier_not_found")
        if not supplier.is_active:
            raise BusinessRuleError(
                "Le fournisseur sélectionné est inactif", code="supplier_inactive"
            )

    def _lot_inputs(self, lines: Sequence[Any]) -> list[LotInput]:
        return [
            LotInput(
                article_id=line.article_id,
                number=line.lot_number,
                expiry_date=line.lot_expiry_date,
                manufacturing_date=line.lot_manufacturing_date,
            )
            for line in lines
        ]

    def _apply_input(self, entry: StockEntry, data: EntryInput) -> None:
        self._check_supplier(data.kind, data.supplier_id)
        presented = present_lines(self.db, data.lines, with_lots=True)
        # Recette, étape 1 (ADR-0046, D3) : article de l'assortiment ACTIF du site, dès le
        # brouillon ; la validation revérifie sous verrou (``StockService``).
        ensure_in_assortment(self.db, entry.site_id, {line.article_id for line in data.lines})
        # Lot 3-G (D5, D8, D17, D4) : lots contrôlés dès le brouillon (revérifiés à la
        # validation) — obligatoires pour un article suivi, interdits sinon.
        lots = self._lot_inputs(data.lines)
        refs = get_article_refs(self.db, {line.article_id for line in data.lines})
        check_lot_inputs(lots, refs)
        check_known_lots(self.db, lots, refs)
        entry.kind = data.kind
        entry.operation_date = self._operation_date(data.operation_date)
        entry.supplier_id = data.supplier_id
        entry.document_reference = data.document_reference
        entry.comment = data.comment
        # Coût unitaire saisi PAR PRÉSENTATION (12 000 le carton) ; montant = quantité × coût.
        entry.lines = [
            StockEntryLine(
                tenant_id=self.ctx.tenant_id,
                line_no=index,
                article_id=line.article_id,
                unit_cost=line.unit_cost,
                amount=round_money(shown.quantity * line.unit_cost),
                lot_number=line.lot_number,
                lot_expiry_date=line.lot_expiry_date,
                lot_manufacturing_date=line.lot_manufacturing_date,
                **shown.columns(),
            )
            for index, (line, shown) in enumerate(zip(data.lines, presented, strict=True), start=1)
        ]

    def create(self, data: EntryCreate) -> StockEntry:
        site_id = operation_site(self.ctx, data.site_id)
        entry = StockEntry(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            number=next_number(self.db, self.ctx.tenant_id, self.sequence_key, self.prefix),
            status=DocumentStatus.DRAFT,
            created_by=self.ctx.user.id,
        )
        self._apply_input(entry, data)
        self.db.add(entry)
        self.db.flush()
        self._audit("created", entry, {"kind": entry.kind.value})
        return entry

    def update(self, entry_id: uuid.UUID, data: EntryInput) -> StockEntry:
        entry = self.get(entry_id, lock=True)
        self._require_status(entry, DocumentStatus.DRAFT, "document_not_draft")
        entry.lines = []
        self.db.flush()  # remplacement des lignes d'un brouillon
        self._apply_input(entry, data)
        self.db.flush()
        self._audit("updated", entry)
        return entry

    def validate(self, entry_id: uuid.UUID) -> StockEntry:
        """Seul moment où une entrée modifie le stock (ENT-07) : un mouvement ENTRÉE par
        ligne, CMUP recalculé, le tout dans la transaction."""
        entry = self.get(entry_id, lock=True)
        self._require_status(entry, DocumentStatus.DRAFT, "document_not_draft")
        if not entry.lines:
            raise BusinessRuleError("Aucune ligne à valider", code="document_empty")
        revalidate_lines(self.db, entry.lines, with_lots=True)
        lots_by_key = self._resolve_lots(entry)
        self._stock().apply(
            entry.site_id,
            [
                MovementRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.ENTRY,
                    # Stock et CMUP en unité de base : coût par unité de base.
                    quantity=line.base_quantity,
                    unit_cost=cost_per_base(line.unit_cost, line.packaging_conversion),
                    source_type=self.source_type,
                    source_id=entry.id,
                    source_line_id=line.id,
                    source_number=entry.number,
                    comment=entry.number,
                    packaging=packaging_snapshot(line),
                    lot_id=line.lot_id,
                )
                for line in entry.lines
            ],
        )
        entry.status = DocumentStatus.VALIDATED
        entry.validated_at = self.now
        entry.validated_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "validated",
            entry,
            {
                "kind": entry.kind.value,
                "lines": len(entry.lines),
                "total": format(sum((line.amount for line in entry.lines), Decimal("0")), "f"),
                **({"lots": lots_by_key} if lots_by_key else {}),
            },
        )
        return entry

    def _resolve_lots(self, entry: StockEntry) -> list[dict[str, str]]:
        """Lot 3-G (D4, D8, D9) : réglages de suivi relus sous verrou partagé de l'article (un
        changement concurrent attend), saisie revalidée, lots retrouvés ou créés, ``lot_id`` fixé
        sur chaque ligne. Renvoie le détail des lots reçus pour l'audit."""
        article_ids = {line.article_id for line in entry.lines}
        flags = lock_lot_flags(self.db, article_ids)
        refs = {
            article_id: replace(
                ref,
                lot_tracked=flags[article_id].lot_tracked,
                expiry_tracked=flags[article_id].expiry_tracked,
            )
            for article_id, ref in get_article_refs(self.db, article_ids).items()
        }
        lots = self._lot_inputs(entry.lines)
        check_lot_inputs(lots, refs)
        resolved = resolve_lots(self.db, self.ctx, lots, refs)
        received: list[dict[str, str]] = []
        for line in entry.lines:
            if line.lot_number is None:
                continue
            line.lot_id = resolved[lot_key(line.article_id, line.lot_number)]
            received.append(
                {
                    "reference": refs[line.article_id].reference,
                    "lot_number": line.lot_number,
                    "base_quantity": format(line.base_quantity, "f"),
                }
            )
        self.db.flush()
        return received

    def cancel(self, entry_id: uuid.UUID, reason: str) -> StockEntry:
        return self._cancel(entry_id, reason, Decimal("-1"))

    def to_out(self, entries: Sequence[StockEntry], with_lines: bool = False) -> list[EntryOut]:
        names = self._common(entries)
        suppliers = supplier_names(self.db, {e.supplier_id for e in entries if e.supplier_id})
        refs = (
            get_article_refs(self.db, {line.article_id for e in entries for line in e.lines})
            if with_lines
            else {}
        )
        expiry = expiry_context(self.db, self.ctx, self.now) if with_lines else None
        return [
            EntryOut(
                **self._base_out(entry, names),
                kind=entry.kind,
                supplier_id=entry.supplier_id,
                supplier_name=suppliers.get(entry.supplier_id) if entry.supplier_id else None,
                document_reference=entry.document_reference,
                lines=self._lines_out(entry, refs, expiry) if with_lines else [],
            )
            for entry in entries
        ]


class ExitService(_DocumentService[StockExit]):
    model = StockExit
    source_type = "stock_exit"
    sequence_key = "stock_exit"
    prefix = "SOR"
    audit_prefix = "stock_exit"
    not_found_code = "stock_exit_not_found"

    def _check_reason(self, reason_id: uuid.UUID) -> None:
        reason = self.db.get(ExitReason, reason_id)
        if reason is None:
            raise BusinessRuleError("Motif introuvable", code="exit_reason_not_found")
        if not reason.is_active:
            raise BusinessRuleError("Le motif sélectionné est inactif", code="exit_reason_inactive")

    def _apply_input(self, document: StockExit, data: ExitInput) -> None:
        self._check_reason(data.reason_id)
        presented = present_lines(self.db, data.lines)
        # Recette, étape 1 (ADR-0046, D3) : article de l'assortiment ACTIF du site, dès le
        # brouillon ; la validation revérifie sous verrou (``StockService``).
        ensure_in_assortment(self.db, document.site_id, {line.article_id for line in data.lines})
        check_lot_choices(self.db, data.lines, presented)
        document.operation_date = self._operation_date(data.operation_date)
        document.reason_id = data.reason_id
        document.beneficiary = data.beneficiary
        document.reference = data.reference
        document.comment = data.comment
        document.lines = [
            StockExitLine(
                tenant_id=self.ctx.tenant_id,
                line_no=index,
                article_id=shown.article_id,
                lots=[
                    StockExitLineLot(
                        tenant_id=self.ctx.tenant_id,
                        article_id=shown.article_id,
                        lot_id=choice.lot_id,
                        position=position,
                        quantity=choice.quantity.quantize(QUANTITY_STEP),
                    )
                    for position, choice in enumerate(line.lots, start=1)
                ],
                **shown.columns(),
            )
            for index, (line, shown) in enumerate(zip(data.lines, presented, strict=True), start=1)
        ]

    def create(self, data: ExitCreate) -> StockExit:
        site_id = operation_site(self.ctx, data.site_id)
        document = StockExit(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            number=next_number(self.db, self.ctx.tenant_id, self.sequence_key, self.prefix),
            status=DocumentStatus.DRAFT,
            created_by=self.ctx.user.id,
        )
        self._apply_input(document, data)
        self.db.add(document)
        self.db.flush()
        self._audit("created", document)
        return document

    def update(self, exit_id: uuid.UUID, data: ExitInput) -> StockExit:
        document = self.get(exit_id, lock=True)
        self._require_status(document, DocumentStatus.DRAFT, "document_not_draft")
        document.lines = []
        self.db.flush()
        self._apply_input(document, data)
        self.db.flush()
        self._audit("updated", document)
        return document

    def validate(self, exit_id: uuid.UUID) -> StockExit:
        """Mouvement SORTIE par ligne au CMUP du site, figé sur la ligne (SOR-03, Q1) ;
        refus total si un stock devenait négatif."""
        document = self.get(exit_id, lock=True)
        self._require_status(document, DocumentStatus.DRAFT, "document_not_draft")
        if not document.lines:
            raise BusinessRuleError("Aucune ligne à valider", code="document_empty")
        revalidate_lines(self.db, document.lines)
        # Lot 3-H-A : article suivi par lot — répartition MANUELLE du brouillon, revalidée par
        # le moteur (lots de l'article sur ce site, soldes sous verrou, somme exacte, O-4) ;
        # lots périmés autorisés en sortie (destruction, mise au rebut, H-D5).
        movements = self._stock().consume(
            document.site_id,
            [
                ConsumptionRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.EXIT,
                    quantity=line.base_quantity,
                    source_type=self.source_type,
                    source_id=document.id,
                    source_line_id=line.id,
                    source_number=document.number,
                    comment=document.number,
                    packaging=packaging_snapshot(line),
                    picks=tuple(LotPick(c.lot_id, c.quantity) for c in line.lots),
                    manual=True,
                )
                for line in document.lines
            ],
            today=tenant_today(self.ctx, self.now),
        )
        # Coût figé = CMUP du site PAR UNITÉ DE BASE (identique pour tous les mouvements d'une
        # ligne : une sortie ne modifie pas le CMUP) ; montant = quantité de base × CMUP.
        costs = {m.source_line_id: m.unit_cost for m in movements}
        for line in document.lines:
            line.unit_cost = costs[line.id]
            line.amount = round_money(line.base_quantity * (line.unit_cost or Decimal("0")))
        document.status = DocumentStatus.VALIDATED
        document.validated_at = self.now
        document.validated_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "validated",
            document,
            {
                "lines": len(document.lines),
                "total": format(
                    sum((line.amount or Decimal("0") for line in document.lines), Decimal("0")), "f"
                ),
                **self._lots_audit(document, movements),
            },
        )
        return document

    def _lots_audit(
        self, document: StockExit, movements: Sequence[StockMovement]
    ) -> dict[str, Any]:
        """Lots consommés, pour l'audit de la validation (la traçabilité reste le journal)."""
        with_lot = [m for m in movements if m.lot_id is not None]
        if not with_lot:
            return {}
        refs = get_article_refs(self.db, {m.article_id for m in with_lot})
        numbers = lot_infos(self.db, {m.lot_id for m in with_lot if m.lot_id})
        return {
            "lots": [
                {
                    "reference": refs[m.article_id].reference,
                    "lot_number": numbers[m.lot_id].number,
                    "base_quantity": format(-m.quantity, "f"),
                }
                for m in with_lot
                if m.lot_id is not None
            ]
        }

    def _line_lots(
        self, document: StockExit, expiry: ExpiryContext | None
    ) -> dict[uuid.UUID, list[LineLotOut]]:
        """Brouillon : choix saisis ; sortie validée ou annulée : répartition réelle lue dans
        le journal des mouvements (source de vérité, M1)."""
        expiry = expiry or expiry_context(self.db, self.ctx, self.now)
        if document.status is DocumentStatus.DRAFT:
            infos = lot_infos(self.db, {c.lot_id for line in document.lines for c in line.lots})
            return {
                line.id: [
                    LineLotOut(
                        lot_id=c.lot_id,
                        lot_number=infos[c.lot_id].number if c.lot_id in infos else "?",
                        expiry_date=infos[c.lot_id].expiry_date if c.lot_id in infos else None,
                        state=expiry.state(infos[c.lot_id].expiry_date)
                        if c.lot_id in infos
                        else None,
                        quantity=c.quantity,
                    )
                    for c in line.lots
                ]
                for line in document.lines
                if line.lots
            }
        return {
            line_id: [
                LineLotOut(
                    lot_id=a.lot_id,
                    lot_number=a.lot_number,
                    expiry_date=a.expiry_date,
                    state=expiry.state(a.expiry_date),
                    quantity=a.quantity,
                )
                for a in allocations
            ]
            for line_id, allocations in lot_allocations(
                self.db, {document.id}, MovementType.EXIT
            ).items()
        }

    def cancel(self, exit_id: uuid.UUID, reason: str) -> StockExit:
        return self._cancel(exit_id, reason, Decimal("1"))

    def to_out(self, documents: Sequence[StockExit], with_lines: bool = False) -> list[ExitOut]:
        names = self._common(documents)
        reasons = _names(self.db, ExitReason, {d.reason_id for d in documents}, ExitReason.label)
        refs = (
            get_article_refs(self.db, {line.article_id for d in documents for line in d.lines})
            if with_lines
            else {}
        )
        return [
            ExitOut(
                **self._base_out(document, names),
                reason_id=document.reason_id,
                reason_label=reasons.get(document.reason_id, ""),
                beneficiary=document.beneficiary,
                reference=document.reference,
                lines=self._lines_out(document, refs) if with_lines else [],
            )
            for document in documents
        ]
