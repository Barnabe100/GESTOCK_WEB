from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.errors import BusinessRuleError
from app.platform.context import DbSession, RegistryDep, RequestContext, require_permission
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.service import current_plan, get_subscription

router = APIRouter(tags=["subscription"])

SubscriptionView = Annotated[
    RequestContext, Depends(require_permission("subscription.subscription.view"))
]


class LimitOut(BaseModel):
    limit: int | None
    used: int


class SubscriptionOut(BaseModel):
    plan_code: str
    plan_name: str
    billing_period: str
    status: str
    effective_status: str
    started_at: datetime
    current_period_start: datetime
    current_period_end: datetime
    grace_days: int
    limits: dict[str, LimitOut]
    features: list[str]
    allowed_access: list[str]


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription_details(
    ctx: SubscriptionView, db: DbSession, registry: RegistryDep
) -> SubscriptionOut:
    subscription = get_subscription(db)
    if subscription is None:
        raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
    plan = current_plan(db)
    policy = PlanPolicy(db, plan, registry)
    return SubscriptionOut(
        plan_code=plan.code,
        plan_name=plan.name,
        billing_period=subscription.billing_period.value,
        status=subscription.status.value,
        effective_status=ctx.capabilities.subscription_status.value,
        started_at=subscription.started_at,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        grace_days=plan.grace_days,
        limits={
            code: LimitOut(limit=usage.limit, used=usage.used)
            for code, usage in policy.snapshot().items()
        },
        features=sorted(ctx.capabilities.features),
        allowed_access=sorted(ctx.capabilities.allowed_access),
    )
