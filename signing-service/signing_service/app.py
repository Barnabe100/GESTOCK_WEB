"""Service de signature des licences (ADR-0034) — processus et déploiement **distincts** de
StockManager Web.

Il ne lit aucune donnée des tenants, n'accède à aucune base, ne décide ni du plan, ni du prix,
ni du quota, ne confirme aucun paiement et ne modifie aucun abonnement : il **signe** les
payloads de licence bien formés que lui soumet la console TechNova (demande authentifiée par
HMAC). La clé privée Ed25519 n'existe que dans ce processus.

Routes : ``GET /health`` ; ``POST /v1/sign``.
Journal : identifiant de licence, ``key_id`` et empreinte SHA-256 du payload — jamais le
payload, la clé, le secret ni la signature de la demande.
"""

import base64
import hashlib
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from signing_service.auth import RequestAuthenticator
from signing_service.canonical import LICENSE_FORMAT, LICENSE_FORMAT_VERSION, canonical_bytes
from signing_service.config import Settings
from signing_service.keys import load_client_secret, load_private_key
from signing_service.payload import SignRequest

logger = logging.getLogger("signing_service")

SIGN_PATH = "/v1/sign"


def _configure_logging() -> None:
    """Journal propre au service (niveau INFO), indépendant de la configuration d'uvicorn."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    _configure_logging()
    # Chargement au démarrage : une configuration invalide empêche le service de démarrer.
    private_key = load_private_key(settings.private_key_file)
    authenticator = RequestAuthenticator(
        load_client_secret(settings.client_secret_file), settings.max_skew_seconds
    )
    key_id = settings.key_id

    app = FastAPI(
        title="StockManager Signing Service",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "key_id": key_id}

    @app.post(SIGN_PATH)
    async def sign(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > settings.max_body_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload_too_large")
        if not authenticator.verify(
            method="POST",
            path=SIGN_PATH,
            timestamp=request.headers.get("x-signing-timestamp"),
            nonce=request.headers.get("x-signing-nonce"),
            signature=request.headers.get("x-signing-signature"),
            body=body,
        ):
            logger.warning("demande de signature refusée : authentification invalide")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")
        try:
            sign_request = SignRequest.model_validate_json(body)
        except ValidationError as exc:
            logger.warning("demande de signature refusée : payload non autorisé")
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={"detail": "invalid_payload", "fields": fields},
            )
        payload: dict[str, Any] = sign_request.payload.model_dump()
        document: dict[str, Any] = {
            "format": LICENSE_FORMAT,
            "version": LICENSE_FORMAT_VERSION,
            "key_id": key_id,
            "payload": payload,
        }
        signed = canonical_bytes(document)
        document["signature"] = base64.b64encode(private_key.sign(signed)).decode("ascii")
        logger.info(
            "licence signée license_id=%s key_id=%s payload_sha256=%s",
            payload["license_id"],
            key_id,
            hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        )
        return JSONResponse(document, headers={"Cache-Control": "no-store"})

    return app
