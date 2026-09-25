import uuid
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.security import hash_password
from app.platform.access.models import (
    MembershipRole,
    MembershipSite,
    MembershipStatus,
    Role,
    RolePermission,
    TenantMembership,
)
from app.platform.access.permissions import effective_role_permissions
from app.platform.access.schemas import (
    MemberCreate,
    MemberOut,
    MemberUpdate,
    PermissionOut,
    RoleAssignment,
    RoleCreate,
    RoleDeactivate,
    RoleDuplicate,
    RoleMemberOut,
    RoleOut,
    RoleTemplateOut,
    RoleUpdate,
)
from app.platform.audit.service import record_audit
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.loader import role_templates
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.identity.passwords import normalize_email, validate_new_password
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.service import current_plan
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter, text_sort
from app.shared.schemas import StatusFilter


class RoleKind(StrEnum):
    SYSTEM = "system"
    CUSTOM = "custom"


def role_out(
    role: Role, registry: ModuleRegistry, member_count: int = 0, delegable: bool = False
) -> RoleOut:
    template = (
        role_templates().get(role.template_code) if role.is_system and role.template_code else None
    )
    return RoleOut(
        id=role.id,
        name=template.name if template else role.name,
        description=template.description if template else role.description,
        template_code=role.template_code,
        is_system=role.is_system,
        is_active=role.is_active,
        protected=bool(template and template.protected),
        member_count=member_count,
        permission_codes=sorted(effective_role_permissions(role, registry)),
        delegable=delegable,
    )


def member_out(membership: TenantMembership) -> MemberOut:
    return MemberOut(
        id=membership.id,
        user_id=membership.user_id,
        email=membership.user.email,
        full_name=membership.user.full_name,
        status=membership.status,
        is_owner=membership.is_owner,
        all_sites=membership.all_sites,
        must_change_password=membership.user.must_change_password,
        roles=[RoleAssignment(role_id=r.role_id, site_id=r.site_id) for r in membership.role_links],
        site_ids=sorted(s.site_id for s in membership.site_links),
        created_at=membership.created_at,
    )


class _AccessBase:
    def __init__(self, db: Session, ctx: RequestContext, registry: ModuleRegistry) -> None:
        self.db = db
        self.ctx = ctx
        self.registry = registry
        self._held_cache: dict[uuid.UUID | None, set[str]] = {}

    # --- Anti-escalade (ADR-0015) ------------------------------------------------------------
    # Un non-propriétaire n'accorde que ce qu'il détient **sur la même portée** : un rôle limité
    # à un site ne donne aucun droit d'accorder quoi que ce soit ailleurs, ni sur tout le tenant.

    def _held(self, site_id: uuid.UUID | None) -> set[str]:
        if site_id not in self._held_cache:
            self._held_cache[site_id] = CapabilityService(self.db, self.registry).held_permissions(
                self.ctx.membership,
                site_id,
                self.ctx.capabilities.modules,
                self.ctx.capabilities.features,
            )
        return self._held_cache[site_id]

    # --- Délégation (Phase 3.2-E, ADR-0030) ----------------------------------------------------
    # Une seule logique : ce que l'acteur peut déléguer est exactement ce que ``_ensure_grantable``
    # accepte. L'interface ne décide jamais ; elle affiche ces projections.

    def _offer(self) -> set[str]:
        """Permissions utilisables dans ce tenant (modules effectifs, fonctionnalités du plan)."""
        return set(
            self.registry.available_permissions(
                self.ctx.capabilities.modules, self.ctx.capabilities.features
            )
        )

    def role_grants(self, role: Role) -> set[str]:
        """Permissions qu'un rôle accorde **réellement** dans ce tenant : celles de son modèle ou
        de sa liste, limitées à l'offre actuelle (le calcul des capacités n'accorde jamais le
        reste). Base de tout contrôle de délégation d'un rôle."""
        return effective_role_permissions(role, self.registry) & self._offer()

    def _site_in_scope(self, site_id: uuid.UUID) -> bool:
        membership = self.ctx.membership
        if membership.is_owner or membership.all_sites:
            return True
        return site_id in {link.site_id for link in membership.site_links}

    def delegable_permissions(self, site_id: uuid.UUID | None = None) -> set[str]:
        """Permissions que l'acteur peut accorder sur tout le tenant (``site_id`` nul) ou sur un
        site de son périmètre : le propriétaire, toute l'offre ; sinon, ce qu'il détient sur
        cette portée."""
        if self.ctx.membership.is_owner:
            return self._offer()
        if site_id is not None and not self._site_in_scope(site_id):
            return set()
        return self._held(site_id) & self._offer()

    def is_delegable(self, role: Role, site_id: uuid.UUID | None = None) -> bool:
        return self.role_grants(role) <= self.delegable_permissions(site_id)

    def _ensure_grantable(
        self, permission_codes: Iterable[str], site_id: uuid.UUID | None = None
    ) -> None:
        """Permissions accordées sur tout le tenant (``site_id`` nul) ou sur un site."""
        if self.ctx.membership.is_owner:
            return
        if site_id is not None:
            self._ensure_sites_in_scope({site_id}, all_sites=False)
        excess = sorted(set(permission_codes) - self._held(site_id))
        if excess:
            raise ForbiddenError(
                "Vous ne pouvez pas accorder des permissions que vous ne détenez pas",
                code="permission_escalation",
                extra={"permissions": excess},
            )

    def _ensure_sites_in_scope(self, site_ids: Iterable[uuid.UUID], *, all_sites: bool) -> None:
        """Accès aux sites : jamais au-delà des sites de l'acteur (ni « tous les sites » s'il ne
        l'a pas lui-même)."""
        membership = self.ctx.membership
        if membership.is_owner or membership.all_sites:
            return
        own = {link.site_id for link in membership.site_links}
        outside = sorted(str(s) for s in set(site_ids) - own)
        if all_sites or outside:
            raise ForbiddenError(
                "Vous ne pouvez pas accorder l'accès à des sites hors de votre périmètre",
                code="site_escalation",
                extra={"site_ids": outside, "all_sites": all_sites},
            )

    def _audit(
        self, action: str, entity_type: str, entity_id: uuid.UUID, data: dict[str, Any]
    ) -> None:
        record_audit(
            self.db,
            action=action,
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            entity_type=entity_type,
            entity_id=entity_id,
            data=data,
            meta=self.ctx.meta,
        )


class RoleService(_AccessBase):
    """Rôles de base (système, non modifiables) et rôles personnalisés du tenant. Aucun rôle
    n'est supprimé : il est désactivé, ses attributions sont conservées (ADR-0015)."""

    # --- Lecture ------------------------------------------------------------------------------

    def available_permissions(self) -> list[PermissionOut]:
        result = []
        features = self.ctx.capabilities.features
        for module in sorted(self.ctx.capabilities.modules):
            for perm in self.registry.get(module).permissions:
                if perm.feature is not None and perm.feature not in features:
                    continue  # fonctionnalité absente du plan : permission non attribuable
                resource, _, action = perm.code[len(module) + 1 :].rpartition(".")
                result.append(
                    PermissionOut(
                        code=perm.code,
                        module=module,
                        access=perm.access,
                        resource=resource,
                        action=action,
                    )
                )
        return result

    def list_all(
        self, kind: RoleKind | None = None, status: StatusFilter = StatusFilter.ALL
    ) -> list[Role]:
        stmt = select(Role).order_by(Role.is_system.desc(), Role.name)
        if kind is not None:
            stmt = stmt.where(Role.is_system.is_(kind is RoleKind.SYSTEM))
        if status is not StatusFilter.ALL:
            stmt = stmt.where(Role.is_active.is_(status is StatusFilter.ACTIVE))
        return list(self.db.scalars(stmt))

    def member_counts(self) -> dict[uuid.UUID, int]:
        rows = self.db.execute(
            select(
                MembershipRole.role_id, func.count(func.distinct(MembershipRole.membership_id))
            ).group_by(MembershipRole.role_id)
        ).tuples()
        return {role_id: int(count) for role_id, count in rows}

    def out(self, role: Role, counts: dict[uuid.UUID, int] | None = None) -> RoleOut:
        counts = self.member_counts() if counts is None else counts
        return role_out(role, self.registry, counts.get(role.id, 0), self.is_delegable(role))

    def delegable_roles(self, site_id: uuid.UUID | None = None) -> list[Role]:
        """Rôles actifs que l'acteur peut attribuer sur cette portée (tout le tenant ou un site
        de son périmètre)."""
        if site_id is not None and self.db.get(Site, site_id) is None:  # RLS : site du tenant
            raise NotFoundError("Site introuvable", code="site_not_found")
        delegable = self.delegable_permissions(site_id)
        return [
            r for r in self.list_all(status=StatusFilter.ACTIVE) if self.role_grants(r) <= delegable
        ]

    def delegable_permission_list(self, site_id: uuid.UUID | None = None) -> list[PermissionOut]:
        if site_id is not None and self.db.get(Site, site_id) is None:
            raise NotFoundError("Site introuvable", code="site_not_found")
        delegable = self.delegable_permissions(site_id)
        return [p for p in self.available_permissions() if p.code in delegable]

    def get(self, role_id: uuid.UUID) -> Role:
        role = self.db.get(Role, role_id)
        if role is None:
            raise NotFoundError("Rôle introuvable", code="role_not_found")
        return role

    def members(self, role_id: uuid.UUID) -> list[RoleMemberOut]:
        role = self.get(role_id)
        rows = self.db.execute(
            select(MembershipRole.site_id, TenantMembership)
            .join(TenantMembership, TenantMembership.id == MembershipRole.membership_id)
            .join(User, User.id == TenantMembership.user_id)
            .where(MembershipRole.role_id == role.id)
            .order_by(User.full_name, MembershipRole.site_id)
        ).tuples()
        return [
            RoleMemberOut(
                membership_id=membership.id,
                user_id=membership.user_id,
                full_name=membership.user.full_name,
                email=membership.user.email,
                status=membership.status,
                site_id=site_id,
            )
            for site_id, membership in rows
        ]

    # --- Règles ---------------------------------------------------------------------------------

    def _validate_permissions(self, codes: list[str]) -> list[str]:
        available = {p.code for p in self.available_permissions()}
        unknown = sorted(set(codes) - available)
        if unknown:
            raise BusinessRuleError(
                "Permissions inconnues", code="unknown_permission", extra={"permissions": unknown}
            )
        # Un rôle n'a pas de portée : il faut détenir ses permissions sur tout le tenant.
        self._ensure_grantable(codes)
        return sorted(set(codes))

    def _check_name(self, name: str, exclude_id: uuid.UUID | None = None) -> None:
        """Nom unique par tenant (casse ignorée) et distinct des noms des rôles de base."""
        lowered = name.strip().lower()
        if any(t.name.strip().lower() == lowered for t in role_templates().values()):
            raise ConflictError("Ce nom est réservé à un rôle de base", code="role_name_reserved")
        stmt = select(Role.id).where(func.lower(Role.name) == lowered, Role.is_system.is_(False))
        if exclude_id is not None:
            stmt = stmt.where(Role.id != exclude_id)
        if self.db.scalars(stmt).first() is not None:
            raise ConflictError("Un rôle porte déjà ce nom", code="role_name_taken")

    def _flush(self) -> None:
        try:
            self.db.flush()
        except IntegrityError as exc:  # concurrence : l'index unique fait foi
            raise ConflictError("Un rôle porte déjà ce nom", code="role_name_taken") from exc

    def _ensure_editable(self, role: Role) -> None:
        if role.is_system:
            raise ForbiddenError("Rôle de base non modifiable", code="system_role")
        # Modifier un rôle revient à accorder ses permissions à ses titulaires.
        self._ensure_grantable(self.role_grants(role))

    def _is_protected(self, role: Role) -> bool:
        template = role_templates().get(role.template_code or "")
        return bool(role.is_system and template and template.protected)

    # --- Écritures -------------------------------------------------------------------------------

    def create(self, data: RoleCreate, *, copied_from: Role | None = None) -> Role:
        self._check_name(data.name)
        codes = self._validate_permissions(data.permissions)
        role = Role(
            tenant_id=self.ctx.tenant_id,
            name=data.name,
            description=data.description or None,
        )
        role.permission_links = [
            RolePermission(tenant_id=self.ctx.tenant_id, permission_code=c) for c in codes
        ]
        self.db.add(role)
        self._flush()
        audit: dict[str, Any] = {"name": role.name, "permissions": codes}
        if copied_from is not None:
            audit["copied_from"] = {"id": str(copied_from.id), "name": copied_from.name}
        self._audit("role.created", "role", role.id, audit)
        return role

    def duplicate(self, role_id: uuid.UUID, data: RoleDuplicate) -> Role:
        """Rôle personnalisé reprenant les permissions (disponibles dans l'offre) d'un rôle."""
        source = self.get(role_id)
        available = {p.code for p in self.available_permissions()}
        codes = sorted(effective_role_permissions(source, self.registry) & available)
        description = data.description if data.description is not None else None
        return self.create(
            RoleCreate(name=data.name, description=description, permissions=codes),
            copied_from=source,
        )

    def update(self, role_id: uuid.UUID, data: RoleUpdate) -> Role:
        role = self.get(role_id)
        self._ensure_editable(role)
        before = {
            "name": role.name,
            "description": role.description,
            "permissions": role.permission_codes,
        }
        if data.name is not None and data.name != role.name:
            self._check_name(data.name, exclude_id=role.id)
            role.name = data.name
        if data.description is not None:
            role.description = data.description or None
        if data.permissions is not None:
            # Permissions enregistrées devenues hors de l'offre (ex. plan réduit) : conservées
            # telles quelles (sans effet tant que l'offre ne les inclut pas) ; jamais ajoutées.
            kept = (set(data.permissions) - self._offer()) & set(role.permission_codes or [])
            codes = self._validate_permissions(sorted(set(data.permissions) - kept))
            wanted = set(codes) | kept
            # Mise à jour différentielle (pas de conflit de clé primaire au remplacement).
            for link in list(role.permission_links):
                if link.permission_code not in wanted:
                    role.permission_links.remove(link)
            existing = {link.permission_code for link in role.permission_links}
            for code in sorted(wanted - existing):
                role.permission_links.append(
                    RolePermission(tenant_id=self.ctx.tenant_id, permission_code=code)
                )
        self._flush()
        after = {
            "name": role.name,
            "description": role.description,
            "permissions": role.permission_codes,
        }
        if before != after:
            self._audit(
                "role.updated",
                "role",
                role.id,
                {
                    "before": before,
                    "after": after,
                    "permissions_added": sorted(
                        set(after["permissions"] or []) - set(before["permissions"] or [])
                    ),
                    "permissions_removed": sorted(
                        set(before["permissions"] or []) - set(after["permissions"] or [])
                    ),
                },
            )
        return role

    def activate(self, role_id: uuid.UUID) -> Role:
        role = self.get(role_id)
        if role.is_active:
            return role
        # Réactiver rend ses permissions à tous ses titulaires.
        self._ensure_grantable(self.role_grants(role))
        role.is_active = True
        self.db.flush()
        members = self.members(role.id)
        self._audit(
            "role.activated",
            "role",
            role.id,
            {"name": role.name, "members": sorted({str(m.membership_id) for m in members})},
        )
        return role

    def deactivate(self, role_id: uuid.UUID, data: RoleDeactivate) -> Role:
        role = self.get(role_id)
        if self._is_protected(role):
            raise ForbiddenError("Ce rôle est protégé", code="role_protected")
        if not role.is_active:
            return role
        self._ensure_grantable(self.role_grants(role))
        members = self.members(role.id)
        if members and not data.confirm:
            raise ConflictError(
                "Rôle attribué à des membres : confirmation requise",
                code="role_in_use",
                extra={
                    "count": len({m.membership_id for m in members}),
                    "members": [
                        {
                            "membership_id": str(m.membership_id),
                            "full_name": m.full_name,
                            "site_id": str(m.site_id) if m.site_id else None,
                        }
                        for m in members
                    ],
                },
            )
        # Les attributions sont conservées : la réactivation rétablit les droits.
        role.is_active = False
        self.db.flush()
        self._audit(
            "role.deactivated",
            "role",
            role.id,
            {
                "name": role.name,
                "members": sorted({str(m.membership_id) for m in members}),
                "confirmed": data.confirm,
            },
        )
        return role

    def templates(self) -> list[RoleTemplateOut]:
        existing = {r.template_code for r in self.list_all() if r.template_code}
        return [
            RoleTemplateOut(
                code=t.code, name=t.name, description=t.description, instantiated=t.code in existing
            )
            for t in role_templates().values()
        ]

    def create_from_template(self, template_code: str) -> Role:
        """Ajoute au tenant un rôle de base manquant (ex. modèle apparu après sa création)."""
        template = role_templates().get(template_code)
        if template is None:
            raise NotFoundError("Modèle de rôle introuvable", code="role_template_not_found")
        if any(r.template_code == template_code for r in self.list_all()):
            raise ConflictError("Ce rôle existe déjà", code="role_template_exists")
        self._ensure_grantable(
            template.resolve(
                set(
                    self.registry.available_permissions(
                        self.ctx.capabilities.modules, self.ctx.capabilities.features
                    )
                )
            )
        )
        role = Role(
            tenant_id=self.ctx.tenant_id,
            name=template.name,
            description=template.description,
            template_code=template.code,
            is_system=True,
        )
        self.db.add(role)
        self._flush()
        self._audit("role.created", "role", role.id, {"template": template.code})
        return role


class MemberService(_AccessBase):
    def __init__(
        self, db: Session, ctx: RequestContext, registry: ModuleRegistry, settings: Settings
    ) -> None:
        super().__init__(db, ctx, registry)
        self.settings = settings

    # Tri des appartenances (liste blanche).
    SORTS: dict[str, Any] = {
        "full_name": text_sort(User.full_name),
        "email": User.email,
        "created_at": TenantMembership.created_at,
        "status": TenantMembership.status,
    }

    def search(
        self,
        params: PageParams,
        search: str | None = None,
        status: StatusFilter = StatusFilter.ALL,
        role_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
    ) -> tuple[list[TenantMembership], int]:
        """Appartenances du tenant (RLS) : recherche nom / e-mail, statut, rôle détenu (toute
        portée), accès à un site (tous les sites, site attribué ou rôle limité au site)."""
        stmt = select(TenantMembership).join(User, User.id == TenantMembership.user_id)
        conditions = [search_filter(search, User.full_name, User.email)]
        if status is StatusFilter.ACTIVE:
            conditions.append(TenantMembership.status == MembershipStatus.ACTIVE)
        elif status is StatusFilter.INACTIVE:
            conditions.append(TenantMembership.status != MembershipStatus.ACTIVE)
        if role_id is not None:
            conditions.append(
                exists().where(
                    MembershipRole.membership_id == TenantMembership.id,
                    MembershipRole.role_id == role_id,
                )
            )
        if site_id is not None:
            conditions.append(
                or_(
                    TenantMembership.all_sites.is_(True),
                    exists().where(
                        MembershipSite.membership_id == TenantMembership.id,
                        MembershipSite.site_id == site_id,
                    ),
                    exists().where(
                        MembershipRole.membership_id == TenantMembership.id,
                        MembershipRole.site_id == site_id,
                    ),
                )
            )
        stmt = stmt.where(*(c for c in conditions if c is not None))
        stmt = apply_sort(stmt, params.sort, self.SORTS, "full_name", TenantMembership.id)
        return paginate(self.db, stmt, params)

    def get(self, membership_id: uuid.UUID) -> TenantMembership:
        membership = self.db.get(TenantMembership, membership_id)
        if membership is None:
            raise NotFoundError("Membre introuvable", code="member_not_found")
        return membership

    def _check_user_limit(self) -> None:
        PlanPolicy(self.db, current_plan(self.db), self.registry).ensure_capacity("max_users")

    def _validate_access(
        self,
        roles: list[RoleAssignment],
        site_ids: list[uuid.UUID],
        all_sites: bool,
        existing: set[tuple[uuid.UUID, uuid.UUID | None]] | None = None,
    ) -> None:
        """Rôles et sites accordés : connus du tenant, dans le périmètre de l'acteur (portée et
        sites), rôles actifs pour toute **nouvelle** attribution (``existing`` : attributions
        déjà détenues, conservées même si leur rôle a été désactivé)."""
        tenant_sites = set(self.db.scalars(select(Site.id)))  # RLS : sites du tenant uniquement
        unknown_sites = {s for s in site_ids if s not in tenant_sites}
        unknown_sites |= {r.site_id for r in roles if r.site_id and r.site_id not in tenant_sites}
        if unknown_sites:
            raise BusinessRuleError("Site inconnu", code="site_not_found")
        for assignment in roles:
            if assignment.site_id and not all_sites and assignment.site_id not in site_ids:
                raise BusinessRuleError(
                    "Un rôle limité à un site exige l'accès à ce site", code="site_not_assigned"
                )
        self._ensure_sites_in_scope(site_ids, all_sites=all_sites)
        role_map = {r.id: r for r in self.db.scalars(select(Role))}  # RLS
        for assignment in roles:
            role = role_map.get(assignment.role_id)
            if role is None:
                raise BusinessRuleError("Rôle inconnu", code="role_not_found")
            is_new = existing is None or (assignment.role_id, assignment.site_id) not in existing
            if is_new and not role.is_active:
                raise BusinessRuleError(
                    "Ce rôle est désactivé", code="role_inactive", extra={"role_id": str(role.id)}
                )
            self._ensure_grantable(self.role_grants(role), assignment.site_id)

    def _apply_access(
        self, membership: TenantMembership, roles: list[RoleAssignment], site_ids: list[uuid.UUID]
    ) -> None:
        """Mise à jour différentielle des rôles et sites (évite les conflits d'unicité que
        provoquerait un remplacement complet : SQLAlchemy insère avant de supprimer). Chaque
        attribution et chaque retrait de rôle est audité."""
        tenant_id = self.ctx.tenant_id
        wanted_roles = {(r.role_id, r.site_id) for r in roles}
        removed = []
        for link in list(membership.role_links):
            if (link.role_id, link.site_id) not in wanted_roles:
                removed.append((link.role_id, link.site_id))
                membership.role_links.remove(link)
        existing_roles = {(link.role_id, link.site_id) for link in membership.role_links}
        added = sorted(wanted_roles - existing_roles, key=str)
        for role_id, site_id in added:
            membership.role_links.append(
                MembershipRole(tenant_id=tenant_id, role_id=role_id, site_id=site_id)
            )

        wanted_sites = set(site_ids)
        removed_sites = []
        for site_link in list(membership.site_links):
            if site_link.site_id not in wanted_sites:
                removed_sites.append(site_link.site_id)
                membership.site_links.remove(site_link)
        existing_sites = {link.site_id for link in membership.site_links}
        added_sites = sorted(wanted_sites - existing_sites)
        for site_id in added_sites:
            membership.site_links.append(MembershipSite(tenant_id=tenant_id, site_id=site_id))
        self.db.flush()
        for action, site_changes in (
            ("member.site_removed", sorted(removed_sites)),
            ("member.site_assigned", added_sites),
        ):
            for site_id in site_changes:
                self._audit(
                    action,
                    "membership",
                    membership.id,
                    {"user_id": str(membership.user_id), "site_id": str(site_id)},
                )

        names = {r.id: r.name for r in self.db.scalars(select(Role))}
        for action, changes in (("member.role_removed", removed), ("member.role_assigned", added)):
            for role_id, site_id in changes:
                self._audit(
                    action,
                    "membership",
                    membership.id,
                    {
                        "user_id": str(membership.user_id),
                        "role_id": str(role_id),
                        "role_name": names.get(role_id),
                        "site_id": str(site_id) if site_id else None,
                    },
                )

    def create(self, data: MemberCreate) -> TenantMembership:
        self._check_user_limit()
        self._validate_access(data.roles, data.site_ids, data.all_sites)
        email = normalize_email(str(data.email))
        user = self.db.scalars(select(User).where(User.email == email)).one_or_none()
        user_created = False
        if user is None:
            if not data.password:
                raise BusinessRuleError(
                    "Mot de passe provisoire requis pour un nouvel utilisateur",
                    code="password_required",
                )
            validate_new_password(data.password, email=email, settings=self.settings)
            user = User(
                email=email,
                full_name=data.full_name.strip(),
                password_hash=hash_password(data.password),
                must_change_password=True,
            )
            self.db.add(user)
            self.db.flush()
            user_created = True
        else:
            # Compte existant (autre tenant) : jamais de modification de son mot de passe ni de
            # son identité ; on crée seulement l'appartenance.
            existing = self.db.scalars(
                select(TenantMembership).where(TenantMembership.user_id == user.id)
            ).one_or_none()
            if existing is not None:
                raise ConflictError("Cet utilisateur est déjà membre", code="member_exists")

        membership = TenantMembership(
            tenant_id=self.ctx.tenant_id, user_id=user.id, all_sites=data.all_sites
        )
        self.db.add(membership)
        self.db.flush()
        self._apply_access(membership, data.roles, data.site_ids)
        self.db.flush()
        self._audit(
            "member.created",
            "membership",
            membership.id,
            {
                "email": email,
                "user_created": user_created,
                "roles": [r.model_dump(mode="json") for r in data.roles],
                "site_ids": [str(s) for s in data.site_ids],
                "all_sites": data.all_sites,
            },
        )
        self.db.refresh(membership)
        return membership

    def update(self, membership_id: uuid.UUID, data: MemberUpdate) -> TenantMembership:
        membership = self.get(membership_id)
        if membership.is_owner:
            raise ForbiddenError("Le propriétaire ne peut pas être modifié", code="owner_protected")
        if membership.id == self.ctx.membership.id:
            raise ForbiddenError(
                "Vous ne pouvez pas modifier vos propres accès", code="self_modification"
            )
        # Modifier un membre suppose de pouvoir accorder ce qu'il détient déjà.
        current_roles = [
            RoleAssignment(role_id=r.role_id, site_id=r.site_id) for r in membership.role_links
        ]
        roles = data.roles if data.roles is not None else current_roles
        site_ids = (
            data.site_ids
            if data.site_ids is not None
            else [s.site_id for s in membership.site_links]
        )
        all_sites = data.all_sites if data.all_sites is not None else membership.all_sites
        # … et que son accès actuel (rôles, sites) soit dans le périmètre de l'acteur.
        current_sites = [s.site_id for s in membership.site_links]
        existing = {(r.role_id, r.site_id) for r in current_roles}
        self._validate_access(current_roles, current_sites, membership.all_sites, existing)
        self._validate_access(roles, site_ids, all_sites, existing)
        before = self._access_snapshot(membership)
        if data.status == MembershipStatus.ACTIVE and membership.status != MembershipStatus.ACTIVE:
            self._check_user_limit()

        membership.all_sites = all_sites
        if data.status is not None:
            membership.status = data.status
        self._apply_access(membership, roles, site_ids)
        self.db.flush()
        after = self._access_snapshot(membership)
        if before != after:
            self._audit(
                "member.updated", "membership", membership.id, {"before": before, "after": after}
            )
        if before["status"] != after["status"]:
            # Appartenance à CE tenant seulement : le compte global et les autres tenants de
            # l'utilisateur ne changent pas ; rôles, sites et historique sont conservés.
            self._audit(
                "member.activated"
                if membership.status == MembershipStatus.ACTIVE
                else "member.deactivated",
                "membership",
                membership.id,
                {
                    "user_id": str(membership.user_id),
                    "before": before["status"],
                    "after": after["status"],
                },
            )
        return membership

    def set_active(self, membership_id: uuid.UUID, active: bool) -> TenantMembership:
        """Activer / désactiver l'appartenance (mêmes garde-fous que ``update`` : propriétaire
        protégé, pas soi-même, accès du membre dans le périmètre de l'acteur, limite du plan à
        la réactivation)."""
        return self.update(
            membership_id,
            MemberUpdate(status=MembershipStatus.ACTIVE if active else MembershipStatus.SUSPENDED),
        )

    @staticmethod
    def _access_snapshot(membership: TenantMembership) -> dict[str, Any]:
        return {
            "roles": sorted(
                (
                    {"role_id": str(r.role_id), "site_id": str(r.site_id) if r.site_id else None}
                    for r in membership.role_links
                ),
                key=lambda r: (r["role_id"], r["site_id"] or ""),
            ),
            "site_ids": sorted(str(s.site_id) for s in membership.site_links),
            "all_sites": membership.all_sites,
            "status": membership.status.value,
        }
