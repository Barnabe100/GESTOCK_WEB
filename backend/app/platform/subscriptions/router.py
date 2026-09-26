import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.core.errors import BusinessRuleError
from app.platform.context import DbSession, RegistryDep, RequestContext, require_permission
from app.platform.subscriptions.models import SubscriptionPaymentStatus
from app.platform.subscriptions.payments import SubscriptionPaymentService
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.schemas import SubscriptionPaymentCreate, SubscriptionPaymentOut
from app.platform.subscriptions.service import current_plan, get_subscription
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(tags=["subscription"])

SubscriptionView = Annotated[
    RequestContext, Depends(require_permission("subscription.subscription.view"))
]
PaymentDeclare = Annotated[
    RequestContext, Depends(require_permission("subscription.payment.declare"))
]


class LimitOut(BaseModel):
    limit: int | None
    used: int


class SubscriptionOut(BaseModel):
    id: uuid.UUID
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
        id=subscription.id,
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


# --- Paiements de l'abonnement (Phase 3.3-A, ADR-0032) ----------------------------------------


@router.get("/subscription/payments", response_model=Page[SubscriptionPaymentOut])
def list_subscription_payments(
    ctx: SubscriptionView,
    db: DbSession,
    params: Annotated[PageParams, Depends(page_params)],
    status: SubscriptionPaymentStatus | None = None,
) -> Page[SubscriptionPaymentOut]:
    """Paiements déclarés par l'entreprise et leur décision (tri : ``created_at`` par défaut
    décroissant, ``amount``, ``status``, ``period_start``)."""
    rows, total = SubscriptionPaymentService(db, ctx).search(params, status)
    return Page(
        items=[SubscriptionPaymentOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/subscription/payments/{payment_id}", response_model=SubscriptionPaymentOut)
def get_subscription_payment(
    payment_id: uuid.UUID, ctx: SubscriptionView, db: DbSession
) -> SubscriptionPaymentOut:
    return SubscriptionPaymentOut.model_validate(
        SubscriptionPaymentService(db, ctx).get(payment_id)
    )


@router.post(
    "/subscription/payments",
    response_model=SubscriptionPaymentOut,
    status_code=status.HTTP_201_CREATED,
)
def declare_subscription_payment(
    body: SubscriptionPaymentCreate, ctx: PaymentDeclare, db: DbSession, response: Response
) -> SubscriptionPaymentOut:
    """Déclare un paiement (``PENDING``) ; seul TechNova le confirme ou le rejette. Même clé
    d'idempotence et même demande : ``200`` et la déclaration existante (aucun doublon)."""
    payment, replayed = SubscriptionPaymentService(db, ctx).declare(body)
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK
    return SubscriptionPaymentOut.model_validate(payment)
