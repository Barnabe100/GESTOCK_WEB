"""Authentification des demandes de signature : HMAC-SHA256 avec un secret partagé avec la
console TechNova, horodatage borné et nonce à usage unique (anti-rejeu).

Message authentifié (octets UTF-8, lignes séparées par ``\\n``) ::

    POST
    /v1/sign
    <horodatage Unix, secondes>
    <nonce>
    <SHA-256 hexadécimal du corps>

En-têtes : ``X-Signing-Timestamp``, ``X-Signing-Nonce``, ``X-Signing-Signature`` (HMAC
hexadécimal). Toute anomalie → ``401`` sans détail exploitable.

Le cache de nonces est en mémoire : **une seule instance** du service (déploiement prévu) ;
plusieurs instances exigeraient un stockage partagé des nonces (évolution documentée).
"""

import hashlib
import hmac
import re
import threading
import time

NONCE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def signing_message(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{method}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()


def sign_request(
    secret: bytes, method: str, path: str, timestamp: str, nonce: str, body: bytes
) -> str:
    return hmac.new(
        secret, signing_message(method, path, timestamp, nonce, body), hashlib.sha256
    ).hexdigest()


class ReplayGuard:
    """Nonces vus pendant la fenêtre d'horodatage."""

    def __init__(self, window_seconds: int) -> None:
        self.window = window_seconds
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def accept(self, nonce: str, now: float) -> bool:
        with self._lock:
            for key in [k for k, expiry in self._seen.items() if expiry <= now]:
                del self._seen[key]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now + 2 * self.window
            return True


class RequestAuthenticator:
    def __init__(self, secret: bytes, max_skew_seconds: int) -> None:
        self._secret = secret
        self.max_skew = max_skew_seconds
        self.replay = ReplayGuard(max_skew_seconds)

    def verify(
        self,
        *,
        method: str,
        path: str,
        timestamp: str | None,
        nonce: str | None,
        signature: str | None,
        body: bytes,
        now: float | None = None,
    ) -> bool:
        if not timestamp or not nonce or not signature:
            return False
        if not timestamp.isdigit() or not NONCE.match(nonce):
            return False
        moment = time.time() if now is None else now
        if abs(moment - int(timestamp)) > self.max_skew:
            return False
        expected = sign_request(self._secret, method, path, timestamp, nonce, body)
        if not hmac.compare_digest(expected, signature.lower()):
            return False
        # Le nonce n'est consommé qu'après une signature valide (pas d'épuisement par un tiers).
        return self.replay.accept(nonce, moment)
