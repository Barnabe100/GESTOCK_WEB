import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.stock.models import ExitReason
from app.modules.stock.schemas import ExitReasonInput
from app.platform.audit.service import audit_action, changes
from app.platform.context import RequestContext
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter, text_sort
from app.shared.schemas import StatusFilter

SORTABLE = {"label": text_sort(ExitReason.label), "created_at": ExitReason.created_at}


class ExitReasonService:
    """Motifs de sortie (SOR-01, Q7). Motifs système : ni supprimés ni renommés ; ils peuvent
    être désactivés (et réactivés) si l'entreprise ne s'en sert pas."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def search(
        self, params: PageParams, search: str | None, status: StatusFilter
    ) -> tuple[list[ExitReason], int]:
        stmt = select(ExitReason)
        condition = search_filter(search, ExitReason.label, ExitReason.description)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(ExitReason.is_active.is_(status is StatusFilter.ACTIVE))
        stmt = apply_sort(stmt, params.sort, SORTABLE, "label", ExitReason.id)
        return paginate(self.db, stmt, params)

    def get(self, reason_id: uuid.UUID) -> ExitReason:
        reason = self.db.get(ExitReason, reason_id)
        if reason is None:
            raise NotFoundError("Motif introuvable", code="exit_reason_not_found")
        return reason

    def _flush(self) -> None:
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Un motif porte déjà ce libellé", code="exit_reason_taken") from exc

    def create(self, data: ExitReasonInput) -> ExitReason:
        reason = ExitReason(tenant_id=self.ctx.tenant_id, **data.model_dump())
        self.db.add(reason)
        self._flush()
        audit_action(
            self.db,
            self.ctx,
            "exit_reason.created",
            entity_type="exit_reason",
            entity_id=reason.id,
            data={"label": reason.label},
        )
        return reason

    def update(self, reason_id: uuid.UUID, data: ExitReasonInput) -> ExitReason:
        reason = self.get(reason_id)
        if reason.is_system:
            raise ForbiddenError("Motif système non modifiable", code="system_exit_reason")
        updates = data.model_dump()
        before = {key: getattr(reason, key) for key in updates}
        for key, value in updates.items():
            setattr(reason, key, value)
        self._flush()
        diff = changes(before, updates)
        if diff:
            audit_action(
                self.db,
                self.ctx,
                "exit_reason.updated",
                entity_type="exit_reason",
                entity_id=reason.id,
                data=diff,
            )
        return reason

    def set_active(self, reason_id: uuid.UUID, active: bool) -> ExitReason:
        reason = self.get(reason_id)
        if reason.is_active != active:
            reason.is_active = active
            audit_action(
                self.db,
                self.ctx,
                "exit_reason.activated" if active else "exit_reason.deactivated",
                entity_type="exit_reason",
                entity_id=reason.id,
                data={"label": reason.label},
            )
        return reason
