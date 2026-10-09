import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.shared.schemas import Money, Quantity
from app.shared.text import Optional150, Optional200, Optional500, Required100

SortOrder = Annotated[int, Field(ge=0, le=100_000)]


# --- Sections -----------------------------------------------------------------------------------


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    sort_order: int
    is_active: bool
    # Éléments de la section (actifs et inactifs).
    item_count: int
    created_at: datetime
    updated_at: datetime


class SectionCreate(BaseModel):
    """``site_id`` facultatif si un site est sélectionné (``X-Site-Id``)."""

    site_id: uuid.UUID | None = None
    name: Required100
    sort_order: SortOrder = 0


class SectionUpdate(BaseModel):
    name: Required100
    sort_order: SortOrder = 0


# --- Éléments -----------------------------------------------------------------------------------


class ItemOut(BaseModel):
    """Élément du menu et état COURANT de sa présentation dans le catalogue et l'assortiment du
    site. Prix = prix de vente du catalogue (aucun prix par site, aucun coût)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    section_id: uuid.UUID
    section_name: str
    section_active: bool
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    packaging_id: uuid.UUID | None
    # ``None`` si l'élément est en unité de base, ou si le conditionnement est désactivé (le
    # catalogue n'expose que les conditionnements actifs).
    packaging_name: str | None
    conversion: Quantity | None
    display_name: str | None
    description: str | None
    sort_order: int
    is_active: bool
    available: bool
    unavailable_reason: str | None
    # Prix du catalogue ; ``None`` : conditionnement sans prix configuré ou désactivé.
    price: Money | None
    # Commandable maintenant ; sinon, motifs (codes stables, traduits par l'interface).
    orderable: bool
    blockers: list[str]
    created_at: datetime
    updated_at: datetime


class ItemCreate(BaseModel):
    """``site_id`` facultatif si un site est sélectionné. ``packaging_id`` nul = unité de base.
    La présentation (article, conditionnement) est fixée à la création."""

    site_id: uuid.UUID | None = None
    section_id: uuid.UUID
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None = None
    display_name: Optional150 = None
    description: Optional500 = None
    sort_order: SortOrder = 0


class ItemUpdate(BaseModel):
    """Informations de l'élément (la présentation ne change jamais : un autre conditionnement
    est un autre élément)."""

    section_id: uuid.UUID
    display_name: Optional150 = None
    description: Optional500 = None
    sort_order: SortOrder = 0


class AvailabilityInput(BaseModel):
    """« Épuisé » manuel : ``available = false`` avec un motif facultatif."""

    available: bool
    reason: Optional200 = None
