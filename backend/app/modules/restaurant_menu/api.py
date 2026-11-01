"""Interface publique du menu (palier R2, ADR-0049) : utilisée par les commandes et la cuisine
(affectations aux postes, R6).

Le menu reste propriétaire de sa règle « commandable » (motifs ``blockers`` calculés sur l'état
COURANT du catalogue, de l'assortiment et du menu) ; les commandes ne lisent jamais ses modèles.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_menu.models import MenuItem, MenuSection
from app.modules.restaurant_menu.service import ItemRow, item_query, item_rows
from app.platform.context import RequestContext, TenantScope

__all__ = [
    "ItemRow",
    "MenuSectionRef",
    "menu_items_of_site",
    "menu_section_ids_of_site",
    "site_menu",
]


@dataclass(frozen=True)
class MenuSectionRef:
    id: uuid.UUID
    name: str
    sort_order: int


def menu_items_of_site(
    db: Session, scope: TenantScope, site_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ItemRow]:
    """Éléments du menu de CE site parmi ``item_ids`` (un élément d'un autre site ou d'une autre
    entreprise est absent du résultat), avec leur prix courant et leurs motifs « non
    commandable »."""
    ids = set(item_ids)
    if not ids:
        return {}
    rows = db.execute(
        item_query(scope.tenant_id).where(MenuItem.site_id == site_id, MenuItem.id.in_(ids))
    ).all()
    return {row.id: row for row in item_rows(db, list(rows))}


def site_menu(
    db: Session, scope: TenantScope, site_id: uuid.UUID
) -> tuple[list[MenuSectionRef], list[ItemRow]]:
    """Menu COMPLET d'un site (lecture seule) : sections ACTIVES dans l'ordre d'affichage et
    éléments ACTIFS de ces sections, dans l'ordre d'affichage, avec leur état « commandable »
    calculé par le menu (``blockers``). Les éléments désactivés et les sections désactivées
    sont absents ; un élément épuisé reste présent (``unavailable``)."""
    sections = [
        MenuSectionRef(id=row.id, name=row.name, sort_order=row.sort_order)
        for row in db.execute(
            select(MenuSection.id, MenuSection.name, MenuSection.sort_order)
            .where(
                MenuSection.tenant_id == scope.tenant_id,
                MenuSection.site_id == site_id,
                MenuSection.is_active.is_(True),
            )
            .order_by(MenuSection.sort_order, MenuSection.name, MenuSection.id)
        )
    ]
    if not sections:
        return [], []
    rows = db.execute(
        item_query(scope.tenant_id)
        .where(
            MenuItem.site_id == site_id,
            MenuItem.is_active.is_(True),
            MenuItem.section_id.in_([s.id for s in sections]),
        )
        .order_by(MenuItem.sort_order, MenuItem.id)
    ).all()
    return sections, item_rows(db, list(rows))


def menu_section_ids_of_site(
    db: Session, ctx: RequestContext, site_id: uuid.UUID, section_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Sections du menu de CE site parmi ``section_ids`` (une section d'un autre site ou d'une
    autre entreprise est absente du résultat)."""
    ids = set(section_ids)
    if not ids:
        return set()
    return set(
        db.scalars(
            select(MenuSection.id).where(
                MenuSection.tenant_id == ctx.tenant_id,
                MenuSection.site_id == site_id,
                MenuSection.id.in_(ids),
            )
        )
    )
