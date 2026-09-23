"""Accès base de données : moteur, sessions et contexte de Row-Level Security.

Chaque transaction applique ``app.tenant_id`` et ``app.user_id`` (``set_config`` local à la
transaction) depuis ``session.info``. Les politiques RLS PostgreSQL s'appuient sur ces valeurs :
sans contexte, aucune ligne tenant-scoped n'est visible.
"""

import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Connection, Engine, MetaData, column, create_engine, event, text
from sqlalchemy.orm import (
    DeclarativeBase,
    ORMExecuteState,
    Session,
    SessionTransaction,
    sessionmaker,
    with_loader_criteria,
)

from app.core.config import Settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class _TenantColumnPlaceholder:
    """Substitut de ``tenant_id`` pour l'analyse du critère (remplacé dans chaque entité)."""

    def __get__(self, obj: object, owner: type) -> Any:
        return column("tenant_id")


class TenantFiltered:
    """Marqueur des entités appartenant à un tenant.

    Quand un tenant est actif sur la session, toute requête ORM les filtre automatiquement
    sur ``tenant_id`` (couche applicative), en plus de la RLS PostgreSQL (couche base).
    Les sous-classes doivent posséder une colonne nommée ``tenant_id`` (le critère est
    analysé une fois puis adapté par nom de colonne)."""

    __abstract__ = True
    tenant_id: Any = _TenantColumnPlaceholder()


TENANT_KEY = "tenant_id"
USER_KEY = "user_id"

_SET_CONTEXT_SQL = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), "
    "set_config('app.user_id', :user_id, true)"
)


def _apply_context(session: Session, connection: Connection) -> None:
    tenant_id = session.info.get(TENANT_KEY)
    user_id = session.info.get(USER_KEY)
    connection.execute(
        _SET_CONTEXT_SQL,
        {
            "tenant_id": str(tenant_id) if tenant_id else "",
            "user_id": str(user_id) if user_id else "",
        },
    )


@event.listens_for(Session, "after_begin")
def _on_begin(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    _apply_context(session, connection)


@event.listens_for(Session, "do_orm_execute")
def _filter_by_tenant(state: ORMExecuteState) -> None:
    tenant_id = state.session.info.get(TENANT_KEY)
    if (
        tenant_id is None
        or not state.is_select
        or state.is_column_load
        or state.is_relationship_load
    ):
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantFiltered,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


def set_db_context(
    session: Session,
    *,
    tenant_id: uuid.UUID | None | Any = ...,
    user_id: uuid.UUID | None | Any = ...,
) -> None:
    """Définit le contexte RLS de la session (et l'applique à la transaction en cours)."""
    if tenant_id is not ...:
        session.info[TENANT_KEY] = tenant_id
    if user_id is not ...:
        session.info[USER_KEY] = user_id
    if session.in_transaction():
        _apply_context(session, session.connection())


def create_db_engine(url: str, settings: Settings) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        echo=settings.db_echo,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db(request: Request) -> Iterator[Session]:
    """Session par requête. Les écritures sont validées explicitement (``session.commit()``) par
    le code applicatif ; tout ce qui n'est pas validé est annulé en fin de requête."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        try:
            yield session
        finally:
            session.rollback()
