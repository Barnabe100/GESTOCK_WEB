from typing import Any

from sqlalchemy import ARRAY, Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.platform.models_base import TimestampMixin


class BusinessSector(TimestampMixin, Base):
    """Secteur d'activité (classification) : ``retail``, ``restaurant``… (ADR-0024)."""

    __tablename__ = "business_sectors"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UxProfile(TimestampMixin, Base):
    """Profil UX : présentation d'un métier (navigation, tableau de bord, terminologie,
    thème). Aucune permission : la sécurité ne dépend jamais de ces données."""

    __tablename__ = "ux_profiles"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Rubriques du menu : [{"group": "stock", "modules": ["stock", "alerts"]}].
    navigation: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    # {"widgets": ["alerts:low_stock", …], "shortcuts": ["pos:open", …]}.
    dashboard: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Terminologie par langue : {"fr": {"catalog": {"item": "Produit"}}}.
    terminology: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # {"accent": "orange", "density": "comfortable", "icon": "pi pi-shop"}.
    theme: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class BusinessProfile(TimestampMixin, Base):
    """Profil d'activité (activité précise d'un secteur : ``restaurant.maquis``…).

    ``navigation``, ``dashboard``, ``terminology`` et ``theme`` sont des **surcharges** du
    profil UX (vides : hérités). Un profil actif a toujours un secteur et un profil UX ; un
    profil retiré du catalogue est désactivé, jamais supprimé (des tenants le référencent)."""

    __tablename__ = "business_profiles"
    __table_args__ = (
        CheckConstraint(
            "NOT is_active OR (sector_code IS NOT NULL AND ux_profile_code IS NOT NULL)",
            name="active_profile_classified",
        ),
    )

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sector_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("business_sectors.code", ondelete="RESTRICT")
    )
    ux_profile_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("ux_profiles.code", ondelete="RESTRICT")
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Surcharge des rubriques du profil UX (même format ; vide : héritées).
    navigation: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    # Surcharge des listes du tableau de bord (clés présentes seulement).
    dashboard: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    # Surcharge de terminologie (fusionnée avec celle du profil UX).
    terminology: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Surcharge du thème (fusionnée clé par clé).
    theme: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    sector: Mapped["BusinessSector | None"] = relationship(lazy="joined")
    ux_profile: Mapped["UxProfile | None"] = relationship(lazy="joined")

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
