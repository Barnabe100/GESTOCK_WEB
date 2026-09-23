from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.errors import BusinessRuleError
from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.context import DbSession, RequestContext, require_permission
from app.platform.subscriptions.service import get_subscription
from app.platform.tenancy.models import Site
from app.platform.tenancy.service import current_plan

router = APIRouter(tags=["subscription"])

SubscriptionView = Annotated[
    RequestContext, Depends(require_permission("subscription.subscription.view"))
]


class UsageOut(BaseModel):
    sites: int
    users: int


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
    limits: dict[str, int]
    allowed_access: list[str]
    usage: UsageOut


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription_details(ctx: SubscriptionView, db: DbSession) -> SubscriptionOut:
    subscription = get_subscription(db)
    if subscription is None:
        raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
    plan = current_plan(db)
    sites = db.scalar(select(func.count()).select_from(Site).where(Site.is_active.is_(True)))
    users = db.scalar(
        select(func.count())
        .select_from(TenantMembership)
        .where(TenantMembership.status == MembershipStatus.ACTIVE)
    )
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
        limits={k: int(v) for k, v in plan.limits.items()},
        allowed_access=sorted(ctx.capabilities.allowed_access),
        usage=UsageOut(sites=sites or 0, users=users or 0),
    )
