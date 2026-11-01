import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.restaurant_qr.models import (
    PLATFORM_MAX_LINES,
    PLATFORM_MAX_PENDING,
    PLATFORM_MAX_QUANTITY,
    ContactFieldMode,
    QrPointKind,
)
from app.shared.text import Required100


class PointOut(BaseModel):
    """Point QR tel que lu : JAMAIS le jeton ni son empreinte."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    kind: QrPointKind
    table_id: uuid.UUID | None
    table_label: str | None
    table_active: bool | None
    zone_name: str | None
    label: str | None
    # Nom affiché : libellé du comptoir ou de la table.
    display_name: str
    is_active: bool
    token_generated_at: datetime
    token_generated_by_name: str | None
    created_at: datetime
    updated_at: datetime


class PointIssued(BaseModel):
    """Réponse d'émission (création ou régénération) : SEULE réponse qui porte l'adresse et
    l'image du QR Code ; ni l'une ni l'autre ne peut être relue ensuite."""

    point: PointOut
    url: str
    svg: str


class PointCreate(BaseModel):
    """``site_id`` facultatif si un site est sélectionné ; table pour un point de table,
    libellé pour un comptoir."""

    site_id: uuid.UUID | None = None
    kind: QrPointKind
    table_id: uuid.UUID | None = None
    label: Required100 | None = None

    @model_validator(mode="after")
    def _target(self) -> "PointCreate":
        if self.kind is QrPointKind.TABLE and (self.table_id is None or self.label is not None):
            raise ValueError("Un point de table exige une table (et aucun libellé)")
        if self.kind is QrPointKind.COUNTER and (self.label is None or self.table_id is not None):
            raise ValueError("Un point de comptoir exige un libellé (et aucune table)")
        return self


class PointUpdate(BaseModel):
    """Seul le libellé d'un comptoir se modifie (la table d'un point est fixe)."""

    label: Required100


class PointRegenerate(BaseModel):
    """Confirmation explicite : les QR Codes imprimés de ce point deviennent invalides."""

    confirm_invalidation: Literal[True]


# --- Réglages QR d'un site ----------------------------------------------------------------------


class QrSettingsOut(BaseModel):
    site_id: uuid.UUID
    max_lines: int
    max_quantity_per_line: int
    max_pending_per_point: int
    customer_name_mode: ContactFieldMode
    customer_phone_mode: ContactFieldMode
    # Plafonds de la plateforme (un réglage ne peut jamais les dépasser).
    platform_max_lines: int = PLATFORM_MAX_LINES
    platform_max_quantity_per_line: int = PLATFORM_MAX_QUANTITY
    platform_max_pending_per_point: int = PLATFORM_MAX_PENDING
    updated_at: datetime


class QrSettingsUpdate(BaseModel):
    """Validé par le serveur ; les plafonds de la plateforme sont aussi des contraintes en
    base."""

    site_id: uuid.UUID | None = None
    max_lines: Annotated[int, Field(ge=1, le=PLATFORM_MAX_LINES)]
    max_quantity_per_line: Annotated[int, Field(ge=1, le=PLATFORM_MAX_QUANTITY)]
    max_pending_per_point: Annotated[int, Field(ge=1, le=PLATFORM_MAX_PENDING)]
    customer_name_mode: ContactFieldMode
    customer_phone_mode: ContactFieldMode
