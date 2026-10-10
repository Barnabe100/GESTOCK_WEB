import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.restaurant_orders.models import (
    ActorKind,
    EventType,
    LineStatus,
    OrderChannel,
    OrderStatus,
    PaymentTiming,
    PrepStatus,
    ServiceMode,
    SettlementStatus,
)
from app.shared.schemas import Money, PositiveQuantity, Quantity
from app.shared.text import Optional40, Optional200, Required500

Minutes = Annotated[int, Field(ge=0, le=1440)]
# Une commande reste un document lisible : au plus 100 lignes par saisie.
MAX_LINES = 100


# --- Réglages du site ---------------------------------------------------------------------------


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    site_id: uuid.UUID
    payment_timing: PaymentTiming
    claim_protection_minutes: int
    claim_cooldown_minutes: int
    # Sans effet avant le palier R7 (QR) ; non modifiable ici.
    qr_auto_accept: bool
    updated_at: datetime


class SettingsUpdate(BaseModel):
    """Le mode de paiement s'applique aux commandes CRÉÉES ensuite : une commande garde le mode
    recopié à sa création (D2)."""

    payment_timing: PaymentTiming
    claim_protection_minutes: Minutes = 5
    claim_cooldown_minutes: Minutes = 0


# --- Saisie -------------------------------------------------------------------------------------


class LineInput(BaseModel):
    menu_item_id: uuid.UUID
    quantity: PositiveQuantity
    note: Optional200 = None


class OrderCreate(BaseModel):
    """Création (T1) : numéro, prix figés ; ni vente, ni paiement, ni stock. ``site_id``
    facultatif si un site est sélectionné. Clé d'idempotence obligatoire : la même clé renvoie
    la commande déjà créée."""

    site_id: uuid.UUID | None = None
    service_mode: ServiceMode
    customer_id: uuid.UUID | None = None
    call_name: Optional40 = None
    lines: list[LineInput] = Field(min_length=1, max_length=MAX_LINES)
    idempotency_key: uuid.UUID


class LinesAdd(BaseModel):
    lines: list[LineInput] = Field(min_length=1, max_length=MAX_LINES)
    # Facultative : la même clé sur la même commande n'ajoute rien une seconde fois (P-8).
    idempotency_key: uuid.UUID | None = None


class LineSelection(BaseModel):
    """Lignes visées par une transition ; absent : toutes les lignes de la commande dans l'état
    de départ de la transition (action sur toute la commande)."""

    line_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=MAX_LINES)


class LinesCancel(BaseModel):
    line_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_LINES)
    reason: Required500


class OrderCancel(BaseModel):
    reason: Required500


class OrderReassign(BaseModel):
    """Réattribution immédiate (D7) : le nouveau responsable doit détenir
    ``restaurant.orders.order.claim`` effectif sur le site de la commande ; motif obligatoire."""

    assignee_user_id: uuid.UUID
    reason: Required500


# --- Lecture ------------------------------------------------------------------------------------


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_no: int
    menu_item_id: uuid.UUID
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None
    label: str
    packaging_name: str | None
    unit: str
    conversion: Quantity | None
    unit_price: Money
    quantity: Quantity
    base_quantity: Quantity
    line_total: Money
    note: str | None
    status: LineStatus
    created_at: datetime
    prepared_at: datetime | None
    ready_at: datetime | None
    served_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None


class LineCounts(BaseModel):
    received: int = 0
    in_preparation: int = 0
    ready: int = 0
    served: int = 0
    cancelled: int = 0


class OrderOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    business_date: date
    daily_number: int
    channel: OrderChannel
    service_mode: ServiceMode
    customer_id: uuid.UUID | None
    customer_name: str | None
    call_name: str | None
    status: OrderStatus
    prep_status: PrepStatus
    settlement_status: SettlementStatus
    payment_timing: PaymentTiming
    assigned_user_id: uuid.UUID | None
    assigned_name: str | None
    # Heure de la dernière prise ou réattribution (début de la protection, D7).
    assigned_at: datetime | None
    created_by: uuid.UUID | None
    created_by_name: str | None
    # Montant aux prix figés des lignes non annulées (à régler) ; l'état financier est lu sur
    # la vente (R2-D).
    total: Money
    line_counts: LineCounts
    # Ancienneté (limites de V1) : création et dernier service.
    created_at: datetime
    last_served_at: datetime | None
    updated_at: datetime
    closed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    version: int
    lines: list[LineOut] | None = None


class EventOut(BaseModel):
    id: uuid.UUID
    event_type: EventType
    actor_kind: ActorKind
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    reason: str | None
    line_ids: list[Any]
    data: dict[str, Any]
    occurred_at: datetime


# --- Ticket de retrait (D14, Q3) ----------------------------------------------------------------


class TicketLineOut(BaseModel):
    label: str
    packaging_name: str | None
    quantity: Quantity
    unit: str
    note: str | None


class TicketOut(BaseModel):
    """Ticket de retrait 80 mm : AUCUN prix, coût ni stock. Construit par le serveur depuis la
    commande enregistrée (lignes non annulées)."""

    company_name: str
    site_name: str
    business_date: date
    daily_number: int
    call_name: str | None
    service_mode: ServiceMode
    created_at: datetime
    timezone: str
    lines: list[TicketLineOut]
