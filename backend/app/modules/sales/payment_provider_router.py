"""Catalogue des opérateurs et établissements : ``/api/v1/payment-providers`` (R6.5, ADR-0051).

Lecture seule, filtrable par pays, devise et type de moyen ; mêmes droits que la lecture des
moyens de paiement. Le catalogue est global (aucune donnée d'entreprise) et jamais requis.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.modules.sales.models import PaymentMethod
from app.platform.catalog.payment_providers import identifier_rules, localized, search_providers
from app.platform.context import DbSession, RequestContext, require_any_permission

router = APIRouter(tags=["payment_providers"])

View = Annotated[
    RequestContext,
    Depends(
        require_any_permission(
            "sales.payment.view", "sales.payment.create", "sales.payment_method.manage"
        )
    ),
]
CountryParam = Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")]
CurrencyParam = Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")]


class IdentifierRuleOut(BaseModel):
    type: str
    min_length: int | None
    max_length: int | None
    pattern: str | None


class PaymentProviderOut(BaseModel):
    code: str
    name: str
    kinds: list[str]
    # Vide : international.
    countries: list[str]
    currencies: list[str]
    identifiers: list[IdentifierRuleOut]
    instructions: str | None
    is_active: bool


@router.get("", response_model=list[PaymentProviderOut])
def list_payment_providers(
    ctx: View,
    db: DbSession,
    country: CountryParam = None,
    currency: CurrencyParam = None,
    kind: PaymentMethod | None = None,
) -> list[PaymentProviderOut]:
    locale = ctx.tenant.locale
    return [
        PaymentProviderOut(
            code=p.code,
            name=localized(p.name, p.names, locale) or p.name,
            kinds=list(p.kinds),
            countries=list(p.countries),
            currencies=list(p.currencies),
            identifiers=[IdentifierRuleOut(**vars(r)) for r in identifier_rules(p)],
            instructions=localized(None, p.instructions, locale),
            is_active=p.is_active,
        )
        for p in search_providers(
            db, country=country, currency=currency, kind=kind.value if kind else None
        )
    ]
