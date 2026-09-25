from fastapi import APIRouter, Depends

from app.api.v1 import health
from app.platform.access.router import router as access_router
from app.platform.audit.router import router as audit_router
from app.platform.capabilities.router import router as capabilities_router
from app.platform.context import require_module
from app.platform.identity.router import router as identity_router
from app.platform.onboarding.router import router as onboarding_router
from app.platform.profiles.router import router as profiles_router
from app.platform.public.router import router as public_router
from app.platform.registry import ModuleRegistry, ModuleStatus, get_registry
from app.platform.subscriptions.router import router as subscription_router
from app.platform.tenancy.router import router as tenancy_router


def mount_module_routers(api_router: APIRouter, registry: ModuleRegistry) -> None:
    """Monte les routeurs des modules métier disponibles, chacun protégé par son module."""
    for manifest in registry.all():
        if manifest.core or manifest.router is None or manifest.status != ModuleStatus.AVAILABLE:
            continue
        module_required = [Depends(require_module(manifest.code, registry))]
        api_router.include_router(
            manifest.router, prefix=manifest.url_prefix, dependencies=module_required
        )
        for prefix, extra_router in manifest.extra_routers:
            api_router.include_router(extra_router, prefix=prefix, dependencies=module_required)


def build_api_router(registry: ModuleRegistry | None = None) -> APIRouter:
    api_router = APIRouter()
    api_router.include_router(health.router)
    api_router.include_router(identity_router)
    api_router.include_router(public_router)
    api_router.include_router(capabilities_router)
    api_router.include_router(tenancy_router)
    api_router.include_router(profiles_router)
    api_router.include_router(access_router)
    api_router.include_router(subscription_router)
    api_router.include_router(audit_router)
    api_router.include_router(onboarding_router)
    mount_module_routers(api_router, registry or get_registry())
    return api_router
