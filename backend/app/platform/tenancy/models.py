import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
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
    # Profil d'activité du SITE (profils / modules par site, palier A, migration 0039) :
    # catalogue global ``business_profiles``. Initialisé avec le profil de l'entreprise ;
    # jamais déduit de ``kind``. Ne pilote encore ni les modules ni les capacités (palier B).
    business_profile_code: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("business_profiles.code", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )


class TenantModule(TimestampMixin, TenantFiltered, Base):
    """**LEGACY** (profils / modules par site, palier C, migration 0040) : ancienne activation des
    modules au niveau du tenant. Conservée intacte comme historique, elle n'est plus ni lue ni
    écrite : la source de vérité est ``site_modules`` (``SiteModule``)."""

    __tablename__ = "tenant_modules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    module_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SiteModule(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Activation d'un module sur UN site (profils / modules par site, palier C, migration 0040) :
    **source de vérité** des activations. Un module est effectif sur le site s'il est proposé par
    le profil DU SITE, inclus dans l'abonnement DU SITE (licence en vigueur, sinon plan) et activé
    ici (dépendances comprises). Jamais supprimée : une désactivation passe ``enabled`` à faux.
    PLAN ≠ PROFIL ≠ ACTIVATION SITE."""

    __tablename__ = "site_modules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "module_code"),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    module_code: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
