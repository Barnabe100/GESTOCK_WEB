"""Port « origine » du module Ventes (ADR-0049, palier R2-D), sur le modèle du port caisse.

Une vente peut être issue d'un document d'un autre module (ex. commande de restauration :
``origin_type = "restaurant_order"``). Le module Ventes ne dépend PAS de ce module : il déclare
ici l'interface appelée à l'annulation d'une telle vente ; le module d'origine (qui dépend des
ventes) l'implémente et l'enregistre au chargement. Aucun cycle de dépendance.

Ordre des verrous : le gestionnaire d'origine verrouille SON document **avant** le verrou de la
vente (même ordre que le règlement : document d'origine → vente), dans la transaction de
l'annulation ; une erreur ultérieure (paiements actifs…) annule tout. Une vente dont l'origine
n'a aucun gestionnaire enregistré ne s'annule pas (``409 sale_origin_unavailable``).
"""

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from app.platform.context import RequestContext

# ``before_sale_cancel(db, ctx, now, origin_id=…, sale_id=…)`` : verrouille le document
# d'origine, refuse l'annulation si elle est interdite (ex. commande close) et met le document
# à jour dans la même transaction. Ne fait rien si la vente n'est plus la vente active du
# document (ancienne vente déjà annulée).
SaleOriginCancelHook = Callable[..., None]

_hooks: dict[str, SaleOriginCancelHook] = {}


def register_sale_origin(origin_type: str, before_sale_cancel: SaleOriginCancelHook) -> None:
    """Appelé par le module d'origine à son chargement."""
    _hooks[origin_type] = before_sale_cancel


def origin_cancel_hook(origin_type: str) -> SaleOriginCancelHook | None:
    return _hooks.get(origin_type)


def notify_before_cancel(
    db: Session,
    ctx: RequestContext,
    now: datetime,
    *,
    origin_type: str,
    origin_id: uuid.UUID,
    sale_id: uuid.UUID,
) -> bool:
    """Gestionnaire appelé ; ``False`` s'il n'en existe aucun pour cette origine."""
    hook = _hooks.get(origin_type)
    if hook is None:
        return False
    hook(db, ctx, now, origin_id=origin_id, sale_id=sale_id)
    return True
