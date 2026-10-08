"""Profils / modules par site, palier F — console TechNova et CLI (D8, lecture seule).

- Console : profil d'activité de CHAQUE site dans la fiche d'une entreprise ; dans la liste,
  profils distincts des sites actifs (``site_profiles``) à côté du profil d'ORIGINE
  (inscription). Aucune route ni aucun droit SQL ne permet à la console de changer un profil.
- CLI : ``create-tenant --business-profile`` fixe le profil d'origine et celui du site initial ;
  ``change-profile`` ne change que le profil d'origine d'une entreprise SANS site — dès qu'un
  site existe, même refus que l'API (``profile_is_per_site``, D2) : aucun contournement.
"""

import io
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.cli import main
from app.core.config import Settings
from tests.conftest import CONSOLE_HEADERS, CONSOLE_PREFIX, Api, add_site, console_login
from tests.test_signup import _api as _signup_api
from tests.test_signup import _signup, offers  # noqa: F401
from tests.test_site_profile_change import _apply

ENTREPOT = "distribution.entrepot"


@pytest.fixture
def admin(console: TestClient, platform_admin: Any) -> TestClient:
    platform_admin()
    assert console_login(console).status_code == 200
    return console


def _get(c: TestClient, path: str, **kw: Any) -> Any:
    response = c.get(f"{CONSOLE_PREFIX}{path}", **kw)
    assert response.status_code == 200, response.text
    return response.json()


def _item(admin: TestClient, tenant_id: Any) -> dict[str, Any]:
    items = _get(admin, "/tenants", params={"limit": 100})["items"]
    found: dict[str, Any] = next(i for i in items if i["id"] == str(tenant_id))
    return found


def _profiles(entry: dict[str, Any]) -> list[str]:
    return [p["code"] for p in entry["site_profiles"]]


@pytest.fixture
def two_profiles(provision: Any, api_for: Any) -> SimpleNamespace:
    """Quincaillerie (profil d'origine) + un dépôt au profil Entrepôt."""
    t = provision("multi", profile="retail.quincaillerie", plan="ENTREPRISE")
    owner = api_for("owner@multi.example.com")
    depot = add_site(owner, "Dépôt", "DEP", "warehouse", business_profile_code=ENTREPOT)
    assert depot.status_code == 201, depot.text
    return SimpleNamespace(
        tenant_id=t.tenant_id, site_id=t.site_id, owner=owner, depot=depot.json()["id"]
    )


# --- Liste : profil d'origine ≠ profils des sites ---------------------------------------------


def test_list_shows_origin_profile_and_distinct_site_profiles(
    admin: TestClient, provision: Any, two_profiles: Any
) -> None:
    single = provision("seul", profile="retail.alimentation")
    entry = _item(admin, single.tenant_id)
    assert entry["business_profile_code"] == "retail.alimentation"
    assert entry["site_profiles"] == [
        {"code": "retail.alimentation", "name": "Alimentation / Supérette"}
    ]

    multi = _item(admin, two_profiles.tenant_id)
    # Profil d'origine inchangé ; profils des sites distincts, triés par nom.
    assert multi["business_profile_code"] == "retail.quincaillerie"
    assert multi["site_profiles"] == [
        {"code": ENTREPOT, "name": "Entrepôt"},
        {"code": "retail.quincaillerie", "name": "Quincaillerie"},
    ]
    assert multi["sites"] == 2


def test_list_ignores_inactive_sites_and_dedupes_profiles(
    admin: TestClient, two_profiles: Any, owner_db: Session
) -> None:
    other = add_site(two_profiles.owner, "Annexe", "ANX")
    assert other.status_code == 201, other.text
    # Deux sites Quincaillerie : un seul profil listé.
    assert _profiles(_item(admin, two_profiles.tenant_id)) == [ENTREPOT, "retail.quincaillerie"]
    owner_db.execute(
        text("UPDATE sites SET is_active = false WHERE id = :s"), {"s": two_profiles.depot}
    )
    owner_db.commit()
    entry = _item(admin, two_profiles.tenant_id)
    assert _profiles(entry) == ["retail.quincaillerie"]
    assert entry["sites"] == 2


def test_tenant_without_site_shows_its_origin_profile_only(
    admin: TestClient,
    client: TestClient,
    offers: None,  # noqa: F811
) -> None:
    owner = _signup_api(client, _signup(client))
    tenant_id = owner.get("/tenant").json()["id"]
    entry = _item(admin, tenant_id)
    assert entry["site_profiles"] == [] and entry["sites"] == 0
    assert entry["business_profile_code"]
    assert _get(admin, f"/tenants/{tenant_id}")["sites_detail"] == []


# --- Fiche : profil de chaque site ---------------------------------------------------------------


def test_detail_lists_every_site_with_its_profile(
    admin: TestClient, two_profiles: Any, owner_db: Session
) -> None:
    owner_db.execute(
        text("UPDATE sites SET is_active = false WHERE id = :s"), {"s": two_profiles.depot}
    )
    owner_db.commit()
    detail = _get(admin, f"/tenants/{two_profiles.tenant_id}")
    assert detail["business_profile_code"] == "retail.quincaillerie"
    # Sites actifs d'abord, puis inactifs (toujours affichés avec leur profil).
    assert detail["sites_detail"] == [
        {
            "id": str(two_profiles.site_id),
            "name": "Site principal",
            "code": "PRINCIPAL",
            "is_active": True,
            "business_profile_code": "retail.quincaillerie",
            "business_profile_name": "Quincaillerie",
        },
        {
            "id": two_profiles.depot,
            "name": "Dépôt",
            "code": "DEP",
            "is_active": False,
            "business_profile_code": ENTREPOT,
            "business_profile_name": "Entrepôt",
        },
    ]
    # Métadonnées seulement : aucune adresse, aucun type, aucune donnée métier du site.
    assert set(detail["sites_detail"][0]) == {
        "id",
        "name",
        "code",
        "is_active",
        "business_profile_code",
        "business_profile_name",
    }


def test_console_reflects_a_profile_change_made_by_the_company(
    admin: TestClient, provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("change", profile="retail.quincaillerie")
    owner: Api = api_for("owner@change.example.com")
    assert _apply(owner, str(t.site_id), ENTREPOT).status_code == 200
    detail = _get(admin, f"/tenants/{t.tenant_id}")
    assert detail["sites_detail"][0]["business_profile_code"] == ENTREPOT
    assert detail["business_profile_code"] == "retail.quincaillerie"  # origine conservée
    assert _profiles(_item(admin, t.tenant_id)) == [ENTREPOT]


def test_tenants_are_isolated_in_site_profiles(
    admin: TestClient, provision: Any, two_profiles: Any
) -> None:
    other = provision("autre", profile="restaurant.maquis")
    detail = _get(admin, f"/tenants/{other.tenant_id}")
    assert [s["code"] for s in detail["sites_detail"]] == ["PRINCIPAL"]
    assert _profiles(_item(admin, other.tenant_id)) == ["restaurant.maquis"]


# --- Aucun changement de profil depuis la console ----------------------------------------------


def test_console_exposes_no_profile_change_route(admin: TestClient, two_profiles: Any) -> None:
    t, site = two_profiles.tenant_id, two_profiles.site_id
    body = {"code": ENTREPOT, "profile_code": ENTREPOT, "reason": "Forçage TechNova"}
    for path in (
        f"/tenants/{t}/business-profile",
        f"/tenants/{t}/profile",
        f"/tenants/{t}/sites/{site}/business-profile",
        f"/tenants/{t}/sites/{site}",
        f"/sites/{site}/business-profile",
    ):
        for method in ("put", "post", "patch"):
            response = getattr(admin, method)(
                f"{CONSOLE_PREFIX}{path}", json=body, headers=CONSOLE_HEADERS
            )
            assert response.status_code in (404, 405), (method, path, response.status_code)
    detail = _get(admin, f"/tenants/{t}")
    assert {s["code"]: s["business_profile_code"] for s in detail["sites_detail"]} == {
        "PRINCIPAL": "retail.quincaillerie",
        "DEP": ENTREPOT,
    }


def test_no_console_route_writes_a_profile(console_app: Any) -> None:
    writes = [
        (sorted(route.methods), route.path)  # type: ignore[attr-defined]
        for route in console_app.routes
        if getattr(route, "methods", None)
        and set(route.methods) & {"POST", "PUT", "PATCH", "DELETE"}  # type: ignore[attr-defined]
        and "profile" in route.path  # type: ignore[attr-defined]
    ]
    assert writes == []


def test_platform_role_reads_but_never_writes_profiles(
    two_profiles: Any, platform_engine: Engine
) -> None:
    with platform_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, business_profile_code FROM sites WHERE tenant_id = :t"),
            {"t": two_profiles.tenant_id},
        ).all()
        assert {r.business_profile_code for r in rows} == {"retail.quincaillerie", ENTREPOT}
        for sql in (
            f"UPDATE sites SET business_profile_code = '{ENTREPOT}'",
            "UPDATE tenants SET business_profile_code = 'retail.sport'",
            "UPDATE site_modules SET enabled = true",
            "SELECT address FROM sites",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(sql))
            conn.rollback()


# --- CLI (D8) ------------------------------------------------------------------------------------


def _tenant_profile(owner_db: Session, tenant_id: Any) -> str:
    code: str = owner_db.execute(
        text("SELECT business_profile_code FROM tenants WHERE id = :t"), {"t": tenant_id}
    ).scalar_one()
    return code


def test_cli_change_profile_is_refused_once_a_site_exists(
    two_profiles: Any, settings: Settings, capsys: Any, owner_db: Session
) -> None:
    t = two_profiles.tenant_id
    for code in ("retail.sport", "x.y"):
        assert main(["change-profile", "--tenant-id", str(t), "--profile", code], settings) == 1
        assert "défini par site" in capsys.readouterr().err
    assert _tenant_profile(owner_db, t) == "retail.quincaillerie"
    sites = owner_db.execute(
        text("SELECT business_profile_code FROM sites WHERE tenant_id = :t ORDER BY code"),
        {"t": t},
    ).scalars()
    assert list(sites) == [ENTREPOT, "retail.quincaillerie"]
    audits = owner_db.execute(
        text(
            "SELECT count(*) FROM audit_logs WHERE tenant_id = :t "
            "AND action = 'tenant.profile_changed'"
        ),
        {"t": t},
    ).scalar_one()
    assert audits == 0


def test_cli_change_profile_of_a_company_without_site(
    client: TestClient,
    offers: None,  # noqa: F811
    settings: Settings,
    capsys: Any,
    owner_db: Session,
) -> None:
    owner = _signup_api(client, _signup(client))
    tenant_id = uuid.UUID(owner.get("/tenant").json()["id"])
    previous = _tenant_profile(owner_db, tenant_id)
    target = "retail.sport" if previous != "retail.sport" else "retail.alimentation"
    argv = ["change-profile", "--tenant-id", str(tenant_id), "--profile", target]
    assert main(argv, settings) == 0
    assert f"{previous} → {target}" in capsys.readouterr().out
    assert _tenant_profile(owner_db, tenant_id) == target
    # Le premier site prend ensuite ce profil d'origine (POST /sites sans profil).
    site = owner.post("/sites", json={"name": "Boutique", "code": "BTQ"})
    assert site.status_code == 201, site.text
    assert site.json()["business_profile_code"] == target
    # Puis la CLI ne peut plus rien changer.
    assert main(argv[:-1] + ["retail.alimentation"], settings) == 1
    assert "défini par site" in capsys.readouterr().err


def test_cli_create_tenant_sets_the_initial_site_profile(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
    owner_db: Session,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("Provisoire-123\n"))
    code = main(
        [
            "create-tenant",
            "--name",
            "Dépôt CLI",
            "--slug",
            "depot-cli",
            "--business-profile",
            ENTREPOT,
            "--country",
            "BF",
            "--plan",
            "STANDARD",
            "--owner-email",
            "depot-cli@example.com",
            "--owner-name",
            "Cli",
            "--owner-password-stdin",
        ],
        settings,
    )
    assert code == 0, capsys.readouterr()
    row = owner_db.execute(
        text(
            "SELECT t.business_profile_code AS origin, s.business_profile_code AS site "
            "FROM tenants t JOIN sites s ON s.tenant_id = t.id WHERE t.slug = 'depot-cli'"
        )
    ).one()
    assert (row.origin, row.site) == (ENTREPOT, ENTREPOT)
