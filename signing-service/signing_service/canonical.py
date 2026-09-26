"""Forme canonique signée d'une licence (format ``stockmanager-license`` v1, ADR-0034).

Octets signés = JSON de ``{"format", "version", "key_id", "payload"}`` (tout le document sauf
``signature``) : clés triées, séparateurs ``,`` et ``:`` sans espace, UTF-8 (pas d'échappement
ASCII), **aucun flottant** (montants et quantités n'y figurent pas ; un flottant est refusé
plutôt que sérialisé de façon ambiguë).

La même fonction existe dans le backend (``app.platform.licensing.canonical``) : les deux
suites de tests vérifient le même vecteur de référence.
"""

import json
from typing import Any

LICENSE_FORMAT = "stockmanager-license"
LICENSE_FORMAT_VERSION = 1


class CanonicalError(ValueError):
    pass


def _check(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise CanonicalError(f"flottant interdit : {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError(f"clé non textuelle : {path}")
            _check(item, f"{path}.{key}")
        return
    raise CanonicalError(f"type non sérialisable : {path} ({type(value).__name__})")


def canonical_bytes(document: dict[str, Any]) -> bytes:
    """Octets canoniques de ``document`` (sans sa clé ``signature`` éventuelle)."""
    signed = {k: v for k, v in document.items() if k != "signature"}
    _check(signed, "$")
    return json.dumps(
        signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
