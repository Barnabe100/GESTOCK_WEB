"""Aides partagées par les tests du stock : monde de test, documents, membres."""

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, login


@dataclass
class World:
    owner: Api
    site: str
    site2: str
    articles: list[str]
    supplier: str
    reasons: dict[str, str]


def make_world(provision: Any, api_for: Any) -> World:
    t = provision("alpha", profile="retail.quincaillerie", plan="ENTREPRISE")
    owner: Api = api_for("owner@alpha.example.com")
    site2 = owner.post("/sites", json={"name": "Dépôt", "code": "DEPOT", "kind": "warehouse"})
    category = owner.post("/catalog/categories", json={"name": "Divers"}).json()
    articles = [
        owner.post(
            "/catalog/articles",
            json={
                "reference": f"A-{i}",
                "designation": f"Article {i}",
                "category_id": category["id"],
                "unit": "u",
                "purchase_price": "100",
                "sale_price": "150",
            },
        ).json()["id"]
        for i in range(3)
    ]
    supplier = owner.post("/suppliers", json={"name": "Faso Import"}).json()["id"]
    reasons = {
        r["code"] or r["label"]: r["id"]
        for r in owner.get("/stock/exit-reasons?limit=50").json()["items"]
    }
    return World(owner, str(t.site_id), site2.json()["id"], articles, supplier, reasons)


def entry(w: World, lines: list[tuple[int, str, str]], **extra: Any) -> dict[str, Any]:
    body = {
        "site_id": w.site,
        "supplier_id": w.supplier,
        "lines": [
            {"article_id": w.articles[i], "quantity": q, "unit_cost": c} for i, q, c in lines
        ],
        **extra,
    }
    response = w.owner.post("/stock/entries", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def validated_entry(w: World, lines: list[tuple[int, str, str]], **extra: Any) -> dict[str, Any]:
    document = entry(w, lines, **extra)
    response = w.owner.post(f"/stock/entries/{document['id']}/validate")
    assert response.status_code == 200, response.text
    return dict(response.json())


def exit_doc(w: World, lines: list[tuple[int, str]], reason: str = "PERTE") -> dict[str, Any]:
    response = w.owner.post(
        "/stock/exits",
        json={
            "site_id": w.site,
            "reason_id": w.reasons[reason],
            "lines": [{"article_id": w.articles[i], "quantity": q} for i, q in lines],
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def level(owner_db: Session, w: World, index: int, site: str | None = None) -> tuple[str, str]:
    owner_db.expire_all()
    row = owner_db.execute(
        text(
            "SELECT quantity::text, average_cost::text FROM stock_levels "
            "WHERE site_id = :s AND article_id = :a"
        ),
        {"s": site or w.site, "a": w.articles[index]},
    ).one_or_none()
    return (row[0], row[1]) if row else ("none", "none")


def count(owner_db: Session, sql: str) -> int:
    return int(owner_db.execute(text(sql)).scalar_one())


# --- Motifs de sortie (Q7) ----------------------------------------------------------------------


def member(w: World, client: TestClient, email: str, template: str, **access: Any) -> Api:
    roles = {r["template_code"]: r["id"] for r in w.owner.get("/roles").json()}
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{"role_id": roles[template]}],
        **access,
    }
    assert w.owner.post("/members", json=body).status_code == 201
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def open_cash(
    w: World, site: str | None = None, opening_float: str = "0", name: str = "Caisse test"
) -> dict[str, Any]:
    """Caisse ouverte sur un site (défaut : site principal) : les paiements espèces exigent une
    session de caisse ouverte sur le site de la vente (Phase 2.9)."""
    register = w.owner.post("/cash/registers", json={"site_id": site or w.site, "name": name})
    assert register.status_code == 201, register.text
    session = w.owner.post(
        "/cash/sessions",
        json={"cash_register_id": register.json()["id"], "opening_float": opening_float},
    )
    assert session.status_code == 201, session.text
    return dict(session.json())
