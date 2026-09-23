"""Règles d'abonnement centralisées : statut effectif, politique d'accès, limites du plan."""

import calendar
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
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
