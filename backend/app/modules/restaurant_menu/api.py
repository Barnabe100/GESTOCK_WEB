"""Interface publique du menu (palier R2, ADR-0049) : utilisée par les commandes.

Le menu reste propriétaire de sa règle « commandable » (motifs ``blockers`` calculés sur l'état
COURANT du catalogue, de l'assortiment et du menu) ; les commandes ne lisent jamais ses modèles.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.modules.restaurant_menu.models import MenuItem
from app.modules.restaurant_menu.service import ItemRow, MenuService
from app.platform.context import RequestContext

__all__ = ["ItemRow", "menu_items_of_site"]


def menu_items_of_site(
    db: Session, ctx: RequestContext, site_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ItemRow]:
    """Éléments du menu de CE site parmi ``item_ids`` (un élément d'un autre site ou d'une autre
    entreprise est absent du résultat), avec leur prix courant et leurs motifs « non
    commandable »."""
    ids = set(item_ids)
    if not ids:
        return {}
    service = MenuService(db, ctx)
    rows = db.execute(
        service._item_query().where(MenuItem.site_id == site_id, MenuItem.id.in_(ids))
    ).all()
    return {row.id: row for row in service._item_rows(list(rows))}
