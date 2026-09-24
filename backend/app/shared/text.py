"""Normalisation des saisies texte : espaces retirés ; pour un champ optionnel, une chaîne
vide efface le champ (None)."""

from typing import Annotated

from pydantic import BeforeValidator, StringConstraints


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
Optional255 = Annotated[Annotated[str, _opt(255)] | None, BlankToNone]
Optional500 = Annotated[Annotated[str, _opt(500)] | None, BlankToNone]
Optional1000 = Annotated[Annotated[str, _opt(1000)] | None, BlankToNone]
