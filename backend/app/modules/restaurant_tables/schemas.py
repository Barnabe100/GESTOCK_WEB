import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.modules.restaurant_tables.models import MAX_SEATS
from app.shared.text import Required40, Required100

SortOrder = Annotated[int, Field(ge=0, le=100_000)]
Seats = Annotated[int, Field(ge=1, le=MAX_SEATS)]


# --- Zones --------------------------------------------------------------------------------------


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    sort_order: int
    is_active: bool
    # Tables de la zone (toutes) et tables actives.
    table_count: int
    active_table_count: int
    created_at: datetime
    updated_at: datetime


class ZoneCreate(BaseModel):
    """``site_id`` facultatif si un site est sélectionné (``X-Site-Id``)."""

    site_id: uuid.UUID | None = None
    name: Required100
    sort_order: SortOrder = 0


class ZoneUpdate(BaseModel):
    name: Required100
    sort_order: SortOrder = 0


# --- Tables -------------------------------------------------------------------------------------


class TableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    zone_id: uuid.UUID | None
    zone_name: str | None
    zone_active: bool | None
    label: str
    seats: int | None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TableCreate(BaseModel):
    """``site_id`` facultatif si un site est sélectionné ; zone facultative (A4), du même site
    et active."""

    site_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    label: Required40
    seats: Seats | None = None
    sort_order: SortOrder = 0


class TableUpdate(BaseModel):
    """Remplacement complet des champs modifiables (le site d'une table ne change jamais)."""

    zone_id: uuid.UUID | None = None
    label: Required40
    seats: Seats | None = None
    sort_order: SortOrder = 0


# --- Plan de salle (R5-B, AR-3) -----------------------------------------------------------------


class TableState(StrEnum):
    """État d'une table, calculé à la lecture (B1 / C2) — priorité : Occupée > À régler > Libre."""

    FREE = "FREE"  # aucune commande à confirmer ou ouverte
    OCCUPIED = "OCCUPIED"  # au moins une commande « en cours »
    TO_SETTLE = "TO_SETTLE"  # toutes ouvertes, non réglées, tout servi


class BoardOrderOut(BaseModel):
    """Commande occupant la table (détail réservé à ``restaurant.orders.order.view`` sur le
    site)."""

    id: uuid.UUID
    business_date: date
    daily_number: int
    call_name: str | None
    status: str
    prep_status: str
    settlement_status: str
    # Vrai si la commande n'attend plus que son règlement (ouverte, non réglée, tout servi).
    to_settle: bool
    created_at: datetime


class BoardTableOut(BaseModel):
    id: uuid.UUID
    label: str
    seats: int | None
    zone_id: uuid.UUID | None
    zone_name: str | None
    is_active: bool
    state: TableState
    # Nombre de commandes occupant la table (plusieurs possibles, A1) et création de la plus
    # ancienne (ancienneté) : visibles avec ``restaurant.tables.table.view``.
    order_count: int
    occupied_since: datetime | None
    # ``None`` sans ``restaurant.orders.order.view`` sur le site.
    orders: list[BoardOrderOut] | None


class BoardCounts(BaseModel):
    free: int
    occupied: int
    to_settle: int


class BoardOut(BaseModel):
    """Plan de salle d'UN site : tables actives (et toute table encore occupée), dans l'ordre des
    zones puis des tables (sans zone en dernier)."""

    site_id: uuid.UUID
    site_name: str
    orders_visible: bool
    counts: BoardCounts
    tables: list[BoardTableOut]
