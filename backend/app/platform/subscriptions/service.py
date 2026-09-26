"""Règles d'abonnement centralisées : statut effectif, politique d'accès, limites du plan."""

import calendar
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.core.errors import BusinessRuleError, NotFoundError
from app.platform.audit.service import record_audit
from app.platform.catalog.models import Plan, SubscriptionAccessPolicy
from app.platform.subscriptions.models import BillingPeriod, Subscription, SubscriptionStatus

_RUNNING = {SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE}


def add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def period_end(start: datetime, billing_period: BillingPeriod) -> datetime:
    return add_months(start, 1 if billing_period is BillingPeriod.MONTHLY else 12)


def subscription_price(
    plan: Plan, billing_period: BillingPeriod
) -> tuple[Decimal | None, str | None]:
    """Prix de la période souscrite, à figer dans l'abonnement : celui du plan si TechNova a
    ouvert cette période (prix et devise), sinon aucun."""
    if billing_period is BillingPeriod.MONTHLY and plan.monthly_price_enabled:
        return plan.monthly_price, plan.currency
    if billing_period is BillingPeriod.ANNUAL and plan.annual_price_enabled:
        return plan.annual_price, plan.currency
    return None, None


@dataclass(frozen=True)
class PlanChange:
    previous_plan: str
    plan: str
    previous_price: Decimal | None
    previous_currency: str | None
    price: Decimal | None
    currency: str | None

    @property
    def changed(self) -> bool:
        return self.previous_plan != self.plan

    @property
    def price_changed(self) -> bool:
        return (self.previous_price, self.previous_currency) != (self.price, self.currency)


def apply_plan_change(subscription: Subscription, plan: Plan) -> PlanChange:
    """Nouveau plan de l'abonnement : les nouvelles conditions commerciales (prix et devise de la
    période souscrite) sont **figées à ce moment**, comme à la souscription ; rien n'est
    rétroactif (l'ancien prix reste dans le journal). Période inchangée, aucune proratisation."""
    previous = PlanChange(
        previous_plan=subscription.plan_code,
        plan=subscription.plan_code,
        previous_price=subscription.price_at_subscription,
        previous_currency=subscription.currency_at_subscription,
        price=subscription.price_at_subscription,
        currency=subscription.currency_at_subscription,
    )
    if subscription.plan_code == plan.code:
        return previous
    price, currency = subscription_price(plan, subscription.billing_period)
    subscription.plan_code = plan.code
    subscription.price_at_subscription = price
    subscription.currency_at_subscription = currency
    return PlanChange(
        previous_plan=previous.previous_plan,
        plan=plan.code,
        previous_price=previous.previous_price,
        previous_currency=previous.previous_currency,
        price=price,
        currency=currency,
    )


def effective_status(
    subscription: Subscription, grace_days: int, now: datetime
) -> SubscriptionStatus:
    """Statut réel à l'instant ``now`` : l'échéance est évaluée à la lecture (pas de tâche
    planifiée nécessaire). Un essai échu expire sans délai de grâce."""
    stored = subscription.status
    if stored not in _RUNNING or now < subscription.current_period_end:
        return stored
    if stored is SubscriptionStatus.TRIAL:
        return SubscriptionStatus.EXPIRED
    if now < subscription.current_period_end + timedelta(days=grace_days):
        return SubscriptionStatus.PAST_DUE
    return SubscriptionStatus.EXPIRED


def allowed_access(session: Session, status: SubscriptionStatus) -> frozenset[str]:
    policy = session.get(SubscriptionAccessPolicy, status.value)
    # Pas de politique = aucun accès (échec sûr) ; le catalogue valide qu'elles existent toutes.
    return frozenset(policy.allowed_access) if policy else frozenset()


def get_subscription(session: Session) -> Subscription | None:
    """Abonnement du tenant actif (filtré par RLS)."""
    return session.scalars(select(Subscription)).one_or_none()


def current_plan(session: Session) -> Plan:
    """Plan de l'abonnement du tenant actif."""
    subscription = get_subscription(session)
    plan = session.get(Plan, subscription.plan_code) if subscription else None
    if plan is None:
        raise BusinessRuleError("Aucun plan actif", code="subscription_missing")
    return plan


def change_plan(
    session: Session, tenant_id: uuid.UUID, plan_code: str, *, actor: str
) -> tuple[str, str]:
    """Change le plan de l'abonnement d'un tenant (opération TechNova), audité. Les données
    sont conservées : ce que le nouveau plan n'inclut pas (modules, fonctionnalités) cesse
    simplement d'être accordé par les capacités. Renvoie (ancien plan, nouveau plan)."""
    set_db_context(session, tenant_id=tenant_id, user_id=None)
    subscription = get_subscription(session)
    if subscription is None:
        raise NotFoundError("Abonnement introuvable", code="subscription_missing")
    plan = session.get(Plan, plan_code)
    if plan is None or not plan.is_active:
        raise BusinessRuleError(f"Plan inconnu : {plan_code}", code="unknown_plan")
    change = apply_plan_change(subscription, plan)
    if change.changed:
        data: dict[str, str | None] = {
            "actor": actor,
            "previous_plan": change.previous_plan,
            "plan": change.plan,
        }
        if change.price_changed:
            data |= price_audit(change)
        record_audit(
            session,
            action="subscription.plan_changed",
            tenant_id=tenant_id,
            user_id=None,
            entity_type="subscription",
            entity_id=subscription.id,
            data=data,
        )
        session.flush()
    return change.previous_plan, change.plan


def price_audit(change: PlanChange) -> dict[str, str | None]:
    """Prix figés avant / après (montants en chaînes, jamais en flottants)."""

    def amount(value: Decimal | None) -> str | None:
        return format(value, "f") if value is not None else None

    return {
        "previous_price": amount(change.previous_price),
        "previous_currency": change.previous_currency,
        "price": amount(change.price),
        "currency": change.currency,
    }
