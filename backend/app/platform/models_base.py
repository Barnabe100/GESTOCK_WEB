"""Colonnes et types communs aux modèles de la plateforme."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.db import TenantFiltered
from app.shared.ids import new_id


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Enum stockée en texte + contrainte CHECK (évolution plus simple qu'un type PG natif)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantScopedMixin(TenantFiltered):
    """Toute table portant ce mixin est soumise à la politique RLS d'isolation par tenant."""

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
        )
