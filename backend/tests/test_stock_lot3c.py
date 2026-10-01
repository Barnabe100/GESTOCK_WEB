"""Lot 3-C (ADR-0041) — conditionnements dans les opérations de stock.

Entrées, sorties, transferts et inventaires saisis dans une PRÉSENTATION (unité de base ou
conditionnement actif de l'article) ; le stock et les mouvements restent en UNITÉ DE BASE,
calculée par le serveur avec le mécanisme commun du catalogue (même conversion que les ventes) ;
la présentation est conservée sur les lignes, les mouvements et les comptages (historique
« 3 Carton 24 → 72 ») ; règle des quantités entières appliquée par le serveur."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World

INVENTORIES = "/inventories"


@pytest.fixture
def coca(world: World) -> dict[str, str]:
    """Article 0 (« u », entier) : Pack 6 et Carton 24."""
    pack = _packaging(world, 0, "Pack 6", "6")
    carton = _packaging(world, 0, "Carton 24", "24")
    return {"pack": pack["id"], "carton": carton["id"]}


def _packaging(w: World, index: int, name: str, conversion: str) -> dict[str, Any]:
    response = w.owner.post(
        f"/catalog/articles/{w.articles[index]}/packagings",
        json={"name": name, "conversion": conversion},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _line(w: World, index: int, quantity: str, packaging: str | None = None, **extra: Any) -> Any:
    line: dict[str, Any] = {"article_id": w.articles[index], "quantity": quantity, **extra}
    if packaging is not None:
        line["packaging_id"] = packaging
    return line


def _entry(w: World, lines: list[Any], api: Api | None = None, **extra: Any) -> Any:
    return (api or w.owner).post(
        "/stock/entries",
        json={"site_id": w.site, "supplier_id": w.supplier, "lines": lines, **extra},
    )


def _exit(w: World, lines: list[Any]) -> Any:
    return w.owner.post(
        "/stock/exits",
        json={"site_id": w.site, "reason_id": w.reasons["PERTE"], "lines": lines},
    )


def _transfer(w: World, lines: list[Any]) -> Any:
    return w.owner.post(
        "/stock/transfers",
        json={"source_site_id": w.site, "destination_site_id": w.site2, "lines": lines},
    )


def _ok(response: Any, status: int = 201) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return dict(response.json())


def _validate(w: World, kind: str, document: dict[str, Any]) -> Any:
    return w.owner.post(f"/stock/{kind}/{document['id']}/validate")


def _code(response: Any) -> str:
    return str(response.json().get("code"))


def _movements(w: World, document_id: str) -> list[dict[str, Any]]:
    page = w.owner.get("/stock/movements", params={"source_id": document_id, "limit": 50})
    assert page.status_code == 200, page.text
    return sorted(page.json()["items"], key=lambda m: (m["movement_type"], m["site_id"]))


# --- 1-2. Entrées --------------------------------------------------------------------------------


def test_entry_in_base_unit_unchanged(world: World, owner_db: Session) -> None:
    entry = _ok(_entry(world, [_line(world, 0, "5", unit_cost="100")]))
    line = entry["lines"][0] if entry["lines"] else None
    document = world.owner.get(f"/stock/entries/{entry['id']}").json()
    line = document["lines"][0]
    assert (line["quantity"], line["base_quantity"], line["packaging_id"]) == (
        "5.000",
        "5.000",
        None,
    )
    _ok(_validate(world, "entries", entry), 200)
    assert sh.level(owner_db, world, 0) == ("5.000", "100.0000")
    movement = _movements(world, entry["id"])[0]
    assert (movement["quantity"], movement["packaging_name"], movement["packaging_quantity"]) == (
        "5.000",
        None,
        None,
    )


def test_entry_in_packaging_moves_base_quantity_and_keeps_presentation(
    world: World, coca: dict[str, str], owner_db: Session
) -> None:
    # 10 cartons de 24 à 12 000 le carton = 240 unités à 500.
    entry = _ok(_entry(world, [_line(world, 0, "10", coca["carton"], unit_cost="12000")]))
    line = world.owner.get(f"/stock/entries/{entry['id']}").json()["lines"][0]
    assert (
        line["quantity"],
        line["packaging_name"],
        line["packaging_conversion"],
        line["base_quantity"],
        line["unit_cost"],
        line["amount"],
    ) == ("10.000", "Carton 24", "24.000", "240.000", "12000.00", "120000.00")
    _ok(_validate(world, "entries", entry), 200)
    assert sh.level(owner_db, world, 0) == ("240.000", "500.0000")
    movement = _movements(world, entry["id"])[0]
    assert (
        movement["quantity"],
        movement["unit_cost"],
        movement["packaging_name"],
        movement["packaging_conversion"],
        movement["packaging_quantity"],
    ) == ("240.000", "500.0000", "Carton 24", "24.000", "10.000")
    # Une ligne par présentation : 10 cartons + 5 unités du même article.
    mixed = _ok(
        _entry(
            world,
            [
                _line(world, 0, "1", coca["carton"], unit_cost="12000"),
                _line(world, 0, "5", unit_cost="500"),
            ],
        )
    )
    _ok(_validate(world, "entries", mixed), 200)
    assert sh.level(owner_db, world, 0)[0] == "269.000"
    duplicate = _entry(
        world,
        [
            _line(world, 0, "1", coca["carton"], unit_cost="1"),
            _line(world, 0, "2", coca["carton"], unit_cost="1"),
        ],
    )
    assert (duplicate.status_code, _code(duplicate)) == (422, "duplicate_article_line")


def test_entry_cost_per_presentation_feeds_cmup_and_valuation(
    world: World, coca: dict[str, str], owner_db: Session
) -> None:
    # Règle validée : le coût saisi est celui de la présentation (le carton) ; le serveur en
    # déduit seul le coût par unité de base, qui alimente CMUP et valorisation.
    _ok(
        world.owner.patch(f"/catalog/packagings/{coca['carton']}", json={"sale_price": "15000"}),
        200,
    )
    first = _ok(_entry(world, [_line(world, 0, "10", coca["carton"], unit_cost="12000")]))
    _ok(_validate(world, "entries", first), 200)
    assert sh.level(owner_db, world, 0) == ("240.000", "500.0000")  # 12 000 / 24
    # Deuxième réception plus chère : 1 carton à 14 400 (600 la bouteille).
    second = _ok(_entry(world, [_line(world, 0, "1", coca["carton"], unit_cost="14400")]))
    _ok(_validate(world, "entries", second), 200)
    assert _movements(world, second["id"])[0]["unit_cost"] == "600.0000"
    # CMUP pondéré en unité de base : (240 × 500 + 24 × 600) / 264.
    assert sh.level(owner_db, world, 0) == ("264.000", "509.0909")
    # Sortie d'un carton : valorisée au CMUP par bouteille × 24.
    exit_ = _ok(_exit(world, [_line(world, 0, "1", coca["carton"])]))
    _ok(_validate(world, "exits", exit_), 200)
    line = world.owner.get(f"/stock/exits/{exit_['id']}").json()["lines"][0]
    assert (line["unit_cost"], line["amount"]) == ("509.0909", "12218.18")
    # Le prix de vente du conditionnement reste indépendant du coût d'achat.
    carton = world.owner.get(f"/catalog/articles/{world.articles[0]}/packagings").json()["items"]
    assert {p["name"]: p["sale_price"] for p in carton}["Carton 24"] == "15000.00"


# --- 3. Sorties ----------------------------------------------------------------------------------


def test_exit_in_packaging(world: World, coca: dict[str, str], owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "100", "500")])
    document = _ok(_exit(world, [_line(world, 0, "3", coca["carton"])]))
    _ok(_validate(world, "exits", document), 200)
    assert sh.level(owner_db, world, 0)[0] == "28.000"  # 100 − 3 × 24
    line = world.owner.get(f"/stock/exits/{document['id']}").json()["lines"][0]
    # Coût figé = CMUP par unité de base ; montant = 72 × 500.
    assert (line["quantity"], line["base_quantity"], line["unit_cost"], line["amount"]) == (
        "3.000",
        "72.000",
        "500.0000",
        "36000.00",
    )
    movement = _movements(world, document["id"])[0]
    assert (movement["quantity"], movement["packaging_name"], movement["packaging_quantity"]) == (
        "-72.000",
        "Carton 24",
        "3.000",
    )
    # Stock insuffisant après conversion : 2 cartons = 48 > 28.
    short = _ok(_exit(world, [_line(world, 0, "2", coca["carton"])]))
    refused = _validate(world, "exits", short)
    assert (refused.status_code, _code(refused)) == (422, "insufficient_stock")
    # Annulation de la sortie : la quantité de base revient, avec la présentation.
    cancelled = world.owner.post(
        f"/stock/exits/{document['id']}/cancel", json={"reason": "Erreur de saisie"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert sh.level(owner_db, world, 0)[0] == "100.000"
    reversal = [
        m for m in _movements(world, document["id"]) if m["movement_type"] == "CANCELLATION"
    ]
    assert (reversal[0]["quantity"], reversal[0]["packaging_quantity"]) == ("72.000", "3.000")


# --- 4. Transferts -------------------------------------------------------------------------------


def test_transfer_in_packaging(world: World, coca: dict[str, str], owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "240", "500")])  # 10 cartons
    transfer = _ok(_transfer(world, [_line(world, 0, "2", coca["carton"])]))
    assert transfer["lines"][0]["base_quantity"] == "48.000"
    _ok(world.owner.post(f"/stock/transfers/{transfer['id']}/validate"), 200)
    assert sh.level(owner_db, world, 0)[0] == "192.000"
    assert sh.level(owner_db, world, 0, world.site2)[0] == "48.000"
    pairs = {m["movement_type"]: m for m in _movements(world, transfer["id"])}
    for kind, quantity in (("TRANSFER_OUT", "-48.000"), ("TRANSFER_IN", "48.000")):
        assert (pairs[kind]["quantity"], pairs[kind]["packaging_name"]) == (quantity, "Carton 24")
        assert pairs[kind]["packaging_quantity"] == "2.000"
    _ok(
        world.owner.post(
            f"/stock/transfers/{transfer['id']}/cancel", json={"reason": "Erreur de site"}
        ),
        200,
    )
    assert sh.level(owner_db, world, 0)[0] == "240.000"
    assert sh.level(owner_db, world, 0, world.site2)[0] == "0.000"


# --- 5. Inventaires ------------------------------------------------------------------------------


def _inventory_line(w: World, inventory: dict[str, Any]) -> dict[str, Any]:
    page = w.owner.get(f"{INVENTORIES}/{inventory['id']}/lines").json()
    return dict(page["items"][0])


def _counting(w: World) -> dict[str, Any]:
    inventory = _ok(
        w.owner.post(
            INVENTORIES,
            json={"site_id": w.site, "inventory_type": "TARGETED", "article_ids": [w.articles[0]]},
        )
    )
    _ok(w.owner.post(f"{INVENTORIES}/{inventory['id']}/start"), 200)
    return inventory


def _count(w: World, inventory: dict[str, Any], count: dict[str, Any]) -> Any:
    line = _inventory_line(w, inventory)
    return w.owner.patch(
        f"{INVENTORIES}/{inventory['id']}/lines",
        json={"counts": [{"line_id": line["id"], **count}]},
    )


def test_inventory_counted_in_packaging(
    world: World, coca: dict[str, str], owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "240", "500")])
    inventory = _counting(world)
    line = _inventory_line(world, inventory)
    # Conditionnements actifs proposés à la saisie.
    assert [p["name"] for p in line["packagings"]] == ["Pack 6", "Carton 24"]
    # 8 cartons + 5 unités = 197 ; écart 197 − 240 = −43.
    _ok(
        _count(
            world,
            inventory,
            {"packaging_id": coca["carton"], "packaging_quantity": "8", "unit_quantity": "5"},
        ),
        200,
    )
    line = _inventory_line(world, inventory)
    assert (
        line["quantity_physical"],
        line["count_packaging_name"],
        line["count_packaging_quantity"],
        line["count_unit_quantity"],
        line["quantity_variance"],
    ) == ("197.000", "Carton 24", "8.000", "5.000", "-43.000")
    # La quantité de base n'est jamais acceptée du client avec un conditionnement.
    forged = _count(
        world,
        inventory,
        {"packaging_id": coca["carton"], "packaging_quantity": "8", "quantity_physical": "999"},
    )
    assert forged.status_code == 422
    _ok(world.owner.post(f"{INVENTORIES}/{inventory['id']}/complete-counting"), 200)
    _ok(world.owner.post(f"{INVENTORIES}/{inventory['id']}/validate"), 200)
    assert sh.level(owner_db, world, 0)[0] == "197.000"
    # Saisie en unité de base : présentation effacée.
    second = _counting(world)
    _ok(_count(world, second, {"quantity_physical": "190"}), 200)
    assert _inventory_line(world, second)["count_packaging_id"] is None


# --- 6, 9, 10. Conversion et quantités décimales -------------------------------------------------


def test_decimal_rules_and_decimal_conversion(world: World, coca: dict[str, str]) -> None:
    for response in (
        _entry(world, [_line(world, 0, "2.5", unit_cost="1")]),
        _entry(world, [_line(world, 0, "1.5", coca["carton"], unit_cost="1")]),
        _exit(world, [_line(world, 0, "0.5")]),
        _transfer(world, [_line(world, 0, "1.5", coca["pack"])]),
    ):
        assert (response.status_code, _code(response)) == (422, "quantity_not_whole")
    inventory = _counting(world)
    for count in (
        {"quantity_physical": "2.5"},
        {"packaging_id": coca["carton"], "packaging_quantity": "1", "unit_quantity": "0.5"},
    ):
        refused = _count(world, inventory, count)
        assert (refused.status_code, _code(refused)) == (422, "quantity_not_whole")
    # Article au poids : décimales autorisées, conversion décimale (1 sac = 25,5 kg).
    sh.allow_decimals(world, 1)
    bag = _packaging(world, 1, "Sac", "25.5")
    entry = _ok(_entry(world, [_line(world, 1, "1.5", bag["id"], unit_cost="17000")]))
    line = world.owner.get(f"/stock/entries/{entry['id']}").json()["lines"][0]
    assert (line["base_quantity"], line["amount"]) == ("38.250", "25500.00")
    _ok(_validate(world, "entries", entry), 200)
    movement = _movements(world, entry["id"])[0]
    # 17 000 / 25,5 = 666,6667 par kg (4 décimales, comme le CMUP).
    assert (movement["quantity"], movement["unit_cost"]) == ("38.250", "666.6667")
    precise = _packaging(world, 1, "Dose", "0.125")
    refused = _exit(world, [_line(world, 1, "0.001", precise["id"])])
    assert (refused.status_code, _code(refused)) == (422, "base_quantity_precision")


# --- 7, 8, 11. Conditionnement inactif, historique, revalidation ---------------------------------


def test_inactive_packaging_refused_history_kept(
    world: World, coca: dict[str, str], owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "240", "500")])
    validated = _ok(_exit(world, [_line(world, 0, "1", coca["carton"])]))
    _ok(_validate(world, "exits", validated), 200)
    draft_entry = _ok(_entry(world, [_line(world, 0, "1", coca["carton"], unit_cost="1")]))
    draft_transfer = _ok(_transfer(world, [_line(world, 0, "1", coca["carton"])]))
    inventory = _counting(world)
    _ok(_count(world, inventory, {"packaging_id": coca["carton"], "packaging_quantity": "9"}), 200)
    _ok(world.owner.post(f"/catalog/packagings/{coca['carton']}/deactivate"), 200)
    # Nouvelle opération : refusée.
    refused = _entry(world, [_line(world, 0, "1", coca["carton"], unit_cost="1")])
    assert (refused.status_code, _code(refused)) == (422, "packaging_inactive")
    # Opérations en cours : revalidées à la validation.
    for response in (
        _validate(world, "entries", draft_entry),
        world.owner.post(f"/stock/transfers/{draft_transfer['id']}/validate"),
    ):
        assert (response.status_code, _code(response)) == (422, "packaging_inactive")
    _ok(world.owner.post(f"{INVENTORIES}/{inventory['id']}/complete-counting"), 200)
    refused = world.owner.post(f"{INVENTORIES}/{inventory['id']}/validate")
    assert (refused.status_code, _code(refused)) == (422, "packaging_inactive")
    assert sh.level(owner_db, world, 0)[0] == "216.000"
    # Historique : la sortie validée garde sa présentation (document et mouvement).
    line = world.owner.get(f"/stock/exits/{validated['id']}").json()["lines"][0]
    assert (line["packaging_name"], line["base_quantity"]) == ("Carton 24", "24.000")
    assert _movements(world, validated["id"])[0]["packaging_name"] == "Carton 24"


def test_server_recomputes_and_revalidates_conversion(
    world: World, coca: dict[str, str], owner_db: Session
) -> None:
    # Une quantité de base envoyée par le client est ignorée : le serveur la calcule.
    entry = _ok(
        _entry(world, [_line(world, 0, "2", coca["carton"], unit_cost="1", base_quantity="999")])
    )
    assert (
        world.owner.get(f"/stock/entries/{entry['id']}").json()["lines"][0]["base_quantity"]
        == "48.000"
    )
    # Conversion figée dès qu'un brouillon de stock l'utilise.
    frozen = world.owner.patch(f"/catalog/packagings/{coca['carton']}", json={"conversion": "12"})
    assert (frozen.status_code, _code(frozen)) == (409, "packaging_in_use")
    # Défense en profondeur : conversion modifiée hors API → validation refusée.
    owner_db.execute(
        text("UPDATE catalog_packagings SET conversion = 12 WHERE id = :p"), {"p": coca["carton"]}
    )
    owner_db.commit()
    refused = _validate(world, "entries", entry)
    assert (refused.status_code, _code(refused)) == (409, "packaging_conversion_changed")
    assert sh.level(owner_db, world, 0) == ("none", "none")


def _race(app: Any, token: str, calls: list[tuple[str, str, str, Any]]) -> dict[str, int]:
    """Requêtes lancées en même temps (barrière), chacune avec sa propre connexion."""
    barrier = threading.Barrier(len(calls))
    statuses: dict[str, int] = {}

    def run(name: str, method: str, path: str, body: Any) -> None:
        with TestClient(app) as client:
            api = Api(client, token)
            barrier.wait()
            response = getattr(api, method)(path, json=body) if body else getattr(api, method)(path)
            statuses[name] = response.status_code

    threads = [threading.Thread(target=run, args=call) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return statuses


def test_conversion_change_vs_stock_draft_concurrency(world: World, app: Any) -> None:
    """Création d'un brouillon de stock et modification de la conversion simultanées : l'un
    attend l'autre (verrou partagé / exclusif du conditionnement). Jamais de ligne à une
    conversion différente de celle du conditionnement."""
    for attempt in range(3):
        pack = _packaging(world, 1, f"Lot {attempt}", "2")
        statuses = _race(
            app,
            world.owner.token,
            [
                (
                    "entry",
                    "post",
                    "/stock/entries",
                    {
                        "site_id": world.site,
                        "supplier_id": world.supplier,
                        "lines": [_line(world, 1, "1", pack["id"], unit_cost="1")],
                    },
                ),
                ("patch", "patch", f"/catalog/packagings/{pack['id']}", {"conversion": "3"}),
            ],
        )
        assert statuses["entry"] == 201, statuses
        assert statuses["patch"] in (200, 409), statuses
        current = world.owner.get(f"/catalog/articles/{world.articles[1]}/packagings").json()
        conversion = next(p["conversion"] for p in current["items"] if p["id"] == pack["id"])
        entries = world.owner.get("/stock/entries", params={"limit": 1}).json()["items"]
        line = world.owner.get(f"/stock/entries/{entries[0]['id']}").json()["lines"][0]
        assert line["packaging_conversion"] == conversion, (statuses, line, conversion)


def test_deactivation_vs_validation_concurrency(
    world: World, coca: dict[str, str], app: Any, owner_db: Session
) -> None:
    """Désactivation du conditionnement et validation d'une entrée simultanées : soit
    l'entrée est validée (conditionnement encore actif), soit elle est refusée ; jamais de
    stock partiel."""
    entry = _ok(_entry(world, [_line(world, 0, "1", coca["pack"], unit_cost="600")]))
    statuses = _race(
        app,
        world.owner.token,
        [
            ("validate", "post", f"/stock/entries/{entry['id']}/validate", None),
            ("deactivate", "post", f"/catalog/packagings/{coca['pack']}/deactivate", None),
        ],
    )
    assert statuses["deactivate"] == 200, statuses
    assert statuses["validate"] in (200, 422), statuses
    expected = "6.000" if statuses["validate"] == 200 else "none"
    assert sh.level(owner_db, world, 0)[0] == expected


# --- 13-14. Isolation -----------------------------------------------------------------------------


def test_tenant_isolation(world: World, coca: dict[str, str], provision: Any, api_for: Any) -> None:
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    category = other.post("/catalog/categories", json={"name": "Divers"}).json()["id"]
    own = other.post(
        "/catalog/articles",
        json={"reference": "B-1", "designation": "B", "category_id": category, "unit": "u"},
    ).json()["id"]
    supplier = other.post("/suppliers", json={"name": "Fournisseur B"}).json()["id"]
    stolen = other.post(
        "/stock/entries",
        json={
            "site_id": str(beta.site_id),
            "supplier_id": supplier,
            "lines": [
                {
                    "article_id": own,
                    "packaging_id": coca["carton"],
                    "quantity": "1",
                    "unit_cost": "1",
                }
            ],
        },
    )
    assert (stolen.status_code, _code(stolen)) == (422, "packaging_not_found")


def test_site_isolation(world: World, coca: dict[str, str], client: TestClient) -> None:
    member = sh.member(world, client, "magasinier@example.com", "manager", site_ids=[world.site])
    refused = member.post(
        "/stock/entries",
        json={
            "site_id": world.site2,
            "supplier_id": world.supplier,
            "lines": [_line(world, 0, "1", coca["carton"], unit_cost="1")],
        },
    )
    assert refused.status_code == 403, refused.text
    accepted = _entry(world, [_line(world, 0, "1", coca["carton"], unit_cost="1")], api=member)
    assert accepted.status_code == 201, accepted.text
    # Journal : les mouvements de l'autre site ne sont pas visibles.
    other = _ok(
        world.owner.post(
            "/stock/entries",
            json={
                "site_id": world.site2,
                "supplier_id": world.supplier,
                "lines": [_line(world, 0, "1", coca["carton"], unit_cost="1")],
            },
        )
    )
    _ok(_validate(world, "entries", other), 200)
    visible = member.get("/stock/movements", params={"source_id": other["id"]}).json()
    assert visible["total"] == 0


# --- 15. Anciens mouvements et ventes -------------------------------------------------------------


def test_legacy_lines_and_sale_movements(
    world: World, coca: dict[str, str], owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "100", "500")])
    # Ligne « ancienne » (saisie avant 3-C) : sans conditionnement, base = quantité.
    owner_db.expire_all()
    legacy = owner_db.execute(
        text(
            "SELECT count(*) FROM stock_entry_lines WHERE packaging_id IS NULL "
            "AND base_quantity = quantity"
        )
    ).scalar_one()
    assert legacy >= 1
    # Vente en carton : son mouvement de stock garde aussi la présentation (historique).
    owner_db.execute(
        text("UPDATE catalog_packagings SET sale_price = 10000 WHERE id = :p"),
        {"p": coca["carton"]},
    )
    owner_db.commit()
    sale = world.owner.post(
        "/sales",
        json={
            "site_id": world.site,
            "lines": [
                {"article_id": world.articles[0], "packaging_id": coca["carton"], "quantity": "2"}
            ],
        },
    ).json()
    validated = world.owner.post(
        f"/sales/{sale['id']}/validate",
        json={"payments": [{"amount": sale["total"], "method": "CASH"}]},
    )
    assert validated.status_code == 200, validated.text
    movement = _movements(world, sale["id"])[0]
    assert (movement["quantity"], movement["packaging_name"], movement["packaging_quantity"]) == (
        "-48.000",
        "Carton 24",
        "2.000",
    )
    assert uuid.UUID(movement["id"])
