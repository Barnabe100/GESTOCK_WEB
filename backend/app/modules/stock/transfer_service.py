"""Transferts inter-sites (Phase 2.5) : BROUILLON → VALIDÉ → ANNULÉ, fonctionnalité de plan
``stock.transfers`` (ADR-0018).

- Le stock ne change qu'à la validation et à l'annulation d'un transfert validé, exclusivement
  via ``StockService`` (``transfer`` puis ``apply_many``), dans la transaction de la requête :
  sortie du site source et entrée du site destination réussissent ou échouent ensemble.
- Le transfert est verrouillé (``SELECT … FOR UPDATE``) avant tout changement de statut : une
  double validation, même simultanée, ne s'applique qu'une fois.
- Sites : deux sites distincts, actifs et accessibles au membre ; la permission de l'opération
  est exigée **sur les deux sites** (un rôle limité à un site n'y suffit pas pour l'autre).
"""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.modules.catalog.api import ArticleRef, get_article_refs
from app.modules.stock.document_service import check_articles
from app.modules.stock.models import (
    DocumentStatus,
    MovementType,
    StockTransfer,
    StockTransferLine,
)
from app.modules.stock.schemas import LineOut, TransferCreate, TransferInput, TransferOut
from app.modules.stock.sites import tenant_today, visible_site_ids
from app.modules.stock.stock_service import (
    MovementRequest,
    StockService,
    TransferItem,
    round_money,
)
from app.platform.audit.service import audit_action
from app.platform.capabilities.service import CapabilityService
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.registry import get_registry
from app.platform.sequences.service import next_number
from app.platform.subscriptions.service import get_subscription
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter

SOURCE_TYPE = "stock_transfer"
AUDIT_PREFIX = "stock_transfer"
NOT_FOUND = "stock_transfer_not_found"
QUANTITY_STEP = Decimal("0.001")


def _names(db: Session, model: Any, ids: set[uuid.UUID | None], column: Any) -> dict[Any, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {
        row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(wanted)))
    }


class TransferService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now
        self._site_permissions: dict[uuid.UUID, frozenset[str]] = {}

    # --- Lecture ------------------------------------------------------------------------------

    def _visible(self) -> Select[tuple[StockTransfer]]:
        """Transferts dont un site est visible (site sélectionné, sinon sites du membre) et
        dont les DEUX sites lui sont accessibles."""
        visible = visible_site_ids(self.ctx)
        accessible = self.ctx.capabilities.accessible_site_ids
        return select(StockTransfer).where(
            or_(
                StockTransfer.source_site_id.in_(visible),
                StockTransfer.destination_site_id.in_(visible),
            ),
            StockTransfer.source_site_id.in_(accessible),
            StockTransfer.destination_site_id.in_(accessible),
        )

    def search(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        status: DocumentStatus | None = None,
        source_site_id: uuid.UUID | None = None,
        destination_site_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[StockTransfer], int]:
        stmt = self._visible()
        conditions: list[Any] = [
            search_filter(search, StockTransfer.number),
            StockTransfer.status == status if status else None,
            StockTransfer.source_site_id == source_site_id if source_site_id else None,
            (
                StockTransfer.destination_site_id == destination_site_id
                if destination_site_id
                else None
            ),
            StockTransfer.operation_date >= date_from if date_from else None,
            StockTransfer.operation_date <= date_to if date_to else None,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        sortable = {
            "number": StockTransfer.number,
            "operation_date": StockTransfer.operation_date,
            "created_at": StockTransfer.created_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "-number", StockTransfer.id)
        return paginate(self.db, stmt, params)

    def get(self, transfer_id: uuid.UUID, *, lock: bool = False) -> StockTransfer:
        stmt = self._visible().where(StockTransfer.id == transfer_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        transfer = self.db.scalars(stmt).one_or_none()
        if transfer is None:
            raise NotFoundError("Transfert introuvable", code=NOT_FOUND)
        return transfer

    # --- Règles -------------------------------------------------------------------------------

    def _permissions_on(self, site_id: uuid.UUID) -> frozenset[str]:
        """Permissions du membre sur un site donné (rôles du tenant + rôles de ce site)."""
        if site_id not in self._site_permissions:
            subscription = get_subscription(self.db)
            if subscription is None:
                return frozenset()
            capabilities = CapabilityService(self.db, get_registry()).resolve(
                tenant=self.ctx.tenant,
                membership=self.ctx.membership,
                subscription=subscription,
                site_id=site_id,
                now=self.now,
            )
            self._site_permissions[site_id] = capabilities.permissions
        return self._site_permissions[site_id]

    def _check_sites(self, source: uuid.UUID, destination: uuid.UUID, permission: str) -> None:
        """Deux sites distincts, actifs, accessibles ; le site sélectionné (X-Site-Id) est l'un
        des deux ; la permission est détenue sur chacun."""
        if source == destination:
            raise BusinessRuleError(
                "Le site destination doit être différent du site source",
                code="same_site_transfer",
            )
        accessible = self.ctx.capabilities.accessible_site_ids
        for site_id in (source, destination):
            # Site inactif, inconnu, d'un autre tenant ou hors du périmètre du membre.
            if site_id not in accessible:
                raise ForbiddenError(
                    "Accès à ce site refusé",
                    code="site_access_denied",
                    extra={"site_id": str(site_id)},
                )
        selected = self.ctx.site.id if self.ctx.site is not None else None
        if selected is not None and selected not in (source, destination):
            raise ForbiddenError(
                "Le transfert ne concerne pas le site sélectionné", code="site_mismatch"
            )
        if selected is None:
            return  # permission vérifiée sur tout le tenant : elle vaut pour chaque site
        for site_id in (source, destination):
            if site_id != selected and permission not in self._permissions_on(site_id):
                raise ForbiddenError(
                    "Permission insuffisante sur l'autre site du transfert",
                    code="site_permission_denied",
                    extra={"site_id": str(site_id), "permission": permission},
                )

    def _operation_date(self, value: date | None) -> date:
        today = tenant_today(self.ctx, self.now)
        chosen = value or today
        if chosen > today:
            raise BusinessRuleError(
                "La date de l'opération ne peut pas être postérieure à aujourd'hui",
                code="future_operation_date",
            )
        return chosen

    def _require_draft(self, transfer: StockTransfer) -> None:
        if transfer.status is not DocumentStatus.DRAFT:
            raise ConflictError(
                "Opération impossible dans l'état actuel du transfert", code="document_not_draft"
            )

    def _snapshot(self, transfer: StockTransfer) -> dict[str, Any]:
        refs = get_article_refs(self.db, {line.article_id for line in transfer.lines})
        return {
            "source_site_id": str(transfer.source_site_id),
            "destination_site_id": str(transfer.destination_site_id),
            "operation_date": transfer.operation_date.isoformat(),
            "comment": transfer.comment,
            "lines": [
                {
                    "article_id": str(line.article_id),
                    "reference": refs[line.article_id].reference
                    if line.article_id in refs
                    else None,
                    "quantity": format(line.quantity, "f"),
                }
                for line in transfer.lines
            ],
        }

    def _audit(self, action: str, transfer: StockTransfer, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"{AUDIT_PREFIX}.{action}",
            entity_type=AUDIT_PREFIX,
            entity_id=transfer.id,
            site_id=transfer.source_site_id,
            data={"number": transfer.number, **data},
        )

    def _stock(self) -> StockService:
        return StockService(self.db, self.ctx.tenant_id, self.ctx.user.id, self.now)

    def _apply_input(self, transfer: StockTransfer, data: TransferInput) -> None:
        check_articles(self.db, [line.article_id for line in data.lines])
        transfer.destination_site_id = data.destination_site_id
        transfer.operation_date = self._operation_date(data.operation_date)
        transfer.comment = data.comment
        transfer.lines = [
            StockTransferLine(
                tenant_id=self.ctx.tenant_id,
                line_no=index,
                article_id=line.article_id,
                # NUMERIC(18,3) : même valeur en réponse, en audit et en base.
                quantity=line.quantity.quantize(QUANTITY_STEP),
            )
            for index, line in enumerate(data.lines, start=1)
        ]

    # --- Cycle de vie -------------------------------------------------------------------------

    def create(self, data: TransferCreate) -> StockTransfer:
        source = data.source_site_id or (self.ctx.site.id if self.ctx.site else None)
        if source is None:
            raise BusinessRuleError("Choisissez le site source", code="site_required")
        self._check_sites(source, data.destination_site_id, "stock.transfer.create")
        transfer = StockTransfer(
            tenant_id=self.ctx.tenant_id,
            number=next_number(self.db, self.ctx.tenant_id, "stock_transfer", "TRF"),
            source_site_id=source,
            status=DocumentStatus.DRAFT,
            created_by=self.ctx.user.id,
        )
        self._apply_input(transfer, data)
        self.db.add(transfer)
        self.db.flush()
        self._audit(
            "created", transfer, {"status": transfer.status.value, **self._snapshot(transfer)}
        )
        return transfer

    def update(self, transfer_id: uuid.UUID, data: TransferInput) -> StockTransfer:
        """Brouillon seulement ; le site source est fixé à la création."""
        transfer = self.get(transfer_id, lock=True)
        self._require_draft(transfer)
        self._check_sites(
            transfer.source_site_id, data.destination_site_id, "stock.transfer.update"
        )
        before = self._snapshot(transfer)
        transfer.lines = []
        self.db.flush()  # remplacement des lignes d'un brouillon
        self._apply_input(transfer, data)
        self.db.flush()
        after = self._snapshot(transfer)
        if before != after:
            self._audit("updated", transfer, {"before": before, "after": after})
        return transfer

    def validate(self, transfer_id: uuid.UUID) -> StockTransfer:
        """Sortie du site source et entrée du site destination en une transaction : tout le
        stock source est contrôlé avant la moindre écriture ; tout échec annule l'ensemble."""
        transfer = self.get(transfer_id, lock=True)
        self._require_draft(transfer)
        if not transfer.lines:
            raise BusinessRuleError("Aucune ligne à valider", code="document_empty")
        self._check_sites(
            transfer.source_site_id, transfer.destination_site_id, "stock.transfer.validate"
        )
        check_articles(self.db, [line.article_id for line in transfer.lines])
        pairs = self._stock().transfer(
            transfer.source_site_id,
            transfer.destination_site_id,
            [
                TransferItem(line_id=line.id, article_id=line.article_id, quantity=line.quantity)
                for line in transfer.lines
            ],
            source_type=SOURCE_TYPE,
            source_id=transfer.id,
            source_number=transfer.number,
        )
        for line, (outgoing, _) in zip(transfer.lines, pairs, strict=True):
            line.unit_cost = outgoing.unit_cost
            line.amount = round_money(line.quantity * (outgoing.unit_cost or Decimal("0")))
        transfer.status = DocumentStatus.VALIDATED
        transfer.validated_at = self.now
        transfer.validated_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "validated",
            transfer,
            {
                "previous_status": DocumentStatus.DRAFT.value,
                "status": DocumentStatus.VALIDATED.value,
                **self._snapshot(transfer),
                "total": format(self._total(transfer) or Decimal("0"), "f"),
            },
        )
        return transfer

    def cancel(self, transfer_id: uuid.UUID, reason: str) -> StockTransfer:
        """Brouillon : abandon, sans effet sur le stock. Transfert validé : mouvements inverses
        ``CANCELLATION`` au coût du transfert — retrait du site destination, remise sur le site
        source — CMUP inchangés (STK-06) ; refus total si le stock destination ne suffit plus.
        Les mouvements d'origine ne sont jamais modifiés."""
        transfer = self.get(transfer_id, lock=True)
        previous = transfer.status
        if previous is DocumentStatus.CANCELLED:
            raise ConflictError("Transfert déjà annulé", code="transfer_already_cancelled")
        self._check_sites(
            transfer.source_site_id, transfer.destination_site_id, "stock.transfer.cancel"
        )
        if previous is DocumentStatus.VALIDATED:
            stock = self._stock()
            outgoing = stock.movements_of(transfer.id, MovementType.TRANSFER_OUT)
            incoming = stock.movements_of(transfer.id, MovementType.TRANSFER_IN)
            comment = f"Annulation {transfer.number}"
            requests: list[tuple[uuid.UUID, MovementRequest]] = []
            for line in transfer.lines:
                # Retrait du site destination (−q), puis remise sur le site source (+q).
                removal = MovementRequest(
                    article_id=line.article_id,
                    movement_type=MovementType.CANCELLATION,
                    quantity=-line.quantity,
                    unit_cost=line.unit_cost,
                    source_type=SOURCE_TYPE,
                    source_id=transfer.id,
                    source_line_id=line.id,
                    source_number=transfer.number,
                    origin_movement_id=incoming[line.id].id,
                    comment=comment,
                )
                restore = replace(
                    removal, quantity=line.quantity, origin_movement_id=outgoing[line.id].id
                )
                requests.append((transfer.destination_site_id, removal))
                requests.append((transfer.source_site_id, restore))
            stock.apply_many(requests)
        transfer.status = DocumentStatus.CANCELLED
        transfer.cancelled_at = self.now
        transfer.cancelled_by = self.ctx.user.id
        transfer.cancellation_reason = reason
        self.db.flush()
        self._audit(
            "cancelled",
            transfer,
            {
                "previous_status": previous.value,
                "status": DocumentStatus.CANCELLED.value,
                "reason": reason,
                "stock_restored": previous is DocumentStatus.VALIDATED,
                **self._snapshot(transfer),
            },
        )
        return transfer

    # --- Sortie API -----------------------------------------------------------------------------

    @staticmethod
    def _total(transfer: StockTransfer) -> Decimal | None:
        amounts = [line.amount for line in transfer.lines]
        if not amounts or any(a is None for a in amounts):
            return None
        return round_money(sum((a for a in amounts if a is not None), Decimal("0")))

    def to_out(
        self, transfers: Sequence[StockTransfer], *, with_lines: bool = False
    ) -> list[TransferOut]:
        users: set[uuid.UUID | None] = {
            u for t in transfers for u in (t.created_by, t.validated_by, t.cancelled_by)
        }
        user_names = _names(self.db, User, users, User.full_name)
        sites: set[uuid.UUID | None] = {
            s for t in transfers for s in (t.source_site_id, t.destination_site_id)
        }
        site_names = _names(self.db, Site, sites, Site.name)
        refs: dict[uuid.UUID, ArticleRef] = (
            get_article_refs(self.db, {line.article_id for t in transfers for line in t.lines})
            if with_lines
            else {}
        )
        return [
            TransferOut(
                id=t.id,
                number=t.number,
                source_site_id=t.source_site_id,
                source_site_name=site_names.get(t.source_site_id, ""),
                destination_site_id=t.destination_site_id,
                destination_site_name=site_names.get(t.destination_site_id, ""),
                status=t.status,
                operation_date=t.operation_date,
                comment=t.comment,
                total_amount=self._total(t),
                line_count=len(t.lines),
                created_at=t.created_at,
                created_by_name=user_names.get(t.created_by) if t.created_by else None,
                validated_at=t.validated_at,
                validated_by_name=user_names.get(t.validated_by) if t.validated_by else None,
                cancelled_at=t.cancelled_at,
                cancelled_by_name=user_names.get(t.cancelled_by) if t.cancelled_by else None,
                cancellation_reason=t.cancellation_reason,
                lines=[_line_out(line, refs) for line in t.lines] if with_lines else [],
            )
            for t in transfers
        ]


def _line_out(line: StockTransferLine, refs: dict[uuid.UUID, ArticleRef]) -> LineOut:
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
