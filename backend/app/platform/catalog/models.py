from typing import Any

from sqlalchemy import ARRAY, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.platform.models_base import TimestampMixin


class BusinessProfile(TimestampMixin, Base):
    __tablename__ = "business_profiles"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Ordre de navigation (codes de modules).
    navigation: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    # Terminologie par langue : {"fr": {"catalog": {"item": "Produit"}}}.
    terminology: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    modules: Mapped[list["BusinessProfileModule"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class BusinessProfileModule(Base):
    __tablename__ = "business_profile_modules"

    profile_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("business_profiles.code", ondelete="CASCADE"), primary_key=True
    )
    module_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Activé automatiquement à la création d'un tenant de ce profil.
    default_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Plan(TimestampMixin, Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Limites : {"max_sites": 1, "max_users": 5} ; absente = illimitée. Les codes sont déclarés
    # par les modules (LimitDef) ; voir PlanPolicy.
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Fonctionnalités optionnelles incluses (codes déclarés par les modules).
    features: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    # Jours de grâce après la fin de période avant l'état « expiré ».
    grace_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    modules: Mapped[list["PlanModule"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class PlanModule(Base):
    __tablename__ = "plan_modules"

    plan_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("plans.code", ondelete="CASCADE"), primary_key=True
    )
    module_code: Mapped[str] = mapped_column(String(64), primary_key=True)


class SubscriptionAccessPolicy(Base):
    """Natures de permissions autorisées pour chaque statut d'abonnement (règle centralisée)."""

    __tablename__ = "subscription_access_policies"

    status: Mapped[str] = mapped_column(String(32), primary_key=True)
    allowed_access: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
