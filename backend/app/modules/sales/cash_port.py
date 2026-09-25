"""Port « caisse » du module Ventes (ADR-0022, révisée en Phase 3.0).

Une vente ne dépend PAS de la caisse : seul un paiement en espèces (``CASH``) en a besoin. Le
module Ventes déclare ici l'interface dont il a besoin ; le module Caisse (``cash_register``,
qui dépend des ventes) l'implémente et l'enregistre au chargement. Aucune dépendance
``sales → cash_register``, aucun cycle :

- caisse absente, module Caisse inactif pour le tenant : un paiement espèces est refusé
  (``cash_session_required``) ; ventes et paiements électroniques restent possibles ;
- caisse active : paiement espèces et mouvement de caisse dans la même transaction.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.platform.context import RequestContext

# Code du module qui fournit la caisse (test de capacité, comme ``require_module``).
CASH_MODULE = "cash_register"


class CashLedger(Protocol):
    """Opérations de caisse utilisées par les paiements (implémentées par ``CashService``)."""

    def record_sale_cash_in(
        self,
        *,
        site_id: uuid.UUID,
        payment_id: uuid.UUID,
        payment_number: str,
        amount: Decimal,
        sale_id: uuid.UUID,
        sale_number: str,
        cash_register_id: uuid.UUID | None,
    ) -> Any: ...

    def record_sale_cash_reversal(self, *, payment_id: uuid.UUID, reason: str) -> Any: ...


CashLedgerFactory = Callable[[Session, RequestContext, datetime], CashLedger]
_factory: CashLedgerFactory | None = None


def register_cash_ledger(factory: CashLedgerFactory) -> None:
    """Appelé par le module Caisse à son chargement."""
    global _factory
    _factory = factory


def cash_ledger(
    db: Session, ctx: RequestContext, now: datetime, *, require_module: bool
) -> CashLedger | None:
    """Caisse utilisable, ou ``None``. ``require_module`` : le module Caisse doit être effectif
    pour le tenant (nouvel encaissement) ; sans cette exigence (annulation), un encaissement
    déjà enregistré est toujours inversé."""
    if _factory is None:
        return None
    if require_module and CASH_MODULE not in ctx.capabilities.modules:
        return None
    return _factory(db, ctx, now)
