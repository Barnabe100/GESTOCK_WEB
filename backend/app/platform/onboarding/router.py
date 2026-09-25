from typing import Annotated

from fastapi import APIRouter, Depends

from app.platform.context import DbSession, RegistryDep, RequestContext, require_permission
from app.platform.onboarding.schemas import OnboardingOut, OnboardingStepUpdate
from app.platform.onboarding.service import OnboardingService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

View = Annotated[RequestContext, Depends(require_permission("organization.onboarding.view"))]
Manage = Annotated[RequestContext, Depends(require_permission("organization.onboarding.manage"))]


@router.get("", response_model=OnboardingOut)
def get_onboarding(ctx: View, db: DbSession, registry: RegistryDep) -> OnboardingOut:
    """Progression persistée ; les étapes manquantes sont créées et les progrès constatés
    enregistrés (idempotent)."""
    result = OnboardingService(db, registry).overview(ctx)
    db.commit()
    return result


@router.patch("/steps/{code}", response_model=OnboardingOut)
def update_step(
    code: str, body: OnboardingStepUpdate, ctx: Manage, db: DbSession, registry: RegistryDep
) -> OnboardingOut:
    result = OnboardingService(db, registry).start(ctx, code, body.status)
    db.commit()
    return result
