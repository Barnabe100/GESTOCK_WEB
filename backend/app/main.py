from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import build_api_router
from app.core.config import Settings, get_settings
from app.core.db import create_db_engine, create_session_factory
from app.core.errors import register_error_handlers
from app.platform.context import verify_declared_requirements
from app.platform.registry import get_registry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    registry = get_registry()  # validé au démarrage (dépendances, cycles, permissions)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = create_db_engine(settings.database_url, settings)
    app.state.session_factory = create_session_factory(app.state.engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Site-Id", "X-QR-Token", "X-QR-Tracking"],
    )
    public_prefix = f"{settings.api_v1_prefix}/public/"

    @app.middleware("http")
    async def _public_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Réponses PUBLIQUES (inscription, QR Code), erreurs comprises : jamais en cache, sans
        référent, non indexées (R7, ADR-0052)."""
        response = await call_next(request)
        if request.url.path.startswith(public_prefix):
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    register_error_handlers(app)
    app.include_router(build_api_router(registry), prefix=settings.api_v1_prefix)
    verify_declared_requirements(registry)
    return app


app = create_app()
