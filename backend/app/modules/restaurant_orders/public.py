"""Création PUBLIQUE d'une commande (palier R7-C, ADR-0052) : canal ``QR``, sans membre.

Même moteur que le personnel (``OrderService._insert`` : verrous, effectivité recalculée,
réglages, table, prix FIGÉS du menu, routage, numéro du jour, évènement, audit) ; seuls changent
l'acteur (``PUBLIC``, aucun utilisateur), l'état initial (« à confirmer », sauf acceptation
automatique du site) et le calcul des modules du site (abonnement du site autorisant l'écriture,
sans membre). Le module QR a vérifié le jeton et son périmètre AVANT (``restaurant_qr``) ; il
fournit le site, la table éventuelle et la clé d'idempotence dérivée du point.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.restaurant_orders.models import (
    ActorKind,
    OrderChannel,
    RestaurantOrder,
    ServiceMode,
)
from app.modules.restaurant_orders.schemas import LineInput, OrderCreate
from app.modules.restaurant_orders.service import OrderService
from app.platform.capabilities.service import CapabilityService
from app.platform.context import PublicScope
from app.platform.registry import get_registry


class PublicOrderService(OrderService):
    """Variante SANS membre du service des commandes : seules la création publique et ses
    lectures sont utilisables (aucun ``ctx`` : toute méthode réservée au personnel échoue)."""

    def __init__(self, db: Session, scope: PublicScope, now: datetime) -> None:
        self.db = db
        self.now = now
        self.tenant = scope.tenant
        self.scope = scope
        self.public_scope = scope
        self.actor_kind = ActorKind.PUBLIC
        self.actor_user_id = None
        self.meta = scope.meta
        self._fresh_modules = None
        self._last_event_id = None

    def _compute_site_modules(self, site_id: uuid.UUID) -> frozenset[str]:
        modules = CapabilityService(self.db, get_registry()).public_site_modules(
            self.tenant, site_id, self.now
        )
        return modules if modules is not None else frozenset()

    def _site_modules(self, site_id: uuid.UUID) -> frozenset[str]:
        return self._fresh_site_modules(site_id)

    def create_public(
        self,
        *,
        table_id: uuid.UUID | None,
        call_name: str | None,
        contact_phone: str | None,
        lines: Sequence[LineInput],
        idempotency_key: uuid.UUID,
        point_id: uuid.UUID,
    ) -> tuple[RestaurantOrder, bool]:
        """``(commande, rejouée)`` ; la clé (dérivée du point par le module QR) est unique par
        site comme pour toute commande."""
        site_id = self.public_scope.site.id
        existing = self._existing(site_id, idempotency_key)
        if existing is not None:
            return existing, True
        data = OrderCreate(
            site_id=site_id,
            service_mode=ServiceMode.ON_SITE if table_id is not None else ServiceMode.COUNTER,
            table_id=table_id,
            call_name=call_name,
            lines=list(lines),
            idempotency_key=idempotency_key,
        )
        order = self._insert(
            site_id,
            data,
            OrderChannel.QR,
            None,
            contact_phone=contact_phone,
            created_data={"qr_point_id": str(point_id)},
        )
        return order, False
