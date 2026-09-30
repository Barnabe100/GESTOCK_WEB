"""Devis de la prochaine période d'un site (Phase 3.3-B4, ADR-0036) — calculé par le serveur,
jamais fourni par le client.

1 site = 1 abonnement = 1 licence : un devis porte toujours sur l'abonnement d'UN site.

- **Période** (R2, R3) : ``next_valid_from`` — lendemain de la couverture en cours ; pendant la
  période de grâce, lendemain de la licence échue (la grâce ne décale pas la période
  contractuelle) ; au-delà, jour même (jamais rétroactif). Durée = période de facturation.
- **Plan** (R4) : celui de l'abonnement, qui s'appliquera à la prochaine licence (il peut
  différer du plan de la licence en vigueur après un changement de plan).
- **Postes** (R1) : ceux de la licence de référence du site ; le nombre demandé à la
  souscription ne sert qu'à la première licence. Une autre valeur n'est retenue que si le
  client la demande **explicitement**, et TechNova la confirme à la génération.
- **Montant** : tarif **figé** de l'abonnement (prix de base + postes supplémentaires) ; nul si
  aucun tarif n'est figé (offre sur devis : le client déclare le montant convenu).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.platform.licensing.service import (
    coverage,
    grace_days_of,
    local_day,
    next_valid_from,
    period_last_day,
    reference_license,
    subscription_licenses,
)
from app.platform.subscriptions.models import BillingPeriod, Subscription, SubscriptionStatus
from app.platform.subscriptions.service import subscription_tariff

INITIAL = "initial"
RENEWAL = "renewal"
# Pas de renouvellement pour un abonnement résilié.
NOT_RENEWABLE = (SubscriptionStatus.CANCELLED,)


@dataclass(frozen=True)
class RenewalQuote:
    subscription_id: uuid.UUID
    site_id: uuid.UUID | None
    # ``initial`` : aucune licence encore (premier paiement) ; ``renewal`` : période suivante.
    kind: str
    plan_code: str
    billing_period: BillingPeriod
    valid_from: date
    valid_until: date
    activations: int
    # Postes de la licence de référence (nul : aucune licence).
    current_activations: int | None
    activations_explicit: bool
    amount: Decimal | None
    currency: str | None
    # Dernier jour couvert par les licences en cours (nul : aucune couverture aujourd'hui).
    coverage_end: date | None
    # La nouvelle période commence avant aujourd'hui (continuité pendant la grâce).
    grace_continuity: bool
    # Le renouvellement est pertinent (action « Renouveler » proposée).
    renewal_due: bool


def renewal_quote(
    session: Session,
    subscription: Subscription,
    *,
    timezone: str,
    now: datetime,
    notice_steps: tuple[int, ...] = (30,),
    requested_activations: int | None = None,
) -> RenewalQuote:
    licenses = subscription_licenses(session, subscription.id)
    today = local_day(now, timezone)
    valid_from = next_valid_from(licenses, today, grace_days_of(session, licenses, subscription))
    reference = reference_license(licenses, now)
    current = reference.max_activations if reference is not None else None
    default = current if current is not None else subscription.requested_activations
    explicit = requested_activations is not None and requested_activations != default
    activations = requested_activations if requested_activations is not None else default
    covered = coverage(licenses, today)
    kind = RENEWAL if licenses else INITIAL
    horizon = max((step for step in notice_steps if step >= 0), default=30)
    renewal_due = (
        kind == RENEWAL
        and subscription.status not in NOT_RENEWABLE
        and (covered is None or (covered.last_day - today).days <= horizon)
    )
    return RenewalQuote(
        subscription_id=subscription.id,
        site_id=subscription.site_id,
        kind=kind,
        plan_code=subscription.plan_code,
        billing_period=subscription.billing_period,
        valid_from=valid_from,
        valid_until=period_last_day(valid_from, subscription.billing_period),
        activations=activations,
        current_activations=current,
        activations_explicit=explicit,
        amount=subscription_tariff(subscription).amount(activations),
        currency=subscription.currency_at_subscription,
        coverage_end=covered.last_day if covered else None,
        grace_continuity=valid_from < today,
        renewal_due=renewal_due,
    )
