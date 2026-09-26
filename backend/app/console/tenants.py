"""Tenants et abonnements vus par TechNova (Phase 3.2-G, ADR-0031).

La console lit des **métadonnées plateforme** (identité, profil, statut, abonnement, compteurs)
et agit sur deux statuts **distincts** :

- ``Tenant.status`` (``active`` / ``suspended``) : suspension administrative par TechNova ; un
  tenant suspendu n'est plus accessible (``403 tenant_suspended``), rien n'est supprimé ;
- ``Subscription.status`` : statut stocké, dont le statut **effectif** (échéance, grâce) est
  calculé à la lecture (``effective_status``, et son équivalent SQL pour les listes).

Actions (raison obligatoire, verrou de ligne, état compatible exigé : une requête rejouée ne
produit jamais un second effet) : suspension, réactivation, activation manuelle transitoire
(``pending_activation`` / ``trial`` → ``active`` ; **aucun paiement** n'est créé ni confirmé),
prolongation, changement de plan (prix figé à ce moment, rien de rétroactif).

**Double audit**, dans la même transaction : journal de la plateforme (auteur TechNova, avant,
après, raison) + entrée miroir dans le journal du tenant (sans identité de l'agent).

Le rôle SQL de la console n'a accès qu'aux colonnes nécessaires (migration 0018) : les requêtes
sélectionnent des colonnes, jamais l'entité ``Tenant`` complète ; aucune table métier.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_tenant_action
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.audit.service import RequestMeta
from app.platform.catalog.models import BusinessProfile, GeoCountry, Plan
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.models import Subscription, SubscriptionStatus
from app.platform.subscriptions.service import (
    add_months,
    apply_plan_change,
    effective_status,
    period_end,
)
from app.platform.tenancy.models import Site, Tenant, TenantStatus
from app.shared.pagination import PageParams, apply_sort, escape_like, paginate_rows, text_sort

# Statuts qui s'écoulent avec le temps (mêmes règles que ``effective_status``).
RUNNING = (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)
# Activation manuelle transitoire : souscription sans paiement confirmé (inscription ou essai).
ACTIVABLE = (SubscriptionStatus.PENDING_ACTIVATION, SubscriptionStatus.TRIAL)
EXTENDABLE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.EXPIRED)
# Au-delà : saisie manifestement erronée (la licence remplacera ce mécanisme, 3.3-B).
MAX_PERIOD_MONTHS = 24
# « À renouveler » (tableau de bord) : échéance dans ce délai.
RENEWAL_WINDOW = timedelta(days=30)


def effective_status_sql(now: datetime) -> ColumnElement[str]:
    """Équivalent SQL de ``effective_status`` (filtres et agrégats sans charger les lignes)."""
    stored = cast(Subscription.status, String)
    ended = and_(Subscription.status.in_(RUNNING), Subscription.current_period_end <= now)
    grace_end = Subscription.current_period_end + func.make_interval(0, 0, 0, Plan.grace_days)
    return case(
        (and_(ended, Subscription.status == SubscriptionStatus.TRIAL), literal("expired")),
        (and_(ended, literal(now) < grace_end), literal("past_due")),
        (ended, literal("expired")),
        else_=stored,
    )


def _sites_count() -> Any:
    return (
        select(func.count())
        .select_from(Site)
        .where(Site.tenant_id == Tenant.id, Site.is_active.is_(True))
        .correlate(Tenant)
        .scalar_subquery()
    )


def _users_count() -> Any:
    return (
        select(func.count())
        .select_from(TenantMembership)
        .where(
            TenantMembership.tenant_id == Tenant.id,
            TenantMembership.status == MembershipStatus.ACTIVE,
        )
        .correlate(Tenant)
        .scalar_subquery()
    )


@dataclass(frozen=True)
class TenantFilters:
    search: str | None = None
    status: TenantStatus | None = None
    plan_code: str | None = None
    subscription_status: SubscriptionStatus | None = None


def local_midnight(day: date, timezone: str) -> datetime:
    """Début du jour ``day`` dans le fuseau du tenant (ADR-0028)."""
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(timezone))


def local_day(moment: datetime, timezone: str) -> date:
    return moment.astimezone(ZoneInfo(timezone)).date()


class TenantAdminService:
    def __init__(self, db: Session, registry: ModuleRegistry, now: datetime) -> None:
        self.db = db
        self.registry = registry
        self.now = now

    # --- Lecture -------------------------------------------------------------------------------

    def _base(self) -> Select[Any]:
        return (
            select(
                Tenant.id,
                Tenant.name,
                Tenant.trade_name,
                Tenant.slug,
                Tenant.status,
                Tenant.business_profile_code,
                BusinessProfile.name.label("business_profile_name"),
                Tenant.created_at,
                Subscription.plan_code,
                Plan.name.label("plan_name"),
                cast(Subscription.status, String).label("subscription_status"),
                effective_status_sql(self.now).label("effective_status"),
                Subscription.current_period_end,
                _sites_count().label("sites"),
                _users_count().label("users"),
            )
            .join(Subscription, Subscription.tenant_id == Tenant.id)
            .join(Plan, Plan.code == Subscription.plan_code)
            .join(BusinessProfile, BusinessProfile.code == Tenant.business_profile_code)
        )

    def search(self, filters: TenantFilters, params: PageParams) -> tuple[list[Any], int]:
        stmt = self._base()
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            stmt = stmt.where(
                or_(
                    Tenant.name.ilike(pattern, escape="\\"),
                    Tenant.trade_name.ilike(pattern, escape="\\"),
                    Tenant.slug.ilike(pattern, escape="\\"),
                )
            )
        if filters.status:
            stmt = stmt.where(Tenant.status == filters.status)
        if filters.plan_code:
            stmt = stmt.where(Subscription.plan_code == filters.plan_code)
        if filters.subscription_status:
            stmt = stmt.where(effective_status_sql(self.now) == filters.subscription_status.value)
        stmt = apply_sort(
            stmt,
            params.sort,
            {
                "name": text_sort(Tenant.name),
                "created_at": Tenant.created_at,
                "current_period_end": Subscription.current_period_end,
                "status": Tenant.status,
            },
            default="name",
            tiebreaker=Tenant.id,
        )
        return paginate_rows(self.db, stmt, params)

    def row(self, tenant_id: uuid.UUID) -> Any:
        found = self.db.execute(self._base().where(Tenant.id == tenant_id)).one_or_none()
        if found is None:
            raise NotFoundError("Entreprise introuvable", code="tenant_not_found")
        return found

    def identity(self, tenant_id: uuid.UUID) -> Any:
        """Identité plateforme (colonnes autorisées au rôle de la console seulement)."""
        found = self.db.execute(
            select(
                Tenant.id,
                Tenant.name,
                Tenant.status,
                Tenant.country_code,
                GeoCountry.name.label("country_name"),
                Tenant.currency,
                Tenant.locale,
                Tenant.timezone,
            )
            .outerjoin(GeoCountry, GeoCountry.code == Tenant.country_code)
            .where(Tenant.id == tenant_id)
        ).one_or_none()
        if found is None:
            raise NotFoundError("Entreprise introuvable", code="tenant_not_found")
        return found

    def subscription(self, tenant_id: uuid.UUID, *, for_update: bool = False) -> Subscription:
        query = select(Subscription).where(Subscription.tenant_id == tenant_id)
        if for_update:
            query = query.with_for_update()
        found = self.db.scalars(query).one_or_none()
        if found is None:
            raise NotFoundError("Abonnement introuvable", code="subscription_missing")
        return found

    def plan(self, code: str) -> Plan:
        plan = self.db.get(Plan, code)
        if plan is None:
            raise NotFoundError("Plan introuvable", code="plan_not_found")
        return plan

    def effective(self, subscription: Subscription) -> SubscriptionStatus:
        grace_days = self.plan(subscription.plan_code).grace_days
        return effective_status(subscription, grace_days, self.now)

    def limits(self, plan: Plan) -> dict[str, int | None]:
        return {code: plan.limits.get(code) for code in sorted(self.registry.limit_codes())}

    def activation_proposal(self, subscription: Subscription, timezone: str) -> tuple[date, date]:
        start = local_day(self.now, timezone)
        end = period_end(local_midnight(start, timezone), subscription.billing_period)
        return start, end.date()

    def extension_proposal(self, subscription: Subscription, timezone: str) -> date:
        base = max(subscription.current_period_end, self.now)
        return local_day(period_end(base, subscription.billing_period), timezone)

    # --- Actions : tenant ----------------------------------------------------------------------

    def _lock_tenant(self, tenant_id: uuid.UUID) -> Any:
        found = self.db.execute(
            select(Tenant.id, Tenant.name, Tenant.status, Tenant.timezone)
            .where(Tenant.id == tenant_id)
            .with_for_update()
        ).one_or_none()
        if found is None:
            raise NotFoundError("Entreprise introuvable", code="tenant_not_found")
        return found

    def _set_tenant_status(
        self,
        tenant_id: uuid.UUID,
        *,
        expected: TenantStatus,
        target: TenantStatus,
        action: str,
        error_code: str,
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> None:
        tenant = self._lock_tenant(tenant_id)
        if tenant.status != expected:
            raise ConflictError("Action impossible dans l'état actuel", code=error_code)
        self.db.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(status=target)
            .execution_options(synchronize_session=False)
        )
        self._audit(
            action=action,
            tenant_id=tenant_id,
            target_type="tenant",
            target_id=tenant_id,
            before={"status": expected.value},
            after={"status": target.value},
            reason=reason,
            actor=actor,
            meta=meta,
            data={"tenant_name": tenant.name},
        )

    def suspend(
        self, tenant_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> None:
        self._set_tenant_status(
            tenant_id,
            expected=TenantStatus.ACTIVE,
            target=TenantStatus.SUSPENDED,
            action="tenant.suspended",
            error_code="tenant_already_suspended",
            reason=reason,
            actor=actor,
            meta=meta,
        )

    def reactivate(
        self, tenant_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> None:
        self._set_tenant_status(
            tenant_id,
            expected=TenantStatus.SUSPENDED,
            target=TenantStatus.ACTIVE,
            action="tenant.reactivated",
            error_code="tenant_not_suspended",
            reason=reason,
            actor=actor,
            meta=meta,
        )

    # --- Actions : abonnement ------------------------------------------------------------------

    def _check_period(self, start: datetime, end: datetime) -> None:
        if end <= start or end <= self.now:
            raise BusinessRuleError(
                "L'échéance doit suivre le début et être à venir", code="invalid_period"
            )
        if end > add_months(start, MAX_PERIOD_MONTHS):
            raise BusinessRuleError(
                f"Période trop longue ({MAX_PERIOD_MONTHS} mois au plus)",
                code="period_too_long",
                extra={"max_months": MAX_PERIOD_MONTHS},
            )

    @staticmethod
    def _period(subscription: Subscription) -> dict[str, Any]:
        return {
            "status": subscription.status.value,
            "current_period_start": subscription.current_period_start.isoformat(),
            "current_period_end": subscription.current_period_end.isoformat(),
        }

    def activate(
        self,
        tenant_id: uuid.UUID,
        *,
        period_start: date | None,
        period_end_on: date | None,
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> None:
        """Activation manuelle **transitoire** (arbitrage D5) : TechNova autorise l'activation ;
        ce n'est ni un paiement confirmé ni une licence (3.3-A, 3.3-B)."""
        tenant = self._lock_tenant(tenant_id)
        subscription = self.subscription(tenant_id, for_update=True)
        if subscription.status not in ACTIVABLE:
            raise ConflictError(
                "Seul un abonnement en attente d'activation ou en essai peut être activé",
                code="subscription_not_activable",
            )
        proposed_start, proposed_end = self.activation_proposal(subscription, tenant.timezone)
        start = local_midnight(period_start or proposed_start, tenant.timezone)
        end = local_midnight(period_end_on or proposed_end, tenant.timezone)
        self._check_period(start, end)
        before = self._period(subscription)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.current_period_start = start
        subscription.current_period_end = end
        self.db.flush()
        self._audit(
            action="subscription.manually_activated",
            tenant_id=tenant_id,
            target_type="subscription",
            target_id=subscription.id,
            before=before,
            after=self._period(subscription),
            reason=reason,
            actor=actor,
            meta=meta,
            data={
                "plan": subscription.plan_code,
                "transitional": True,
                "payment_confirmed": False,
            },
        )

    def extend(
        self,
        tenant_id: uuid.UUID,
        *,
        period_end_on: date,
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> None:
        """Prolongation (même cadre transitoire) : nouvelle échéance postérieure à l'actuelle ;
        une période déjà échue repart du jour même."""
        tenant = self._lock_tenant(tenant_id)
        subscription = self.subscription(tenant_id, for_update=True)
        if subscription.status not in EXTENDABLE:
            raise ConflictError(
                "Seul un abonnement actif, échu ou expiré peut être prolongé",
                code="subscription_not_extendable",
            )
        end = local_midnight(period_end_on, tenant.timezone)
        if end <= subscription.current_period_end:
            raise BusinessRuleError(
                "La nouvelle échéance doit suivre l'échéance actuelle", code="invalid_period"
            )
        lapsed = subscription.current_period_end <= self.now
        start = (
            local_midnight(local_day(self.now, tenant.timezone), tenant.timezone)
            if lapsed
            else subscription.current_period_start
        )
        self._check_period(max(start, subscription.current_period_end, self.now), end)
        before = self._period(subscription)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.current_period_start = start
        subscription.current_period_end = end
        self.db.flush()
        self._audit(
            action="subscription.extended",
            tenant_id=tenant_id,
            target_type="subscription",
            target_id=subscription.id,
            before=before,
            after=self._period(subscription),
            reason=reason,
            actor=actor,
            meta=meta,
            data={"plan": subscription.plan_code, "payment_confirmed": False},
        )

    def change_plan(
        self,
        tenant_id: uuid.UUID,
        *,
        plan_code: str,
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> None:
        self._lock_tenant(tenant_id)
        subscription = self.subscription(tenant_id, for_update=True)
        plan = self.db.get(Plan, plan_code)
        if plan is None or not plan.is_active:
            raise BusinessRuleError("Plan inconnu ou retiré du catalogue", code="unknown_plan")
        if plan.code == subscription.plan_code:
            raise BusinessRuleError("C'est déjà le plan de l'abonnement", code="plan_unchanged")
        change = apply_plan_change(subscription, plan)
        self.db.flush()
        self._audit(
            action="subscription.plan_changed",
            tenant_id=tenant_id,
            target_type="subscription",
            target_id=subscription.id,
            before={
                "plan_code": change.previous_plan,
                "price_at_subscription": change.previous_price,
                "currency_at_subscription": change.previous_currency,
            },
            after={
                "plan_code": change.plan,
                "price_at_subscription": change.price,
                "currency_at_subscription": change.currency,
            },
            reason=reason,
            actor=actor,
            meta=meta,
            # Mêmes clés que le changement de plan par la CLI, dans le journal du tenant.
            data={"previous_plan": change.previous_plan, "plan": change.plan},
        )

    # --- Double audit --------------------------------------------------------------------------

    def _audit(
        self,
        *,
        action: str,
        tenant_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
        data: dict[str, Any],
    ) -> None:
        """Double audit (plateforme + miroir de l'entreprise), même transaction."""
        record_tenant_action(
            self.db,
            actor=actor,
            action=action,
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            reason=reason,
            data=data,
            meta=meta,
        )

    # --- Tableau de bord -----------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        tenants: dict[str, int] = {
            status: count
            for status, count in self.db.execute(
                select(cast(Tenant.status, String), func.count()).group_by(Tenant.status)
            ).all()
        }
        effective = effective_status_sql(self.now)
        subscriptions: dict[str, int] = {
            status: count
            for status, count in self.db.execute(
                select(effective, func.count())
                .select_from(Subscription)
                .join(Plan, Plan.code == Subscription.plan_code)
                .group_by(effective)
            ).all()
        }
        renewal_due = (
            self.db.scalar(
                select(func.count())
                .select_from(Subscription)
                .join(Plan, Plan.code == Subscription.plan_code)
                .where(
                    effective.in_(("active", "past_due")),
                    Subscription.current_period_end <= self.now + RENEWAL_WINDOW,
                )
            )
            or 0
        )
        return {
            "tenants_total": sum(tenants.values()),
            "tenants_active": tenants.get("active", 0),
            "tenants_suspended": tenants.get("suspended", 0),
            "subscriptions_active": subscriptions.get("active", 0),
            "subscriptions_trial": subscriptions.get("trial", 0),
            "subscriptions_pending_activation": subscriptions.get("pending_activation", 0),
            "subscriptions_past_due": subscriptions.get("past_due", 0),
            "subscriptions_expired": subscriptions.get("expired", 0),
            "subscriptions_renewal_due": renewal_due,
        }
