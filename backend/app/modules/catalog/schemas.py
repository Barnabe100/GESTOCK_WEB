import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.shared.schemas import Money, PositiveQuantity, Quantity
from app.shared.text import (
    Optional50,
    Optional1000,
    Required20,
    Required50,
    Required100,
    Required255,
)


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CategoryInput(BaseModel):
    name: Required100


# Assortiment par site (ADR-0046).
class AssortmentState(StrEnum):
    ACTIVE = "active"  # dans l'assortiment du site
    REMOVED = "removed"  # retiré (ligne conservée, réactivable)
    NONE = "none"  # jamais associé au site


class ArticleOut(BaseModel):
    id: uuid.UUID
    reference: str
    designation: str
    category_id: uuid.UUID
    category_name: str
    unit: str
    main_supplier_id: uuid.UUID | None
    main_supplier_name: str | None
    sale_price: Money
    min_stock: Quantity
    max_stock: Quantity | None
    description: str | None
    barcode: str | None
    is_active: bool
    stock_managed: bool
    decimal_quantity_allowed: bool
    # Lot 3-G (ADR-0045) : suivi par lot et suivi de péremption.
    lot_tracked: bool = False
    expiry_tracked: bool = False
    created_at: datetime
    updated_at: datetime
    # Coût interne (Lot 3-A) : présent SEULEMENT avec ``catalog.article.cost_view`` — sinon le
    # champ est absent de la réponse (jamais remplacé par une valeur fictive).
    purchase_price: Money | None = None
    # Assortiment (ADR-0046) : présent seulement si la liste est demandée pour un site
    # (``site_id``) — ``active``, ``removed`` ou ``none`` (jamais associé à ce site).
    site_assortment: AssortmentState | None = None


class ArticleCreate(BaseModel):
    reference: Required50
    designation: Required255
    category_id: uuid.UUID
    unit: Required20
    main_supplier_id: uuid.UUID | None = None
    # Prix : définis à la création seulement avec ``catalog.article.price_update`` (sinon 0).
    purchase_price: Money = Decimal("0")
    sale_price: Money = Decimal("0")
    min_stock: Quantity = Decimal("0")
    max_stock: Quantity | None = None
    description: Optional1000 = None
    barcode: Optional50 = None
    stock_managed: bool = True
    # Lot 3-B : quantités vendues décimales (kg, m, L) ; défaut : entières seulement.
    decimal_quantity_allowed: bool = False
    # Lot 3-G : suivi par lot et de péremption.
    lot_tracked: bool = False
    expiry_tracked: bool = False
    # Assortiment (ADR-0046, D6) : sites où l'article est ajouté dès sa création. Vide par
    # défaut : un nouvel article n'est associé à AUCUN site ; ``catalog.assortment.manage``
    # exigée sur chaque site choisi, tout ou rien.
    site_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class ArticleUpdate(BaseModel):
    """Champs absents : inchangés. Aucun champ de stock ni de coût moyen (ART-11)."""

    reference: Required50 | None = None
    designation: Required255 | None = None
    category_id: uuid.UUID | None = None
    unit: Required20 | None = None
    main_supplier_id: uuid.UUID | None = None
    purchase_price: Money | None = None
    sale_price: Money | None = None
    min_stock: Quantity | None = None
    max_stock: Quantity | None = None
    description: Optional1000 = None
    barcode: Optional50 = None
    stock_managed: bool | None = None
    decimal_quantity_allowed: bool | None = None
    lot_tracked: bool | None = None
    expiry_tracked: bool | None = None


class LotTrackingOut(BaseModel):
    """Fermeture P1-b (Lot 3-G) : ``available`` faux tant que le Lot 3-H n'est pas livré —
    l'interface ne propose alors pas l'activation du suivi par lot."""

    available: bool


class PriceChangeOut(BaseModel):
    """Changement de prix catalogue d'un article : UNE entrée du journal d'audit existant
    (création ou modification), aucune table dédiée (Lot 3-A). Prix de vente : ``sale_price_*``
    (nul si inchangé) ; prix d'achat (coût interne) : ``purchase_price_*``, présents SEULEMENT
    avec ``catalog.article.cost_view``."""

    id: uuid.UUID
    occurred_at: datetime
    user_name: str | None
    sale_price_before: Money | None
    sale_price_after: Money | None
    purchase_price_before: Money | None = None
    purchase_price_after: Money | None = None


class PackagingOut(BaseModel):
    """Conditionnement de vente (Lot 3-B) : quantité de base = quantité × ``conversion``.
    ``in_use`` : figure sur au moins une vente — la conversion est alors figée.
    ``sale_price`` nul : prix NON CONFIGURÉ, conditionnement invendable."""

    id: uuid.UUID
    article_id: uuid.UUID
    name: str
    conversion: Quantity
    sale_price: Money | None
    is_active: bool
    in_use: bool
    created_at: datetime
    updated_at: datetime


class PackagingCreate(BaseModel):
    name: Required50
    # Unités de base contenues dans UN conditionnement (> 0, décimale possible : 25.5 kg).
    conversion: PositiveQuantity
    # Défini seulement avec ``catalog.article.price_update`` ; absent : prix NON CONFIGURÉ
    # (conditionnement invendable tant qu'un habilité ne l'a pas fixé), jamais 0 implicite.
    sale_price: Money | None = None


class PackagingUpdate(BaseModel):
    """Champs absents : inchangés. Conversion figée dès qu'une vente utilise le
    conditionnement (désactiver, puis en créer un nouveau)."""

    name: Required50 | None = None
    conversion: PositiveQuantity | None = None
    sale_price: Money | None = None


class BarcodeOut(BaseModel):
    """Code-barres d'une présentation (Lot 3-D, ADR-0042) : ``PRIMARY`` (code principal de
    l'article, modifié sur l'article), ``ADDITIONAL`` (code supplémentaire de l'article, unité
    de base) ou ``PACKAGING`` (code d'un conditionnement). ``is_active`` : porté par un élément
    actif — un élément désactivé libère ses codes, qui restent affichés."""

    id: uuid.UUID
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None
    packaging_name: str | None
    code: str
    kind: str
    is_active: bool
    created_at: datetime


class BarcodeCreate(BaseModel):
    """Texte libre (EAN, code fournisseur, code interne…), 50 caractères au plus ; aucune
    validation EAN imposée."""

    code: Required50


class ScanOut(BaseModel):
    """Présentation identifiée par un scan exact (Lot 3-D) : l'article, et le conditionnement
    lorsque le code est celui d'un conditionnement (sinon unité de base)."""

    article: ArticleOut
    packaging: PackagingOut | None


# --- Assortiment par site (Recette, étape 1, ADR-0046) ------------------------------------------


class AssortmentStatusFilter(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"
    ALL = "all"


class SiteArticleOut(BaseModel):
    """Article de l'assortiment d'un site (catalogue ≠ assortiment ≠ stock : aucun stock ici)."""

    article_id: uuid.UUID
    reference: str
    designation: str
    category_id: uuid.UUID
    category_name: str
    unit: str
    article_active: bool
    stock_managed: bool
    state: AssortmentState
    added_at: datetime
    added_by_name: str | None
    removed_at: datetime | None
    removed_by_name: str | None


class AssortmentArticlesInput(BaseModel):
    article_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class AssortmentCopyInput(BaseModel):
    """Copie d'assortiment (D6) : ajout seulement, depuis un autre site ; catégorie facultative."""

    source_site_id: uuid.UUID
    category_id: uuid.UUID | None = None


class AssortmentChangeOut(BaseModel):
    """Résultat d'un ajout ou d'une copie : ajoutés, réactivés, déjà présents."""

    added: int
    reactivated: int
    unchanged: int


class AssortmentRemovalOut(BaseModel):
    removed: int
    unchanged: int


class ArticleSiteOut(BaseModel):
    """Situation d'un article dans l'assortiment d'un site visible du membre."""

    site_id: uuid.UUID
    site_name: str
    state: AssortmentState
    added_at: datetime | None
    removed_at: datetime | None
