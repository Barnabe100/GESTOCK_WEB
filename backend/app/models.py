"""Import de tous les modèles : utilisé par Alembic (métadonnées complètes)."""

from app.core.db import Base
from app.platform.access.models import (
    MembershipRole,
    MembershipSite,
    Role,
    RolePermission,
    TenantMembership,
)
from app.platform.audit.models import AuditLog
from app.platform.catalog.models import (
    BusinessProfile,
    BusinessProfileModule,
    Plan,
    PlanModule,
    SubscriptionAccessPolicy,
)
from app.platform.identity.models import AuthSession, User
from app.platform.subscriptions.models import Subscription
from app.platform.tenancy.models import Site, Tenant, TenantModule

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "BusinessProfile",
    "BusinessProfileModule",
    "MembershipRole",
    "MembershipSite",
    "Plan",
    "PlanModule",
    "Role",
    "RolePermission",
    "Site",
    "Subscription",
    "SubscriptionAccessPolicy",
    "Tenant",
    "TenantMembership",
    "TenantModule",
    "User",
]
