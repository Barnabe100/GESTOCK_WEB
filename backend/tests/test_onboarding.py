"""Phase 3.2-B — Onboarding persistant : étapes créées et évaluées automatiquement, progression
et statut global, étapes obligatoires / recommandées, statuts qui ne font qu'avancer
(``COMPLETED`` définitif, aussi en base), permissions, isolation (API et RLS), tenants
existants évalués au premier accès, onboarding ≠ activation de l'abonnement."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.platform.onboarding.definitions import (
    OnboardingAction,
    OnboardingEnv,
    OnboardingStatus,
    OnboardingStepDef,
)
from app.platform.onboarding.schemas import OnboardingStepOut
from app.platform.onboarding.service import build_overview
from app.platform.onboarding.steps import users_applicable
from app.platform.registry import ModuleManifest, ModuleRegistry, PermissionDef, RegistryError
from tests.conftest import PASSWORD as CLI_PASSWORD
from tests.conftest import Api, login
from tests.test_signup import PASSWORD, _api, _signup, offers  # noqa: F401

STEPS = [
    "account",
    "company",
    "business_profile",
    "subscription",
    "first_site",
    "catalogue",
    "users",
    "configuration",
]
REQUIRED = {"account", "company", "business_profile", "subscription", "first_site"}


def _rows(owner_db: Session, tenant_id: Any = None) -> dict[str, dict[str, Any]]:
    query = (
        "SELECT step_code, status, completed_at, completed_by, metadata, id FROM onboarding_steps"
    )
    params: dict[str, Any] = {}
    if tenant_id is not None:
        query += " WHERE tenant_id = :t"
        params["t"] = tenant_id
    return {r.step_code: dict(r._mapping) for r in owner_db.execute(text(query), params).fetchall()}


def _statuses(overview: dict[str, Any]) -> dict[str, str]:
    return {s["code"]: s["status"] for s in overview["steps"]}


def _tenant_of(owner_db: Session, email: str) -> uuid.UUID:
    return owner_db.execute(
        text(
            "SELECT m.tenant_id FROM tenant_memberships m JOIN users u ON u.id = m.user_id "
            "WHERE u.email = :e AND m.is_owner"
        ),
        {"e": email},
    ).scalar_one()


@pytest.fixture
def signed(client: TestClient, offers: None) -> Api:  # noqa: F811
    """Inscription publique : STANDARD sans essai → ``pending_activation``, sans site."""
    return _api(client, _signup(client))


def _member(owner: Api, client: TestClient, email: str, template: str) -> Api:
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    created = owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": [{"role_id": roles[template]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": CLI_PASSWORD}
    )
    return Api(client, login(client, email, CLI_PASSWORD).json()["access_token"])


# --- Création et validation automatique à l'inscription ----------------------------------------


def test_signup_creates_and_evaluates_steps(signed: Api, owner_db: Session) -> None:
    tenant_id = _tenant_of(owner_db, "awa@superette.example")
    owner_id = owner_db.execute(
        text("SELECT id FROM users WHERE email = 'awa@superette.example'")
    ).scalar_one()
    rows = _rows(owner_db)
    assert set(rows) == set(STEPS)
    # Compte, entreprise (nom, pays, devise), profil choisi à l'inscription (aucune seconde
    # sélection) et abonnement pending_activation : terminés, attribués au propriétaire.
    for code in ("account", "company", "business_profile", "subscription"):
        assert rows[code]["status"] == "COMPLETED", code
        assert rows[code]["completed_by"] == owner_id
        assert rows[code]["metadata"]["trigger"] == "signup"
    assert rows["first_site"]["status"] == "NOT_STARTED"
    # Recommandées : absentes ou partielles (nom commercial, téléphone, ville seulement).
    assert rows["configuration"]["status"] == "IN_PROGRESS"
    assert rows["catalogue"]["status"] == "NOT_STARTED"
    assert rows["users"]["status"] == "NOT_STARTED"
    completed = owner_db.execute(
        text(
            "SELECT entity_id FROM audit_logs WHERE action = 'onboarding.step.completed' "
            "AND tenant_id = :t ORDER BY entity_id"
        ),
        {"t": tenant_id},
    ).scalars()
    assert list(completed) == sorted(["account", "business_profile", "company", "subscription"])

    overview = signed.get("/onboarding").json()
    assert [s["code"] for s in overview["steps"]] == STEPS
    assert {s["code"] for s in overview["steps"] if s["required"]} == REQUIRED
    assert overview["status"] == "IN_PROGRESS" and overview["completed"] is False
    assert overview["progress"] == {
        "completed": 4,
        "total": 8,
        "percentage": 50,
        "required_completed": 4,
        "required_total": 5,
    }
    assert overview["current_step"] == "first_site"
    assert overview["next_action"] == {
        "route": "/organization/sites?create=1",
        "label": "onboarding.actions.first_site",
        "permission": "organization.site.manage",
        "available": True,
        "blocked_reason": None,
    }
    assert overview["subscription_status"] == "pending_activation"
    step = next(s for s in overview["steps"] if s["code"] == "company")
    assert step["title"] == "onboarding.steps.company.title"
    assert step["description"] == "onboarding.steps.company.description"
    assert next(s for s in overview["steps"] if s["code"] == "account")["action"] is None
    # La consultation ne recrée ni n'écrase rien.
    assert _rows(owner_db) == rows


def test_first_site_finishes_onboarding_but_never_activates(signed: Api, owner_db: Session) -> None:
    created = signed.post("/sites", json={"name": "Boutique", "code": "BTQ"})
    assert created.status_code == 201, created.text
    owner_id = owner_db.execute(
        text("SELECT id FROM users WHERE email = 'awa@superette.example'")
    ).scalar_one()
    row = _rows(owner_db)["first_site"]
    assert row["status"] == "COMPLETED" and row["completed_by"] == owner_id
    assert row["metadata"]["trigger"] == "site.created"

    overview = signed.get("/onboarding").json()
    # Toutes les obligatoires terminées : onboarding terminé, bien que les recommandées
    # restent à faire (elles ne comptent pas dans la progression).
    assert overview["status"] == "COMPLETED" and overview["completed"] is True
    assert overview["progress"]["completed"] == 5 and overview["progress"]["percentage"] == 62
    assert _statuses(overview)["configuration"] == "IN_PROGRESS"
    assert _statuses(overview)["users"] == "NOT_STARTED"
    # Prochaine recommandation : le catalogue, bloqué tant que l'abonnement n'est pas activé.
    assert overview["current_step"] == "catalogue"
    assert overview["next_action"]["available"] is False
    assert overview["next_action"]["blocked_reason"] == "subscription_restricted"

    # Onboarding 100 % des obligatoires ≠ activation : l'abonnement reste en attente et les
    # opérations métier restent refusées par le backend.
    assert overview["subscription_status"] == "pending_activation"
    assert (
        owner_db.execute(text("SELECT status FROM subscriptions")).scalar_one()
        == "pending_activation"
    )
    refused = signed.post("/catalog/categories", json={"name": "Boissons"})
    assert refused.status_code == 403 and refused.json()["code"] == "subscription_restricted"


def test_member_creation_completes_users_step(
    signed: Api, client: TestClient, owner_db: Session
) -> None:
    signed.post("/sites", json={"name": "Boutique", "code": "BTQ"})
    _member(signed, client, "caissier@superette.example", "seller")
    row = _rows(owner_db)["users"]
    assert row["status"] == "COMPLETED" and row["metadata"]["trigger"] == "member.created"
    assert row["completed_by"] is not None


def test_reconnection_resumes_progress(signed: Api, client: TestClient, owner_db: Session) -> None:
    signed.post("/sites", json={"name": "Boutique", "code": "BTQ"})
    before = signed.get("/onboarding").json()
    signed.post("/auth/logout")
    response = login(client, "awa@superette.example", PASSWORD)
    again = Api(client, response.json()["access_token"]).get("/onboarding").json()
    assert again == before
    assert len(_rows(owner_db)) == len(STEPS)


# --- Transitions : jamais de COMPLETED déclaratif, jamais de régression -------------------------


@pytest.mark.parametrize(
    ("body", "status_code", "code"),
    [
        ({"status": "COMPLETED"}, 422, "onboarding_transition_not_allowed"),
        ({"status": "NOT_STARTED"}, 422, "onboarding_transition_not_allowed"),
        ({"status": "SKIPPED"}, 422, "validation_error"),
        ({"status": "IN_PROGRESS", "completed_at": "2026-01-01"}, 422, "validation_error"),
    ],
)
def test_client_cannot_declare_a_step(
    signed: Api, owner_db: Session, body: dict[str, Any], status_code: int, code: str
) -> None:
    before = _rows(owner_db)
    response = signed.patch("/onboarding/steps/first_site", json=body)
    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert _rows(owner_db) == before


def test_manual_start_is_the_only_transition(signed: Api, owner_db: Session) -> None:
    assert signed.patch("/onboarding/steps/inconnue", json={"status": "IN_PROGRESS"}).json()[
        "code"
    ] == ("onboarding_step_not_found")
    started = signed.patch("/onboarding/steps/users", json={"status": "IN_PROGRESS"})
    assert started.status_code == 200
    assert _statuses(started.json())["users"] == "IN_PROGRESS"
    # Idempotent : pas de second audit ; une étape terminée ne régresse pas.
    signed.patch("/onboarding/steps/users", json={"status": "IN_PROGRESS"})
    kept = signed.patch("/onboarding/steps/account", json={"status": "IN_PROGRESS"})
    assert _statuses(kept.json())["account"] == "COMPLETED"
    audits = owner_db.execute(
        text("SELECT entity_id FROM audit_logs WHERE action = 'onboarding.step.started'")
    ).scalars()
    assert list(audits) == ["users"]
    row = _rows(owner_db)["users"]
    assert row["status"] == "IN_PROGRESS" and row["completed_at"] is None
    assert row["metadata"]["started_by"]


def test_completed_is_final_even_if_condition_disappears(signed: Api, owner_db: Session) -> None:
    site = signed.post("/sites", json={"name": "Boutique", "code": "BTQ"}).json()
    completed_at = _rows(owner_db)["first_site"]["completed_at"]
    assert signed.patch(f"/sites/{site['id']}", json={"is_active": False}).status_code == 200
    overview = signed.get("/onboarding").json()
    assert _statuses(overview)["first_site"] == "COMPLETED" and overview["completed"] is True
    assert _rows(owner_db)["first_site"]["completed_at"] == completed_at


@pytest.mark.parametrize(
    "update",
    [
        "status = 'NOT_STARTED', completed_at = NULL WHERE step_code = 'account'",
        "status = 'IN_PROGRESS', completed_at = NULL WHERE step_code = 'account'",
        "completed_at = now() - interval '1 day' WHERE step_code = 'account'",
        "status = 'NOT_STARTED' WHERE step_code = 'configuration'",
    ],
)
def test_database_forbids_regression(signed: Api, owner_db: Session, update: str) -> None:
    with pytest.raises(DBAPIError, match="cannot regress"):
        owner_db.execute(text(f"UPDATE onboarding_steps SET {update}"))
    owner_db.rollback()


# --- Tenants existants : évaluation au premier accès -------------------------------------------


def test_existing_tenant_is_evaluated_on_first_access(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("historique")  # CLI : site initial, abonnement actif
    owner_db.execute(
        text("UPDATE tenants SET country_code = NULL WHERE id = :t"), {"t": t.tenant_id}
    )
    owner_db.commit()
    assert _rows(owner_db) == {}  # aucune donnée d'onboarding écrite d'avance
    owner = api_for("owner@historique.example.com")
    category = owner.post("/catalog/categories", json={"name": "Divers"}).json()
    article = owner.post(
        "/catalog/articles",
        json={
            "reference": "A-1",
            "designation": "Article",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "100",
            "sale_price": "150",
        },
    )
    assert article.status_code == 201, article.text

    overview = owner.get("/onboarding").json()
    statuses = _statuses(overview)
    for code in ("account", "business_profile", "subscription", "first_site", "catalogue"):
        assert statuses[code] == "COMPLETED", code
    # Pays NULL (décision G4, jamais complété artificiellement) : entreprise incomplète.
    assert statuses["company"] == "IN_PROGRESS" and overview["completed"] is False
    assert overview["current_step"] == "company"
    rows = _rows(owner_db)
    # Constat par évaluation : complétion non attribuée.
    assert rows["first_site"]["completed_by"] is None
    assert rows["first_site"]["metadata"]["trigger"] == "evaluation"

    # Idempotent : un second accès ne recrée ni n'écrase rien, ni n'audite à nouveau.
    audits = "SELECT count(*) FROM audit_logs WHERE action = 'onboarding.step.completed'"
    count = owner_db.execute(text(audits)).scalar_one()
    assert owner.get("/onboarding").json() == overview
    assert _rows(owner_db) == rows
    assert owner_db.execute(text(audits)).scalar_one() == count

    # Le pays renseigné termine l'étape, attribuée à l'auteur de la modification.
    assert owner.patch("/tenant", json={"country_code": "BF"}).status_code == 200
    company = _rows(owner_db)["company"]
    assert company["status"] == "COMPLETED" and company["completed_by"] is not None
    assert company["metadata"]["trigger"] == "tenant.updated"
    assert owner.get("/onboarding").json()["completed"] is True


def test_recommended_company_information(provision: Any, api_for: Any, owner_db: Session) -> None:
    provision("config")
    owner = api_for("owner@config.example.com")
    assert _statuses(owner.get("/onboarding").json())["configuration"] == "NOT_STARTED"
    owner.patch("/tenant", json={"trade_name": "Config", "phone": "+226 70 00 00 00"})
    assert _statuses(owner.get("/onboarding").json())["configuration"] == "IN_PROGRESS"
    owner.patch(
        "/tenant",
        json={
            "logo_url": "https://exemple.com/logo.png",
            "email": "contact@config.example.com",
            "address": "Avenue 1",
            "city": "Ouagadougou",
            "region": "Centre",
            "tax_id": "IFU-1",
            "trade_register": "RCCM-1",
        },
    )
    assert _statuses(owner.get("/onboarding").json())["configuration"] == "COMPLETED"
    # Une information recommandée effacée ensuite ne fait pas régresser l'étape.
    owner.patch("/tenant", json={"region": None})
    assert _statuses(owner.get("/onboarding").json())["configuration"] == "COMPLETED"


# --- Permissions et isolation ---------------------------------------------------------------


def test_permissions(provision: Any, api_for: Any, client: TestClient) -> None:
    provision("droits")
    owner = api_for("owner@droits.example.com")
    seller = _member(owner, client, "vendeur@droits.example.com", "seller")
    viewer = _member(owner, client, "lecteur@droits.example.com", "viewer")
    assert seller.get("/onboarding").json()["code"] == "permission_denied"
    view = viewer.get("/onboarding")
    assert view.status_code == 200
    # Disponibilité des actions selon les permissions du rôle (le backend refuse de toute
    # façon sur l'écran ciblé) : le Consultant ne peut que consulter l'abonnement.
    actions = {s["code"]: s["action"] for s in view.json()["steps"] if s["action"]}
    assert actions["subscription"]["available"] is True
    blocked = [a for code, a in actions.items() if code != "subscription"]
    assert blocked and all(
        not a["available"] and a["blocked_reason"] == "permission_denied" for a in blocked
    )
    denied = viewer.patch("/onboarding/steps/users", json={"status": "IN_PROGRESS"})
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    assert client.get("/api/v1/onboarding").status_code == 401


def test_isolation_api_and_rls(
    provision: Any, api_for: Any, app_engine: Engine, owner_db: Session
) -> None:
    a = provision("alpha")
    b = provision("beta")
    api_for("owner@alpha.example.com").post("/sites", json={"name": "Dépôt", "code": "DEP"})
    beta = api_for("owner@beta.example.com")
    beta.patch("/onboarding/steps/users", json={"status": "IN_PROGRESS"})
    assert _statuses(beta.get("/onboarding").json())["users"] == "IN_PROGRESS"
    assert _rows(owner_db, a.tenant_id)["users"]["status"] == "NOT_STARTED"

    a_row = _rows(owner_db, a.tenant_id)["users"]["id"]
    with create_session_factory(app_engine)() as db:
        assert db.execute(text("SELECT count(*) FROM onboarding_steps")).scalar_one() == 0
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        tenants = db.execute(text("SELECT DISTINCT tenant_id FROM onboarding_steps")).scalars()
        assert list(tenants) == [b.tenant_id]
        updated = db.execute(
            text("UPDATE onboarding_steps SET status = 'IN_PROGRESS' WHERE id = :id"),
            {"id": a_row},
        )
        assert updated.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO onboarding_steps (id, tenant_id, step_code, status, metadata) "
                    "VALUES (:id, :t, 'intrus', 'NOT_STARTED', '{}')"
                ),
                {"id": uuid.uuid4(), "t": a.tenant_id},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM onboarding_steps"))


# --- Règles pures : progression, applicabilité, registre ---------------------------------------


def _item(code: str, required: bool, status: OnboardingStatus) -> OnboardingStepOut:
    return OnboardingStepOut(
        code=code,
        order=0,
        required=required,
        title="t",
        description="d",
        status=status,
        completed_at=None,
        action=None,
    )


def test_progress_rules() -> None:
    done, todo, doing = (
        OnboardingStatus.COMPLETED,
        OnboardingStatus.NOT_STARTED,
        OnboardingStatus.IN_PROGRESS,
    )
    none = build_overview([_item("a", True, todo), _item("b", False, todo)], "active")
    assert none.status is todo and none.progress.percentage == 0
    assert none.current_step == "a"
    # Recommandée faite avant l'obligatoire : l'étape actuelle reste l'obligatoire.
    mixed = build_overview([_item("a", True, doing), _item("b", False, done)], "active")
    assert mixed.status is doing and not mixed.completed and mixed.current_step == "a"
    assert mixed.progress.percentage == 50
    finished = build_overview(
        [_item("a", True, done), _item("b", False, todo), _item("c", False, todo)], "active"
    )
    assert finished.status is done and finished.completed and finished.current_step == "b"
    assert finished.progress.percentage == 33  # arrondi inférieur, 100 % seulement si tout fait
    everything = build_overview([_item("a", True, done), _item("b", False, done)], "active")
    assert everything.progress.percentage == 100 and everything.current_step is None
    assert everything.next_action is None


def test_users_step_applies_only_with_several_users() -> None:
    def env(limit: int | None) -> OnboardingEnv:
        limits: Callable[[str], int | None] = lambda code: limit  # noqa: E731
        return OnboardingEnv(db=None, tenant=None, modules=frozenset(), limit=limits)  # type: ignore[arg-type]

    assert users_applicable(env(None)) and users_applicable(env(5))
    assert not users_applicable(env(1))


def _step(code: str, permission: str = "m.x.view") -> OnboardingStepDef:
    return OnboardingStepDef(
        code=code,
        order=1,
        required=False,
        title="t",
        description="d",
        evaluate=lambda env: OnboardingStatus.NOT_STARTED,
        action=OnboardingAction(route="/x", label="l", permission=permission),
    )


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ((_step("dup"), _step("dup")), "en double"),
        ((_step("Invalide"),), "invalide"),
        ((_step("ok", permission="m.inconnue.view"),), "permission inconnue"),
    ],
)
def test_registry_validates_steps(steps: tuple[OnboardingStepDef, ...], message: str) -> None:
    permissions = (PermissionDef("m.x.view", "read"),)  # type: ignore[arg-type]
    with pytest.raises(RegistryError, match=message):
        ModuleRegistry([ModuleManifest(code="m", permissions=permissions, onboarding=steps)])


def test_registry_proposes_steps_of_effective_modules_only() -> None:
    permissions = (PermissionDef("m.x.view", "read"),)  # type: ignore[arg-type]
    registry = ModuleRegistry(
        [
            ModuleManifest(code="m", permissions=permissions, onboarding=(_step("etape"),)),
            ModuleManifest(code="n"),
        ]
    )
    assert [s.code for s in registry.onboarding_steps({"m", "n"})] == ["etape"]
    assert registry.onboarding_steps({"n"}) == []
