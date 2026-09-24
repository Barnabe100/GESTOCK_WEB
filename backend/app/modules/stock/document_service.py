"""Entrées et sorties de stock : cycle BROUILLON → VALIDÉE → ANNULÉE (ENT-*, SOR-*).

Le stock n'est modifié qu'à la validation et à l'annulation, exclusivement via
``StockService``, dans la transaction de la requête. Le document est verrouillé
(``SELECT … FOR UPDATE``) avant tout changement de statut : deux validations simultanées ne
peuvent pas s'appliquer deux fois.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import ArticleRef, get_article_refs
from app.modules.stock.models import (
    DocumentStatus,
    EntryKind,
    ExitReason,
    MovementType,
    StockEntry,
    StockEntryLine,
    StockExit,
    StockExitLine,
    StockMovement,
)
from app.modules.stock.schemas import (
    EntryCreate,
    EntryInput,
    EntryOut,
    ExitCreate,
    ExitInput,
    ExitOut,
    LineOut,
)
from app.modules.stock.sites import (
    ensure_document_site,
    operation_site,
    tenant_today,
    visible_site_ids,
)
from app.modules.stock.stock_service import MovementRequest, StockService, round_money
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
    ) -> tuple[list[Doc], int]:
        stmt = self._base_query()
        conditions: list[Any] = [
            search_filter(search, self.model.number, *search_columns),
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

    def _articles(self, article_ids: list[uuid.UUID]) -> dict[uuid.UUID, ArticleRef]:
        """Articles des lignes : existants, actifs (ART-13), sans doublon."""
        if len(set(article_ids)) != len(article_ids):
            raise BusinessRuleError(
                "Un article apparaît sur plusieurs lignes", code="duplicate_article_line"
            )
        refs = get_article_refs(self.db, set(article_ids))
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
        return refs

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

    def _origin_movements(self, document: Doc) -> dict[uuid.UUID, StockMovement]:
        rows = self.db.scalars(
            select(StockMovement).where(
                StockMovement.source_id == document.id,
                StockMovement.movement_type != MovementType.CANCELLATION,
            )
        )
        return {m.source_line_id: m for m in rows}

    def _cancel(self, document_id: uuid.UUID, reason: str, sign: Decimal) -> Doc:
        """Mouvement inverse par ligne ; refus total si un stock devenait négatif (ENT-08)."""
        document = self.get(document_id, lock=True)
        self._require_status(document, DocumentStatus.VALIDATED, "document_not_validated")
        origins = self._origin_movements(document)
        self._stock().apply(
            document.site_id,
            [
                MovementRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.CANCELLATION,
                    quantity=sign * line.quantity,
                    unit_cost=line.unit_cost,
                    source_type=self.source_type,
                    source_id=document.id,
                    source_line_id=line.id,
                    source_number=document.number,
                    origin_movement_id=origins[line.id].id if line.id in origins else None,
                    comment=f"Annulation {document.number}",
                )
                for line in document.lines
            ],
        )
        document.status = DocumentStatus.CANCELLED
        document.cancelled_at = self.now
        document.cancelled_by = self.ctx.user.id
        document.cancellation_reason = reason
        self.db.flush()
        self._audit("cancelled", document, {"reason": reason})
        return document

    # --- Sortie API -----------------------------------------------------------------------------

    def _line_out(self, line: Any, refs: dict[uuid.UUID, ArticleRef]) -> LineOut:
        ref = refs.get(line.article_id)
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

    def _apply_input(self, entry: StockEntry, data: EntryInput) -> None:
        self._check_supplier(data.kind, data.supplier_id)
        self._articles([line.article_id for line in data.lines])
        entry.kind = data.kind
        entry.operation_date = self._operation_date(data.operation_date)
        entry.supplier_id = data.supplier_id
        entry.document_reference = data.document_reference
        entry.comment = data.comment
        entry.lines = [
            StockEntryLine(
                tenant_id=self.ctx.tenant_id,
                line_no=index,
                article_id=line.article_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                amount=round_money(line.quantity * line.unit_cost),
            )
            for index, line in enumerate(data.lines, start=1)
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
        self._articles([line.article_id for line in entry.lines])
        self._stock().apply(
            entry.site_id,
            [
                MovementRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.ENTRY,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost,
                    source_type=self.source_type,
                    source_id=entry.id,
                    source_line_id=line.id,
                    source_number=entry.number,
                    comment=entry.number,
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
            },
        )
        return entry

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
        return [
            EntryOut(
                **self._base_out(entry, names),
                kind=entry.kind,
                supplier_id=entry.supplier_id,
                supplier_name=suppliers.get(entry.supplier_id) if entry.supplier_id else None,
                document_reference=entry.document_reference,
                lines=[self._line_out(line, refs) for line in entry.lines] if with_lines else [],
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
        self._articles([line.article_id for line in data.lines])
        document.operation_date = self._operation_date(data.operation_date)
        document.reason_id = data.reason_id
        document.beneficiary = data.beneficiary
        document.reference = data.reference
        document.comment = data.comment
        document.lines = [
            StockExitLine(
                tenant_id=self.ctx.tenant_id,
                line_no=index,
                article_id=line.article_id,
                quantity=line.quantity,
            )
            for index, line in enumerate(data.lines, start=1)
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
        self._articles([line.article_id for line in document.lines])
        movements = self._stock().apply(
            document.site_id,
            [
                MovementRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.EXIT,
                    quantity=-line.quantity,
                    source_type=self.source_type,
                    source_id=document.id,
                    source_line_id=line.id,
                    source_number=document.number,
                    comment=document.number,
                )
                for line in document.lines
            ],
        )
        for line, movement in zip(document.lines, movements, strict=True):
            line.unit_cost = movement.unit_cost
            line.amount = round_money(line.quantity * (movement.unit_cost or Decimal("0")))
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
            },
        )
        return document

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
                lines=[self._line_out(line, refs) for line in document.lines] if with_lines else [],
            )
            for document in documents
        ]
