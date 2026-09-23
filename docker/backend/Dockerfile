# Image backend (développement) — FastAPI + uv
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir "uv>=0.8,<0.9"

WORKDIR /app

# Dépendances d'abord (cache Docker), puis le projet (fournit la commande `stockmanager`).
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-install-project

COPY backend/ ./
RUN uv sync --frozen

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
