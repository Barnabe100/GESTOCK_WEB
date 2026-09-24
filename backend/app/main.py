from fastapi import FastAPI
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
        allow_headers=["Authorization", "Content-Type", "X-Site-Id"],
    )
    register_error_handlers(app)
    app.include_router(build_api_router(registry), prefix=settings.api_v1_prefix)
    verify_declared_requirements(registry)
    return app


app = create_app()
