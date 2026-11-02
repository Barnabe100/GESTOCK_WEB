"""Rapports (palier R10, ADR-0055). Montants en chaînes (``Money``). Coûts et marges
(``cost``, ``margin``, ``margin_rate``) retirés des réponses sans ``catalog.article.cost_view``
(``REPORT_COST_FIELDS``)."""

import uuid
from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from app.shared.schemas import Money, Quantity


class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"  # semaine ISO (du lundi)
    MONTH = "month"


class Dimension(StrEnum):
    SITE = "site"
    SELLER = "seller"
    CHANNEL = "channel"
    CUSTOMER = "customer"
    ARTICLE = "article"
    CATEGORY = "category"


class SalesFigures(BaseModel):
    sales_count: int
    revenue: Money  # chiffre d'affaires : Σ total (remises déduites)
    subtotal: Money  # Σ sous-totaux (avant remise globale)
    discounts: Money  # Σ remises
    average_basket: Money  # CA / nombre de ventes
    paid: Money  # Σ paiements effectués de ces ventes
    remaining: Money  # Σ reste dû
    cost: Money  # coût des sorties de stock de ces ventes (CMUP à la sortie)
    margin: Money  # CA − coût
    margin_rate: Money | None  # marge / CA × 100 (nul si CA nul)


class SeriesPoint(BaseModel):
    period: date  # début de la période (jour, lundi, 1er du mois)
    sales_count: int
    revenue: Money
    discounts: Money
    cost: Money
    margin: Money


class SalesSummaryOut(BaseModel):
    date_from: date
    date_to: date
    granularity: Granularity
    site_ids: list[uuid.UUID]
    totals: SalesFigures
    series: list[SeriesPoint]


class BreakdownRow(BaseModel):
    key: str | None  # identifiant (site, vendeur, client, article, catégorie) ou code du canal
    label: str | None  # libellé (canal : traduit par l'interface)
    reference: str | None = None  # article : référence
    unit: str | None = None  # article : unité de base de ``quantity``
    sales_count: int
    quantity: Quantity | None = None  # article : quantité vendue en unité de base
    revenue: Money  # dimensions de vente : Σ total ; article / catégorie : Σ lignes
    discounts: Money | None = None  # dimensions de vente seulement
    cost: Money
    margin: Money
    margin_rate: Money | None
    share: Money | None  # part du CA du périmètre, en %


class PaymentMethodRow(BaseModel):
    method: str  # type (comportement) : CASH, MOBILE_MONEY…
    method_label: str  # libellé figé au paiement
    payment_count: int
    amount: Money
    share: Money | None
