import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import BusinessRuleError, NotFoundError
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.models import Plan
from app.platform.context import (
    DbSession,
    NowDep,
    RegistryDep,
    RequestContext,
    SettingsDep,
    require_permission,
)
from app.platform.licensing.activations import active_count
from app.platform.licensing.renewal import renewal_quote
from app.platform.licensing.schemas import LicenseSummary, license_summary
from app.platform.licensing.service import reference_license, subscription_licenses
from app.platform.subscriptions.models import Subscription, SubscriptionPaymentStatus
from app.platform.subscriptions.payments import SubscriptionPaymentService
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.schemas import SubscriptionPaymentCreate, SubscriptionPaymentOut
from app.platform.subscriptions.service import site_subscription, tenant_subscriptions
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Money, Page

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


class PlanRef(BaseModel):
    code: str
    name: str


class RenewalQuoteOut(BaseModel):
    """Prochaine période d'un site, **calculée par le serveur** (3.3-B4) : le client ne choisit
    ni la période, ni le montant ; il peut seulement demander explicitement un autre nombre de
    postes (``requested_activations``), que TechNova confirme à la génération de la licence."""

    subscription_id: uuid.UUID
    site_id: uuid.UUID | None
    kind: str
    plan: PlanRef
    billing_period: str
    valid_from: date
    valid_until: date
    activations: int
    current_activations: int | None
    activations_explicit: bool
    amount: Money | None
    currency: str | None
    coverage_end: date | None
    grace_continuity: bool
    renewal_due: bool


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
    # R4 (3.3-B4) : offre dont les droits sont en vigueur (licence en vigueur, sinon plan de
    # l'abonnement) et, si différente, offre de la prochaine licence.
    effective_plan: PlanRef
    next_plan: PlanRef | None
    # Prochaine période (devis serveur) et pertinence du renouvellement.
    renewal: RenewalQuoteOut


def _plan_ref(db: DbSession, code: str) -> PlanRef:
    plan = db.get(Plan, code)
    return PlanRef(code=code, name=plan.name if plan else code)


def _quote_out(
    db: DbSession,
    ctx: RequestContext,
    subscription: Subscription,
    now: datetime,
    settings: Settings,
    requested_activations: int | None = None,
) -> RenewalQuoteOut:
    quote = renewal_quote(
        db,
        subscription,
        timezone=ctx.tenant.timezone,
        now=now,
        notice_steps=settings.renewal_notice_steps,
        requested_activations=requested_activations,
    )
    return RenewalQuoteOut(
        subscription_id=quote.subscription_id,
        site_id=quote.site_id,
        kind=quote.kind,
        plan=_plan_ref(db, quote.plan_code),
        billing_period=quote.billing_period.value,
        valid_from=quote.valid_from,
        valid_until=quote.valid_until,
        activations=quote.activations,
        current_activations=quote.current_activations,
        activations_explicit=quote.activations_explicit,
        amount=quote.amount,
        currency=quote.currency or ctx.tenant.currency,
        coverage_end=quote.coverage_end,
        grace_continuity=quote.grace_continuity,
        renewal_due=quote.renewal_due,
    )


def _subscription_out(
    db: DbSession,
    registry: RegistryDep,
    ctx: RequestContext,
    subscription: Subscription,
    now: datetime,
    settings: Settings,
) -> SubscriptionOut:
    service = CapabilityService(db, registry)
    # Profil du site de l'abonnement (jamais celui du tenant).
    grant = service.grant(subscription, service.subscription_profile(subscription, ctx.tenant), now)
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
        effective_plan=_plan_ref(db, grant.terms.plan_code),
        next_plan=(
            _plan_ref(db, subscription.plan_code)
            if grant.terms.plan_code != subscription.plan_code
            else None
        ),
        renewal=_quote_out(db, ctx, subscription, now, settings),
    )


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(
    ctx: SubscriptionView,
    db: DbSession,
    registry: RegistryDep,
    now: NowDep,
    settings: SettingsDep,
) -> list[SubscriptionOut]:
    """Abonnements de l'entreprise, un par site (sites accessibles au membre seulement), et
    l'abonnement non rattaché éventuel."""
    visible = ctx.capabilities.accessible_site_ids
    return [
        _subscription_out(db, registry, ctx, s, now, settings)
        for s in tenant_subscriptions(db)
        if s.site_id is None or s.site_id in visible
    ]


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription_details(
    ctx: SubscriptionView,
    db: DbSession,
    registry: RegistryDep,
    now: NowDep,
    settings: SettingsDep,
) -> SubscriptionOut:
    """Abonnement du site sélectionné (``X-Site-Id``) ; sans site sélectionné, l'abonnement
    représentatif de l'entreprise (celui des capacités)."""
    if ctx.site is not None:
        subscription = site_subscription(db, ctx.site.id)
    else:
        subscription = db.get(Subscription, ctx.capabilities.subscription_id)
    if subscription is None:
        raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
    return _subscription_out(db, registry, ctx, subscription, now, settings)


@router.get("/subscriptions/{subscription_id}/renewal-quote", response_model=RenewalQuoteOut)
def get_renewal_quote(
    subscription_id: uuid.UUID,
    ctx: SubscriptionView,
    db: DbSession,
    now: NowDep,
    settings: SettingsDep,
    requested_activations: Annotated[int | None, Query(ge=1, le=10_000)] = None,
) -> RenewalQuoteOut:
    """Devis de la prochaine période d'un site (période, plan, postes, montant) calculé par le
    serveur ; ``requested_activations`` : chiffrer un changement explicite du nombre de
    postes. Abonnement d'un site non accessible : ``404``."""
    subscription = db.get(Subscription, subscription_id)
    visible = ctx.capabilities.accessible_site_ids
    if subscription is None or (
        subscription.site_id is not None and subscription.site_id not in visible
    ):
        raise NotFoundError("Abonnement introuvable", code="subscription_not_found")
    return _quote_out(db, ctx, subscription, now, settings, requested_activations)


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
