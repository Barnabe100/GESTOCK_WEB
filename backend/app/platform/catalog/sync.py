"""Synchronisation du catalogue (fichiers → base). Exécutée avec le rôle propriétaire."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.catalog.loader import Catalog
from app.platform.catalog.models import (
    BusinessProfile,
    BusinessProfileModule,
    BusinessSector,
    Plan,
    PlanModule,
    SubscriptionAccessPolicy,
    UxProfile,
)
from app.platform.catalog.ux import dashboard_json, navigation_json


@dataclass(frozen=True)
class SyncReport:
    profiles: int
    plans: int
    policies: int
    deactivated_profiles: list[str]
    deactivated_plans: list[str]
    sectors: int = 0
    ux_profiles: int = 0
    deactivated_sectors: list[str] = field(default_factory=list)
    deactivated_ux_profiles: list[str] = field(default_factory=list)


def sync_catalog(session: Session, catalog: Catalog) -> SyncReport:
    """Aligne la base sur les fichiers (secteurs, profils UX, profils, plans, politiques). Un
    élément retiré des fichiers est désactivé, jamais supprimé (des tenants le référencent)."""
    existing_sectors = {s.code: s for s in session.scalars(select(BusinessSector))}
    for sdef in catalog.sectors.values():
        sector = existing_sectors.get(sdef.code) or BusinessSector(code=sdef.code)
        sector.name = sdef.name
        sector.description = sdef.description
        sector.sort_order = sdef.sort_order
        sector.icon = sdef.icon
        sector.is_active = sdef.is_active
        session.add(sector)
    existing_ux = {u.code: u for u in session.scalars(select(UxProfile))}
    for udef in catalog.ux_profiles.values():
        ux = existing_ux.get(udef.code) or UxProfile(code=udef.code)
        ux.name = udef.name
        ux.description = udef.description
        ux.is_active = udef.is_active
        ux.navigation = navigation_json(udef.config.navigation)
        ux.dashboard = dashboard_json(udef.config.widgets, udef.config.shortcuts)
        ux.terminology = udef.config.terminology
        ux.theme = udef.config.theme
        session.add(ux)
    session.flush()

    existing_profiles = {p.code: p for p in session.scalars(select(BusinessProfile))}
    for pdef in catalog.profiles.values():
        profile = existing_profiles.get(pdef.code) or BusinessProfile(code=pdef.code)
        profile.name = pdef.name
        profile.description = pdef.description
        profile.is_active = pdef.is_active
        profile.sector_code = pdef.sector
        profile.ux_profile_code = pdef.ux_profile
        profile.sort_order = pdef.sort_order
        profile.navigation = navigation_json(pdef.navigation)
        profile.dashboard = pdef.dashboard
        profile.terminology = pdef.terminology
        profile.theme = pdef.theme
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
    # Après les profils : un secteur ou profil UX retiré n'est plus référencé par un profil actif.
    deactivated_sectors = sorted(set(existing_sectors) - set(catalog.sectors))
    for code in deactivated_sectors:
        existing_sectors[code].is_active = False
    deactivated_ux = sorted(set(existing_ux) - set(catalog.ux_profiles))
    for code in deactivated_ux:
        existing_ux[code].is_active = False

    existing_plans = {p.code: p for p in session.scalars(select(Plan))}
    for plan_def in catalog.plans.values():
        plan = existing_plans.get(plan_def.code) or Plan(code=plan_def.code)
        plan.name = plan_def.name
        plan.description = plan_def.description
        plan.is_active = True
        plan.sort_order = plan_def.sort_order
        plan.grace_days = plan_def.grace_days
        plan.limits = dict(plan_def.limits)
        plan.features = list(plan_def.features)
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
        sectors=len(catalog.sectors),
        ux_profiles=len(catalog.ux_profiles),
        deactivated_sectors=deactivated_sectors,
        deactivated_ux_profiles=deactivated_ux,
    )
