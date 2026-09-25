import uuid
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantFiltered
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"  # suspension administrative par la plateforme


class Tenant(IdMixin, TimestampMixin, Base):
    """Isolé par RLS (``id`` = tenant actif) ; toujours chargé par son identifiant."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        str_enum(TenantStatus, "tenant_status"), default=TenantStatus.ACTIVE, nullable=False
    )
    business_profile_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("business_profiles.code", ondelete="RESTRICT"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="XOF", nullable=False)
    locale: Mapped[str] = mapped_column(String(10), default="fr", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Ouagadougou", nullable=False)

    # --- Entreprise (Phase 3.2) : informations persistantes, reprises par les reçus et les
    # futurs documents. ``name`` est la raison sociale. Pays obligatoire pour tout nouveau
    # tenant (contrôlé par l'application) ; nul seulement pour les tenants antérieurs, jusqu'à
    # sa saisie ; jamais effacé ensuite.
    country_code: Mapped[str | None] = mapped_column(
        String(2), ForeignKey("geo_countries.code", ondelete="RESTRICT")
    )
    trade_name: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(100))
    website: Mapped[str | None] = mapped_column(String(500))
    # Identifiant fiscal (IFU) et registre du commerce (RCCM).
    tax_id: Mapped[str | None] = mapped_column(String(50))
    trade_register: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    # Référence du logo (URL https) ; stockage de fichiers : phase ultérieure.
    logo_url: Mapped[str | None] = mapped_column(String(500))


class SiteKind(StrEnum):
    STORE = "store"  # boutique / point de vente
    WAREHOUSE = "warehouse"  # dépôt
    RESTAURANT = "restaurant"  # salle de restauration
    OTHER = "other"


class Site(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        # Cible des clés étrangères composites (empêche les références inter-tenants).
        UniqueConstraint("tenant_id", "id"),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    kind: Mapped[SiteKind] = mapped_column(
        str_enum(SiteKind, "site_kind"), default=SiteKind.STORE, nullable=False
    )
    address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TenantModule(TimestampMixin, TenantFiltered, Base):
    """Activation d'un module par le tenant (dans les limites du profil et du plan)."""

    __tablename__ = "tenant_modules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    module_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
