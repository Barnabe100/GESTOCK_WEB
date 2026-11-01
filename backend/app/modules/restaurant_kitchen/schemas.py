import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.restaurant_orders.api import OrderPriority
from app.shared.schemas import Quantity
from app.shared.text import Optional500, Required100

SortOrder = Annotated[int, Field(ge=0, le=100_000)]
# Une correction porte sur une commande : au plus 100 lignes par requête.
MAX_LINES = 100


# --- Postes -------------------------------------------------------------------------------------


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    sort_order: int
    is_active: bool
    # Sections et éléments du menu affectés à ce poste.
    route_count: int
    created_at: datetime
    updated_at: datetime


class StationCreate(BaseModel):
    """``site_id`` facultatif si un site est sélectionné (``X-Site-Id``)."""

    site_id: uuid.UUID | None = None
    name: Required100
    sort_order: SortOrder = 0


class StationUpdate(BaseModel):
    name: Required100
    sort_order: SortOrder = 0


# --- Affectations -------------------------------------------------------------------------------


class RouteOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    station_id: uuid.UUID
    station_name: str
    station_active: bool
    section_id: uuid.UUID | None
    menu_item_id: uuid.UUID | None
    updated_at: datetime


class RouteSet(BaseModel):
    """Affectation d'UNE section ou d'UN élément du menu du site à un poste ; ``station_id``
    nul : l'affectation est retirée (routage par la section, puis « Sans poste »)."""

    site_id: uuid.UUID | None = None
    section_id: uuid.UUID | None = None
    menu_item_id: uuid.UUID | None = None
    station_id: uuid.UUID | None

    @model_validator(mode="after")
    def _one_target(self) -> "RouteSet":
        if (self.section_id is None) == (self.menu_item_id is None):
            raise ValueError("Indiquez une section OU un élément du menu")
        return self


# --- Commandes : priorité et poste des lignes ---------------------------------------------------


class PrioritySet(BaseModel):
    priority: OrderPriority
    reason: Optional500 = None


class LinesRoute(BaseModel):
    """Poste de lignes non finales (``station_id`` nul : « Sans poste »)."""

    line_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_LINES)
    station_id: uuid.UUID | None
    reason: Optional500 = None


# --- Écran cuisine ------------------------------------------------------------------------------


class TicketState(StrEnum):
    """Colonne d'un ticket : état de sa ligne la MOINS avancée (ADR-0050 K4)."""

    RECEIVED = "RECEIVED"
    IN_PREPARATION = "IN_PREPARATION"
    READY = "READY"


class BoardLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    label: str
    packaging_name: str | None
    quantity: Quantity
    unit: str
    note: str | None
    status: str
    received_at: datetime
    prepared_at: datetime | None
    ready_at: datetime | None


class BoardTicketOut(BaseModel):
    """Une commande ouverte × un poste (``station_id`` nul : « Sans poste »)."""

    order_id: uuid.UUID
    daily_number: int
    business_date: date
    service_mode: str
    table_label: str | None
    call_name: str | None
    priority: OrderPriority
    assigned_name: str | None
    awaiting_payment: bool
    station_id: uuid.UUID | None
    station_name: str | None
    state: TicketState
    # Réception de la première ligne du ticket (ancienneté, distincte de la priorité).
    received_at: datetime
    order_created_at: datetime
    lines: list[BoardLineOut]


class BoardStationOut(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool


class BoardCounts(BaseModel):
    received: int = 0
    in_preparation: int = 0
    ready: int = 0


class BoardOut(BaseModel):
    site_id: uuid.UUID
    site_name: str
    # Postes du site (filtres) : actifs, plus les inactifs encore porteurs de lignes.
    stations: list[BoardStationOut]
    counts: BoardCounts
    tickets: list[BoardTicketOut]
