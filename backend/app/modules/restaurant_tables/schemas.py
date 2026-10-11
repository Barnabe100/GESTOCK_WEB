import uuid
from datetime import datetime
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
