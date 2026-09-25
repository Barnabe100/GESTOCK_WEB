"""Caisse (Phase 2.9, ADR-0022) : caisses d'un site, sessions (ouverture / clôture) et
mouvements (fond initial, encaissements espèces des ventes, entrées et sorties manuelles).

- **Solde calculé** : solde théorique = Σ mouvements signés de la session (fond initial
  compris) ; jamais une valeur mutable. Figé à la clôture avec le montant compté et l'écart.
- **Une session ouverte par caisse** : verrou de la caisse à l'ouverture + index unique
  partiel en base.
- **Verrou de la session** (``SELECT … FOR UPDATE``) pour tout mouvement et pour la clôture :
  sorties, encaissements et clôture concurrents s'exécutent l'un après l'autre ; une sortie ne
  peut jamais rendre le solde négatif ; aucun mouvement après la clôture.
- **Encaissement espèces d'une vente** : créé par ``PaymentService`` dans la transaction du
  paiement (API publique ``cash_register.api``) ; tout ou rien.
- Ordre des verrous constant : vente → paiement → session (la clôture ne verrouille que la
  session) : aucun interblocage.

Le service ne valide jamais la transaction (ADR-0008) ; audit dans la même transaction.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.cash_register.models import (
    IN_CATEGORIES,
    INFLOWS,
    MANUAL,
    OUT_CATEGORIES,
    OUTFLOWS,
    CashMovement,
    CashMovementType,
    CashRegister,
    CashSession,
    CashSessionStatus,
)
from app.modules.cash_register.schemas import (
    CashMovementCreate,
    CashMovementOut,
    CashRegisterCreate,
    CashRegisterOut,
    CashRegisterUpdate,
    CashSessionOut,
    CurrentSessionOut,
)
from app.modules.stock.api import ensure_document_site, filter_site_ids, operation_site
from app.platform.audit.service import audit_action, changes
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.sequences.service import next_number
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter, text_sort
from app.shared.schemas import StatusFilter

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
REGISTER_NOT_FOUND = "cash_register_not_found"
SESSION_NOT_FOUND = "cash_session_not_found"


def signed(amount: Any, movement_type: Any) -> Any:
    """Montant signé (SQL) : + entrée, − sortie."""
    return case((movement_type.in_(OUTFLOWS), -amount), else_=amount)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


@dataclass(frozen=True)
class SessionTotals:
    cash_in: Decimal  # entrées hors fond initial
    cash_out: Decimal
    balance: Decimal  # solde théorique (fond initial compris)
    count: int


def session_totals(db: Session, session_ids: set[uuid.UUID]) -> dict[uuid.UUID, SessionTotals]:
    """Totaux des sessions en UNE agrégation (aucun N+1)."""
    if not session_ids:
        return {}
    m = CashMovement
    rows = db.execute(
        select(
            m.cash_session_id,
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                m.movement_type.in_(INFLOWS),
                                m.movement_type != CashMovementType.OPENING_FLOAT,
                            ),
                            m.amount,
                        ),
                        else_=ZERO,
                    )
                ),
                ZERO,
            ),
            func.coalesce(
                func.sum(case((m.movement_type.in_(OUTFLOWS), m.amount), else_=ZERO)), ZERO
            ),
            func.coalesce(func.sum(signed(m.amount, m.movement_type)), ZERO),
            func.count(),
        )
        .where(m.cash_session_id.in_(session_ids))
        .group_by(m.cash_session_id)
    ).all()
    return {r[0]: SessionTotals(r[1], r[2], r[3], r[4]) for r in rows}


def _names(db: Session, model: Any, ids: set[Any], column: Any) -> dict[Any, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(ids)))}


REGISTER_SORT = {
    "code": CashRegister.code,
    "name": text_sort(CashRegister.name),
    "created_at": CashRegister.created_at,
}
SESSION_SORT = {
    "number": CashSession.number,
    "opened_at": CashSession.opened_at,
    "closed_at": CashSession.closed_at,
}


class CashService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Outils -------------------------------------------------------------------------------

    def _audit(
        self, action: str, entity_type: str, entity_id: uuid.UUID, site_id: uuid.UUID, **data: Any
    ) -> None:
        audit_action(
            self.db,
            self.ctx,
            action,
            entity_type=entity_type,
            entity_id=entity_id,
            site_id=site_id,
            data=data,
        )

    def _day_bounds(self, date_from: date | None, date_to: date | None) -> tuple[Any, Any]:
        """Période en dates du fuseau du tenant → bornes horodatées."""
        tz = ZoneInfo(self.ctx.tenant.timezone)
        start = datetime.combine(date_from, time.min, tz) if date_from else None
        end = datetime.combine(date_to + timedelta(days=1), time.min, tz) if date_to else None
        return start, end

    # --- Caisses ------------------------------------------------------------------------------

    def search_registers(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        site_id: uuid.UUID | None = None,
        status: StatusFilter = StatusFilter.ALL,
    ) -> tuple[list[CashRegister], int]:
        stmt: Select[tuple[CashRegister]] = select(CashRegister).where(
            CashRegister.site_id.in_(filter_site_ids(self.ctx, site_id))
        )
        condition = search_filter(search, CashRegister.code, CashRegister.name)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(CashRegister.is_active.is_(status is StatusFilter.ACTIVE))
        stmt = apply_sort(stmt, params.sort, REGISTER_SORT, "code", CashRegister.id)
        return paginate(self.db, stmt, params)

    def get_register(self, register_id: uuid.UUID, *, lock: bool = False) -> CashRegister:
        stmt = select(CashRegister).where(CashRegister.id == register_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        register = self.db.scalars(stmt).one_or_none()
        if register is None:
            raise NotFoundError("Caisse introuvable", code=REGISTER_NOT_FOUND)
        ensure_document_site(self.ctx, register.site_id, REGISTER_NOT_FOUND)
        return register

    def create_register(self, data: CashRegisterCreate) -> CashRegister:
        site_id = operation_site(self.ctx, data.site_id)
        register = CashRegister(
            tenant_id=self.ctx.tenant_id,
            code=next_number(self.db, self.ctx.tenant_id, "cash_register", "CAI", width=3),
            site_id=site_id,
            name=data.name,
            description=data.description,
            is_active=True,
            created_by=self.ctx.user.id,
        )
        self.db.add(register)
        self.db.flush()
        self._audit(
            "cash_register.created",
            "cash_register",
            register.id,
            site_id,
            code=register.code,
            name=register.name,
        )
        return register

    def update_register(self, register_id: uuid.UUID, data: CashRegisterUpdate) -> CashRegister:
        register = self.get_register(register_id, lock=True)
        updates = data.model_dump(exclude_unset=True)
        if "name" in updates and updates["name"] is None:
            del updates["name"]
        before = {key: getattr(register, key) for key in updates}
        for key, value in updates.items():
            setattr(register, key, value)
        self.db.flush()
        diff = changes(before, updates)
        if diff:
            self._audit(
                "cash_register.updated",
                "cash_register",
                register.id,
                register.site_id,
                code=register.code,
                **diff,
            )
        return register

    def set_register_active(self, register_id: uuid.UUID, active: bool) -> CashRegister:
        register = self.get_register(register_id, lock=True)
        if register.is_active == active:
            return register
        if not active and self._open_session_of(register.id) is not None:
            raise ConflictError(
                "Clôturez d'abord la session ouverte de cette caisse",
                code="cash_register_has_open_session",
            )
        register.is_active = active
        self.db.flush()
        self._audit(
            "cash_register.activated" if active else "cash_register.deactivated",
            "cash_register",
            register.id,
            register.site_id,
            code=register.code,
            name=register.name,
        )
        return register

    def _open_session_of(self, register_id: uuid.UUID) -> CashSession | None:
        return self.db.scalars(
            select(CashSession).where(
                CashSession.cash_register_id == register_id,
                CashSession.status == CashSessionStatus.OPEN,
            )
        ).one_or_none()

    def registers_out(self, registers: Sequence[CashRegister]) -> list[CashRegisterOut]:
        sites = _names(self.db, Site, {r.site_id for r in registers}, Site.name)
        sessions = {
            s.cash_register_id: s
            for s in self.db.scalars(
                select(CashSession).where(
                    CashSession.cash_register_id.in_({r.id for r in registers}),
                    CashSession.status == CashSessionStatus.OPEN,
                )
            )
        }
        totals = session_totals(self.db, {s.id for s in sessions.values()})
        users = _names(self.db, User, {s.opened_by for s in sessions.values()}, User.full_name)
        result = []
        for r in registers:
            session = sessions.get(r.id)
            current = None
            if session is not None:
                total = totals.get(session.id)
                current = CurrentSessionOut(
                    id=session.id,
                    number=session.number,
                    opened_at=session.opened_at,
                    opened_by_name=users.get(session.opened_by),
                    opening_float=session.opening_float,
                    theoretical_balance=total.balance if total else ZERO,
                )
            result.append(
                CashRegisterOut(
                    id=r.id,
                    code=r.code,
                    name=r.name,
                    description=r.description,
                    site_id=r.site_id,
                    site_name=sites.get(r.site_id, ""),
                    is_active=r.is_active,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    current_session=current,
                )
            )
        return result

    # --- Sessions -----------------------------------------------------------------------------

    def search_sessions(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        register_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        status: CashSessionStatus | None = None,
        opened_by: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[CashSession], int]:
        stmt: Select[tuple[CashSession]] = select(CashSession).where(
            CashSession.site_id.in_(filter_site_ids(self.ctx, site_id))
        )
        condition = search_filter(search, CashSession.number)
        if condition is not None:
            stmt = stmt.where(condition)
        start, end = self._day_bounds(date_from, date_to)
        for extra in (
            CashSession.cash_register_id == register_id if register_id else None,
            CashSession.status == status if status else None,
            CashSession.opened_by == opened_by if opened_by else None,
            CashSession.opened_at >= start if start is not None else None,
            CashSession.opened_at < end if end is not None else None,
        ):
            if extra is not None:
                stmt = stmt.where(extra)
        stmt = apply_sort(stmt, params.sort, SESSION_SORT, "-opened_at", CashSession.id)
        return paginate(self.db, stmt, params)

    def get_session(self, session_id: uuid.UUID, *, lock: bool = False) -> CashSession:
        stmt = select(CashSession).where(CashSession.id == session_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        session = self.db.scalars(stmt).one_or_none()
        if session is None:
            raise NotFoundError("Session de caisse introuvable", code=SESSION_NOT_FOUND)
        ensure_document_site(self.ctx, session.site_id, SESSION_NOT_FOUND)
        return session

    def balance(self, session_id: uuid.UUID) -> Decimal:
        totals = session_totals(self.db, {session_id}).get(session_id)
        return totals.balance if totals else ZERO

    def open_session(self, register_id: uuid.UUID, opening_float: Decimal) -> CashSession:
        """Ouverture : verrou de la caisse (deux ouvertures simultanées s'exécutent l'une après
        l'autre ; la seconde trouve la session ouverte et échoue). Le fond initial est figé et
        enregistré comme premier mouvement (traçable)."""
        register = self.get_register(register_id, lock=True)
        if not register.is_active:
            raise ConflictError("Cette caisse est désactivée", code="cash_register_inactive")
        existing = self._open_session_of(register.id)
        if existing is not None:
            raise ConflictError(
                "Une session est déjà ouverte sur cette caisse",
                code="cash_session_already_open",
                extra={"session_number": existing.number},
            )
        amount = _money(opening_float)
        session = CashSession(
            tenant_id=self.ctx.tenant_id,
            number=next_number(self.db, self.ctx.tenant_id, "cash_session", "SES"),
            cash_register_id=register.id,
            site_id=register.site_id,
            status=CashSessionStatus.OPEN,
            opening_float=amount,
            opened_at=self.now,
            opened_by=self.ctx.user.id,
        )
        self.db.add(session)
        self.db.flush()
        if amount > 0:
            self._add_movement(session, CashMovementType.OPENING_FLOAT, amount)
        self._audit(
            "cash_session.opened",
            "cash_session",
            session.id,
            session.site_id,
            number=session.number,
            cash_register_id=str(register.id),
            cash_register_code=register.code,
            opening_float=format(amount, "f"),
            opened_at=self.now.isoformat(),
        )
        return session

    def close_session(
        self, session_id: uuid.UUID, counted: Decimal, note: str | None
    ) -> CashSession:
        """Clôture atomique : verrou de la session (aucun mouvement concurrent), solde théorique
        recalculé, montant compté enregistré, écart = compté − théorique calculé par le serveur.
        Aucun mouvement d'écart n'est créé (décision future)."""
        session = self.get_session(session_id, lock=True)
        self._require_open(session)
        theoretical = self.balance(session.id)
        counted = _money(counted)
        session.status = CashSessionStatus.CLOSED
        session.closed_at = self.now
        session.closed_by = self.ctx.user.id
        session.theoretical_balance = theoretical
        session.counted_balance = counted
        session.variance = counted - theoretical
        session.closing_note = note
        self.db.flush()
        self._audit(
            "cash_session.closed",
            "cash_session",
            session.id,
            session.site_id,
            number=session.number,
            cash_register_id=str(session.cash_register_id),
            theoretical_balance=format(theoretical, "f"),
            counted_balance=format(counted, "f"),
            variance=format(session.variance, "f"),
            note=note,
        )
        return session

    @staticmethod
    def _require_open(session: CashSession) -> None:
        if session.status is not CashSessionStatus.OPEN:
            raise ConflictError("Cette session de caisse est clôturée", code="cash_session_closed")

    def sessions_out(self, sessions: Sequence[CashSession]) -> list[CashSessionOut]:
        registers = {
            r.id: r
            for r in self.db.scalars(
                select(CashRegister).where(
                    CashRegister.id.in_({s.cash_register_id for s in sessions})
                )
            )
        }
        sites = _names(self.db, Site, {s.site_id for s in sessions}, Site.name)
        users = _names(
            self.db, User, {u for s in sessions for u in (s.opened_by, s.closed_by)}, User.full_name
        )
        totals = session_totals(self.db, {s.id for s in sessions})
        result = []
        for s in sessions:
            register = registers[s.cash_register_id]
            total = totals.get(s.id, SessionTotals(ZERO, ZERO, ZERO, 0))
            closed = s.status is CashSessionStatus.CLOSED
            result.append(
                CashSessionOut(
                    id=s.id,
                    number=s.number,
                    cash_register_id=register.id,
                    cash_register_code=register.code,
                    cash_register_name=register.name,
                    site_id=s.site_id,
                    site_name=sites.get(s.site_id, ""),
                    status=s.status,
                    opening_float=s.opening_float,
                    opened_at=s.opened_at,
                    opened_by_name=users.get(s.opened_by),
                    closed_at=s.closed_at,
                    closed_by_name=users.get(s.closed_by),
                    cash_in_total=total.cash_in,
                    cash_out_total=total.cash_out,
                    theoretical_balance=(
                        s.theoretical_balance
                        if closed and s.theoretical_balance is not None
                        else total.balance
                    ),
                    counted_balance=s.counted_balance,
                    variance=s.variance,
                    closing_note=s.closing_note,
                    movement_count=total.count,
                )
            )
        return result

    # --- Mouvements ---------------------------------------------------------------------------

    def _add_movement(
        self, session: CashSession, movement_type: CashMovementType, amount: Decimal, **extra: Any
    ) -> CashMovement:
        movement = CashMovement(
            tenant_id=self.ctx.tenant_id,
            cash_session_id=session.id,
            cash_register_id=session.cash_register_id,
            site_id=session.site_id,
            movement_type=movement_type,
            amount=amount,
            occurred_at=self.now,
            created_by=self.ctx.user.id,
            **extra,
        )
        self.db.add(movement)
        self.db.flush()
        return movement

    def _ensure_available(self, session: CashSession, amount: Decimal) -> None:
        """Une sortie ne rend jamais le solde théorique négatif (session verrouillée)."""
        available = self.balance(session.id)
        if amount > available:
            raise BusinessRuleError(
                "Solde de caisse insuffisant pour cette sortie",
                code="cash_insufficient_balance",
                extra={"balance": format(available, "f")},
            )

    def manual_movement(
        self, session_id: uuid.UUID, data: CashMovementCreate
    ) -> tuple[CashMovement, bool]:
        """Entrée ou sortie manuelle motivée. Renvoie ``(mouvement, rejoué)``."""
        if data.movement_type not in MANUAL:
            raise BusinessRuleError(
                "Seules les entrées et sorties manuelles peuvent être saisies",
                code="cash_movement_type_invalid",
            )
        allowed = (
            IN_CATEGORIES
            if data.movement_type is CashMovementType.MANUAL_CASH_IN
            else (OUT_CATEGORIES)
        )
        if data.category not in allowed:
            raise BusinessRuleError(
                "Nature incompatible avec le sens du mouvement",
                code="cash_movement_category_invalid",
            )
        # Verrou de la session : sorties, encaissements et clôture concurrents sérialisés.
        session = self.get_session(session_id, lock=True)
        if data.idempotency_key is not None:
            existing = self.db.scalars(
                select(CashMovement).where(CashMovement.idempotency_key == data.idempotency_key)
            ).one_or_none()
            if existing is not None:
                if (
                    existing.cash_session_id == session.id
                    and existing.movement_type is data.movement_type
                    and existing.amount == data.amount
                ):
                    return existing, True
                raise ConflictError(
                    "Clé d'idempotence déjà utilisée pour un autre mouvement",
                    code="idempotency_key_reused",
                )
        self._require_open(session)
        amount = _money(data.amount)
        if data.movement_type is CashMovementType.MANUAL_CASH_OUT:
            self._ensure_available(session, amount)
        movement = self._add_movement(
            session,
            data.movement_type,
            amount,
            category=data.category,
            reason=data.reason,
            reference=data.reference,
            idempotency_key=data.idempotency_key,
        )
        self._audit(
            "cash_movement.created",
            "cash_movement",
            movement.id,
            session.site_id,
            session_number=session.number,
            cash_register_id=str(session.cash_register_id),
            movement_type=movement.movement_type.value,
            amount=format(amount, "f"),
            category=data.category.value,
            reason=data.reason,
            reference=data.reference,
            balance_after=format(self.balance(session.id), "f"),
        )
        return movement, False

    def search_movements(
        self,
        params: PageParams,
        *,
        session_id: uuid.UUID | None = None,
        register_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        movement_type: CashMovementType | None = None,
        created_by: uuid.UUID | None = None,
        search: str | None = None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[Any], int]:
        """Journal : mouvements des sites visibles, avec le solde de la session après chaque
        mouvement (fenêtre sur TOUS les mouvements de la session, avant filtrage)."""
        m = CashMovement
        running = (
            select(
                m.id.label("movement_id"),
                func.sum(signed(m.amount, m.movement_type))
                .over(partition_by=m.cash_session_id, order_by=(m.occurred_at, m.id))
                .label("balance_after"),
            )
            .where(m.site_id.in_(filter_site_ids(self.ctx, site_id)))
            .subquery("running")
        )
        stmt: Select[Any] = select(m, running.c.balance_after).join(
            running, running.c.movement_id == m.id
        )
        text_match = search_filter(search, m.reference, m.source_number, m.reason)
        start, end = self._day_bounds(date_from, date_to)
        for extra in (
            m.cash_session_id == session_id if session_id else None,
            m.cash_register_id == register_id if register_id else None,
            m.movement_type == movement_type if movement_type else None,
            m.created_by == created_by if created_by else None,
            text_match,
            m.amount >= min_amount if min_amount is not None else None,
            m.amount <= max_amount if max_amount is not None else None,
            m.occurred_at >= start if start is not None else None,
            m.occurred_at < end if end is not None else None,
        ):
            if extra is not None:
                stmt = stmt.where(extra)
        sortable = {"occurred_at": m.occurred_at, "amount": m.amount}
        stmt = apply_sort(stmt, params.sort, sortable, "-occurred_at", m.id)
        total = self.db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
        rows = list(self.db.execute(stmt.limit(params.limit).offset(params.offset)).all())
        return rows, int(total or 0)

    def movements_out(self, rows: Sequence[Any]) -> list[CashMovementOut]:
        movements: list[CashMovement] = [r[0] for r in rows]
        sessions = {
            s.id: s
            for s in self.db.scalars(
                select(CashSession).where(
                    CashSession.id.in_({mv.cash_session_id for mv in movements})
                )
            )
        }
        registers = {
            r.id: r
            for r in self.db.scalars(
                select(CashRegister).where(
                    CashRegister.id.in_({mv.cash_register_id for mv in movements})
                )
            )
        }
        sites = _names(self.db, Site, {mv.site_id for mv in movements}, Site.name)
        users = _names(self.db, User, {mv.created_by for mv in movements}, User.full_name)
        result = []
        for row in rows:
            mv, balance_after = row[0], row[1]
            register = registers[mv.cash_register_id]
            result.append(
                CashMovementOut(
                    id=mv.id,
                    cash_session_id=mv.cash_session_id,
                    cash_session_number=sessions[mv.cash_session_id].number,
                    cash_register_id=register.id,
                    cash_register_code=register.code,
                    cash_register_name=register.name,
                    site_id=mv.site_id,
                    site_name=sites.get(mv.site_id, ""),
                    movement_type=mv.movement_type,
                    amount=mv.amount,
                    signed_amount=-mv.amount if mv.movement_type in OUTFLOWS else mv.amount,
                    category=mv.category,
                    reason=mv.reason,
                    reference=mv.reference,
                    source_type=mv.source_type,
                    source_id=mv.source_id,
                    source_number=mv.source_number,
                    payment_id=mv.payment_id,
                    occurred_at=mv.occurred_at,
                    created_by_name=users.get(mv.created_by),
                    balance_after=balance_after,
                )
            )
        return result

    def movement_out(self, movement: CashMovement) -> CashMovementOut:
        """Un mouvement et le solde de sa session juste après lui."""
        m = CashMovement
        balance = self.db.scalar(
            select(func.coalesce(func.sum(signed(m.amount, m.movement_type)), ZERO)).where(
                m.cash_session_id == movement.cash_session_id,
                or_(
                    m.occurred_at < movement.occurred_at,
                    and_(m.occurred_at == movement.occurred_at, m.id <= movement.id),
                ),
            )
        )
        return self.movements_out([(movement, balance)])[0]

    # --- Encaissements des ventes (API publique, appelée par PaymentService) ------------------

    def record_sale_cash_in(
        self,
        *,
        site_id: uuid.UUID,
        payment_id: uuid.UUID,
        payment_number: str,
        amount: Decimal,
        sale_id: uuid.UUID,
        sale_number: str,
        cash_register_id: uuid.UUID | None,
    ) -> CashMovement:
        """Encaissement espèces d'une vente : exige une session OUVERTE d'une caisse active du
        site de la vente (jamais une caisse d'un autre site, jamais sans session). Caisse
        choisie par le serveur : celle demandée (vérifiée), sinon la session ouverte par
        l'utilisateur sur ce site, sinon l'unique session ouverte du site."""
        session = self._session_for_cash(site_id, cash_register_id)
        return self._add_movement(
            session,
            CashMovementType.SALE_CASH_IN,
            _money(amount),
            reference=payment_number,
            source_type="sale",
            source_id=sale_id,
            source_number=sale_number,
            payment_id=payment_id,
        )

    def _session_for_cash(
        self, site_id: uuid.UUID, cash_register_id: uuid.UUID | None
    ) -> CashSession:
        candidates = list(
            self.db.scalars(
                select(CashSession)
                .join(CashRegister, CashRegister.id == CashSession.cash_register_id)
                .where(
                    CashSession.site_id == site_id,
                    CashSession.status == CashSessionStatus.OPEN,
                    CashRegister.is_active.is_(True),
                )
                .order_by(CashSession.opened_at)
            )
        )
        if cash_register_id is not None:
            candidates = [s for s in candidates if s.cash_register_id == cash_register_id]
        elif len(candidates) > 1:
            own = [s for s in candidates if s.opened_by == self.ctx.user.id]
            if len(own) != 1:
                raise BusinessRuleError(
                    "Plusieurs caisses sont ouvertes sur ce site : choisissez la caisse",
                    code="cash_register_required",
                    extra={"cash_register_ids": [str(s.cash_register_id) for s in candidates]},
                )
            candidates = own
        if not candidates:
            raise BusinessRuleError(
                "Aucune caisse ouverte sur le site de la vente : ouvrez une session de caisse "
                "pour encaisser en espèces",
                code="cash_session_required",
            )
        # Verrou de la session choisie, puis contrôle : une clôture concurrente l'emporte.
        session = self.get_session(candidates[0].id, lock=True)
        if session.status is not CashSessionStatus.OPEN:
            raise BusinessRuleError(
                "La session de caisse vient d'être clôturée", code="cash_session_required"
            )
        return session

    def record_sale_cash_reversal(
        self, *, payment_id: uuid.UUID, reason: str
    ) -> CashMovement | None:
        """Annulation d'un paiement espèces : sortie de même montant dans la MÊME session, si
        elle est encore ouverte (une session clôturée est immuable : refus). Aucun encaissement
        de caisse (paiement antérieur à la caisse) : rien à inverser."""
        cash_in = self.db.scalars(
            select(CashMovement).where(
                CashMovement.payment_id == payment_id,
                CashMovement.movement_type == CashMovementType.SALE_CASH_IN,
            )
        ).one_or_none()
        if cash_in is None:
            return None
        session = self.get_session(cash_in.cash_session_id, lock=True)
        if session.status is not CashSessionStatus.OPEN:
            raise ConflictError(
                "La session de caisse de ce paiement est clôturée : annulation impossible",
                code="cash_session_closed",
                extra={"session_number": session.number},
            )
        self._ensure_available(session, cash_in.amount)
        return self._add_movement(
            session,
            CashMovementType.SALE_CASH_REVERSAL,
            cash_in.amount,
            reason=reason,
            reference=cash_in.reference,
            source_type=cash_in.source_type,
            source_id=cash_in.source_id,
            source_number=cash_in.source_number,
            payment_id=payment_id,
        )
