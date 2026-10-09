"""Initialisation des activations d'un NOUVEAU site (profils / modules par site, palier C).

Règle validée : modules ``default_enabled`` du profil DU SITE ∩ modules de SON abonnement
(licence en vigueur, sinon plan) → activés ; modules optionnels du profil inclus dans l'offre →
lignes désactivées. Rien n'est copié d'un autre site. Appelée dans la transaction qui crée le
site (``POST /sites``, provisioning) : la configuration du site est indépendante dès sa
création.

Palier R2-A (ADR-0049) : un module DISPONIBLE qui devient activé sur un site y est initialisé
par son ``site_setup`` (``run_site_setup``), dans la même transaction.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.catalog.models import BusinessProfile
from app.platform.registry import ModuleRegistry, ModuleStatus
from app.platform.tenancy.models import Site, SiteModule


def init_site_modules(
    session: Session,
    tenant_id: uuid.UUID,
    site_id: uuid.UUID,
    profile: BusinessProfile,
    offer_modules: Iterable[str],
    registry: ModuleRegistry,
) -> set[str]:
    """Crée les activations du site et initialise les modules activés ; renvoie ces modules."""
    offered = set(offer_modules)
    enabled: set[str] = set()
    for link in profile.modules:
        if link.module_code not in offered:
            continue
        session.add(
            SiteModule(
                tenant_id=tenant_id,
                site_id=site_id,
                module_code=link.module_code,
                enabled=link.default_enabled,
            )
        )
        if link.default_enabled:
            enabled.add(link.module_code)
    session.flush()
    run_site_setup(session, registry, tenant_id, site_id, profile, enabled)
    return enabled


def run_site_setup(
    session: Session,
    registry: ModuleRegistry,
    tenant_id: uuid.UUID,
    site_id: uuid.UUID,
    profile: BusinessProfile,
    codes: Iterable[str],
) -> None:
    """Initialise sur le site les modules qui viennent d'y être activés (``site_setup`` de leur
    manifeste), dans l'ordre des codes. Un module planifié n'est jamais initialisé : son
    activation est inerte (E.1). Une fois livré, il est initialisé à sa prochaine activation
    sur le site ; si son activation inerte y subsiste, le module crée ce qui lui manque à son
    premier usage (ADR-0049, D14)."""
    for code in sorted(set(codes)):
        if code not in registry:
            continue
        manifest = registry.get(code)
        if manifest.status is ModuleStatus.AVAILABLE and manifest.site_setup is not None:
            manifest.site_setup(session, tenant_id, site_id, profile)


def lock_site(session: Session, site_id: uuid.UUID) -> Site | None:
    """Verrou exclusif du site pour une reconfiguration (changement de profil, activation d'un
    module ; palier D). Premier verrou pris : site → ``site_modules`` → verrous des modules
    (``site_footprint``). Il attend les écritures en cours qui référencent le site (clés
    étrangères) et les suspend jusqu'à la fin de la transaction."""
    return session.scalars(
        select(Site)
        .where(Site.id == site_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
