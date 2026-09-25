"""Étapes d'onboarding du socle plateforme (déclarées par ``app.platform.manifests``).

Chaque règle constate un statut à partir des données réelles du tenant ; elle n'écrit rien.
Les étapes des modules métier sont déclarées par leur propre manifeste (ex. ``catalogue``).
"""

from collections.abc import Callable

from sqlalchemy import exists, func, select

from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.catalog.models import BusinessProfile
from app.platform.identity.models import User
from app.platform.onboarding.definitions import (
    OnboardingAction,
    OnboardingEnv,
    OnboardingStatus,
    OnboardingStepDef,
    status_from,
)
from app.platform.subscriptions.models import SubscriptionStatus
from app.platform.subscriptions.service import get_subscription
from app.platform.tenancy.identity import (
    RECOMMENDED_COMPANY_FIELDS,
    REQUIRED_COMPANY_FIELDS,
    filled,
)
from app.platform.tenancy.models import Site

# Abonnement enregistré (étape administrative) : une souscription en attente d'activation
# suffit — l'onboarding ne dit rien de l'activation, qui reste gouvernée par l'abonnement.
VALID_SUBSCRIPTION_STATUSES = frozenset(
    {
        SubscriptionStatus.PENDING_ACTIVATION,
        SubscriptionStatus.TRIAL,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
    }
)


def step(
    code: str,
    order: int,
    *,
    required: bool,
    evaluate: Callable[[OnboardingEnv], OnboardingStatus],
    action: tuple[str, str] | None = None,
    applicable: Callable[[OnboardingEnv], bool] | None = None,
) -> OnboardingStepDef:
    """Déclaration d'étape avec les clés i18n conventionnelles (``onboarding.steps.<code>.*``,
    ``onboarding.actions.<code>``) ; ``action`` = (écran, permission)."""
    return OnboardingStepDef(
        code=code,
        order=order,
        required=required,
        title=f"onboarding.steps.{code}.title",
        description=f"onboarding.steps.{code}.description",
        evaluate=evaluate,
        action=(
            OnboardingAction(
                route=action[0], label=f"onboarding.actions.{code}", permission=action[1]
            )
            if action
            else None
        ),
        applicable=applicable,
    )


def _active_members(env: OnboardingEnv) -> int:
    return (
        env.db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .join(User, User.id == TenantMembership.user_id)
            .where(TenantMembership.status == MembershipStatus.ACTIVE, User.is_active.is_(True))
        )
        or 0
    )


def evaluate_account(env: OnboardingEnv) -> OnboardingStatus:
    """Compte du propriétaire créé et actif."""
    owner_active = env.db.scalar(
        select(
            exists()
            .where(TenantMembership.user_id == User.id)
            .where(
                TenantMembership.is_owner.is_(True),
                TenantMembership.status == MembershipStatus.ACTIVE,
                User.is_active.is_(True),
            )
        )
    )
    return status_from(bool(owner_active))


def evaluate_company(env: OnboardingEnv) -> OnboardingStatus:
    """Nom, pays et devise renseignés (pays NULL : tenant historique, incomplet — G4)."""
    present = [filled(getattr(env.tenant, f)) for f in REQUIRED_COMPANY_FIELDS]
    return status_from(all(present), any(present))


def evaluate_business_profile(env: OnboardingEnv) -> OnboardingStatus:
    """Profil d'activité associé au tenant (choisi à l'inscription) et actif au catalogue."""
    profile = env.db.get(BusinessProfile, env.tenant.business_profile_code)
    return status_from(profile is not None and profile.is_active)


def evaluate_subscription(env: OnboardingEnv) -> OnboardingStatus:
    subscription = get_subscription(env.db)
    return status_from(
        subscription is not None and subscription.status in VALID_SUBSCRIPTION_STATUSES
    )


def evaluate_first_site(env: OnboardingEnv) -> OnboardingStatus:
    has_site = env.db.scalar(select(exists().where(Site.is_active.is_(True))))
    return status_from(bool(has_site))


def evaluate_users(env: OnboardingEnv) -> OnboardingStatus:
    """Au moins un utilisateur actif en plus du propriétaire."""
    return status_from(_active_members(env) >= 2)


def users_applicable(env: OnboardingEnv) -> bool:
    """Sans objet si le plan n'autorise qu'un utilisateur."""
    limit = env.limit("max_users")
    return limit is None or limit > 1


def evaluate_configuration(env: OnboardingEnv) -> OnboardingStatus:
    """Informations recommandées de l'entreprise (reçus, factures, documents) : toutes
    renseignées → terminée ; une partie → en cours."""
    present = [filled(getattr(env.tenant, f)) for f in RECOMMENDED_COMPANY_FIELDS]
    return status_from(all(present), any(present))


# Étapes par module du socle (le registre les collecte depuis les manifestes).
USERS_STEPS = (
    step("account", 10, required=True, evaluate=evaluate_account),
    step(
        "users",
        70,
        required=False,
        evaluate=evaluate_users,
        action=("/users/members?create=1", "users.member.manage"),
        applicable=users_applicable,
    ),
)
ORGANIZATION_STEPS = (
    step(
        "company",
        20,
        required=True,
        evaluate=evaluate_company,
        action=("/organization/company", "organization.tenant.update"),
    ),
    step(
        "business_profile",
        30,
        required=True,
        evaluate=evaluate_business_profile,
        action=("/organization/company", "organization.profile.manage"),
    ),
    step(
        "first_site",
        50,
        required=True,
        evaluate=evaluate_first_site,
        action=("/organization/sites?create=1", "organization.site.manage"),
    ),
    step(
        "configuration",
        80,
        required=False,
        evaluate=evaluate_configuration,
        action=("/organization/company", "organization.tenant.update"),
    ),
)
SUBSCRIPTION_STEPS = (
    step(
        "subscription",
        40,
        required=True,
        evaluate=evaluate_subscription,
        action=("/subscription", "subscription.subscription.view"),
    ),
)
