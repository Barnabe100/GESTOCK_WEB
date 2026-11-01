"""Secrets publics des commandes par QR Code (R7, ADR-0052) : jeton d'un point, secret de suivi.

- générés par ``secrets`` (256 bits, URL-safe, 43 caractères) ;
- jamais stockés : seule l'empreinte SHA-256 l'est (une valeur aléatoire de 256 bits rend un
  hachage lent inutile) ; comparaison par égalité d'empreinte en base ;
- format contrôlé AVANT tout hachage (une valeur malformée est refusée sans requête) ;
- jamais journalisés ni renvoyés après leur émission.
"""

import hashlib
import re
import secrets

TOKEN_BYTES = 32
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def new_secret() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def well_formed(value: str | None) -> bool:
    return value is not None and _TOKEN_RE.fullmatch(value) is not None
