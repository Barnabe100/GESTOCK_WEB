import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
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


def role_out(role: Role, registry: ModuleRegistry) -> RoleOut:
    return RoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        template_code=role.template_code,
        is_system=role.is_system,
        permission_codes=sorted(effective_role_permissions(role, registry)),
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
                self.ctx.membership, site_id, set(self.ctx.capabilities.modules)
            )
        return self._held_cache[site_id]

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
    def available_permissions(self) -> list[PermissionOut]:
        result = []
        for module in sorted(self.ctx.capabilities.modules):
            for perm in self.registry.get(module).permissions:
                result.append(PermissionOut(code=perm.code, module=module, access=perm.access))
        return result

    def list_all(self) -> list[Role]:
        return list(self.db.scalars(select(Role).order_by(Role.is_system.desc(), Role.name)))

    def get(self, role_id: uuid.UUID) -> Role:
        role = self.db.get(Role, role_id)
        if role is None:
            raise NotFoundError("Rôle introuvable", code="role_not_found")
        return role

    def _validate_permissions(self, codes: list[str]) -> list[str]:
        available = {p.code for p in self.available_permissions()}
        unknown = sorted(set(codes) - available)
        if unknown:
            raise BusinessRuleError(
                "Permissions inconnues", code="unknown_permission", extra={"permissions": unknown}
            )
        self._ensure_grantable(codes)
        return sorted(set(codes))

    def _flush(self) -> None:
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Un rôle porte déjà ce nom", code="role_name_taken") from exc

    def create(self, data: RoleCreate) -> Role:
        codes = self._validate_permissions(data.permissions)
        role = Role(tenant_id=self.ctx.tenant_id, name=data.name, description=data.description)
        role.permission_links = [
            RolePermission(tenant_id=self.ctx.tenant_id, permission_code=c) for c in codes
        ]
        self.db.add(role)
        self._flush()
        self._audit("role.created", "role", role.id, {"name": role.name, "permissions": codes})
        return role

    def _ensure_editable(self, role: Role) -> None:
        if role.is_system:
            raise ForbiddenError("Rôle système non modifiable", code="system_role")
        # Modifier un rôle revient à accorder ses permissions à ses titulaires.
        self._ensure_grantable(effective_role_permissions(role, self.registry))

    def update(self, role_id: uuid.UUID, data: RoleUpdate) -> Role:
        role = self.get(role_id)
        self._ensure_editable(role)
        before = {"name": role.name, "permissions": role.permission_codes}
        if data.name is not None:
            role.name = data.name
        if data.description is not None:
            role.description = data.description
        if data.permissions is not None:
            codes = self._validate_permissions(data.permissions)
            role.permission_links = [
                RolePermission(tenant_id=self.ctx.tenant_id, permission_code=c) for c in codes
            ]
        self._flush()
        after = {"name": role.name, "permissions": role.permission_codes}
        self._audit("role.updated", "role", role.id, {"before": before, "after": after})
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
        """Ajoute au tenant un rôle système manquant (ex. modèle apparu après sa création)."""
        template = role_templates().get(template_code)
        if template is None:
            raise NotFoundError("Modèle de rôle introuvable", code="role_template_not_found")
        if any(r.template_code == template_code for r in self.list_all()):
            raise ConflictError("Ce rôle existe déjà", code="role_template_exists")
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

    def delete(self, role_id: uuid.UUID) -> None:
        role = self.get(role_id)
        self._ensure_editable(role)
        in_use = self.db.scalar(
            select(func.count())
            .select_from(MembershipRole)
            .where(MembershipRole.role_id == role.id)
        )
        if in_use:
            raise ConflictError("Rôle attribué à des membres", code="role_in_use")
        self._audit("role.deleted", "role", role.id, {"name": role.name})
        self.db.delete(role)
        self.db.flush()


class MemberService(_AccessBase):
    def __init__(
        self, db: Session, ctx: RequestContext, registry: ModuleRegistry, settings: Settings
    ) -> None:
        super().__init__(db, ctx, registry)
        self.settings = settings

    def list_all(self) -> list[TenantMembership]:
        return list(
            self.db.scalars(
                select(TenantMembership)
                .join(User, User.id == TenantMembership.user_id)
                .order_by(TenantMembership.is_owner.desc(), User.full_name)
            )
        )

    def get(self, membership_id: uuid.UUID) -> TenantMembership:
        membership = self.db.get(TenantMembership, membership_id)
        if membership is None:
            raise NotFoundError("Membre introuvable", code="member_not_found")
        return membership

    def _check_user_limit(self) -> None:
        PlanPolicy(self.db, current_plan(self.db), self.registry).ensure_capacity("max_users")

    def _validate_access(
        self, roles: list[RoleAssignment], site_ids: list[uuid.UUID], all_sites: bool
    ) -> None:
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
            self._ensure_grantable(
                effective_role_permissions(role, self.registry), assignment.site_id
            )

    def _apply_access(
        self, membership: TenantMembership, roles: list[RoleAssignment], site_ids: list[uuid.UUID]
    ) -> None:
        """Mise à jour différentielle des rôles et sites (évite les conflits d'unicité que
        provoquerait un remplacement complet : SQLAlchemy insère avant de supprimer)."""
        tenant_id = self.ctx.tenant_id
        wanted_roles = {(r.role_id, r.site_id) for r in roles}
        for link in list(membership.role_links):
            if (link.role_id, link.site_id) not in wanted_roles:
                membership.role_links.remove(link)
        existing_roles = {(link.role_id, link.site_id) for link in membership.role_links}
        for role_id, site_id in sorted(wanted_roles - existing_roles, key=str):
            membership.role_links.append(
                MembershipRole(tenant_id=tenant_id, role_id=role_id, site_id=site_id)
            )

        wanted_sites = set(site_ids)
        for site_link in list(membership.site_links):
            if site_link.site_id not in wanted_sites:
                membership.site_links.remove(site_link)
        existing_sites = {link.site_id for link in membership.site_links}
        for site_id in sorted(wanted_sites - existing_sites):
            membership.site_links.append(MembershipSite(tenant_id=tenant_id, site_id=site_id))
        self.db.flush()

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
        self._validate_access(
            current_roles, [s.site_id for s in membership.site_links], membership.all_sites
        )
        self._validate_access(roles, site_ids, all_sites)
        if data.status == MembershipStatus.ACTIVE and membership.status != MembershipStatus.ACTIVE:
            self._check_user_limit()

        membership.all_sites = all_sites
        if data.status is not None:
            membership.status = data.status
        self._apply_access(membership, roles, site_ids)
        self.db.flush()
        self._audit(
            "member.updated",
            "membership",
            membership.id,
            {
                "roles": [r.model_dump(mode="json") for r in roles],
                "site_ids": [str(s) for s in site_ids],
                "all_sites": all_sites,
                "status": membership.status.value,
            },
        )
        return membership
