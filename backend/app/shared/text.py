"""Normalisation des saisies texte : espaces retirés ; pour un champ optionnel, une chaîne
vide efface le champ (None)."""

import re
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, StringConstraints


def _blank_to_none(value: object) -> object:
    if isinstance(value, str):
        return value.strip() or None
    return value


BlankToNone = BeforeValidator(_blank_to_none)


def _req(n: int) -> StringConstraints:
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=n)


def _opt(n: int) -> StringConstraints:
    return StringConstraints(max_length=n)


Required20 = Annotated[str, _req(20)]
Required50 = Annotated[str, _req(50)]
Required100 = Annotated[str, _req(100)]
Required150 = Annotated[str, _req(150)]
Required255 = Annotated[str, _req(255)]

Optional30 = Annotated[Annotated[str, _opt(30)] | None, BlankToNone]
Optional50 = Annotated[Annotated[str, _opt(50)] | None, BlankToNone]
Optional100 = Annotated[Annotated[str, _opt(100)] | None, BlankToNone]
Optional150 = Annotated[Annotated[str, _opt(150)] | None, BlankToNone]
Optional200 = Annotated[Annotated[str, _opt(200)] | None, BlankToNone]
Optional255 = Annotated[Annotated[str, _opt(255)] | None, BlankToNone]
Optional500 = Annotated[Annotated[str, _opt(500)] | None, BlankToNone]
Optional1000 = Annotated[Annotated[str, _opt(1000)] | None, BlankToNone]


_PHONE_SEPARATORS = re.compile(r"[\s.\-()/]")
_PHONE = re.compile(r"^\+?\d{4,20}$")


def normalize_phone(value: str | None) -> str | None:
    """Téléphone normalisé : séparateurs (espaces, points, tirets, parenthèses) retirés,
    « + » initial conservé. ``70 11 22 33`` → ``70112233`` ; ``+226 70-11-22-33`` →
    ``+22670112233``."""
    if value is None:
        return None
    cleaned = _PHONE_SEPARATORS.sub("", value)
    if not _PHONE.match(cleaned):
        raise ValueError("numéro de téléphone invalide")
    return cleaned


def phone_digits(term: str) -> str | None:
    """Terme de recherche ressemblant à un numéro, normalisé comme les téléphones stockés."""
    cleaned = _PHONE_SEPARATORS.sub("", term.strip())
    return cleaned if cleaned and re.fullmatch(r"\+?\d+", cleaned) else None


OptionalPhone = Annotated[Optional30, AfterValidator(normalize_phone)]
