"""Lecture des licences et règles de période partagées par l'API des entreprises et la console.

- **Licence en vigueur** d'un abonnement : ``ISSUED`` et ``starts_at <= maintenant <
  ends_at`` ; ses conditions (modules, fonctionnalités, limites) remplacent celles du plan
  pour ce site (``subscription_terms``) : une licence payée ne change pas si le catalogue ou le
  plan de l'abonnement change ensuite (le nouveau plan vaut à la licence suivante).
- **Couverture** : licences ``ISSUED`` contiguës à partir de celle en vigueur ; l'abonnement
  du site reflète cette couverture (``sync_subscription``), sans nouveau moteur de statut
  (échéance, grâce et politiques d'accès inchangées).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.catalog.models import Plan
from app.platform.licensing.models import License, LicenseStatus
from app.platform.subscriptions.models import BillingPeriod, Subscription, SubscriptionStatus
from app.platform.subscriptions.plan_policy import PlanTerms
from app.platform.subscriptions.service import add_months, subscription_plan, tenant_subscriptions
from app.shared.clock import utcnow


def local_midnight(day: date, timezone: str) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(timezone))


def local_day(moment: datetime, timezone: str) -> date:
    return moment.astimezone(ZoneInfo(timezone)).date()


def period_last_day(valid_from: date, billing_period: BillingPeriod) -> date:
    """Dernier jour (inclus) d'une période commençant ``valid_from`` : 01/10/2026 annuel →
    30/09/2027."""
    months = 1 if billing_period is BillingPeriod.MONTHLY else 12
    start = datetime.combine(valid_from, time.min)
    return add_months(start, months).date() - timedelta(days=1)


def license_in_force(session: Session, subscription_id: uuid.UUID, now: datetime) -> License | None:
    return session.scalars(
        select(License)
        .where(
            License.subscription_id == subscription_id,
            License.status == LicenseStatus.ISSUED,
            License.starts_at <= now,
            License.ends_at > now,
        )
        .order_by(License.starts_at.desc(), License.id)
        .limit(1)
    ).one_or_none()


def subscription_licenses(session: Session, subscription_id: uuid.UUID) -> list[License]:
    return list(
        session.scalars(
            select(License)
            .where(License.subscription_id == subscription_id)
            .order_by(License.starts_at, License.license_version, License.id)
        )
    )


def terms_of_license(license: License) -> PlanTerms:
    return PlanTerms(
        plan_code=license.plan_code,
        modules=frozenset(license.modules),
        features=frozenset(license.features),
        limits=dict(license.limits),
        license_id=license.id,
    )


def subscription_terms(
    session: Session, subscription: Subscription, plan: Plan, now: datetime
) -> PlanTerms:
    """Conditions en vigueur de l'abonnement : licence en vigueur, sinon plan."""
    current = license_in_force(session, subscription.id, now)
    return terms_of_license(current) if current else PlanTerms.of_plan(plan)


def current_terms(
    session: Session, subscription: Subscription, now: datetime | None = None
) -> PlanTerms:
    return subscription_terms(
        session, subscription, subscription_plan(session, subscription), now or utcnow()
    )


def tenant_terms(session: Session, now: datetime | None = None) -> list[PlanTerms]:
    """Conditions en vigueur de chaque abonnement du tenant actif (RLS)."""
    return [current_terms(session, s, now) for s in tenant_subscriptions(session)]


@dataclass(frozen=True)
class Coverage:
    first_day: date
    last_day: date


def coverage(licenses: list[License], today: date) -> Coverage | None:
    """Période couverte sans interruption par les licences ``ISSUED`` à partir de celle en
    vigueur ``today`` (renouvellements anticipés inclus) ; ``None`` si aucune ne couvre ce
    jour."""
    issued = sorted(
        (lic for lic in licenses if lic.status is LicenseStatus.ISSUED),
        key=lambda lic: (lic.valid_from, lic.valid_until),
    )
    current = [lic for lic in issued if lic.valid_from <= today <= lic.valid_until]
    if not current:
        return None
    first = min(lic.valid_from for lic in current)
    last = max(lic.valid_until for lic in current)
    for lic in issued:
        if lic.valid_from <= last + timedelta(days=1) and lic.valid_until > last:
            last = lic.valid_until
    return Coverage(first_day=first, last_day=last)


def next_valid_from(licenses: list[License], today: date) -> date:
    """Début d'une nouvelle licence : lendemain de la couverture en cours (renouvellement : la
    période continue, aucun jour perdu ni offert), sinon aujourd'hui."""
    covered = coverage(licenses, today)
    return covered.last_day + timedelta(days=1) if covered else today


def sync_subscription(
    subscription: Subscription,
    licenses: list[License],
    *,
    timezone: str,
    now: datetime,
    revoked: License | None = None,
) -> None:
    """Aligne l'abonnement du site sur ses licences : couverture en cours → ``active`` sur
    cette période ; licence en vigueur révoquée sans autre couverture → ``suspended``
    (politique d'accès correspondante ; les données sont conservées). Sinon inchangé : une
    couverture échue suit le statut effectif habituel (grâce puis expiration)."""
    today = local_day(now, timezone)
    covered = coverage(licenses, today)
    if covered is not None:
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.current_period_start = local_midnight(covered.first_day, timezone)
        subscription.current_period_end = local_midnight(
            covered.last_day + timedelta(days=1), timezone
        )
        return
    if revoked is not None and revoked.starts_at <= now < revoked.ends_at:
        subscription.status = SubscriptionStatus.SUSPENDED
