"""Filtres de l'historique des ventes (Lot 2) : UN seul objet, lu de la même façon par la
liste et par l'export, pour garantir le même périmètre quel que soit le format."""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import Query

from app.modules.sales.models import SaleChannel, SalePaymentStatus, SaleStatus

Text = Annotated[str | None, Query(max_length=100)]


@dataclass(frozen=True)
class SaleFilters:
    search: str | None = None
    status: SaleStatus | None = None
    site_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    payment_status: SalePaymentStatus | None = None
    channel: SaleChannel | None = None
    # Vendeur / opérateur : utilisateur qui a enregistré la vente (``sales.created_by``).
    seller_id: uuid.UUID | None = None
    # « Mes ventes » : ventes enregistrées par l'utilisateur courant.
    mine: bool = False
    article_id: uuid.UUID | None = None
    # Référence article (référence ou code-barres) et référence de paiement (n° de
    # transaction, ex. Orange Money) : deux critères distincts.
    article_reference: str | None = None
    payment_reference: str | None = None

    def used(self) -> dict[str, Any]:
        """Filtres réellement renseignés (audit des exports) : aucune valeur inventée."""
        values: dict[str, Any] = {}
        for name, value in self.__dict__.items():
            if value is None or value is False or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(value, uuid.UUID | date):
                values[name] = str(value) if isinstance(value, uuid.UUID) else value.isoformat()
            elif hasattr(value, "value"):
                values[name] = value.value
            else:
                values[name] = value.strip() if isinstance(value, str) else value
        return values


def sale_filters(
    search: Text = None,
    status_filter: Annotated[SaleStatus | None, Query(alias="status")] = None,
    site_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    payment_status: SalePaymentStatus | None = None,
    channel: SaleChannel | None = None,
    seller_id: uuid.UUID | None = None,
    mine: bool = False,
    article_id: uuid.UUID | None = None,
    article_reference: Text = None,
    payment_reference: Text = None,
) -> SaleFilters:
    return SaleFilters(
        search=search,
        status=status_filter,
        site_id=site_id,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        payment_status=payment_status,
        channel=channel,
        seller_id=seller_id,
        mine=mine,
        article_id=article_id,
        article_reference=article_reference,
        payment_reference=payment_reference,
    )
