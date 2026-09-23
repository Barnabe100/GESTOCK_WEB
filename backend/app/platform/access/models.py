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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TenantFiltered
from app.platform.identity.models import User
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class TenantMembership(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Appartenance d'un utilisateur à un tenant."""

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        UniqueConstraint("tenant_id", "id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        str_enum(MembershipStatus, "membership_status"),
        default=MembershipStatus.ACTIVE,
        nullable=False,
    )
    # Propriétaire : toutes les permissions des modules actifs, tous les sites, non modifiable
    # par les autres membres.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Accès à tous les sites (présents et futurs) ; sinon, voir membership_sites.
    all_sites: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(lazy="joined")
    site_links: Mapped[list["MembershipSite"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    role_links: Mapped[list["MembershipRole"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class Role(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Rôle défini par tenant (éventuellement issu d'un modèle système)."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        UniqueConstraint("tenant_id", "id"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Code du modèle dont le rôle est issu (ex. "administrator"), null pour un rôle personnalisé.
    template_code: Mapped[str | None] = mapped_column(String(50))
    # Rôle système : ni modifiable ni supprimable par le tenant.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permission_links: Mapped[list["RolePermission"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def permission_codes(self) -> list[str]:
        return sorted(link.permission_code for link in self.permission_links)


class RolePermission(TenantFiltered, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "role_id"], ["roles.tenant_id", "roles.id"], ondelete="CASCADE"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    permission_code: Mapped[str] = mapped_column(String(100), primary_key=True)


class MembershipSite(TenantFiltered, Base):
    """Sites auxquels un membre a accès (si ``all_sites`` est faux)."""

    __tablename__ = "membership_sites"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="CASCADE"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)


class MembershipRole(IdMixin, TenantFiltered, Base):
    """Rôle attribué à un membre, pour tout le tenant (site_id nul) ou pour un site."""

    __tablename__ = "membership_roles"
    __table_args__ = (
        UniqueConstraint("membership_id", "role_id", "site_id", postgresql_nulls_not_distinct=True),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"], ["roles.tenant_id", "roles.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="CASCADE"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    site_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
