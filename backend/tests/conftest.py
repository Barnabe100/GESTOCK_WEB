"""Fixtures de test : PostgreSQL réel.

- La base de test est recréée (schéma vidé puis migrations Alembic) une fois par session.
- L'API s'exécute avec le rôle applicatif (sans BYPASSRLS) : la RLS est réellement appliquée.
- Le rôle propriétaire sert uniquement à préparer/nettoyer les données.

Variables : SM_TEST_DATABASE_URL (rôle applicatif), SM_TEST_MIGRATION_DATABASE_URL (propriétaire).
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import create_db_engine, create_session_factory
from app.main import create_app
from app.platform.catalog.loader import load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.provisioning.service import (
    ProvisionResult,
    ProvisionTenantCommand,
    TenantProvisioningService,
)
from app.platform.registry import get_registry
from app.platform.subscriptions.models import BillingPeriod
from app.shared.clock import utcnow

APP_URL = os.environ.get(
    "SM_TEST_DATABASE_URL",
    "postgresql+psycopg://stockmanager_app:stockmanager_app@localhost:5432/stockmanager_test",
)
OWNER_URL = os.environ.get(
    "SM_TEST_MIGRATION_DATABASE_URL",
    "postgresql+psycopg://stockmanager:stockmanager@localhost:5432/stockmanager_test",
)
PASSWORD = "Motdepasse-123"

DATA_TABLES = (
    "stock_transfer_lines",
    "stock_transfers",
    "sale_lines",
    "sales",
    "stock_movements",
    "stock_levels",
    "stock_entry_lines",
    "stock_entries",
    "stock_exit_lines",
    "stock_exits",
    "stock_exit_reasons",
    "document_sequences",
    "catalog_articles",
    "catalog_categories",
    "suppliers",
    "customers",
    "audit_logs",
    "membership_roles",
    "membership_sites",
    "role_permissions",
    "roles",
    "tenant_memberships",
    "tenant_modules",
    "subscriptions",
    "sites",
    "tenants",
    "auth_sessions",
    "users",
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url=APP_URL,
        migration_database_url=OWNER_URL,
        refresh_cookie_secure=False,
        db_pool_size=5,
    )


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(OWNER_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config("alembic.ini")
    config.attributes["database_url"] = OWNER_URL
    command.upgrade(config, "head")
    with Session(owner_engine) as session:
        sync_catalog(session, load_catalog(get_registry()))
        session.commit()


@pytest.fixture(scope="session")
def app_engine(settings: Settings, migrated: None) -> Iterator[Engine]:
    engine = create_db_engine(APP_URL, settings)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(request: pytest.FixtureRequest) -> None:
    if "migrated" not in request.fixturenames and not _needs_db(request):
        return
    owner_engine: Engine = request.getfixturevalue("owner_engine")
    request.getfixturevalue("migrated")
    with owner_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(DATA_TABLES)} CASCADE"))


def _needs_db(request: pytest.FixtureRequest) -> bool:
    return bool({"client", "app_engine", "db", "provision", "app"} & set(request.fixturenames))


@pytest.fixture(scope="session")
def app(settings: Settings, migrated: None) -> Any:
    return create_app(settings)


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(app_engine: Engine) -> Iterator[Session]:
    with create_session_factory(app_engine)() as session:
        yield session


@pytest.fixture
def owner_db(owner_engine: Engine, migrated: None) -> Iterator[Session]:
    """Session propriétaire (superutilisateur en test) : prépare des états hors API."""
    with Session(owner_engine) as session:
        yield session


@pytest.fixture
def provision(app_engine: Engine, owner_engine: Engine, settings: Settings) -> Any:
    catalog = load_catalog(get_registry())

    def _provision(
        slug: str,
        *,
        profile: str = "alimentation",
        plan: str = "ENTREPRISE",
        owner_email: str | None = None,
        password: str = PASSWORD,
        trial_days: int | None = None,
        activate_owner: bool = True,
    ) -> ProvisionResult:
        with create_session_factory(app_engine)() as session:
            result = TenantProvisioningService(
                session, settings, get_registry(), catalog.role_templates, utcnow()
            ).provision(
                ProvisionTenantCommand(
                    name=f"Entreprise {slug}",
                    slug=slug,
                    profile_code=profile,
                    plan_code=plan,
                    billing_period=BillingPeriod.MONTHLY,
                    owner_email=owner_email or f"owner@{slug}.example.com",
                    owner_full_name=f"Owner {slug}",
                    owner_password=password,
                    trial_days=trial_days,
                ),
                actor="test",
            )
            session.commit()
        if activate_owner:
            with owner_engine.begin() as conn:
                conn.execute(
                    text("UPDATE users SET must_change_password = false WHERE id = :id"),
                    {"id": result.owner_user_id},
                )
        return result

    return _provision


@dataclass
class Api:
    """Client HTTP authentifié (jeton lié à un tenant, site optionnel)."""

    client: TestClient
    token: str
    site_id: uuid.UUID | None = None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if self.site_id:
            headers["X-Site-Id"] = str(self.site_id)
        return headers | (extra or {})

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(
            f"/api/v1{path}", headers=self._headers(kw.pop("headers", None)), **kw
        )

    def post(self, path: str, **kw: Any) -> Any:
        return self.client.post(
            f"/api/v1{path}", headers=self._headers(kw.pop("headers", None)), **kw
        )

    def patch(self, path: str, **kw: Any) -> Any:
        return self.client.patch(
            f"/api/v1{path}", headers=self._headers(kw.pop("headers", None)), **kw
        )

    def put(self, path: str, **kw: Any) -> Any:
        return self.client.put(
            f"/api/v1{path}", headers=self._headers(kw.pop("headers", None)), **kw
        )

    def delete(self, path: str, **kw: Any) -> Any:
        return self.client.delete(
            f"/api/v1{path}", headers=self._headers(kw.pop("headers", None)), **kw
        )


def login(
    client: TestClient,
    email: str,
    password: str = PASSWORD,
    tenant_id: uuid.UUID | None = None,
) -> Any:
    body: dict[str, Any] = {"email": email, "password": password}
    if tenant_id:
        body["tenant_id"] = str(tenant_id)
    return client.post("/api/v1/auth/login", json=body)


@pytest.fixture
def api_for(client: TestClient) -> Any:
    def _api_for(email: str, tenant_id: uuid.UUID | None = None, password: str = PASSWORD) -> Api:
        response = login(client, email, password, tenant_id)
        assert response.status_code == 200, response.text
        return Api(client=client, token=response.json()["access_token"])

    return _api_for


@pytest.fixture
def world(provision: Any, api_for: Any) -> Any:
    """Tenant ENTREPRISE à deux sites, trois articles, un fournisseur (tests du stock)."""
    from tests.stock_helpers import make_world

    return make_world(provision, api_for)
