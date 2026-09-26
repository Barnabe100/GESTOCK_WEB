"""Accès d'une appartenance à un site, pour les limites d'utilisateurs **par site**
(1 site = 1 abonnement, ADR-0033 ; arbitrage Q1).

Un utilisateur compte dans la limite ``max_users`` de chaque site auquel il a accès :

- propriétaire ou « tous les sites » : tous les sites de l'entreprise ;
- sites attribués (``membership_sites``) : ces sites-là ;
- aucun site attribué : utilisateur de l'entreprise (catalogue, clients…), compté sur **tous**
  les sites (jamais hors de toute limite).
"""

import uuid

from sqlalchemy import ColumnElement, exists, not_, or_

from app.platform.access.models import MembershipSite, TenantMembership


def site_access_condition(site_id: uuid.UUID) -> ColumnElement[bool]:
    """Condition SQL : l'appartenance (``TenantMembership``) donne accès au site."""
    return or_(
        TenantMembership.is_owner.is_(True),
        TenantMembership.all_sites.is_(True),
        exists().where(
            MembershipSite.membership_id == TenantMembership.id,
            MembershipSite.site_id == site_id,
        ),
        not_(exists().where(MembershipSite.membership_id == TenantMembership.id)),
    )


def accessible_sites(membership: TenantMembership, tenant_sites: set[uuid.UUID]) -> set[uuid.UUID]:
    """Sites (actifs ou non) dont l'appartenance consomme la limite d'utilisateurs."""
    links = {link.site_id for link in membership.site_links}
    if membership.is_owner or membership.all_sites or not links:
        return set(tenant_sites)
    return links & tenant_sites
