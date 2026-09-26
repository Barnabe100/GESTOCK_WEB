import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.core.errors import BusinessRuleError
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.models import BusinessProfile
from app.platform.context import (
    DbSession,
    NowDep,
    RegistryDep,
    RequestContext,
    require_permission,
)
from app.platform.licensing.activations import active_count
from app.platform.licensing.schemas import LicenseSummary, license_summary
from app.platform.licensing.service import reference_license, subscription_licenses
from app.platform.subscriptions.models import Subscription, SubscriptionPaymentStatus
from app.platform.subscriptions.payments import SubscriptionPaymentService
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.schemas import SubscriptionPaymentCreate, SubscriptionPaymentOut
from app.platform.subscriptions.service import site_subscription, tenant_subscriptions
from app.platform.tenancy.models import Site
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


class SiteRef(BaseModel):
    id: uuid.UUID
    name: str
    code: str


class SubscriptionOut(BaseModel):
    """Abonnement d'un site (1 site = 1 abonnement, ADR-0033) ; ``site`` nul : abonnement pris
    à l'inscription, pas encore rattaché (il le sera au premier site créé)."""

    id: uuid.UUID
    site: SiteRef | None
    plan_code: str
    plan_name: str
    billing_period: str
    status: str
    effective_status: str
    started_at: datetime
    current_period_start: datetime
    current_period_end: datetime
    grace_days: int
    requested_activations: int
    limits: dict[str, LimitOut]
    features: list[str]
    allowed_access: list[str]
    # Licence en vigueur du site, sinon la plus récente (ADR-0034) ; nulle : aucune licence.
    license: LicenseSummary | None


def _subscription_out(
    db: DbSession,
    registry: RegistryDep,
    ctx: RequestContext,
    subscription: Subscription,
    now: datetime,
) -> SubscriptionOut:
    profile = db.get(BusinessProfile, ctx.tenant.business_profile_code)
    assert profile is not None
    grant = CapabilityService(db, registry).grant(subscription, profile, now)
    site = db.get(Site, subscription.site_id) if subscription.site_id else None
    policy = PlanPolicy(db, grant.terms, registry)
    return SubscriptionOut(
        id=subscription.id,
        site=SiteRef(id=site.id, name=site.name, code=site.code) if site else None,
        plan_code=grant.plan.code,
        plan_name=grant.plan.name,
        billing_period=subscription.billing_period.value,
        status=subscription.status.value,
        effective_status=grant.status.value,
        started_at=subscription.started_at,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        grace_days=grant.plan.grace_days,
        requested_activations=subscription.requested_activations,
        limits={
            code: LimitOut(limit=usage.limit, used=usage.used)
            for code, usage in policy.snapshot(subscription.site_id).items()
        },
        features=sorted(grant.features),
        allowed_access=sorted(grant.access),
        license=license_summary(
            reference_license(subscription_licenses(db, subscription.id), now),
            now,
            active_count(db, subscription.id),
        ),
    )


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(
    ctx: SubscriptionView, db: DbSession, registry: RegistryDep, now: NowDep
) -> list[SubscriptionOut]:
    """Abonnements de l'entreprise, un par site (sites accessibles au membre seulement), et
    l'abonnement non rattaché éventuel."""
    visible = ctx.capabilities.accessible_site_ids
    return [
        _subscription_out(db, registry, ctx, s, now)
        for s in tenant_subscriptions(db)
        if s.site_id is None or s.site_id in visible
    ]


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription_details(
    ctx: SubscriptionView, db: DbSession, registry: RegistryDep, now: NowDep
) -> SubscriptionOut:
    """Abonnement du site sélectionné (``X-Site-Id``) ; sans site sélectionné, l'abonnement
    représentatif de l'entreprise (celui des capacités)."""
    if ctx.site is not None:
        subscription = site_subscription(db, ctx.site.id)
    else:
        subscription = db.get(Subscription, ctx.capabilities.subscription_id)
    if subscription is None:
        raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
    return _subscription_out(db, registry, ctx, subscription, now)


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
