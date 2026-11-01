"""Réponses PUBLIQUES (palier R7, ADR-0052) : uniquement ce qu'il faut au client pour commander
— jamais de stock, de coût, de marge, de référence interne, d'identifiant d'article, de membre ni
de donnée d'un autre client."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.restaurant_qr.models import (
    PLATFORM_MAX_LINES,
    PLATFORM_MAX_QUANTITY,
    ContactFieldMode,
    QrPointKind,
)
from app.shared.schemas import Money, Quantity
from app.shared.text import Optional30, Optional40, Optional200


class PublicMenuItem(BaseModel):
    id: uuid.UUID
    name: str
    packaging_name: str | None
    description: str | None
    price: Money
    # Épuisé (manuel) : visible mais non commandable.
    available: bool


class PublicMenuSection(BaseModel):
    id: uuid.UUID
    name: str
    items: list[PublicMenuItem]


class PublicPoint(BaseModel):
    kind: QrPointKind
    # Table (libellé) ou comptoir (libellé du point).
    label: str


class PublicMenuOut(BaseModel):
    establishment: str
    site_name: str
    point: PublicPoint
    currency: str
    sections: list[PublicMenuSection]
    ordering: "PublicOrdering"


class PublicOrdering(BaseModel):
    """Règles de saisie du site (contrôlées à nouveau par le serveur à l'envoi)."""

    max_lines: int
    max_quantity_per_line: int
    customer_name_mode: ContactFieldMode
    customer_phone_mode: ContactFieldMode


# --- Commande publique --------------------------------------------------------------------------


class PublicOrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_item_id: uuid.UUID
    # Quantité ENTIÈRE ; bornée ici par le plafond de la plateforme, puis par celui du site.
    quantity: Annotated[int, Field(strict=True, ge=1, le=PLATFORM_MAX_QUANTITY)]
    # Instructions de la ligne (« sans glaçons »).
    note: Optional200 = None


class PublicOrderCreate(BaseModel):
    """Seuls le contenu et les informations demandées : AUCUN champ de décision (site, table,
    prix, canal, état…) n'est accepté — tout champ inconnu est refusé."""

    model_config = ConfigDict(extra="forbid")

    # Clé aléatoire (UUID v4) générée par le navigateur pour CETTE commande ; limitée au point.
    client_key: uuid.UUID
    lines: list[PublicOrderLine] = Field(min_length=1, max_length=PLATFORM_MAX_LINES)
    customer_name: Optional40 = None
    customer_phone: Optional30 = None

    @field_validator("client_key")
    @classmethod
    def _random_key(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version != 4:
            raise ValueError("Clé aléatoire (UUID v4) attendue")
        return value


class PublicOrderPlaced(BaseModel):
    """Réponse d'envoi : numéro, état et lien de suivi (secret émis à CETTE réponse seulement)."""

    daily_number: int
    status: str
    tracking_url: str
    # Même requête renvoyée avec la même clé : aucune nouvelle commande ; nouveau lien de suivi
    # (l'ancien ne fonctionne plus).
    replayed: bool


# --- Suivi public -------------------------------------------------------------------------------


class PublicTrackingLine(BaseModel):
    name: str
    packaging_name: str | None
    quantity: Quantity
    # RECEIVED, IN_PREPARATION, READY, SERVED, CANCELLED
    status: str


class PublicTrackingOut(BaseModel):
    """Suivi d'UNE commande : état et reste à régler, rien d'interne (ni membre, ni coût, ni
    stock, ni autre client) ; motif affiché pour un refus seulement."""

    establishment: str
    site_name: str
    point: PublicPoint | None
    currency: str
    daily_number: int
    # PENDING_CONFIRMATION, OPEN, CLOSED, CANCELLED, REJECTED
    status: str
    prep_status: str
    lines: list[PublicTrackingLine]
    total: Money
    remaining_due: Money
    rejection_reason: str | None
    # Fin du suivi (la commande est finie) : le lien cesse ensuite de fonctionner.
    available_until: datetime | None
