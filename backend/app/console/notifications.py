"""Job des rappels d'échéance (Phase 3.3-B4, ADR-0036) : ``stockmanager notifications run``.

Lancé chaque jour par cron / systemd (aucune autre infrastructure), avec le rôle SQL de la
console (lecture des abonnements des sites, insertion des notifications ; aucune donnée métier).

Règles :

- **Échéance** d'un abonnement de site : dernier jour couvert (licences, sinon période), dans le
  fuseau de l'entreprise. J0 = « expire aujourd'hui », J+1 = premier jour non couvert.
- **Étapes** : ``SM_RENEWAL_NOTICE_DAYS`` (défaut 30,15,10,5,1,0,-1,-7) ; essais : seulement
  J-5, J-1, J0 (parmi les étapes configurées).
- **Sites concernés** : abonnements actifs, en grâce ou expirés (jusqu'à la dernière étape) ;
  jamais ``pending_activation``, ``suspended`` ni ``cancelled`` ; entreprise suspendue exclue.
- **Idempotence** : unicité ``(abonnement, type, étape, échéance)`` ; relancé, le job ne crée
  rien de plus. Un renouvellement change l'échéance : l'ancienne série s'arrête, une nouvelle
  commencera à son heure.
- **Pas de rafale** : seule l'étape la plus récente due est envoyée ; les étapes antérieures
  jamais enregistrées (job manqué) sont notées ``SKIPPED``.
- **Exclusion mutuelle** : verrou consultatif transactionnel ; une seconde exécution
  simultanée s'arrête sans rien écrire.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_platform_audit
from app.platform.catalog.models import Plan
from app.platform.licensing.service import local_day
from app.platform.notifications.models import (
    SUBSCRIPTION_EXPIRY,
    Notification,
    NotificationStatus,
)
from app.platform.subscriptions.models import Subscription, SubscriptionStatus
from app.platform.subscriptions.service import effective_status
from app.platform.tenancy.models import Tenant, TenantStatus

# Clé du verrou consultatif (constante du job).
LOCK_KEY = 3_300_004
TRIAL_STEPS = frozenset({5, 1, 0})
NOTIFIED_STATUSES = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.EXPIRED,
    SubscriptionStatus.TRIAL,
)


@dataclass
class RunResult:
    locked: bool = False
    examined: int = 0
    sent: int = 0
    skipped: int = 0
    notifications: list[uuid.UUID] = field(default_factory=list)


def reference_date(subscription: Subscription, timezone: str) -> date:
    """Dernier jour couvert : ``current_period_end`` est l'instant où la couverture cesse
    (minuit local du lendemain pour une licence)."""
    return local_day(subscription.current_period_end - timedelta(microseconds=1), timezone)


def due_step(steps: tuple[int, ...], days_left: int) -> int | None:
    """Étape la plus récente due (``days_left`` = jours restants jusqu'à l'échéance) : la plus
    petite étape ≥ ``days_left`` ; aucune avant la première ni après la dernière étape."""
    if not steps or days_left > max(steps) or days_left < min(steps):
        return None
    return min(step for step in steps if step >= days_left)


class RenewalNoticeJob:
    def __init__(
        self, db: Session, steps: tuple[int, ...], now: datetime, actor: PlatformActor
    ) -> None:
        self.db = db
        self.steps = steps
        self.now = now
        self.actor = actor

    def run(self) -> RunResult:
        result = RunResult()
        if not self.db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY}):
            result.locked = True
            return result
        rows = self.db.execute(
            select(Subscription, Tenant.timezone, Plan.grace_days)
            .join(Tenant, Tenant.id == Subscription.tenant_id)
            .join(Plan, Plan.code == Subscription.plan_code)
            .where(
                Subscription.site_id.is_not(None),
                Subscription.status.in_(NOTIFIED_STATUSES),
                Tenant.status == TenantStatus.ACTIVE,
            )
            .order_by(Subscription.tenant_id, Subscription.id)
        ).all()
        for subscription, timezone, grace_days in rows:
            result.examined += 1
            self._notify(subscription, timezone, grace_days, result)
        self.db.flush()
        record_platform_audit(
            self.db,
            actor=self.actor,
            action="notifications.run",
            target_type="notifications",
            data={
                "examined": result.examined,
                "sent": result.sent,
                "skipped": result.skipped,
                "steps": list(self.steps),
            },
        )
        return result

    def _notify(
        self, subscription: Subscription, timezone: str, grace_days: int, result: RunResult
    ) -> None:
        trial = subscription.status is SubscriptionStatus.TRIAL
        steps = tuple(s for s in self.steps if s in TRIAL_STEPS) if trial else self.steps
        reference = reference_date(subscription, timezone)
        days_left = (reference - local_day(self.now, timezone)).days
        step = due_step(steps, days_left)
        if step is None:
            return
        recorded = set(
            self.db.scalars(
                select(Notification.step).where(
                    Notification.tenant_id == subscription.tenant_id,
                    Notification.subscription_id == subscription.id,
                    Notification.kind == SUBSCRIPTION_EXPIRY,
                    Notification.reference_date == reference,
                )
            )
        )
        if step in recorded:
            return
        data: dict[str, Any] = {
            "days_left": days_left,
            "plan_code": subscription.plan_code,
            "effective_status": effective_status(subscription, grace_days, self.now).value,
            "trial": trial,
        }
        # Étapes antérieures manquées : notées, jamais envoyées en rafale.
        for missed in sorted(s for s in steps if s > step and s not in recorded):
            if self._insert(subscription, missed, reference, NotificationStatus.SKIPPED, data):
                result.skipped += 1
        created = self._insert(subscription, step, reference, NotificationStatus.SENT, data)
        if created is not None:
            result.sent += 1
            result.notifications.append(created)

    def _insert(
        self,
        subscription: Subscription,
        step: int,
        reference: date,
        status: NotificationStatus,
        data: dict[str, Any],
    ) -> uuid.UUID | None:
        """Insertion idempotente (contrainte d'unicité : ``ON CONFLICT DO NOTHING``)."""
        notification_id = uuid.uuid4()
        inserted = self.db.execute(
            insert(Notification)
            .values(
                id=notification_id,
                tenant_id=subscription.tenant_id,
                site_id=subscription.site_id,
                subscription_id=subscription.id,
                kind=SUBSCRIPTION_EXPIRY,
                step=step,
                reference_date=reference,
                status=status,
                data=data,
                created_at=self.now,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "subscription_id", "kind", "step", "reference_date"]
            )
            .returning(Notification.id)
        ).scalar_one_or_none()
        return inserted
