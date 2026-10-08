"""Initialisation des activations d'un NOUVEAU site (profils / modules par site, palier C).

Règle validée : modules ``default_enabled`` du profil DU SITE ∩ modules de SON abonnement
(licence en vigueur, sinon plan) → activés ; modules optionnels du profil inclus dans l'offre →
lignes désactivées. Rien n'est copié d'un autre site. Appelée dans la transaction qui crée le
site (``POST /sites``, provisioning) : la configuration du site est indépendante dès sa
création.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.catalog.models import BusinessProfile
from app.platform.tenancy.models import Site, SiteModule


def init_site_modules(
    session: Session,
    tenant_id: uuid.UUID,
    site_id: uuid.UUID,
    profile: BusinessProfile,
    offer_modules: Iterable[str],
) -> set[str]:
    """Crée les activations du site ; renvoie les modules activés."""
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
    return enabled


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
