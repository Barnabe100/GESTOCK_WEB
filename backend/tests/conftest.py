"""Fixtures de test : PostgreSQL réel.

- La base de test est recréée (schéma vidé puis migrations Alembic) une fois par session.
- L'API s'exécute avec le rôle applicatif (sans BYPASSRLS) : la RLS est réellement appliquée.
- Le rôle propriétaire sert uniquement à préparer/nettoyer les données.

Variables : SM_TEST_DATABASE_URL (rôle applicatif), SM_TEST_MIGRATION_DATABASE_URL (propriétaire).
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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
from app.modules.catalog import lot_tracking
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
# Rôle SQL de la console TechNova (ADR-0031) : sans BYPASSRLS, droits minimaux.
PLATFORM_URL = os.environ.get(
    "SM_TEST_PLATFORM_DATABASE_URL",
    "postgresql+psycopg://stockmanager_platform:stockmanager_platform@localhost:5432/"
    "stockmanager_test",
)
PASSWORD = "Motdepasse-123"

DATA_TABLES = (
    "platform_audit_logs",
    "subscription_payments",
    "platform_sessions",
    "rate_limit_hits",
    "onboarding_steps",
    "cash_movements",
    "cash_sessions",
    "cash_registers",
    "inventory_lines",
    "inventories",
    "stock_transfer_lines",
    "stock_transfers",
    "payments",
    "payment_method_sites",
    "payment_methods",
    "cash_site_settings",
    "sale_lines",
    "catalog_packagings",
    "sales",
    "stock_movements",
    "stock_levels",
    "stock_entry_lines",
    "stock_entries",
    "stock_exit_lines",
    "stock_exits",
    "stock_exit_reasons",
    "document_sequences",
    "catalog_site_articles",
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
        platform_database_url=PLATFORM_URL,
        refresh_cookie_secure=False,
        platform_cookie_secure=False,
        db_pool_size=5,
        # Inscriptions de test nombreuses depuis la même adresse ; limite testée à part.
        signup_rate_limit_attempts=10_000,
        sales_contact_email="ventes@technova.example",
    )


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(OWNER_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine) -> Iterator[None]:
    with owner_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config("alembic.ini")
    config.attributes["database_url"] = OWNER_URL
    command.upgrade(config, "head")
    with Session(owner_engine) as session:
        sync_catalog(session, load_catalog(get_registry()))
        session.commit()
    yield
    # Base laissée vide de données métier en fin de session : l'étape « migrations
    # réversibles » de la CI (``alembic downgrade base``) s'exécute ensuite sur cette base, et
    # le retour arrière de la migration 0038 refuse — à juste titre — de perdre des choix
    # d'assortiment faits par des utilisateurs (ici, ceux du dernier test).
    with owner_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(DATA_TABLES)} CASCADE"))


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
    return bool(
        {"client", "app_engine", "db", "provision", "app", "console", "platform_engine"}
        & set(request.fixturenames)
    )


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
        profile: str = "retail.alimentation",
        plan: str = "ENTREPRISE",
        owner_email: str | None = None,
        password: str = PASSWORD,
        trial_days: int | None = None,
        activate_owner: bool = True,
        country: str = "BF",
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
                    country_code=country,
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


@pytest.fixture
def bare_world(provision: Any, api_for: Any) -> Any:
    """Même monde, articles au catalogue SEULEMENT : aucun site ne les propose (ADR-0046)."""
    from tests.stock_helpers import make_world

    return make_world(provision, api_for, assorted=False)


# --- Console TechNova (ADR-0031) : processus et rôle SQL distincts -----------------------------

PLATFORM_ADMIN_EMAIL = "admin@technova.example"
PLATFORM_ADMIN_PASSWORD = "Console-TechNova-2026"
CONSOLE_PREFIX = "/platform-api/v1"
CONSOLE_HEADERS = {"X-TechNova-Console": "1"}


@pytest.fixture(scope="session")
def platform_engine(migrated: None) -> Iterator[Engine]:
    engine = create_engine(PLATFORM_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def console_app(settings: Settings, migrated: None) -> Any:
    from app.console.main import create_console_app

    return create_console_app(settings)


@pytest.fixture
def console(console_app: Any) -> Iterator[TestClient]:
    with TestClient(console_app) as c:
        yield c


@pytest.fixture
def platform_admin(owner_engine: Engine, settings: Settings) -> Any:
    """Crée un administrateur TechNova comme le fait la CLI (rôle propriétaire)."""
    from app.console.admins import create_platform_admin
    from app.console.audit import CLI_ACTOR

    def _create(email: str = PLATFORM_ADMIN_EMAIL, name: str = "Admin TechNova") -> uuid.UUID:
        with Session(owner_engine) as session:
            user = create_platform_admin(
                session,
                settings,
                email=email,
                full_name=name,
                password=PLATFORM_ADMIN_PASSWORD,
                actor=CLI_ACTOR,
            )
            session.commit()
            return user.id

    return _create


def console_login(
    console: TestClient, email: str = PLATFORM_ADMIN_EMAIL, password: str = PLATFORM_ADMIN_PASSWORD
) -> Any:
    return console.post(
        f"{CONSOLE_PREFIX}/auth/login",
        json={"email": email, "password": password},
        headers=CONSOLE_HEADERS,
    )


# --- Sites : 1 site = 1 abonnement (Phase 3.3-B1, ADR-0033) -----------------------------------

_owner_engine_for_sites: Engine | None = None


def _sites_engine() -> Engine:
    global _owner_engine_for_sites
    if _owner_engine_for_sites is None:
        _owner_engine_for_sites = create_engine(OWNER_URL)
    return _owner_engine_for_sites


@contextmanager
def published_plan(code: str) -> Iterator[None]:
    """Plan souscriptible par le client (publié, prix mensuel ouvert) le temps du bloc, puis
    rétabli : les tests qui n'en dépendent pas gardent les paramètres commerciaux par défaut."""
    columns = "listed, contact_required, monthly_price_enabled, monthly_price, currency"
    with _sites_engine().begin() as conn:
        previous = conn.execute(
            text(f"SELECT {columns} FROM plans WHERE code = :c"), {"c": code}
        ).one()
        conn.execute(
            text(
                "UPDATE plans SET listed = true, contact_required = false, "
                "monthly_price_enabled = true, monthly_price = coalesce(monthly_price, 10000), "
                "currency = coalesce(currency, 'XOF') WHERE code = :c"
            ),
            {"c": code},
        )
    try:
        yield
    finally:
        with _sites_engine().begin() as conn:
            conn.execute(
                text(
                    "UPDATE plans SET listed = :listed, contact_required = :contact, "
                    "monthly_price_enabled = :enabled, monthly_price = :price, "
                    "currency = :currency WHERE code = :c"
                ),
                {
                    "listed": previous.listed,
                    "contact": previous.contact_required,
                    "enabled": previous.monthly_price_enabled,
                    "price": previous.monthly_price,
                    "currency": previous.currency,
                    "c": code,
                },
            )


def activate_site(site_id: str | uuid.UUID) -> None:
    """Abonnement du site actif pour un an : état que donnera la licence (paiement confirmé +
    licence, Phase 3.3-B) ; les tests hors abonnement n'ont pas à rejouer ce parcours."""
    with _sites_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE subscriptions SET status = 'active', current_period_start = now(), "
                "current_period_end = now() + interval '1 year' WHERE site_id = :s"
            ),
            {"s": str(site_id)},
        )


def add_site(
    api: "Api",
    name: str,
    code: str,
    kind: str = "store",
    *,
    plan: str = "ENTREPRISE",
    active: bool = True,
    **extra: Any,
) -> Any:
    """Nouveau site par l'API (avec son abonnement, plan choisi parmi les plans publiés) ;
    ``active`` : abonnement du site rendu opérationnel (comme après paiement et licence)."""
    body = {
        "name": name,
        "code": code,
        "kind": kind,
        "plan_code": plan,
        "billing_period": "monthly",
    }
    with published_plan(plan):
        response = api.post("/sites", json=body | extra)
    if active and response.status_code == 201:
        activate_site(response.json()["id"])
    return response


@pytest.fixture
def lot_tracking_open(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """RÉSERVÉ AUX TESTS : garantit le suivi par lot disponible le temps d'un test
    (``monkeypatch``). Depuis la levée de P1-b (clôture du Lot 3-H, ADR-0045), la constante vaut
    déjà ``True`` : la fixture reste explicite dans les tests des lots, sans effet."""
    monkeypatch.setattr(lot_tracking, "LOT_TRACKING_AVAILABLE", True)
    yield
