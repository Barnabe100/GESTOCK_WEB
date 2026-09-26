# Image du Signing Service (ADR-0034) — déployée À PART de StockManager Web.
# Aucune clé dans l'image : la clé privée et le secret client sont montés à l'exécution
# (secrets Docker ou fichiers en lecture seule), par exemple :
#   docker run -e SIGNING_KEY_ID=technova-ed25519-2026-01 \
#     -e SIGNING_PRIVATE_KEY_FILE=/run/secrets/signing_private_key \
#     -e SIGNING_CLIENT_SECRET_FILE=/run/secrets/signing_client_secret \
#     -v /srv/signing/secrets:/run/secrets:ro stockmanager-signing
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir "uv>=0.8,<0.9" \
    && useradd --system --uid 10001 --home /nonexistent signing

WORKDIR /srv/signing-service
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY signing_service/ ./signing_service/
RUN uv sync --frozen --no-dev

USER signing
EXPOSE 8100
CMD ["uvicorn", "--factory", "signing_service.app:create_app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
