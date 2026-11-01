"""Lecture du catalogue des opérateurs et établissements financiers (R6.5, ADR-0051).

Référentiel global, en lecture seule pour l'application ; jamais requis pour enregistrer un
moyen de réception (opérateur personnalisé toujours possible). Les libellés sont rendus dans la
langue demandée, sinon dans la langue de référence du fichier.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.catalog.models import GeoCountry, PaymentProvider


@dataclass(frozen=True)
class IdentifierRule:
    type: str
    min_length: int | None
    max_length: int | None
    pattern: str | None


def localized(default: str | None, translations: dict[str, str], locale: str) -> str | None:
    """Texte dans ``locale`` (« fr-BF » → « fr »), sinon le texte de référence."""
    for key in (locale, locale.split("-")[0]):
        if translations.get(key):
            return translations[key]
    return default


def identifier_rules(provider: PaymentProvider) -> list[IdentifierRule]:
    rules: dict[str, Any] = provider.identifiers or {}
    return [
        IdentifierRule(
            type=kind,
            min_length=rule.get("min_length"),
            max_length=rule.get("max_length"),
            pattern=rule.get("pattern"),
        )
        for kind, rule in rules.items()
    ]


def calling_codes(db: Session, codes: set[str | None]) -> dict[str, int]:
    """Indicatifs téléphoniques (référentiel des pays) des codes donnés."""
    wanted = {c for c in codes if c}
    if not wanted:
        return {}
    rows = db.execute(
        select(GeoCountry.code, GeoCountry.calling_code).where(GeoCountry.code.in_(wanted))
    )
    return {code: cc for code, cc in rows if cc is not None}


def get_provider(db: Session, code: str) -> PaymentProvider | None:
    return db.get(PaymentProvider, code)


def search_providers(
    db: Session,
    *,
    country: str | None = None,
    currency: str | None = None,
    kind: str | None = None,
    include_inactive: bool = False,
) -> list[PaymentProvider]:
    """Opérateurs couvrant le pays (ou internationaux), la devise et le type demandés."""
    stmt = select(PaymentProvider).order_by(PaymentProvider.name, PaymentProvider.code)
    if not include_inactive:
        stmt = stmt.where(PaymentProvider.is_active.is_(True))
    if currency is not None:
        stmt = stmt.where(PaymentProvider.currencies.contains([currency]))
    if kind is not None:
        stmt = stmt.where(PaymentProvider.kinds.contains([kind]))
    providers = list(db.scalars(stmt))
    if country is not None:
        providers = [p for p in providers if not p.countries or country in p.countries]
    return providers
