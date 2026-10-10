"""Moteur unique des commandes de restauration (palier R2, sous-étape R2-B ; ADR-0049 D2, D3,
D13, D14).

- **T1 — création** : numéro court du site pour le jour de l'entreprise, lignes issues du menu
  du site (élément commandable, contrôlé par le serveur), prix du catalogue FIGÉ sur chaque
  ligne, mode de paiement du site recopié ; ni vente, ni paiement, ni stock. Idempotente (clé
  par site). Le canal est fourni par la route, jamais par le client (un seul moteur pour tous
  les canaux).
- **Préparation** : reçue → en préparation → prête → servie / remise ; prête → en préparation
  (correction) ; par commande (toutes les lignes de l'état de départ) ou par ligne (P-2).
  « À la commande » : aucune préparation avant le règlement (``order_not_settled``).
- **Annulations** (commande non réglée) : ligne reçue → ``order.cancel`` ; ligne en
  préparation ou prête → ``order.cancel_prepared`` ; motif obligatoire ; commande entière
  refusée si une ligne est servie ; commande vide → ``order.cancel``.
- **Prise en charge** (R2-C, D7) : « Prendre » une commande ouverte ; protection d'une
  commande prise (réglage du site, 0 = désactivée) ; délai entre deux prises d'un même employé
  sur le site (dernière prise lue dans l'historique, seule l'action « Prendre » compte) ;
  réattribution immédiate motivée par ``order.reassign`` vers un membre détenant
  ``order.claim`` effectif sur le site (``assignee_not_eligible``). Responsable ≠ rôle.
- **Règlement T2** (R2-D, D6) : une transaction, tout ou rien — vente du canal
  ``RESTAURANT`` aux prix FIGÉS de la commande (``sales.api``), validée par le moteur commun
  (stock FEFO, lots, CMUP, crédit, caisse), paiements immédiats ; échec : commande intacte,
  « à régler ». Une vente active par commande (verrou de la commande + index
  ``uq_sales_active_origin``). Clôture (définitive) quand la commande est réglée ET servie.
- **Annulation de la vente** (Z3) : port d'origine des ventes, sous le verrou de la commande
  AVANT celui de la vente ; commande close : ``order_closed`` ; sinon « à régler ».

Verrous (``RESTAURANT.md`` §7) : verrou consultatif de l'employé (site + employé, « Prendre »)
→ réglages du site en partage (création, ajout de lignes, prise) → commande (exclusif) →
lignes (exclusif) → vente, paiements, caisse → stock. États finaux protégés aussi en base
(déclencheur). Le service ne valide pas la transaction (ADR-0008).
"""

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import active_packagings, base_quantity, get_article_refs
from app.modules.customers.api import get_customer_refs
from app.modules.restaurant_menu.api import menu_items_of_site
from app.modules.restaurant_orders.models import (
    ACTIVE_ORDER_STATUSES,
    FINAL_LINE_STATUSES,
    ActorKind,
    EventType,
    LineStatus,
    OrderChannel,
    OrderStatus,
    PaymentTiming,
    PrepStatus,
    RestaurantOrder,
    RestaurantOrderEvent,
    RestaurantOrderLine,
    SettlementStatus,
)
from app.modules.restaurant_orders.permissions import (
    ORDER_CANCEL,
    ORDER_CANCEL_PREPARED,
    ORDER_CLAIM,
    ORDER_CREATE,
    ORDER_PREPARE,
    ORDER_REASSIGN,
    ORDER_SERVE,
    ORDER_VIEW,
    SETTINGS_MANAGE,
)
from app.modules.restaurant_orders.schemas import (
    EventOut,
    LineCounts,
    LineInput,
    LineOut,
    LinesAdd,
    OrderCreate,
    OrderOut,
    OrderSettle,
    SettingsUpdate,
    TicketLineOut,
    TicketOut,
)
from app.modules.restaurant_orders.settings import (
    business_date,
    ensure_settings,
    next_order_number,
)
from app.modules.restaurant_orders.sites import (
    ensure_row_site,
    member_holds,
    operation_site,
    readable_site_ids,
    require_site_permission,
)
from app.modules.sales.api import (
    ORIGIN_RESTAURANT_ORDER,
    OriginLine,
    SaleService,
    sale_by_idempotency_key,
    sale_financial_states,
)
from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.audit.service import audit_action
from app.platform.capabilities.service import CapabilityService
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.registry import get_registry
from app.platform.tenancy.identity import document_identity
from app.platform.tenancy.models import Site
from app.shared.ids import new_id
from app.shared.pagination import PageParams, apply_sort, escape_like, paginate

MONEY_STEP = Decimal("0.01")
CUSTOMERS_MODULE = "customers"
# Règlement (D6, D12) : permissions EXISTANTES des ventes sur le site de la commande ; aucune
# permission nouvelle (le Serveur n'encaisse jamais).
SALE_CREATE = "sales.sale.create"
SALE_VALIDATE = "sales.sale.validate"
PAYMENT_CREATE = "sales.payment.create"


class OrderState(StrEnum):
    """Filtre de liste : commandes en cours (défaut), closes, annulées ou toutes."""

    ACTIVE = "active"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    ALL = "all"


@dataclass(frozen=True)
class Transition:
    source: LineStatus
    target: LineStatus
    permission: str
    event: EventType


TRANSITIONS: dict[str, Transition] = {
    "start": Transition(
        LineStatus.RECEIVED, LineStatus.IN_PREPARATION, ORDER_PREPARE, EventType.PREP_STARTED
    ),
    "ready": Transition(
        LineStatus.IN_PREPARATION, LineStatus.READY, ORDER_PREPARE, EventType.READY
    ),
    "revert": Transition(
        LineStatus.READY, LineStatus.IN_PREPARATION, ORDER_PREPARE, EventType.READY_REVERTED
    ),
    "serve": Transition(LineStatus.READY, LineStatus.SERVED, ORDER_SERVE, EventType.SERVED),
}

SORTS = {
    "created_at": RestaurantOrder.created_at,
    "number": RestaurantOrder.daily_number,
    "business_date": RestaurantOrder.business_date,
}


def _line_state(line: RestaurantOrderLine) -> dict[str, Any]:
    return {"line_no": line.line_no, "status": line.status.value}


def round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def prep_status(lines: Sequence[RestaurantOrderLine]) -> PrepStatus:
    """Ligne active la MOINS avancée ; ``SERVED`` si toutes les lignes non annulées le sont ;
    ``NONE`` sans ligne active."""
    active = [line.status for line in lines if line.status is not LineStatus.CANCELLED]
    if not active:
        return PrepStatus.NONE
    for state in (LineStatus.RECEIVED, LineStatus.IN_PREPARATION, LineStatus.READY):
        if state in active:
            return PrepStatus(state.value)
    return PrepStatus.SERVED


@dataclass(frozen=True)
class PreparedLine:
    menu_item_id: uuid.UUID
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None
    label: str
    packaging_name: str | None
    unit: str
    conversion: Decimal | None
    unit_price: Decimal
    quantity: Decimal
    base_quantity: Decimal
    line_total: Decimal
    note: str | None


class OrderService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now
        # Évènements d'une même requête : même horodatage ; identifiants croissants pour que
        # l'historique (trié par heure puis identifiant) garde l'ordre d'écriture.
        self._last_event_id: uuid.UUID | None = None

    # --- Commun -------------------------------------------------------------------------------

    def _profile(self, site_id: uuid.UUID) -> Any:
        return CapabilityService(self.db, get_registry()).site_profile(site_id)

    def _site_modules(self, site_id: uuid.UUID) -> frozenset[str]:
        if self.ctx.site is not None and self.ctx.site.id == site_id:
            return frozenset(self.ctx.capabilities.modules)
        return frozenset(self.ctx.site_capabilities(site_id).modules)

    def _audit(self, action: str, order: RestaurantOrder, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"restaurant_order.{action}",
            entity_type="restaurant_order",
            entity_id=order.id,
            site_id=order.site_id,
            data={
                "business_date": order.business_date.isoformat(),
                "daily_number": order.daily_number,
                **data,
            },
        )

    def _event(
        self,
        order: RestaurantOrder,
        event_type: EventType,
        lines: Sequence[RestaurantOrderLine] = (),
        *,
        reason: str | None = None,
        data: dict[str, Any] | None = None,
        idempotency_key: uuid.UUID | None = None,
    ) -> None:
        event_id = new_id()
        while self._last_event_id is not None and event_id <= self._last_event_id:
            event_id = new_id()
        self._last_event_id = event_id
        self.db.add(
            RestaurantOrderEvent(
                id=event_id,
                tenant_id=self.ctx.tenant_id,
                order_id=order.id,
                site_id=order.site_id,
                event_type=event_type,
                actor_kind=ActorKind.STAFF,
                actor_user_id=self.ctx.user.id,
                reason=reason,
                line_ids=[str(line.id) for line in lines],
                data=data or {},
                idempotency_key=idempotency_key,
                occurred_at=self.now,
            )
        )

    def _lock_order(self, order_id: uuid.UUID, *, write: bool = True) -> RestaurantOrder:
        order = self.db.scalars(
            select(RestaurantOrder)
            .where(RestaurantOrder.tenant_id == self.ctx.tenant_id, RestaurantOrder.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if order is None:
            raise NotFoundError("Commande introuvable", code="order_not_found")
        ensure_row_site(self.ctx, order.site_id, write=write)
        return order

    def _lock_lines(self, order: RestaurantOrder) -> list[RestaurantOrderLine]:
        return list(
            self.db.scalars(
                select(RestaurantOrderLine)
                .where(
                    RestaurantOrderLine.tenant_id == self.ctx.tenant_id,
                    RestaurantOrderLine.order_id == order.id,
                )
                .order_by(RestaurantOrderLine.line_no)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _require_open(order: RestaurantOrder) -> None:
        if order.status is OrderStatus.OPEN:
            return
        code = {
            OrderStatus.CLOSED: "order_closed",
            OrderStatus.CANCELLED: "order_cancelled",
        }.get(order.status, "order_not_open")
        raise ConflictError("Cette commande n'est plus ouverte", code=code)

    @staticmethod
    def _require_unsettled(order: RestaurantOrder) -> None:
        if order.settlement_status is SettlementStatus.SETTLED:
            raise ConflictError(
                "Commande réglée : ni ajout ni annulation de lignes (nouvelle commande, ou "
                "annulation de la vente d'abord)",
                code="order_settled",
            )

    def _touch(self, order: RestaurantOrder, lines: Sequence[RestaurantOrderLine]) -> None:
        """Résumé de préparation recalculé, version incrémentée ; clôture si la commande est
        réglée et que toutes ses lignes non annulées sont servies (définitif)."""
        order.prep_status = prep_status(lines)
        order.version += 1
        if (
            order.settlement_status is SettlementStatus.SETTLED
            and order.prep_status is PrepStatus.SERVED
            and order.status is OrderStatus.OPEN
        ):
            order.status = OrderStatus.CLOSED
            order.closed_at = self.now
            self._event(order, EventType.CLOSED)
            self._audit("closed", order, {})

    # --- Réglages du site ---------------------------------------------------------------------

    def get_settings(self, site_id: uuid.UUID) -> Any:
        if site_id not in readable_site_ids(self.ctx, site_id):
            raise NotFoundError("Site introuvable", code="site_not_found")
        return ensure_settings(self.db, self.ctx.tenant_id, site_id, self._profile(site_id))

    def update_settings(self, site_id: uuid.UUID, data: SettingsUpdate) -> Any:
        if site_id not in self.ctx.capabilities.accessible_site_ids:
            raise NotFoundError("Site introuvable", code="site_not_found")
        self.ctx.ensure_site_allows(site_id)
        require_site_permission(self.ctx, site_id, SETTINGS_MANAGE)
        settings = ensure_settings(
            self.db, self.ctx.tenant_id, site_id, self._profile(site_id), lock="update"
        )
        before = {
            "payment_timing": settings.payment_timing.value,
            "claim_protection_minutes": settings.claim_protection_minutes,
            "claim_cooldown_minutes": settings.claim_cooldown_minutes,
        }
        settings.payment_timing = data.payment_timing
        settings.claim_protection_minutes = data.claim_protection_minutes
        settings.claim_cooldown_minutes = data.claim_cooldown_minutes
        after = {
            "payment_timing": data.payment_timing.value,
            "claim_protection_minutes": data.claim_protection_minutes,
            "claim_cooldown_minutes": data.claim_cooldown_minutes,
        }
        self.db.flush()
        if before != after:
            audit_action(
                self.db,
                self.ctx,
                "restaurant_order.settings_updated",
                entity_type="restaurant_site_settings",
                entity_id=settings.id,
                site_id=site_id,
                data={"before": before, "after": after},
            )
        return settings

    # --- Lignes depuis le menu ----------------------------------------------------------------

    def _prepare_lines(self, site_id: uuid.UUID, inputs: Sequence[LineInput]) -> list[PreparedLine]:
        """Contrôles du serveur : élément du menu de CE site et commandable maintenant (élément
        et section actifs, non épuisé, article actif dans l'assortiment actif, conditionnement
        actif avec un prix) ; quantités entières si l'article l'exige ; prix du catalogue FIGÉ."""
        items = menu_items_of_site(self.db, self.ctx, site_id, {i.menu_item_id for i in inputs})
        missing = sorted({str(i.menu_item_id) for i in inputs if i.menu_item_id not in items})
        if missing:
            raise NotFoundError(
                "Élément du menu introuvable sur ce site",
                code="menu_item_not_found",
                extra={"items": missing},
            )
        blocked = [
            {"id": str(item.id), "label": item.display_name or item.designation, "blockers": b}
            for item in items.values()
            if (b := item.blockers)
        ]
        if blocked:
            raise BusinessRuleError(
                "Des éléments ne sont pas commandables actuellement",
                code="menu_item_not_orderable",
                extra={"items": blocked},
            )
        article_ids = {item.article_id for item in items.values()}
        refs = get_article_refs(self.db, article_ids)
        packagings = {
            p.id: p for ps in active_packagings(self.db, article_ids).values() for p in ps
        }
        prepared: list[PreparedLine] = []
        for line in inputs:
            item = items[line.menu_item_id]
            packaging = packagings.get(item.packaging_id) if item.packaging_id else None
            if item.price is None or (item.packaging_id is not None and packaging is None):
                raise BusinessRuleError(  # défense en profondeur : déjà couvert par ``blockers``
                    "Des éléments ne sont pas commandables actuellement",
                    code="menu_item_not_orderable",
                    extra={"items": [{"id": str(item.id), "blockers": ["price_unavailable"]}]},
                )
            quantity = line.quantity
            base = base_quantity(refs[item.article_id], quantity, packaging)
            prepared.append(
                PreparedLine(
                    menu_item_id=item.id,
                    article_id=item.article_id,
                    packaging_id=item.packaging_id,
                    label=item.display_name or item.designation,
                    packaging_name=packaging.name if packaging is not None else None,
                    unit=item.unit,
                    conversion=packaging.conversion if packaging is not None else None,
                    unit_price=item.price,
                    quantity=quantity,
                    base_quantity=base,
                    line_total=round_money(quantity * item.price),
                    note=line.note,
                )
            )
        return prepared

    def _add_lines(
        self, order: RestaurantOrder, prepared: Sequence[PreparedLine], start: int
    ) -> list[RestaurantOrderLine]:
        created = [
            RestaurantOrderLine(
                tenant_id=self.ctx.tenant_id,
                order_id=order.id,
                site_id=order.site_id,
                line_no=start + index,
                menu_item_id=p.menu_item_id,
                article_id=p.article_id,
                packaging_id=p.packaging_id,
                label=p.label,
                packaging_name=p.packaging_name,
                unit=p.unit,
                conversion=p.conversion,
                unit_price=p.unit_price,
                quantity=p.quantity,
                base_quantity=p.base_quantity,
                line_total=p.line_total,
                note=p.note,
                status=LineStatus.RECEIVED,
                created_at=self.now,
            )
            for index, p in enumerate(prepared)
        ]
        self.db.add_all(created)
        self.db.flush()
        return created

    @staticmethod
    def _lines_audit(lines: Sequence[RestaurantOrderLine]) -> list[dict[str, Any]]:
        return [
            {
                "line_no": line.line_no,
                "label": line.label,
                "packaging": line.packaging_name,
                "quantity": format(line.quantity, "f"),
                "unit_price": format(line.unit_price, "f"),
            }
            for line in lines
        ]

    # --- T1 : création ------------------------------------------------------------------------

    def create(
        self, data: OrderCreate, channel: OrderChannel = OrderChannel.STAFF
    ) -> tuple[RestaurantOrder, bool]:
        """Commande créée ``(commande, rejouée)`` : la même clé d'idempotence sur le même site
        renvoie la commande déjà enregistrée, sans rien créer."""
        site_id = operation_site(self.ctx, data.site_id)
        require_site_permission(self.ctx, site_id, ORDER_CREATE)
        # Deux soumissions simultanées de la même clé s'exécutent l'une après l'autre.
        self.db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"restaurant.order:{site_id}:{data.idempotency_key}", 0)
                )
            )
        )
        existing = self.db.scalars(
            select(RestaurantOrder).where(
                RestaurantOrder.tenant_id == self.ctx.tenant_id,
                RestaurantOrder.site_id == site_id,
                RestaurantOrder.idempotency_key == data.idempotency_key,
            )
        ).one_or_none()
        if existing is not None:
            return existing, True
        settings = ensure_settings(
            self.db, self.ctx.tenant_id, site_id, self._profile(site_id), lock="share"
        )
        customer_name = self._customer(site_id, data.customer_id)
        prepared = self._prepare_lines(site_id, data.lines)
        day = business_date(self.ctx.tenant.timezone, self.now)
        order = RestaurantOrder(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            business_date=day,
            daily_number=next_order_number(self.db, self.ctx.tenant_id, site_id, day),
            channel=channel,
            service_mode=data.service_mode,
            customer_id=data.customer_id,
            call_name=data.call_name,
            status=OrderStatus.OPEN,
            prep_status=PrepStatus.RECEIVED,
            settlement_status=SettlementStatus.UNSETTLED,
            # Recopié : changer le réglage du site ne touche aucune commande existante (D2).
            payment_timing=settings.payment_timing,
            idempotency_key=data.idempotency_key,
            created_by=self.ctx.user.id,
            version=1,
        )
        self.db.add(order)
        self.db.flush()
        lines = self._add_lines(order, prepared, 1)
        self._event(order, EventType.CREATED, lines, data={"channel": channel.value})
        self._audit(
            "created",
            order,
            {
                "channel": channel.value,
                "service_mode": order.service_mode.value,
                "payment_timing": order.payment_timing.value,
                "customer": customer_name,
                "call_name": order.call_name,
                "lines": self._lines_audit(lines),
            },
        )
        return order, False

    def _customer(self, site_id: uuid.UUID, customer_id: uuid.UUID | None) -> str | None:
        """Client facultatif, seulement si le module Clients est effectif sur le site (capacité
        testée, jamais le profil) ; du tenant et actif."""
        if customer_id is None:
            return None
        if CUSTOMERS_MODULE not in self._site_modules(site_id):
            raise BusinessRuleError(
                "Les clients ne sont pas disponibles sur ce site", code="customer_unavailable"
            )
        ref = get_customer_refs(self.db, {customer_id}).get(customer_id)
        if ref is None:
            raise BusinessRuleError("Client introuvable", code="customer_not_found")
        if not ref.is_active:
            raise BusinessRuleError(
                "Ce client est désactivé",
                code="customer_inactive",
                extra={"customer_code": ref.code},
            )
        return ref.name

    def add_lines(self, order_id: uuid.UUID, data: LinesAdd) -> RestaurantOrder:
        """Ajout à une commande ouverte NON réglée ; la même clé d'idempotence n'ajoute rien une
        seconde fois (P-8)."""
        site_id = self._site_of(order_id)
        settings_profile = self._profile(site_id)
        ensure_settings(self.db, self.ctx.tenant_id, site_id, settings_profile, lock="share")
        order = self._lock_order(order_id)
        require_site_permission(self.ctx, order.site_id, ORDER_CREATE)
        if data.idempotency_key is not None:
            replay = self.db.scalar(
                select(RestaurantOrderEvent.id).where(
                    RestaurantOrderEvent.tenant_id == self.ctx.tenant_id,
                    RestaurantOrderEvent.order_id == order.id,
                    RestaurantOrderEvent.idempotency_key == data.idempotency_key,
                )
            )
            if replay is not None:
                return order
        self._require_open(order)
        self._require_unsettled(order)
        lines = self._lock_lines(order)
        prepared = self._prepare_lines(order.site_id, data.lines)
        added = self._add_lines(order, prepared, max((li.line_no for li in lines), default=0) + 1)
        self._event(order, EventType.LINES_ADDED, added, idempotency_key=data.idempotency_key)
        self._touch(order, [*lines, *added])
        self.db.flush()
        self._audit("lines_added", order, {"lines": self._lines_audit(added)})
        return order

    def _site_of(self, order_id: uuid.UUID) -> uuid.UUID:
        site_id = self.db.scalar(
            select(RestaurantOrder.site_id).where(
                RestaurantOrder.tenant_id == self.ctx.tenant_id, RestaurantOrder.id == order_id
            )
        )
        if site_id is None:
            raise NotFoundError("Commande introuvable", code="order_not_found")
        ensure_row_site(self.ctx, site_id, write=True)
        return site_id

    # --- Préparation et service ---------------------------------------------------------------

    @staticmethod
    def _select(
        lines: Sequence[RestaurantOrderLine], line_ids: Sequence[uuid.UUID] | None
    ) -> list[RestaurantOrderLine]:
        if line_ids is None:
            return list(lines)
        by_id = {line.id: line for line in lines}
        unknown = sorted(str(i) for i in set(line_ids) if i not in by_id)
        if unknown:
            raise NotFoundError(
                "Ligne introuvable dans cette commande",
                code="order_line_not_found",
                extra={"lines": unknown},
            )
        return [by_id[i] for i in dict.fromkeys(line_ids)]

    def transition(
        self, order_id: uuid.UUID, action: str, line_ids: Sequence[uuid.UUID] | None
    ) -> RestaurantOrder:
        rule = TRANSITIONS[action]
        order = self._lock_order(order_id)
        require_site_permission(self.ctx, order.site_id, rule.permission)
        self._require_open(order)
        if (
            rule.source is LineStatus.RECEIVED
            and order.payment_timing is PaymentTiming.AT_ORDER
            and order.settlement_status is not SettlementStatus.SETTLED
        ):
            raise ConflictError(
                "Paiement à la commande : réglez la commande avant sa préparation",
                code="order_not_settled",
            )
        lines = self._lock_lines(order)
        selected = self._select(lines, line_ids)
        if line_ids is None:
            targets = [line for line in selected if line.status is rule.source]
        else:
            wrong = [line for line in selected if line.status is not rule.source]
            if wrong:
                raise ConflictError(
                    "Une ligne n'est pas dans l'état attendu pour cette action",
                    code="order_line_state_invalid",
                    extra={
                        "expected": rule.source.value,
                        "lines": [_line_state(li) for li in wrong],
                    },
                )
            targets = selected
        if not targets:
            raise ConflictError(
                "Aucune ligne dans l'état attendu pour cette action",
                code="order_lines_not_in_state",
                extra={"expected": rule.source.value},
            )
        for line in targets:
            line.status = rule.target
            if action == "start":
                line.prepared_at = self.now
                line.prepared_by = self.ctx.user.id
            elif action == "ready":
                line.ready_at = self.now
            elif action == "revert":
                line.ready_at = None
            else:
                line.served_at = self.now
                line.served_by = self.ctx.user.id
        self._event(order, rule.event, targets)
        self._touch(order, lines)
        self.db.flush()
        self._audit(
            rule.event.value.lower(),
            order,
            {"lines": [li.line_no for li in targets], "status": rule.target.value},
        )
        return order

    # --- Annulations --------------------------------------------------------------------------

    def _cancel_permission(self, line: RestaurantOrderLine) -> str:
        return ORDER_CANCEL if line.status is LineStatus.RECEIVED else ORDER_CANCEL_PREPARED

    def _cancel(self, lines: Sequence[RestaurantOrderLine], reason: str) -> None:
        for line in lines:
            line.status = LineStatus.CANCELLED
            line.cancelled_at = self.now
            line.cancelled_by = self.ctx.user.id
            line.cancel_reason = reason

    def cancel_lines(
        self, order_id: uuid.UUID, line_ids: Sequence[uuid.UUID], reason: str
    ) -> RestaurantOrder:
        """Commande NON réglée : sans effet sur vente, paiement, caisse ou stock (rien n'a été
        vendu ni sorti) ; une ligne servie ou annulée ne change plus."""
        order = self._lock_order(order_id)
        self._require_open(order)
        self._require_unsettled(order)
        lines = self._lock_lines(order)
        targets = self._select(lines, line_ids)
        final = [line for line in targets if line.status in FINAL_LINE_STATUSES]
        if final:
            raise ConflictError(
                "Une ligne servie ou annulée ne peut plus être annulée",
                code="order_line_final",
                extra={"lines": [_line_state(li) for li in final]},
            )
        for permission in sorted({self._cancel_permission(line) for line in targets}):
            require_site_permission(self.ctx, order.site_id, permission)
        previous = {line.id: line.status.value for line in targets}
        self._cancel(targets, reason)
        self._event(order, EventType.LINE_CANCELLED, targets, reason=reason)
        self._touch(order, lines)
        self.db.flush()
        self._audit(
            "lines_cancelled",
            order,
            {
                "reason": reason,
                "lines": [
                    {"line_no": li.line_no, "previous_status": previous[li.id]} for li in targets
                ],
            },
        )
        return order

    def cancel(self, order_id: uuid.UUID, reason: str) -> RestaurantOrder:
        """Annulation motivée de toute la commande : refusée si elle est réglée ou si une ligne a
        été servie ; ses lignes non finales sont annulées (permission selon leur état) ; une
        commande sans ligne restante s'annule avec ``order.cancel`` (N4)."""
        order = self._lock_order(order_id)
        self._require_open(order)
        self._require_unsettled(order)
        lines = self._lock_lines(order)
        if any(line.status is LineStatus.SERVED for line in lines):
            raise ConflictError(
                "Une ligne a déjà été servie : la commande ne peut plus être annulée",
                code="order_has_served_lines",
            )
        open_lines = [line for line in lines if line.status not in FINAL_LINE_STATUSES]
        permissions = {self._cancel_permission(line) for line in open_lines} or {ORDER_CANCEL}
        for permission in sorted(permissions):
            require_site_permission(self.ctx, order.site_id, permission)
        self._cancel(open_lines, reason)
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = self.now
        order.cancelled_by = self.ctx.user.id
        order.cancel_reason = reason
        order.prep_status = PrepStatus.NONE
        order.version += 1
        self._event(order, EventType.CANCELLED, open_lines, reason=reason)
        self.db.flush()
        self._audit(
            "cancelled", order, {"reason": reason, "lines": [li.line_no for li in open_lines]}
        )
        return order

    # --- Prise en charge (R2-C, D7) -----------------------------------------------------------

    def _last_claim_at(self, site_id: uuid.UUID) -> datetime | None:
        """Dernière action « Prendre » de l'employé sur le site, lue dans l'historique (aucune
        table ; index ``ix_restaurant_order_events_actor``) ; ni la création ni une
        réattribution ne comptent."""
        return self.db.scalar(
            select(func.max(RestaurantOrderEvent.occurred_at)).where(
                RestaurantOrderEvent.tenant_id == self.ctx.tenant_id,
                RestaurantOrderEvent.site_id == site_id,
                RestaurantOrderEvent.actor_user_id == self.ctx.user.id,
                RestaurantOrderEvent.event_type == EventType.CLAIMED,
            )
        )

    def _user_name(self, user_id: uuid.UUID | None) -> str | None:
        if user_id is None:
            return None
        return self.db.scalar(select(User.full_name).where(User.id == user_id))

    def claim(self, order_id: uuid.UUID) -> RestaurantOrder:
        """« Prendre » : l'employé devient responsable d'une commande ouverte (réglée ou non).

        Ordre des verrous (§7) : verrou consultatif de l'employé (site + employé : ses prises
        sur le site s'exécutent l'une après l'autre) → réglages du site en partage → commande ;
        responsable, heure de prise et réglages relus sous ces verrous. Deux prises simultanées
        d'une commande protégée : exactement une réussit (``409 order_claim_protected``).
        Commande déjà prise par l'employé : rien ne change (aucun évènement)."""
        site_id = self._site_of(order_id)
        require_site_permission(self.ctx, site_id, ORDER_CLAIM)
        self.db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"restaurant.claim:{site_id}:{self.ctx.user.id}", 0)
                )
            )
        )
        settings = ensure_settings(
            self.db, self.ctx.tenant_id, site_id, self._profile(site_id), lock="share"
        )
        order = self._lock_order(order_id)
        self._require_open(order)
        if order.assigned_user_id == self.ctx.user.id:
            return order
        protection = timedelta(minutes=settings.claim_protection_minutes)
        if (
            order.assigned_user_id is not None
            and order.assigned_at is not None
            and protection
            and self.now < order.assigned_at + protection
        ):
            raise ConflictError(
                "Cette commande est déjà prise en charge",
                code="order_claim_protected",
                extra={
                    "assigned_user_id": str(order.assigned_user_id),
                    "assigned_name": self._user_name(order.assigned_user_id),
                    "protected_until": (order.assigned_at + protection).isoformat(),
                },
            )
        cooldown = timedelta(minutes=settings.claim_cooldown_minutes)
        if cooldown:
            last = self._last_claim_at(site_id)
            if last is not None and self.now < last + cooldown:
                raise ConflictError(
                    "Délai entre deux prises en charge non écoulé",
                    code="claim_cooldown_active",
                    extra={"available_at": (last + cooldown).isoformat()},
                )
        previous = order.assigned_user_id
        order.assigned_user_id = self.ctx.user.id
        order.assigned_at = self.now
        order.assigned_by = self.ctx.user.id
        order.version += 1
        self._event(
            order,
            EventType.CLAIMED,
            data={"previous_user_id": str(previous) if previous else None},
        )
        self.db.flush()
        self._audit(
            "claimed",
            order,
            {
                "previous_user_id": str(previous) if previous else None,
                "previous_name": self._user_name(previous),
            },
        )
        return order

    def assignees(self, order_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
        """Membres éligibles à une réattribution (aide à la saisie, R2-E) : appartenance active
        et ``order.claim`` EFFECTIVE sur le site de la commande — le calcul exact du contrôle
        ``assignee_not_eligible``, qui reste fait à la réattribution."""
        order = self.get(order_id)
        require_site_permission(self.ctx, order.site_id, ORDER_REASSIGN)
        rows = self.db.execute(
            select(TenantMembership.user_id, User.full_name)
            .join(User, User.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == self.ctx.tenant_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
            .order_by(User.full_name, User.id)
        ).all()
        return [
            (user_id, name)
            for user_id, name in rows
            if member_holds(self.db, self.ctx, user_id, order.site_id, ORDER_CLAIM, self.now)
        ]

    def reassign(self, order_id: uuid.UUID, assignee_id: uuid.UUID, reason: str) -> RestaurantOrder:
        """Réattribution IMMÉDIATE (sans égard à la protection), motivée et auditée ; le nouveau
        responsable doit détenir ``order.claim`` effectif sur le site, contrôlé sous le verrou
        de la commande (``422 assignee_not_eligible``). Ne compte pas comme une prise (délai).
        Même responsable : rien ne change."""
        site_id = self._site_of(order_id)
        require_site_permission(self.ctx, site_id, ORDER_REASSIGN)
        order = self._lock_order(order_id)
        self._require_open(order)
        if not member_holds(self.db, self.ctx, assignee_id, order.site_id, ORDER_CLAIM, self.now):
            raise BusinessRuleError(
                "Ce membre ne peut pas prendre en charge les commandes de ce site",
                code="assignee_not_eligible",
                extra={"assignee_user_id": str(assignee_id)},
            )
        if order.assigned_user_id == assignee_id:
            return order
        previous = order.assigned_user_id
        order.assigned_user_id = assignee_id
        order.assigned_at = self.now
        order.assigned_by = self.ctx.user.id
        order.version += 1
        self._event(
            order,
            EventType.REASSIGNED,
            reason=reason,
            data={
                "previous_user_id": str(previous) if previous else None,
                "assignee_user_id": str(assignee_id),
            },
        )
        self.db.flush()
        self._audit(
            "reassigned",
            order,
            {
                "reason": reason,
                "previous_user_id": str(previous) if previous else None,
                "previous_name": self._user_name(previous),
                "assignee_user_id": str(assignee_id),
                "assignee_name": self._user_name(assignee_id),
            },
        )
        return order

    def set_customer(
        self, order_id: uuid.UUID, customer_id: uuid.UUID, reason: str | None
    ) -> RestaurantOrder:
        """Association tardive d'un client (R2-E, Z1) : commande OUVERTE et NON réglée (le client
        de la vente est celui de la commande au règlement, sous le même verrou ; après une
        annulation de la vente, la commande redevient « à régler » et peut changer de client).
        Même permission que le choix du client à la création (``order.create`` sur le site) ;
        mêmes contrôles du client (module Clients effectif sur le site, client du tenant, actif).
        Aucun droit au crédit n'en découle : le règlement applique les règles existantes des
        ventes (``sales.sale.credit_create``, limite, dérogation). Remplacer un client déjà
        associé exige un motif ; même client : rien ne change (aucun évènement)."""
        site_id = self._site_of(order_id)
        require_site_permission(self.ctx, site_id, ORDER_CREATE)
        order = self._lock_order(order_id)
        self._require_open(order)
        self._require_unsettled(order)
        name = self._customer(order.site_id, customer_id)
        previous = order.customer_id
        if previous == customer_id:
            return order
        if previous is not None and reason is None:
            raise BusinessRuleError(
                "Motif obligatoire pour remplacer le client de la commande",
                code="customer_change_reason_required",
            )
        previous_ref = (
            get_customer_refs(self.db, {previous}).get(previous) if previous is not None else None
        )
        change = {
            "customer_id": str(customer_id),
            "customer_name": name,
            "previous_customer_id": str(previous) if previous is not None else None,
            "previous_customer_name": previous_ref.name if previous_ref is not None else None,
        }
        order.customer_id = customer_id
        order.version += 1
        self._event(order, EventType.CUSTOMER_SET, reason=reason, data=change)
        self.db.flush()
        self._audit("customer_set", order, {"reason": reason, **change})
        return order

    # --- Règlement T2 (R2-D, D6) ---------------------------------------------------------------

    @staticmethod
    def _sale_lines(lines: Sequence[RestaurantOrderLine]) -> list[OriginLine]:
        """Lignes de la vente depuis l'instantané des lignes NON annulées, regroupées par
        présentation (une vente porte chaque présentation une seule fois) : quantités et totaux
        FIGÉS additionnés (total de la vente = total de la commande) ; prix unitaire figé commun,
        ou moyen pondéré si une même présentation a été commandée à des prix différents."""
        groups: dict[tuple[uuid.UUID, uuid.UUID | None], list[RestaurantOrderLine]] = {}
        for line in lines:
            if line.status is not LineStatus.CANCELLED:
                groups.setdefault((line.article_id, line.packaging_id), []).append(line)
        result: list[OriginLine] = []
        for (article_id, packaging_id), items in groups.items():
            quantity = sum((li.quantity for li in items), Decimal(0))
            total = sum((li.line_total for li in items), Decimal(0))
            prices = {li.unit_price for li in items}
            first = items[0]
            result.append(
                OriginLine(
                    article_id=article_id,
                    packaging_id=packaging_id,
                    packaging_name=first.packaging_name,
                    packaging_conversion=first.conversion,
                    quantity=quantity,
                    unit_price=prices.pop() if len(prices) == 1 else round_money(total / quantity),
                    line_total=total,
                )
            )
        return result

    def settle(
        self, order_id: uuid.UUID, data: OrderSettle
    ) -> tuple[RestaurantOrder, uuid.UUID, bool]:
        """Règlement ``(commande, vente, rejoué)``. Tout ou rien dans la transaction de la
        requête : en cas d'échec (stock, lot, article, assortiment, caisse, moyen de paiement,
        crédit), ni vente, ni paiement, ni mouvement ; la commande reste « à régler ».

        Verrous : clé d'idempotence (consultatif) → commande → lignes → vente (création et
        validation par le module Ventes). Deux règlements concurrents : le second attend le
        verrou de la commande puis trouve la commande réglée (``409 order_settled``) — ou, avec
        la même clé, la vente déjà enregistrée (rejeu). Base : ``uq_sales_active_origin``."""
        site_id = self._site_of(order_id)
        for permission in (SALE_CREATE, SALE_VALIDATE):
            require_site_permission(self.ctx, site_id, permission)
        if data.payments:
            require_site_permission(self.ctx, site_id, PAYMENT_CREATE)
        self.db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"restaurant.settle:{data.idempotency_key}", 0)
                )
            )
        )
        order = self._lock_order(order_id)
        existing = sale_by_idempotency_key(self.db, data.idempotency_key)
        if existing is not None:
            if existing.origin_type == ORIGIN_RESTAURANT_ORDER and existing.origin_id == order.id:
                return order, existing.id, True
            raise ConflictError(
                "Cette clé d'idempotence a déjà servi à une autre opération",
                code="idempotency_key_reused",
            )
        self._require_open(order)
        if order.settlement_status is SettlementStatus.SETTLED:
            raise ConflictError("Cette commande est déjà réglée", code="order_settled")
        lines = self._lock_lines(order)
        sale_lines = self._sale_lines(lines)
        if not sale_lines:
            raise ConflictError(
                "Toutes les lignes de cette commande sont annulées : annulez la commande",
                code="order_empty",
            )
        sale = SaleService(self.db, self.ctx, self.now).create_from_order(
            site_id=order.site_id,
            origin_type=ORIGIN_RESTAURANT_ORDER,
            origin_id=order.id,
            customer_id=order.customer_id,
            lines=sale_lines,
            notes=f"Commande n° {order.daily_number} du {order.business_date:%d/%m/%Y}",
            idempotency_key=data.idempotency_key,
            payments=data.payments,
            credit_override=data.credit_override,
            expired_lot_override=data.expired_lot_override,
        )
        order.settlement_status = SettlementStatus.SETTLED
        order.sale_id = sale.id
        self._event(
            order,
            EventType.SETTLED,
            data={"sale_id": str(sale.id), "sale_number": sale.number},
        )
        self._touch(order, lines)
        self.db.flush()
        self._audit(
            "settled",
            order,
            {
                "sale_id": str(sale.id),
                "sale_number": sale.number,
                "total": format(sale.total, "f"),
                "is_credit": sale.is_credit,
                "closed": order.status is OrderStatus.CLOSED,
            },
        )
        return order, sale.id, False

    def before_sale_cancel(self, origin_id: uuid.UUID, sale_id: uuid.UUID) -> None:
        """Port d'origine des ventes (Z3) : appelé par l'annulation d'une vente issue d'une
        commande, AVANT le verrou de la vente. Commande close : refus définitif
        (``order_closed``) ; vente active de la commande : la commande redevient « à régler »
        (évènement ``SALE_CANCELLED``), dans la même transaction que l'annulation. Une ancienne
        vente (déjà remplacée) ne touche pas la commande. Aucune permission de la restauration
        exigée : l'annulation de la vente relève des permissions des ventes."""
        order = self.db.scalars(
            select(RestaurantOrder)
            .where(RestaurantOrder.tenant_id == self.ctx.tenant_id, RestaurantOrder.id == origin_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if order is None or order.sale_id != sale_id:
            return
        if order.status is OrderStatus.CLOSED:
            raise ConflictError(
                "Commande close : sa vente ne peut plus être annulée (annulez un paiement puis "
                "enregistrez-en un nouveau sur la même vente)",
                code="order_closed",
            )
        number = sale_financial_states(self.db, {sale_id})[sale_id].number
        order.settlement_status = SettlementStatus.UNSETTLED
        order.sale_id = None
        order.version += 1
        self._event(
            order, EventType.SALE_CANCELLED, data={"sale_id": str(sale_id), "sale_number": number}
        )
        self.db.flush()
        self._audit("sale_cancelled", order, {"sale_id": str(sale_id), "sale_number": number})

    # --- Lecture ------------------------------------------------------------------------------

    def _query(self, site_ids: set[uuid.UUID]) -> Select[Any]:
        return select(RestaurantOrder).where(
            RestaurantOrder.tenant_id == self.ctx.tenant_id,
            RestaurantOrder.site_id.in_(site_ids),
        )

    def search(
        self,
        params: PageParams,
        *,
        site_id: uuid.UUID | None = None,
        state: OrderState = OrderState.ACTIVE,
        prep: PrepStatus | None = None,
        settlement: SettlementStatus | None = None,
        unsettled_served: bool = False,
        search: str | None = None,
        day: Any = None,
    ) -> tuple[list[OrderOut], int]:
        """Commandes des sites lisibles ; par défaut les commandes en cours, de la plus ancienne à
        la plus récente (ancienneté). ``unsettled_served`` : commandes servies non réglées
        (limites de V1). Recherche : numéro exact ou nom d'appel."""
        stmt = self._query(readable_site_ids(self.ctx, site_id))
        if state is OrderState.ACTIVE:
            stmt = stmt.where(RestaurantOrder.status.in_(ACTIVE_ORDER_STATUSES))
        elif state is OrderState.CLOSED:
            stmt = stmt.where(RestaurantOrder.status == OrderStatus.CLOSED)
        elif state is OrderState.CANCELLED:
            stmt = stmt.where(
                RestaurantOrder.status.in_((OrderStatus.CANCELLED, OrderStatus.REJECTED))
            )
        if prep is not None:
            stmt = stmt.where(RestaurantOrder.prep_status == prep)
        if settlement is not None:
            stmt = stmt.where(RestaurantOrder.settlement_status == settlement)
        if unsettled_served:
            served = select(RestaurantOrderLine.id).where(
                RestaurantOrderLine.tenant_id == RestaurantOrder.tenant_id,
                RestaurantOrderLine.order_id == RestaurantOrder.id,
                RestaurantOrderLine.status == LineStatus.SERVED,
            )
            stmt = stmt.where(
                RestaurantOrder.settlement_status == SettlementStatus.UNSETTLED,
                RestaurantOrder.status == OrderStatus.OPEN,
                served.exists(),
            )
        if day is not None:
            stmt = stmt.where(RestaurantOrder.business_date == day)
        term = (search or "").strip().lstrip("#")
        if term:
            conditions: list[ColumnElement[bool]] = [
                RestaurantOrder.call_name.ilike(f"%{escape_like(term)}%", escape="\\")
            ]
            if term.isdigit():
                conditions.append(RestaurantOrder.daily_number == int(term))
            stmt = stmt.where(or_(*conditions))
        stmt = apply_sort(stmt, params.sort, SORTS, "created_at", RestaurantOrder.id)
        orders, total = paginate(self.db, stmt, params)
        return self.to_out(orders), total

    def get(self, order_id: uuid.UUID) -> RestaurantOrder:
        order = self.db.scalars(
            select(RestaurantOrder).where(
                RestaurantOrder.tenant_id == self.ctx.tenant_id, RestaurantOrder.id == order_id
            )
        ).one_or_none()
        if order is None:
            raise NotFoundError("Commande introuvable", code="order_not_found")
        ensure_row_site(self.ctx, order.site_id, write=False)
        return order

    def _lines_of(self, order_ids: set[uuid.UUID]) -> dict[uuid.UUID, list[RestaurantOrderLine]]:
        result: dict[uuid.UUID, list[RestaurantOrderLine]] = {i: [] for i in order_ids}
        if not order_ids:
            return result
        for line in self.db.scalars(
            select(RestaurantOrderLine)
            .where(
                RestaurantOrderLine.tenant_id == self.ctx.tenant_id,
                RestaurantOrderLine.order_id.in_(order_ids),
            )
            .order_by(RestaurantOrderLine.order_id, RestaurantOrderLine.line_no)
        ):
            result[line.order_id].append(line)
        return result

    def to_out(
        self, orders: Sequence[RestaurantOrder], *, with_lines: bool = False
    ) -> list[OrderOut]:
        lines = self._lines_of({o.id for o in orders})
        user_ids = {u for o in orders for u in (o.created_by, o.assigned_user_id) if u is not None}
        users: dict[uuid.UUID, str] = {
            row.id: row.full_name
            for row in self.db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))
        }
        sites: dict[uuid.UUID, str] = {
            row.id: row.name
            for row in self.db.execute(
                select(Site.id, Site.name).where(Site.id.in_({o.site_id for o in orders}))
            )
        }
        customers = get_customer_refs(
            self.db, {o.customer_id for o in orders if o.customer_id is not None}
        )
        finances = sale_financial_states(
            self.db, {o.sale_id for o in orders if o.sale_id is not None}
        )
        result: list[OrderOut] = []
        for order in orders:
            own = lines[order.id]
            counts = Counter(line.status for line in own)
            served = [line.served_at for line in own if line.served_at is not None]
            customer = customers.get(order.customer_id) if order.customer_id else None
            finance = finances.get(order.sale_id) if order.sale_id else None
            result.append(
                OrderOut(
                    id=order.id,
                    site_id=order.site_id,
                    site_name=sites.get(order.site_id, ""),
                    business_date=order.business_date,
                    daily_number=order.daily_number,
                    channel=order.channel,
                    service_mode=order.service_mode,
                    customer_id=order.customer_id,
                    customer_name=customer.name if customer is not None else None,
                    call_name=order.call_name,
                    status=order.status,
                    prep_status=order.prep_status,
                    settlement_status=order.settlement_status,
                    payment_timing=order.payment_timing,
                    assigned_user_id=order.assigned_user_id,
                    assigned_name=users.get(order.assigned_user_id)
                    if order.assigned_user_id
                    else None,
                    assigned_at=order.assigned_at,
                    created_by=order.created_by,
                    created_by_name=users.get(order.created_by) if order.created_by else None,
                    total=sum(
                        (li.line_total for li in own if li.status is not LineStatus.CANCELLED),
                        Decimal("0.00"),
                    ),
                    sale_id=order.sale_id,
                    sale_number=finance.number if finance else None,
                    payment_status=finance.payment_status if finance else None,
                    amount_due=finance.remaining_amount if finance else None,
                    line_counts=LineCounts(
                        received=counts[LineStatus.RECEIVED],
                        in_preparation=counts[LineStatus.IN_PREPARATION],
                        ready=counts[LineStatus.READY],
                        served=counts[LineStatus.SERVED],
                        cancelled=counts[LineStatus.CANCELLED],
                    ),
                    created_at=order.created_at,
                    last_served_at=max(served) if served else None,
                    updated_at=order.updated_at,
                    closed_at=order.closed_at,
                    cancelled_at=order.cancelled_at,
                    cancel_reason=order.cancel_reason,
                    version=order.version,
                    lines=[LineOut.model_validate(li) for li in own] if with_lines else None,
                )
            )
        return result

    def detail(self, order_id: uuid.UUID) -> OrderOut:
        self.db.expire_all()
        return self.to_out([self.get(order_id)], with_lines=True)[0]

    def events(self, order_id: uuid.UUID) -> list[EventOut]:
        order = self.get(order_id)
        rows = self.db.execute(
            select(RestaurantOrderEvent, User.full_name)
            .outerjoin(User, User.id == RestaurantOrderEvent.actor_user_id)
            .where(
                RestaurantOrderEvent.tenant_id == self.ctx.tenant_id,
                RestaurantOrderEvent.order_id == order.id,
            )
            .order_by(RestaurantOrderEvent.occurred_at, RestaurantOrderEvent.id)
        ).all()
        return [
            EventOut(
                id=event.id,
                event_type=event.event_type,
                actor_kind=event.actor_kind,
                actor_user_id=event.actor_user_id,
                actor_name=name,
                reason=event.reason,
                line_ids=event.line_ids,
                data=event.data,
                occurred_at=event.occurred_at,
            )
            for event, name in rows
        ]

    def ticket(self, order_id: uuid.UUID) -> TicketOut:
        """Ticket de retrait (D14, Q3) : numéro, nom d'appel, mode, lignes non annulées ; aucun
        prix, coût ni stock. Réimpression libre pour qui peut consulter la commande ; impression
        non journalisée en V1."""
        order = self.get(order_id)
        site = self.db.get(Site, order.site_id)
        identity = document_identity(self.db, self.ctx.tenant)
        lines = self._lines_of({order.id})[order.id]
        return TicketOut(
            company_name=identity.trade_name or identity.name,
            site_name=site.name if site is not None else "",
            business_date=order.business_date,
            daily_number=order.daily_number,
            call_name=order.call_name,
            service_mode=order.service_mode,
            created_at=order.created_at,
            timezone=self.ctx.tenant.timezone,
            lines=[
                TicketLineOut(
                    label=line.label,
                    packaging_name=line.packaging_name,
                    quantity=line.quantity,
                    unit=line.unit,
                    note=line.note,
                )
                for line in lines
                if line.status is not LineStatus.CANCELLED
            ],
        )


__all__ = ["ORDER_VIEW", "OrderService", "OrderState", "TRANSITIONS"]
