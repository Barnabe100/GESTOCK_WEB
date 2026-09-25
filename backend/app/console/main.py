"""Application de la console TechNova : processus distinct de l'API des tenants (ADR-0031).

    uvicorn app.console.main:app --port 8001

- Connexion à la base avec le rôle SQL de la console (``SM_PLATFORM_DATABASE_URL``), jamais
  avec le rôle applicatif des tenants ; aucun routeur de l'API des tenants n'est monté.
- Aucun CORS : l'interface de la console est servie sur la même origine (proxy).
- À exposer uniquement sur un réseau restreint (VPN, liste d'adresses) : cette restriction
  relève de l'infrastructure, pas de ce code (la MFA est une évolution future).
"""

from fastapi import FastAPI

from app import __version__
from app.console.router import router
from app.core.config import Settings, get_settings
from app.core.db import create_db_engine, create_session_factory
from app.core.errors import register_error_handlers
from app.platform.registry import get_registry


def create_console_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.environment == "production" and not settings.platform_cookie_secure:
        raise ValueError("SM_PLATFORM_COOKIE_SECURE doit rester actif en production")
    get_registry()  # catalogue technique validé au démarrage
    prefix = settings.platform_api_prefix
    app = FastAPI(
        title=f"{settings.app_name} — Console TechNova",
        version=__version__,
        debug=settings.debug,
        openapi_url=f"{prefix}/openapi.json" if settings.environment != "production" else None,
        docs_url=f"{prefix}/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = create_db_engine(settings.platform_database_url, settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    register_error_handlers(app)
    app.include_router(router, prefix=prefix)
    return app


app = create_console_app()
