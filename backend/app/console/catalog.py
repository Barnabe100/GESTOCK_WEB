"""Catalogue technique vu par la console : **lecture seule**.

Source de vérité : le code (manifestes des modules : permissions, fonctionnalités, limites) et
les fichiers versionnés (``data/*.toml`` : rôles de base, secteurs, profils, profils UX,
politiques, pays), synchronisés en base par ``catalog sync``. La console ne les modifie jamais.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.console.plans import known_currencies
from app.console.schemas import (
    CatalogModuleOut,
    CatalogOut,
    CatalogPermissionOut,
    CatalogPolicyOut,
    CatalogProfileOut,
    CatalogRoleTemplateOut,
    CatalogSectorOut,
    CatalogUxProfileOut,
)
from app.platform.catalog.loader import role_templates
from app.platform.catalog.models import (
    BusinessProfile,
    BusinessSector,
    GeoCountry,
    SubscriptionAccessPolicy,
    UxProfile,
)
from app.platform.registry import ModuleRegistry


def active_countries(db: Session) -> int:
    return db.scalar(select(func.count()).where(GeoCountry.is_active.is_(True))) or 0


def build_catalog(db: Session, registry: ModuleRegistry) -> CatalogOut:
    modules = [
        CatalogModuleOut(
            code=m.code,
            status=m.status,
            core=m.core,
            depends_on=list(m.depends_on),
            features=list(m.features),
            limits=[limit.code for limit in m.limits],
            permissions=[
                CatalogPermissionOut(code=p.code, access=p.access, feature=p.feature)
                for p in m.permissions
            ],
        )
        for m in sorted(registry.all(), key=lambda m: (not m.core, m.code))
    ]
    sectors = db.scalars(select(BusinessSector).order_by(BusinessSector.sort_order)).all()
    profiles = db.scalars(
        select(BusinessProfile).order_by(BusinessProfile.sector_code, BusinessProfile.sort_order)
    ).unique()
    ux_profiles = db.scalars(select(UxProfile).order_by(UxProfile.code)).all()
    policies = db.scalars(
        select(SubscriptionAccessPolicy).order_by(SubscriptionAccessPolicy.status)
    ).all()
    return CatalogOut(
        modules=modules,
        sectors=[
            CatalogSectorOut(code=s.code, name=s.name, is_active=s.is_active) for s in sectors
        ],
        profiles=[
            CatalogProfileOut(
                code=p.code,
                name=p.name,
                sector_code=p.sector_code,
                ux_profile_code=p.ux_profile_code,
                is_active=p.is_active,
                modules=sorted(link.module_code for link in p.modules),
            )
            for p in profiles
        ],
        ux_profiles=[
            CatalogUxProfileOut(code=u.code, name=u.name, is_active=u.is_active)
            for u in ux_profiles
        ],
        role_templates=[
            CatalogRoleTemplateOut(
                code=t.code,
                name=t.name,
                description=t.description,
                protected=t.protected,
                permission_patterns=list(t.permission_patterns),
                exclude_patterns=list(t.exclude_patterns),
            )
            for t in role_templates().values()
        ],
        policies=[
            CatalogPolicyOut(
                status=p.status, allowed_access=list(p.allowed_access), description=p.description
            )
            for p in policies
        ],
        currencies=sorted(known_currencies(db)),
        active_countries=active_countries(db),
    )
