"""Scénario des tests de sauvegarde / restauration (R11-0) — PAS un module de tests pytest.

Utilisé par ``ops/backup/sm-backup-drill.sh`` contre une base JETABLE (cluster temporaire ou
projet Docker Compose de test), jamais contre une base existante :

    uv run python -m tests.backup_scenario seed          → données multi-entreprises (JSON)
    uv run python -m tests.backup_scenario write-loop --seconds 20 --tag avant
    uv run python -m tests.backup_scenario fingerprint   → empreinte des données métier
    uv run python -m tests.backup_scenario console-admins → deux administrateurs TechNova (CLI)
    uv run python -m tests.backup_scenario console --as a --method POST --path /… [--json …]
    uv run python -m tests.backup_scenario write-one --slug gamma --tag avant

Les données passent par l'API réelle (RLS, StockService, CMUP, ventes) : trois entreprises,
chacune avec un magasin et un dépôt, un utilisateur membre de deux entreprises, articles,
réceptions, sorties, transferts, ventes à crédit. ``write-loop`` écrit en continu dans deux
entreprises (réceptions validées, catégories) : écritures concurrentes d'une sauvegarde.
Variables : celles des tests (SM_TEST_DATABASE_URL, SM_TEST_MIGRATION_DATABASE_URL…)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.core.db import create_db_engine, create_session_factory
from app.main import create_app
from app.platform.catalog.loader import load_catalog
from app.platform.provisioning.service import (
    ProvisionTenantCommand,
    TenantProvisioningService,
)
from app.platform.registry import get_registry
from app.platform.subscriptions.models import BillingPeriod
from app.shared.clock import utcnow
from tests.conftest import (
    APP_URL,
    OWNER_URL,
    PASSWORD,
    PLATFORM_URL,
    PURGE_URL,
    Api,
    add_site,
    login,
)

SHARED_EMAIL = "partage@multi.example.com"


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url=APP_URL,
        migration_database_url=OWNER_URL,
        platform_database_url=PLATFORM_URL,
        purge_database_url=PURGE_URL,
        refresh_cookie_secure=False,
        platform_cookie_secure=False,
        db_pool_size=5,
        signup_rate_limit_attempts=10_000,
    )


def _check(response: Any, *codes: int) -> Any:
    if response.status_code not in codes:
        raise SystemExit(
            f"échec {response.request.method} {response.request.url} : "
            f"{response.status_code} {response.text[:400]}"
        )
    return response.json() if response.content else None


def _provision(settings: Settings, slug: str) -> uuid.UUID:
    catalog = load_catalog(get_registry())
    engine = create_db_engine(APP_URL, settings)
    with create_session_factory(engine)() as session:
        result = TenantProvisioningService(
            session, settings, get_registry(), catalog.role_templates, utcnow()
        ).provision(
            ProvisionTenantCommand(
                name=f"Entreprise {slug}",
                slug=slug,
                profile_code="retail.quincaillerie",
                plan_code="ENTREPRISE",
                billing_period=BillingPeriod.MONTHLY,
                owner_email=f"owner@{slug}.example.com",
                owner_full_name=f"Owner {slug}",
                country_code="BF",
                owner_password=PASSWORD,
                trial_days=None,
            ),
            actor="backup-scenario",
        )
        session.commit()
    engine.dispose()
    with create_engine(OWNER_URL).begin() as conn:
        conn.execute(
            text("UPDATE users SET must_change_password = false WHERE id = :id"),
            {"id": result.owner_user_id},
        )
    return result.tenant_id


def _owner(client: TestClient, slug: str) -> Api:
    token = _check(login(client, f"owner@{slug}.example.com"), 200)["access_token"]
    return Api(client=client, token=token)


def _world(api: Api, slug: str) -> dict[str, Any]:
    sites = [s["id"] for s in _check(api.get("/sites"), 200)]
    depot = _check(add_site(api, f"Dépôt {slug}", "DEPOT", "warehouse"), 201)["id"]
    shop = sites[0]
    category = _check(api.post("/catalog/categories", json={"name": f"Divers {slug}"}), 201)
    articles = []
    for i in range(3):
        body = {
            "reference": f"{slug.upper()}-{i}",
            "designation": f"Article {i} {slug}",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "100",
            "sale_price": "150",
            "site_ids": [shop, depot],
        }
        articles.append(_check(api.post("/catalog/articles", json=body), 201)["id"])
    supplier = _check(api.post("/suppliers", json={"name": f"Fournisseur {slug}"}), 201)["id"]
    customer = _check(
        api.post("/customers", json={"customer_type": "INDIVIDUAL", "name": f"Client {slug}"}),
        201,
    )["id"]
    return {
        "shop": shop,
        "depot": depot,
        "articles": articles,
        "supplier": supplier,
        "customer": customer,
    }


def _entry(api: Api, w: dict[str, Any], site: str, qty: str, cost: str) -> None:
    body = {
        "site_id": site,
        "supplier_id": w["supplier"],
        "lines": [{"article_id": a, "quantity": qty, "unit_cost": cost} for a in w["articles"]],
    }
    entry = _check(api.post("/stock/entries", json=body), 201)
    _check(api.post(f"/stock/entries/{entry['id']}/validate"), 200)


def seed() -> dict[str, Any]:
    settings = _settings()
    out: dict[str, Any] = {"tenants": {}}
    with TestClient(create_app(settings)) as client:
        for slug in ("alpha", "beta", "gamma"):
            tenant = _provision(settings, slug)
            api = _owner(client, slug)
            w = _world(api, slug)
            _entry(api, w, w["shop"], "40", "100")
            _entry(api, w, w["depot"], "25", "120")
            reasons = {
                r["code"] or r["label"]: r["id"]
                for r in _check(api.get("/stock/exit-reasons?limit=50"), 200)["items"]
            }
            exit_doc = _check(
                api.post(
                    "/stock/exits",
                    json={
                        "site_id": w["shop"],
                        "reason_id": reasons["PERTE"],
                        "lines": [{"article_id": w["articles"][0], "quantity": "2"}],
                    },
                ),
                201,
            )
            _check(api.post(f"/stock/exits/{exit_doc['id']}/validate"), 200)
            transfer = _check(
                api.post(
                    "/stock/transfers",
                    json={
                        "source_site_id": w["depot"],
                        "destination_site_id": w["shop"],
                        "lines": [{"article_id": w["articles"][1], "quantity": "5"}],
                    },
                ),
                201,
            )
            _check(api.post(f"/stock/transfers/{transfer['id']}/validate"), 200)
            sale = _check(
                api.post(
                    "/sales",
                    json={
                        "site_id": w["shop"],
                        "customer_id": w["customer"],
                        "lines": [{"article_id": w["articles"][2], "quantity": "3"}],
                    },
                ),
                201,
            )
            _check(api.post(f"/sales/{sale['id']}/validate"), 200)
            out["tenants"][slug] = {"id": str(tenant), **w}
        # Un même compte global membre de deux entreprises (ADR-0029).
        for slug in ("alpha", "beta"):
            api = _owner(client, slug)
            roles = {r["template_code"]: r["id"] for r in _check(api.get("/roles"), 200)}
            _check(
                api.post(
                    "/members",
                    json={
                        "email": SHARED_EMAIL,
                        "full_name": "Membre partagé",
                        "password": PASSWORD,
                        "roles": [{"role_id": roles["seller"]}],
                        "all_sites": True,
                    },
                ),
                201,
                200,
            )
        out["shared_user"] = SHARED_EMAIL
    return out


def write_loop(seconds: float, tag: str) -> dict[str, Any]:
    """Écritures continues dans alpha ET beta (API) ; renvoie le nombre d'opérations validées."""
    settings = _settings()
    done = {"alpha": 0, "beta": 0}
    deadline = time.monotonic() + seconds
    with TestClient(create_app(settings)) as client:
        apis = {slug: _owner(client, slug) for slug in done}
        worlds = {}
        for slug, api in apis.items():
            sites = [s["id"] for s in _check(api.get("/sites"), 200)]
            listing = _check(api.get("/catalog/articles?limit=10"), 200)["items"]
            articles = [a["id"] for a in listing]
            suppliers = _check(api.get("/suppliers?limit=1"), 200)["items"]
            worlds[slug] = {
                "shop": sites[0],
                "articles": articles[:3],
                "supplier": suppliers[0]["id"],
            }
        n = 0
        while time.monotonic() < deadline:
            for slug, api in apis.items():
                n += 1
                _check(api.post("/catalog/categories", json={"name": f"{tag}-{slug}-{n}"}), 201)
                _entry(api, worlds[slug], worlds[slug]["shop"], "1", "100")
                done[slug] += 1
    return {"tag": tag, "operations": done}


def fingerprint() -> dict[str, Any]:
    """Empreinte des données métier (toutes les tables portant tenant_id, ordre stable)."""
    engine = create_engine(OWNER_URL)
    result: dict[str, Any] = {}
    with engine.connect() as conn:
        tables = (
            conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
                )
            )
            .scalars()
            .all()
        )
        digest = hashlib.sha256()
        for table in tables:
            rows = conn.execute(
                text(
                    f"SELECT md5(coalesce(string_agg(t::text, '\n' ORDER BY t::text), '')), "
                    f'count(*) FROM "{table}" t'
                )
            ).one()
            result[table] = rows[1]
            digest.update(f"{table}:{rows[0]}:{rows[1]}\n".encode())
    engine.dispose()
    return {"sha256": digest.hexdigest(), "rows": result}


def console_status() -> dict[str, Any]:
    """Statut tel que la console le calcule (rôle SQL de la console, status/ en lecture)."""
    import os
    from datetime import UTC, datetime
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.console.backups import backup_status

    engine = create_engine(PLATFORM_URL)
    with Session(engine) as db:
        data = backup_status(db, Path(os.environ["SM_BACKUP_STATUS_DIR"]), datetime.now(UTC))
    engine.dispose()
    result: dict[str, Any] = json.loads(json.dumps(data, default=str))
    return result


CONSOLE_ADMINS = {"a": "exercice-a@technova.example", "b": "exercice-b@technova.example"}
CONSOLE_PASSWORD = "Exercice-Console-2026"


def console_admins() -> dict[str, Any]:
    """Deux administrateurs TechNova DISTINCTS, créés comme le fait la CLI (double approbation)."""
    from sqlalchemy.orm import Session

    from app.console.admins import create_platform_admin
    from app.console.audit import CLI_ACTOR

    settings = _settings()
    engine = create_engine(OWNER_URL)
    out = {}
    with Session(engine) as session:
        for key, email in CONSOLE_ADMINS.items():
            user = create_platform_admin(
                session,
                settings,
                email=email,
                full_name=f"Exercice {key.upper()}",
                password=CONSOLE_PASSWORD,
                actor=CLI_ACTOR,
            )
            out[key] = str(user.id)
        session.commit()
    engine.dispose()
    return out


def console_call(who: str, method: str, path: str, body: str | None, expect: int) -> Any:
    """Appel de l'API RÉELLE de la console (session d'un administrateur, rôle SQL de la console)."""
    from app.console.main import create_console_app

    settings = _settings()
    headers = {"X-TechNova-Console": "1"}
    prefix = settings.platform_api_prefix
    with TestClient(create_console_app(settings)) as client:
        _check(
            client.post(
                f"{prefix}/auth/login",
                json={"email": CONSOLE_ADMINS[who], "password": CONSOLE_PASSWORD},
                headers=headers,
            ),
            200,
        )
        response = client.request(
            method, f"{prefix}{path}", headers=headers, json=json.loads(body) if body else None
        )
        if response.status_code != expect:
            raise SystemExit(
                f"console {method} {path} : {response.status_code} (attendu {expect}) "
                f"{response.text[:600]}"
            )
        return response.json() if response.content else None


def write_one(slug: str, tag: str) -> dict[str, Any]:
    """Une écriture (catégorie) dans UNE entreprise par l'API ; code HTTP renvoyé tel quel."""
    with TestClient(create_app(_settings())) as client:
        api = _owner(client, slug)
        response = api.post("/catalog/categories", json={"name": f"{tag}-{slug}"})
        return {"status": response.status_code, "code": (response.json() or {}).get("code")}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="backup_scenario")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    loop = sub.add_parser("write-loop")
    loop.add_argument("--seconds", type=float, default=10)
    loop.add_argument("--tag", default="ecriture")
    sub.add_parser("fingerprint")
    sub.add_parser("console-status")
    sub.add_parser("console-admins")
    call = sub.add_parser("console")
    call.add_argument("--as", dest="who", choices=sorted(CONSOLE_ADMINS), required=True)
    call.add_argument("--method", default="GET")
    call.add_argument("--path", required=True)
    call.add_argument("--json", dest="body")
    call.add_argument("--expect", type=int, default=200)
    one = sub.add_parser("write-one")
    one.add_argument("--slug", required=True)
    one.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    if args.command == "seed":
        data = seed()
    elif args.command == "write-loop":
        data = write_loop(args.seconds, args.tag)
    elif args.command == "console-status":
        data = console_status()
    elif args.command == "console-admins":
        data = console_admins()
    elif args.command == "console":
        data = console_call(args.who, args.method, args.path, args.body, args.expect)
    elif args.command == "write-one":
        data = write_one(args.slug, args.tag)
    else:
        data = fingerprint()
    print(json.dumps(data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
