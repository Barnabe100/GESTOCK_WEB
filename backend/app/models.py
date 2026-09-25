"""Import de tous les modèles : utilisé par Alembic (métadonnées complètes)."""

from app.core.db import Base
from app.modules.catalog.models import Article, Category
from app.modules.stock.models import (
    ExitReason,
    StockEntry,
    StockEntryLine,
    StockExit,
    StockExitLine,
    StockLevel,
    StockMovement,
)
from app.modules.suppliers.models import Supplier
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
    GeoCountry,
    Plan,
    PlanModule,
    SubscriptionAccessPolicy,
)
from app.platform.identity.models import AuthSession, User
from app.platform.onboarding.models import OnboardingStep
from app.platform.ratelimit.models import RateLimitHit
from app.platform.sequences.models import DocumentSequence
from app.platform.subscriptions.models import Subscription
from app.platform.tenancy.models import Site, Tenant, TenantModule

__all__ = [
    "DocumentSequence",
    "ExitReason",
    "StockEntry",
    "StockEntryLine",
    "StockExit",
    "StockExitLine",
    "StockLevel",
    "StockMovement",
    "Article",
    "Category",
    "Supplier",
    "AuditLog",
    "AuthSession",
    "Base",
    "BusinessProfile",
    "BusinessProfileModule",
    "GeoCountry",
    "MembershipRole",
    "MembershipSite",
    "OnboardingStep",
    "Plan",
    "PlanModule",
    "RateLimitHit",
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
