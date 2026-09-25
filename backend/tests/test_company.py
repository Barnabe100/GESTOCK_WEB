"""Phase 3.2-C — Configuration de l'entreprise et identité documentaire : le tenant est la
source unique de l'identité ; champs obligatoires / recommandés / facultatifs ; en-tête
documentaire sans « N/A » ; onboarding (``company``, ``configuration``) ; permissions,
isolation, audit. Les validations de base (e-mail, téléphone, URL, pays) sont aussi couvertes
par ``test_saas_data.py``."""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.platform.tenancy.identity import (
    OPTIONAL_COMPANY_FIELDS,
    RECOMMENDED_COMPANY_FIELDS,
    REQUIRED_COMPANY_FIELDS,
)
from tests.stock_helpers import member
from tests.test_signup import _api, _signup, offers  # noqa: F401

RECOMMENDED = {
    "trade_name": "Chez Awa",
    "logo_url": "https://cdn.chezawa.bf/logo.png",
    "phone": "+226 70 11 22 33",
    "email": "contact@chezawa.bf",
    "address": "Avenue Kwame Nkrumah",
    "city": "Ouagadougou",
    "region": "Centre",
    "tax_id": "00012345A",
    "trade_register": "BF-OUA-2026-B-1234",
}


def _statuses(api: Any) -> dict[str, str]:
    return {s["code"]: s["status"] for s in api.get("/onboarding").json()["steps"]}


def _row(owner_db: Session, tenant_id: Any, code: str) -> dict[str, Any]:
    return dict(
        owner_db.execute(
            text(
                "SELECT status, completed_at, completed_by, metadata FROM onboarding_steps "
                "WHERE tenant_id = :t AND step_code = :c"
            ),
            {"t": tenant_id, "c": code},
        )
        .one()
        ._mapping
    )


def test_field_classification_matches_the_validated_rules() -> None:
    assert REQUIRED_COMPANY_FIELDS == ("name", "country_code", "currency")
    assert set(RECOMMENDED_COMPANY_FIELDS) == set(RECOMMENDED)
    assert OPTIONAL_COMPANY_FIELDS == ("website", "description")


# --- Champs obligatoires et validations -----------------------------------------------------


@pytest.mark.parametrize("name", ["", "   "])
def test_company_name_is_required(provision: Any, api_for: Any, name: str) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    response = owner.patch("/tenant", json={"name": name})
    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    renamed = owner.patch("/tenant", json={"name": "  Alpha SARL  "}).json()
    assert renamed["name"] == "Alpha SARL"


def test_country_must_be_active_but_current_one_stays_valid(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    owner_db.execute(text("UPDATE geo_countries SET is_active = false WHERE code IN ('ML', 'BF')"))
    owner_db.commit()
    try:
        refused = owner.patch("/tenant", json={"country_code": "ML"})
        assert (refused.status_code, refused.json()["code"]) == (422, "unknown_country")
        # Pays actuel désactivé depuis : toujours accepté (aucune perte de données).
        assert owner.patch("/tenant", json={"country_code": "BF"}).status_code == 200
    finally:
        owner_db.execute(
            text("UPDATE geo_countries SET is_active = true WHERE code IN ('ML','BF')")
        )
        owner_db.commit()


def test_currency_is_fixed_and_proposed_by_the_country(
    provision: Any, api_for: Any, client: TestClient
) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    # Changer de pays ne change jamais la devise, figée à la création (décision G5).
    moved = owner.patch("/tenant", json={"country_code": "FR", "currency": "EUR"}).json()
    assert (moved["country_code"], moved["currency"]) == ("FR", "XOF")
    # Devise proposée par le référentiel (servi par l'API, jamais embarqué dans l'interface).
    countries = {c["code"]: c for c in client.get("/api/v1/public/geo/countries").json()}
    assert (countries["FR"]["currency"], countries["BF"]["currency"]) == ("EUR", "XOF")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("logo_url", "http://cdn.chezawa.bf/logo.png"),
        ("logo_url", "https://admin:secret@cdn.chezawa.bf/logo.png"),
        ("logo_url", "ftp://cdn.chezawa.bf/logo.png"),
        ("logo_url", "https://localhost/logo.png"),
        ("email", "contact@"),
        ("phone", "+226 70 11 22 33 44 55 66 77 88 99 00"),
        ("phone", "12"),
    ],
)
def test_invalid_contact_and_logo_are_refused(
    provision: Any, api_for: Any, owner_db: Session, field: str, value: str
) -> None:
    t = provision("alpha")
    response = api_for("owner@alpha.example.com").patch("/tenant", json={field: value})
    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    stored = owner_db.execute(
        text(f"SELECT {field} FROM tenants WHERE id = :t"), {"t": t.tenant_id}
    ).scalar_one()
    assert stored is None


def test_update_is_audited_with_before_and_after(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("alpha")
    owner = api_for("owner@alpha.example.com")
    owner.patch("/tenant", json={"trade_name": "Chez Awa", "tax_id": "IFU-1"})
    owner.patch("/tenant", json={"tax_id": None})
    rows = owner_db.execute(
        text(
            "SELECT data FROM audit_logs WHERE tenant_id = :t AND action = 'tenant.updated' "
            "ORDER BY occurred_at"
        ),
        {"t": t.tenant_id},
    ).scalars()
    first, second = list(rows)
    assert first["after"] == {"trade_name": "Chez Awa", "tax_id": "IFU-1"}
    assert first["before"] == {"trade_name": None, "tax_id": None}
    assert (second["before"], second["after"]) == ({"tax_id": "IFU-1"}, {"tax_id": None})


# --- Identité documentaire ------------------------------------------------------------------


def test_document_identity_uses_only_available_data(provision: Any, api_for: Any) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    bare = owner.get("/tenant/document-identity").json()
    # Rien d'inventé : seules les données présentes (ici le pays) forment l'en-tête.
    assert bare == {
        "name": "Entreprise alpha",
        "trade_name": None,
        "logo_url": None,
        "contact": [{"kind": "locality", "value": "Burkina Faso"}],
        "identifiers": [],
        "missing_recommended": list(RECOMMENDED_COMPANY_FIELDS),
    }
    assert "N/A" not in str(bare)

    owner.patch("/tenant", json={**RECOMMENDED, "website": "https://chezawa.bf"})
    full = owner.get("/tenant/document-identity").json()
    assert (full["trade_name"], full["logo_url"]) == ("Chez Awa", RECOMMENDED["logo_url"])
    assert full["contact"] == [
        {"kind": "phone", "value": "+22670112233"},
        {"kind": "email", "value": "contact@chezawa.bf"},
        {"kind": "address", "value": "Avenue Kwame Nkrumah"},
        {"kind": "locality", "value": "Ouagadougou, Centre, Burkina Faso"},
    ]
    assert full["identifiers"] == [
        {"kind": "tax_id", "value": "00012345A"},
        {"kind": "trade_register", "value": "BF-OUA-2026-B-1234"},
    ]
    assert full["missing_recommended"] == []
    # Facultatifs (site web, description) : hors en-tête.
    assert 'chezawa.bf"' not in str(full["contact"]) and "website" not in str(full)

    # Une ligne dont la donnée est effacée disparaît (jamais « N/A »).
    owner.patch("/tenant", json={"email": None, "region": "", "tax_id": None})
    partial = owner.get("/tenant/document-identity").json()
    assert [line["kind"] for line in partial["contact"]] == ["phone", "address", "locality"]
    assert partial["contact"][-1]["value"] == "Ouagadougou, Burkina Faso"
    assert partial["identifiers"] == [{"kind": "trade_register", "value": "BF-OUA-2026-B-1234"}]
    assert partial["missing_recommended"] == ["email", "region", "tax_id"]


def test_document_identity_of_a_historical_tenant_without_country(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("ancien")
    owner_db.execute(
        text("UPDATE tenants SET country_code = NULL WHERE id = :t"), {"t": t.tenant_id}
    )
    owner_db.commit()
    owner = api_for("owner@ancien.example.com")
    assert owner.get("/tenant/document-identity").json()["contact"] == []
    owner.patch("/tenant", json={"city": "Bobo-Dioulasso"})
    contact = owner.get("/tenant/document-identity").json()["contact"]
    assert contact == [{"kind": "locality", "value": "Bobo-Dioulasso"}]
    # Aucun pays attribué par hypothèse.
    assert owner.get("/tenant").json()["country_code"] is None


def test_identity_permissions_and_isolation(
    provision: Any, api_for: Any, client: TestClient, app_engine: Engine
) -> None:
    a = provision("alpha")
    provision("beta")
    alpha = api_for("owner@alpha.example.com")
    beta = api_for("owner@beta.example.com")
    alpha.patch("/tenant", json={"trade_name": "Alpha SARL", "tax_id": "IFU-A"})
    assert beta.get("/tenant/document-identity").json()["trade_name"] is None
    assert beta.get("/tenant").json()["tax_id"] is None

    ctx = SimpleNamespace(owner=alpha)
    viewer = member(ctx, client, "lecteur@alpha.example.com", "viewer")
    assert viewer.get("/tenant/document-identity").json()["trade_name"] == "Alpha SARL"
    assert viewer.patch("/tenant", json={"trade_name": "X"}).json()["code"] == "permission_denied"
    seller = member(ctx, client, "vendeur@alpha.example.com", "seller")
    assert seller.get("/tenant/document-identity").json()["code"] == "permission_denied"
    assert client.get("/api/v1/tenant/document-identity").status_code == 401

    # RLS : sans contexte, aucun tenant visible ; dans le contexte de A, seulement A.
    with create_session_factory(app_engine)() as session:
        assert session.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 0
        set_db_context(session, tenant_id=a.tenant_id, user_id=None)
        assert session.execute(text("SELECT id FROM tenants")).scalars().all() == [a.tenant_id]


# --- Onboarding -----------------------------------------------------------------------------


def test_company_and_configuration_steps_follow_the_company_page(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("ancien")
    owner_db.execute(
        text("UPDATE tenants SET country_code = NULL WHERE id = :t"), {"t": t.tenant_id}
    )
    owner_db.commit()
    owner = api_for("owner@ancien.example.com")
    statuses = _statuses(owner)
    assert (statuses["company"], statuses["configuration"]) == ("IN_PROGRESS", "NOT_STARTED")

    # Des informations recommandées sans pays : configuration avance, company non.
    owner.patch("/tenant", json={"trade_name": "Ancien", "city": "Koudougou"})
    statuses = _statuses(owner)
    assert (statuses["company"], statuses["configuration"]) == ("IN_PROGRESS", "IN_PROGRESS")

    owner.patch("/tenant", json={"country_code": "BF", **RECOMMENDED})
    company = _row(owner_db, t.tenant_id, "company")
    configuration = _row(owner_db, t.tenant_id, "configuration")
    for row in (company, configuration):
        assert row["status"] == "COMPLETED"
        assert row["completed_by"] == t.owner_user_id
        assert row["metadata"]["trigger"] == "tenant.updated"

    # Complétion définitive : effacer ensuite des informations ne rouvre rien.
    owner.patch("/tenant", json={"logo_url": None, "tax_id": None, "phone": ""})
    statuses = _statuses(owner)
    assert (statuses["company"], statuses["configuration"]) == ("COMPLETED", "COMPLETED")
    assert (
        _row(owner_db, t.tenant_id, "configuration")["completed_at"]
        == (configuration["completed_at"])
    )


def test_company_configuration_never_activates_the_subscription(
    client: TestClient,
    offers: None,  # noqa: F811
    owner_db: Session,
) -> None:
    owner = _api(client, _signup(client))
    assert owner.patch("/tenant", json=RECOMMENDED).status_code == 200
    statuses = _statuses(owner)
    assert (statuses["company"], statuses["configuration"]) == ("COMPLETED", "COMPLETED")
    status = owner_db.execute(text("SELECT status FROM subscriptions")).scalar_one()
    assert status == "pending_activation"
    refused = owner.post("/catalog/categories", json={"name": "Riz"})
    assert refused.json()["code"] == "subscription_restricted"
