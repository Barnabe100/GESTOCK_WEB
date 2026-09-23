"""Synchronisation du catalogue (fichiers → base). Exécutée avec le rôle propriétaire."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.catalog.loader import Catalog
from app.platform.catalog.models import (
    BusinessProfile,
    BusinessProfileModule,
    Plan,
    PlanModule,
    SubscriptionAccessPolicy,
)


@dataclass(frozen=True)
class SyncReport:
    profiles: int
    plans: int
    policies: int
    deactivated_profiles: list[str]
    deactivated_plans: list[str]


def sync_catalog(session: Session, catalog: Catalog) -> SyncReport:
    """Aligne la base sur les fichiers. Un profil/plan retiré des fichiers est désactivé,
    jamais supprimé (des tenants peuvent le référencer)."""
    existing_profiles = {p.code: p for p in session.scalars(select(BusinessProfile))}
    for pdef in catalog.profiles.values():
        profile = existing_profiles.get(pdef.code) or BusinessProfile(code=pdef.code)
        profile.name = pdef.name
        profile.description = pdef.description
        profile.is_active = True
        profile.navigation = list(pdef.navigation)
        profile.terminology = pdef.terminology
        profile.settings = pdef.settings
        profile.modules = [
            *(BusinessProfileModule(module_code=c, default_enabled=True) for c in pdef.modules),
            *(
                BusinessProfileModule(module_code=c, default_enabled=False)
                for c in pdef.optional_modules
            ),
        ]
        session.add(profile)
        session.flush()
    deactivated_profiles = sorted(set(existing_profiles) - set(catalog.profiles))
    for code in deactivated_profiles:
        existing_profiles[code].is_active = False

    existing_plans = {p.code: p for p in session.scalars(select(Plan))}
    for plan_def in catalog.plans.values():
        plan = existing_plans.get(plan_def.code) or Plan(code=plan_def.code)
        plan.name = plan_def.name
        plan.description = plan_def.description
        plan.is_active = True
        plan.sort_order = plan_def.sort_order
        plan.grace_days = plan_def.grace_days
        plan.limits = dict(plan_def.limits)
        plan.modules = [PlanModule(module_code=c) for c in plan_def.modules]
        session.add(plan)
        session.flush()
    deactivated_plans = sorted(set(existing_plans) - set(catalog.plans))
    for code in deactivated_plans:
        existing_plans[code].is_active = False

    existing_policies = {p.status: p for p in session.scalars(select(SubscriptionAccessPolicy))}
    for policy_def in catalog.policies.values():
        policy = existing_policies.get(policy_def.status) or SubscriptionAccessPolicy(
            status=policy_def.status
        )
        policy.description = policy_def.description
        policy.allowed_access = list(policy_def.allowed_access)
        session.add(policy)

    session.flush()
    return SyncReport(
        profiles=len(catalog.profiles),
        plans=len(catalog.plans),
        policies=len(catalog.policies),
        deactivated_profiles=deactivated_profiles,
        deactivated_plans=deactivated_plans,
    )
