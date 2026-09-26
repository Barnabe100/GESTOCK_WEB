"""Client du Signing Service (ADR-0034), utilisé par la console TechNova seulement.

La console ne détient **aucune** clé privée : elle soumet le payload d'une licence au Signing
Service (demande authentifiée par HMAC-SHA256, horodatage, nonce à usage unique) puis
**vérifie** le document renvoyé avec le trousseau des clés publiques avant de l'enregistrer :
même payload que celui soumis, format v1, clé connue et active, signature Ed25519 valide.

Transport : bibliothèque standard (``urllib``) ; injectable pour les tests.
"""

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.errors import BadGatewayError, ServiceUnavailableError
from app.platform.licensing.keyring import Keyring, LicenseVerificationError, verify_document

SIGN_PATH = "/v1/sign"

# (URL, corps, en-têtes, délai) → (statut HTTP, corps de la réponse)
Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]


def request_signature(
    secret: bytes, method: str, path: str, timestamp: str, nonce: str, body: bytes
) -> str:
    """Même construction que ``signing_service.auth.sign_request``."""
    digest = hashlib.sha256(body).hexdigest()
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def urllib_transport(
    url: str, body: bytes, headers: dict[str, str], timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def _unavailable() -> ServiceUnavailableError:
    return ServiceUnavailableError(
        "Service de signature indisponible", code="signing_service_unavailable"
    )


@dataclass
class SigningClient:
    base_url: str
    secret: bytes
    keyring: Keyring
    timeout: float = 10.0
    transport: Transport = urllib_transport

    def sign(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Document ``.lic`` signé et **vérifié** pour ``payload``. Erreurs : ``503``
        (service injoignable), ``502`` (refus ou réponse invalide) ; rien n'est enregistré."""
        body = json.dumps({"payload": payload}, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        headers = {
            "Content-Type": "application/json",
            "X-Signing-Timestamp": timestamp,
            "X-Signing-Nonce": nonce,
            "X-Signing-Signature": request_signature(
                self.secret, "POST", SIGN_PATH, timestamp, nonce, body
            ),
        }
        try:
            status, content = self.transport(
                self.base_url.rstrip("/") + SIGN_PATH, body, headers, self.timeout
            )
        except (OSError, TimeoutError) as exc:
            raise _unavailable() from exc
        if status >= 500:
            raise _unavailable()
        if status != 200:
            raise BadGatewayError(
                "Le service de signature a refusé la demande",
                code="signing_failed",
                extra={"status": status},
            )
        try:
            document = json.loads(content)
        except ValueError as exc:
            raise BadGatewayError("Réponse de signature illisible", code="signing_failed") from exc
        try:
            signed_payload = verify_document(document, self.keyring, require_active_key=True)
        except LicenseVerificationError as exc:
            raise BadGatewayError(
                "Signature de licence invalide", code=exc.code, extra={"cause": exc.code}
            ) from exc
        if signed_payload != payload:
            raise BadGatewayError(
                "Le document signé ne correspond pas à la licence demandée",
                code="license_signature_invalid",
            )
        return dict(document)
