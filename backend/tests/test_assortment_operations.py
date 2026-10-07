"""Recette, étape 1 — assortiment par site (ADR-0046), palier 2 : application aux opérations.

CATALOGUE TENANT ≠ ASSORTIMENT SITE ≠ STOCK SITE.

- Garde centrale de ``StockService`` : tout mouvement (réception, sortie, vente, transfert source
  ET destination, ajustement) exige l'article ACTIF dans l'assortiment du site, sous verrou
  partagé (article → assortiment → niveaux → lots) ; annulations exemptées (D3-7).
- Contrôle de saisie dès le brouillon (entrées, sorties, transferts, ventes, inventaires ciblés)
  et contrôle FAISANT FOI à la validation : ``422 article_not_in_site_assortment``, jamais
  d'ajout automatique (D3).
- Point de vente : seulement l'assortiment ACTIF du site (recherche) ; scan d'un article hors
  assortiment refusé (422) ; encaissement refusé.
- Niveaux : assortiment actif ∪ stock non nul (``in_assortment``), plus de produit cartésien ;
  alertes limitées à l'assortiment. Inventaires : candidats et inventaire complet = assortiment
  actif (y compris jamais reçu). Seuils et emplacements refusés hors assortiment.
- Stock recréé par une annulation après retrait : visible « hors assortiment », inutilisable sans
  réactivation (D4).
"""

import threading
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from tests import stock_helpers as sh
from tests.conftest import Api, add_site
from tests.stock_helpers import World

NOT_IN = "article_not_in_site_assortment"


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _refused(response: Any) -> dict[str, Any]:
    assert response.status_code == 422, response.text
    body = dict(response.json())
    assert body["code"] == NOT_IN, body
    return body


def _remove(w: World, site: str, *indexes: int) -> Any:
    return w.owner.post(
        f"/catalog/sites/{site}/articles/remove",
        json={"article_ids": [w.articles[i] for i in indexes]},
    )


def _drop_row(owner_db: Session, w: World, site: str, index: int) -> None:
    """Retrait hors API (comme un retrait concurrent survenu après l'enregistrement d'un
    brouillon) : le contrôle FAISANT FOI est celui de la validation."""
    owner_db.execute(
        text(
            "UPDATE catalog_site_articles SET is_active = false, removed_at = now()"
            " WHERE site_id = :s AND article_id = :a"
        ),
        {"s": site, "a": w.articles[index]},
    )
    owner_db.commit()


def _sale_body(w: World, lines: list[tuple[int, str]], site: str | None = None) -> dict[str, Any]:
    return {
        "site_id": site or w.site,
        "customer_id": sh.credit_customer(w),
        "lines": [{"article_id": w.articles[i], "quantity": q} for i, q in lines],
    }


def _checkout(w: World, lines: list[tuple[int, str]]) -> Any:
    return w.owner.post(
        "/pos/checkout", json={**_sale_body(w, lines), "idempotency_key": str(uuid.uuid4())}
    )


def _levels(w: World, site: str, **params: Any) -> dict[str, dict[str, Any]]:
    page = _ok(w.owner.get("/stock/levels", params={"site_id": site, "limit": 100, **params}))
    return {item["reference"]: item for item in page["items"]}


def _movements(owner_db: Session, site: str) -> int:
    return sh.count(owner_db, f"SELECT count(*) FROM stock_movements WHERE site_id = '{site}'")


@pytest.fixture
def partial(bare_world: World) -> World:
    """Boutique : A-0 et A-1 dans l'assortiment ; A-2 au catalogue seulement. Dépôt : A-0."""
    w = bare_world
    sh.assort(w.owner, w.site, w.articles[0], w.articles[1])
    sh.assort(w.owner, w.site2, w.articles[0])
    return w


# --- Entrées, sorties -----------------------------------------------------------------------------


def test_entry_refused_at_draft_and_at_validation(partial: World, owner_db: Session) -> None:
    w = partial
    body = _refused(
        w.owner.post(
            "/stock/entries",
            json={
                "site_id": w.site,
                "supplier_id": w.supplier,
                "lines": [
                    {"article_id": w.articles[0], "quantity": "1", "unit_cost": "100"},
                    {"article_id": w.articles[2], "quantity": "1", "unit_cost": "100"},
                ],
            },
        )
    )
    assert body["articles"] == ["A-2"] and body["site_id"] == w.site
    # Aucun ajout automatique à l'assortiment, aucun document.
    assert sh.count(owner_db, "SELECT count(*) FROM stock_entries") == 0
    assert (
        sh.count(
            owner_db,
            f"SELECT count(*) FROM catalog_site_articles WHERE article_id = '{w.articles[2]}'",
        )
        == 0
    )
    # Brouillon enregistré, puis article retiré : la validation FAIT FOI.
    draft = sh.entry(w, [(1, "5", "100")])
    _drop_row(owner_db, w, w.site, 1)
    _refused(w.owner.post(f"/stock/entries/{draft['id']}/validate"))
    assert _movements(owner_db, w.site) == 0
    # Mise à jour du brouillon : même contrôle de saisie.
    _refused(
        w.owner.put(
            f"/stock/entries/{draft['id']}",
            json={
                "supplier_id": w.supplier,
                "lines": [{"article_id": w.articles[1], "quantity": "2", "unit_cost": "100"}],
            },
        )
    )


def test_exit_refused_at_draft_and_at_validation(partial: World, owner_db: Session) -> None:
    w = partial
    sh.validated_entry(w, [(0, "10", "100"), (1, "10", "100")])
    _refused(
        w.owner.post(
            "/stock/exits",
            json={
                "site_id": w.site,
                "reason_id": w.reasons["PERTE"],
                "lines": [{"article_id": w.articles[2], "quantity": "1"}],
            },
        )
    )
    draft = sh.exit_doc(w, [(1, "2")])
    _drop_row(owner_db, w, w.site, 1)
    _refused(w.owner.post(f"/stock/exits/{draft['id']}/validate"))
    assert sh.level(owner_db, w, 1)[0] == "10.000"


# --- Transferts : source ET destination ----------------------------------------------------------


def _transfer(w: World, index: int, quantity: str = "2") -> Any:
    return w.owner.post(
        "/stock/transfers",
        json={
            "source_site_id": w.site,
            "destination_site_id": w.site2,
            "lines": [{"article_id": w.articles[index], "quantity": quantity}],
        },
    )


def test_transfer_requires_both_sites(partial: World, owner_db: Session) -> None:
    w = partial
    sh.validated_entry(w, [(0, "10", "100"), (1, "10", "100")])
    # A-1 : dans l'assortiment de la boutique, pas du dépôt (destination).
    body = _refused(_transfer(w, 1))
    assert body["site_id"] == w.site2 and body["articles"] == ["A-1"]
    # A-2 : ni l'un ni l'autre (la source est contrôlée d'abord).
    assert _refused(_transfer(w, 2))["site_id"] == w.site
    # Validation : destination retirée après le brouillon → refus, rien n'a bougé.
    draft = _ok(_transfer(w, 0), 201)
    _drop_row(owner_db, w, w.site2, 0)
    _refused(w.owner.post(f"/stock/transfers/{draft['id']}/validate"))
    assert sh.level(owner_db, w, 0)[0] == "10.000"
    assert sh.level(owner_db, w, 0, w.site2)[0] == "none"
    # Source retirée (destination réactivée) → refus aussi.
    sh.assort(w.owner, w.site2, w.articles[0])
    _drop_row(owner_db, w, w.site, 0)
    _refused(w.owner.post(f"/stock/transfers/{draft['id']}/validate"))


# --- Ventes et point de vente ------------------------------------------------------------------


def test_sale_refused_at_draft_and_at_validation(partial: World, owner_db: Session) -> None:
    w = partial
    sh.validated_entry(w, [(0, "10", "100"), (1, "10", "100")])
    _refused(w.owner.post("/sales", json=_sale_body(w, [(0, "1"), (2, "1")])))
    assert sh.count(owner_db, "SELECT count(*) FROM sales") == 0
    draft = _ok(w.owner.post("/sales", json=_sale_body(w, [(1, "1")])), 201)
    _refused(w.owner.put(f"/sales/{draft['id']}", json=_sale_body(w, [(2, "1")])))
    _drop_row(owner_db, w, w.site, 1)
    _refused(w.owner.post(f"/sales/{draft['id']}/validate"))
    assert sh.level(owner_db, w, 1)[0] == "10.000"
    assert _ok(w.owner.get(f"/sales/{draft['id']}"))["status"] == "DRAFT"


def test_unmanaged_article_also_checked_at_validation(partial: World, owner_db: Session) -> None:
    """Un article non géré en stock ne passe pas par ``StockService`` : la vente le contrôle
    elle-même, sous verrou partagé."""
    w = partial
    _ok(w.owner.patch(f"/catalog/articles/{w.articles[1]}", json={"stock_managed": False}))
    draft = _ok(w.owner.post("/sales", json=_sale_body(w, [(1, "1")])), 201)
    _drop_row(owner_db, w, w.site, 1)
    _refused(w.owner.post(f"/sales/{draft['id']}/validate"))
    sh.assort(w.owner, w.site, w.articles[1])
    assert _ok(w.owner.post(f"/sales/{draft['id']}/validate"))["status"] == "VALIDATED"


def test_pos_shows_only_the_active_assortment(partial: World, owner_db: Session) -> None:
    w = partial
    owner_db.execute(
        text("UPDATE catalog_articles SET barcode = 'SCAN-' || reference WHERE id = ANY(:ids)"),
        {"ids": [uuid.UUID(a) for a in w.articles]},
    )
    owner_db.commit()
    sh.validated_entry(w, [(0, "10", "100"), (1, "10", "100")])

    def pos(**params: Any) -> list[str]:
        found = _ok(w.owner.get("/pos/articles", params={"site_id": w.site, **params}))
        return sorted(item["reference"] for item in found)

    assert pos() == ["A-0", "A-1"]
    assert pos(search="A-2") == []
    # Scan : article hors assortiment → 422 (rien n'est ajouté au panier), inconnu → 404.
    _refused(
        w.owner.get("/pos/articles/by-barcode", params={"site_id": w.site, "barcode": "SCAN-A-2"})
    )
    unknown = w.owner.get("/pos/articles/by-barcode", params={"site_id": w.site, "barcode": "X"})
    assert unknown.json()["code"] == "barcode_unknown"
    scanned = w.owner.get(
        "/pos/articles/by-barcode", params={"site_id": w.site, "barcode": "SCAN-A-0"}
    )
    assert _ok(scanned)["reference"] == "A-0"
    # Encaissement : refus explicite, rien d'enregistré.
    _refused(_checkout(w, [(2, "1")]))
    assert sh.count(owner_db, "SELECT count(*) FROM sales") == 0
    # Stock résiduel hors assortiment (annulation après retrait) : jamais proposé à la caisse.
    sold = _ok(_checkout(w, [(1, "10")]), 201)["sale"]
    _ok(_remove(w, w.site, 1))
    _ok(w.owner.post(f"/sales/{sold['id']}/cancel", json={"reason": "Erreur de caisse"}))
    assert sh.level(owner_db, w, 1)[0] == "10.000"
    assert pos() == ["A-0"]
    _refused(_checkout(w, [(1, "1")]))


# --- Annulations après retrait (D3-7, D4) -----------------------------------------------------


def test_cancellations_after_removal_restore_out_of_assortment_stock(
    partial: World, owner_db: Session
) -> None:
    w = partial
    entry = sh.validated_entry(w, [(1, "6", "100")])
    out = sh.exit_doc(w, [(1, "6")])
    _ok(w.owner.post(f"/stock/exits/{out['id']}/validate"))
    _ok(_remove(w, w.site, 1))
    # Annulation de la sortie : stock recréé alors que l'article est retiré.
    _ok(w.owner.post(f"/stock/exits/{out['id']}/cancel", json={"reason": "Erreur"}))
    assert sh.level(owner_db, w, 1)[0] == "6.000"
    levels = _levels(w, w.site)
    assert levels["A-1"]["in_assortment"] is False and levels["A-1"]["quantity"] == "6.000"
    assert levels["A-0"]["in_assortment"] is True
    # Inutilisable sans réactivation…
    _refused(
        w.owner.post(
            "/stock/exits",
            json={
                "site_id": w.site,
                "reason_id": w.reasons["PERTE"],
                "lines": [{"article_id": w.articles[1], "quantity": "1"}],
            },
        )
    )
    # … mais l'annulation de la réception reste possible (historique).
    _ok(w.owner.post(f"/stock/entries/{entry['id']}/cancel", json={"reason": "Doublon"}))
    assert sh.level(owner_db, w, 1)[0] == "0.000"
    # Stock nul et hors assortiment : plus présenté par le site.
    assert "A-1" not in _levels(w, w.site)
    # Réactivation : de nouveau présenté et utilisable.
    sh.assort(w.owner, w.site, w.articles[1])
    assert _levels(w, w.site)["A-1"]["in_assortment"] is True
    sh.validated_entry(w, [(1, "1", "100")])


def test_transfer_cancellation_after_removal_on_destination(
    partial: World, owner_db: Session
) -> None:
    w = partial
    sh.validated_entry(w, [(0, "10", "100")])
    transfer = _ok(_transfer(w, 0, "4"), 201)
    _ok(w.owner.post(f"/stock/transfers/{transfer['id']}/validate"))
    # Retrait refusé tant que le dépôt a du stock (palier 1) ; retrait forcé hors API pour
    # vérifier que l'annulation (retrait du dépôt, remise en boutique) reste possible.
    _drop_row(owner_db, w, w.site2, 0)
    _ok(w.owner.post(f"/stock/transfers/{transfer['id']}/cancel", json={"reason": "Erreur"}))
    assert sh.level(owner_db, w, 0)[0] == "10.000"
    assert sh.level(owner_db, w, 0, w.site2)[0] == "0.000"


# --- Niveaux, alertes, seuils, emplacements -----------------------------------------------------


def test_levels_are_assortment_union_non_zero_stock(partial: World, owner_db: Session) -> None:
    w = partial
    # Plus de produit cartésien catalogue × sites : A-2 n'est proposé par aucun site.
    shop = _levels(w, w.site)
    assert set(shop) == {"A-0", "A-1"}
    assert {r["state"] for r in shop.values()} == {"not_stocked"}
    assert all(r["in_assortment"] for r in shop.values())
    assert set(_levels(w, w.site2)) == {"A-0"}
    every = _ok(w.owner.get("/stock/levels", params={"limit": 100}))["items"]
    assert sorted((r["site_name"], r["reference"]) for r in every) == sorted(
        [(shop["A-0"]["site_name"], "A-0"), (shop["A-0"]["site_name"], "A-1"), ("Dépôt", "A-0")]
    )
    # Stock faible : alerte tant que l'article est dans l'assortiment.
    sh.validated_entry(w, [(0, "1", "100"), (1, "1", "100")])
    for index in (0, 1):
        _ok(
            w.owner.put(
                f"/stock/levels/{w.site}/{w.articles[index]}/thresholds",
                json={"min_stock": "5"},
            )
        )
    alerts = _ok(w.owner.get("/alerts/stock", params={"site_id": w.site}))["items"]
    assert sorted(a["reference"] for a in alerts) == ["A-0", "A-1"]
    assert _ok(w.owner.get("/alerts/stock/summary", params={"site_id": w.site})) == {
        "out": 0,
        "low": 2,
    }
    # Hors assortiment (stock résiduel) : jamais une alerte, état conservé à l'affichage.
    _drop_row(owner_db, w, w.site, 1)
    alerts = _ok(w.owner.get("/alerts/stock", params={"site_id": w.site}))["items"]
    assert [a["reference"] for a in alerts] == ["A-0"]
    assert _ok(w.owner.get("/alerts/stock/summary", params={"site_id": w.site}))["low"] == 1
    low = _levels(w, w.site, state="low")
    assert set(low) == {"A-0"}
    residual = _levels(w, w.site)["A-1"]
    assert residual["in_assortment"] is False and residual["quantity"] == "1.000"


def test_thresholds_and_locations_refused_outside_assortment(
    partial: World, owner_db: Session
) -> None:
    w = partial
    _refused(
        w.owner.put(f"/stock/levels/{w.site}/{w.articles[2]}/thresholds", json={"min_stock": "5"})
    )
    location = _ok(w.owner.post("/stock/locations", json={"site_id": w.site, "name": "R1"}), 201)
    _refused(
        w.owner.put(
            f"/stock/levels/{w.site}/{w.articles[2]}/location",
            json={"location_id": location["id"]},
        )
    )
    # Aucun niveau créé par la tentative.
    assert sh.level(owner_db, w, 2)[0] == "none"
    # Dans l'assortiment : accepté ; après retrait, seuil et emplacement conservés mais inertes.
    _ok(w.owner.put(f"/stock/levels/{w.site}/{w.articles[1]}/thresholds", json={"min_stock": "5"}))
    _ok(
        w.owner.put(
            f"/stock/levels/{w.site}/{w.articles[1]}/location",
            json={"location_id": location["id"]},
        )
    )
    _ok(_remove(w, w.site, 1))
    kept = owner_db.execute(
        text(
            "SELECT l.min_stock::text, (SELECT count(*) FROM stock_article_locations x"
            " WHERE x.site_id = l.site_id AND x.article_id = l.article_id)"
            " FROM stock_levels l WHERE l.site_id = :s AND l.article_id = :a"
        ),
        {"s": w.site, "a": w.articles[1]},
    ).one()
    assert kept == ("5.000", 1)
    _refused(
        w.owner.put(f"/stock/levels/{w.site}/{w.articles[1]}/thresholds", json={"min_stock": "1"})
    )
    # Affecter : refusé ; désaffecter (nettoyage de configuration) : permis hors assortiment.
    _refused(
        w.owner.put(
            f"/stock/levels/{w.site}/{w.articles[1]}/location",
            json={"location_id": location["id"]},
        )
    )
    cleared = _ok(
        w.owner.put(f"/stock/levels/{w.site}/{w.articles[1]}/location", json={"location_id": None})
    )
    assert cleared["location_id"] is None and cleared["in_assortment"] is False


# --- Inventaires -----------------------------------------------------------------------------


def test_inventories_follow_the_active_assortment(partial: World, owner_db: Session) -> None:
    w = partial
    sh.validated_entry(w, [(0, "10", "100")])
    candidates = _ok(w.owner.get("/inventories/candidates", params={"site_id": w.site}))
    assert sorted(c["reference"] for c in candidates["items"]) == ["A-0", "A-1"]
    # Complet : assortiment actif, y compris un article jamais reçu (A-1, stock 0).
    full = _ok(
        w.owner.post("/inventories", json={"site_id": w.site, "inventory_type": "FULL"}), 201
    )
    lines = _ok(w.owner.get(f"/inventories/{full['id']}/lines"))["items"]
    assert {line["reference"]: line["stock_theoretical_initial"] for line in lines} == {
        "A-0": "10.000",
        "A-1": "0.000",
    }
    _ok(w.owner.post(f"/inventories/{full['id']}/cancel", json={"reason": "Essai"}))
    # Ciblé : création et ajout refusés hors assortiment.
    body = _refused(
        w.owner.post(
            "/inventories",
            json={
                "site_id": w.site,
                "inventory_type": "TARGETED",
                "article_ids": [w.articles[0], w.articles[2]],
            },
        )
    )
    assert body["articles"] == ["A-2"]
    targeted = _ok(
        w.owner.post(
            "/inventories",
            json={"site_id": w.site, "inventory_type": "TARGETED", "article_ids": [w.articles[0]]},
        ),
        201,
    )
    _refused(
        w.owner.put(f"/inventories/{targeted['id']}", json={"add_article_ids": [w.articles[2]]})
    )
    # Site sans assortiment : inventaire complet vide.
    site3 = add_site(w.owner, "Kiosque", "KIOSQUE").json()["id"]
    empty = w.owner.post("/inventories", json={"site_id": site3, "inventory_type": "FULL"})
    assert empty.status_code == 422 and empty.json()["code"] == "inventory_empty"


def test_inventory_adjustment_guarded_at_validation(partial: World, owner_db: Session) -> None:
    w = partial
    sh.validated_entry(w, [(0, "10", "100")])
    inventory = _ok(
        w.owner.post(
            "/inventories",
            json={"site_id": w.site, "inventory_type": "TARGETED", "article_ids": [w.articles[0]]},
        ),
        201,
    )
    _ok(w.owner.post(f"/inventories/{inventory['id']}/start"))
    line = _ok(w.owner.get(f"/inventories/{inventory['id']}/lines"))["items"][0]
    _ok(
        w.owner.patch(
            f"/inventories/{inventory['id']}/lines",
            json={"counts": [{"line_id": line["id"], "quantity_physical": "8"}]},
        )
    )
    _ok(w.owner.post(f"/inventories/{inventory['id']}/complete-counting"))
    _drop_row(owner_db, w, w.site, 0)
    _refused(w.owner.post(f"/inventories/{inventory['id']}/validate"))
    assert sh.level(owner_db, w, 0)[0] == "10.000"


# --- Moteur central --------------------------------------------------------------------------


def test_engine_guard_and_cancellation_exemption(
    partial: World, db: Session, owner_db: Session
) -> None:
    from app.core.errors import BusinessRuleError
    from app.modules.stock.models import MovementType
    from app.modules.stock.stock_service import MovementRequest, StockService
    from app.shared.clock import utcnow

    w = partial
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": w.site}
    ).scalar_one()

    def request(kind: MovementType, quantity: str) -> MovementRequest:
        return MovementRequest(
            article_id=uuid.UUID(w.articles[2]),
            movement_type=kind,
            quantity=Decimal(quantity),
            unit_cost=Decimal("100"),
            source_type="test",
            source_id=uuid.uuid4(),
            source_line_id=uuid.uuid4(),
        )

    set_db_context(db, tenant_id=tenant)
    stock = StockService(db, tenant, None, utcnow())
    for kind in (MovementType.ADJUSTMENT, MovementType.ENTRY):
        with pytest.raises(BusinessRuleError) as refused:
            stock.apply(uuid.UUID(w.site), [request(kind, "1")])
        assert refused.value.code == NOT_IN
        db.rollback()
        set_db_context(db, tenant_id=tenant)
    with pytest.raises(BusinessRuleError) as locked:
        stock.lock_levels(uuid.UUID(w.site), {uuid.UUID(w.articles[2])})
    assert locked.value.code == NOT_IN
    db.rollback()
    set_db_context(db, tenant_id=tenant)
    # Une annulation n'exige pas l'assortiment (restauration de l'historique).
    stock.apply(uuid.UUID(w.site), [request(MovementType.CANCELLATION, "1")])
    db.rollback()


# --- Concurrence : retrait pendant une validation ----------------------------------------------


def test_validation_waits_for_a_concurrent_removal_and_sees_it(
    partial: World, app: Any, owner_engine: Engine, owner_db: Session
) -> None:
    w = partial
    sh.validated_entry(w, [(0, "10", "100")])
    draft = sh.exit_doc(w, [(0, "2")])
    results: list[Any] = []
    with owner_engine.connect() as removal:
        # « Retrait » en cours : verrou exclusif de la ligne d'assortiment.
        removal.execute(
            text(
                "SELECT id FROM catalog_site_articles WHERE site_id = :s AND article_id = :a"
                " FOR UPDATE"
            ),
            {"s": w.site, "a": w.articles[0]},
        )

        def run() -> None:
            with TestClient(app) as client:
                results.append(
                    Api(client, w.owner.token).post(f"/stock/exits/{draft['id']}/validate")
                )

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            waiting = owner_db.execute(
                text("SELECT count(*) FROM pg_locks WHERE NOT granted")
            ).scalar_one()
            owner_db.rollback()
            if waiting:
                break
            time.sleep(0.05)
        assert waiting, "la validation aurait dû attendre le verrou de l'assortiment"
        assert results == []
        removal.execute(
            text(
                "UPDATE catalog_site_articles SET is_active = false, removed_at = now()"
                " WHERE site_id = :s AND article_id = :a"
            ),
            {"s": w.site, "a": w.articles[0]},
        )
        removal.commit()
        thread.join(timeout=60)
    _refused(results[0])
    assert sh.level(owner_db, w, 0)[0] == "10.000"


def test_removal_waits_for_a_running_validation(
    partial: World, app: Any, app_engine: Engine, owner_db: Session
) -> None:
    """Sens inverse : une opération tient le verrou PARTAGÉ (comme une validation en cours) ;
    le retrait attend, puis voit le stock créé et refuse (``article_has_stock``)."""
    from app.modules.catalog.api import lock_site_assortment
    from app.modules.stock.models import MovementType
    from app.modules.stock.stock_service import MovementRequest, StockService
    from app.shared.clock import utcnow

    w = partial
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": w.site}
    ).scalar_one()
    results: list[Any] = []
    with Session(app_engine) as session:
        set_db_context(session, tenant_id=tenant)
        assert lock_site_assortment(session, uuid.UUID(w.site), {uuid.UUID(w.articles[1])})
        StockService(session, tenant, None, utcnow()).apply(
            uuid.UUID(w.site),
            [
                MovementRequest(
                    article_id=uuid.UUID(w.articles[1]),
                    movement_type=MovementType.ENTRY,
                    quantity=Decimal("3"),
                    unit_cost=Decimal("100"),
                    source_type="test",
                    source_id=uuid.uuid4(),
                    source_line_id=uuid.uuid4(),
                )
            ],
        )

        def run() -> None:
            with TestClient(app) as client:
                results.append(
                    Api(client, w.owner.token).post(
                        f"/catalog/sites/{w.site}/articles/remove",
                        json={"article_ids": [w.articles[1]]},
                    )
                )

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 30
        waiting = 0
        while time.monotonic() < deadline:
            waiting = owner_db.execute(
                text("SELECT count(*) FROM pg_locks WHERE NOT granted")
            ).scalar_one()
            owner_db.rollback()
            if waiting:
                break
            time.sleep(0.05)
        assert waiting, "le retrait aurait dû attendre la fin de l'opération"
        session.commit()
        thread.join(timeout=60)
    assert results[0].status_code == 409, results[0].text
    assert results[0].json()["code"] == "article_has_stock"
    assert _levels(w, w.site)["A-1"]["in_assortment"] is True


def test_static_every_stock_entry_point_goes_through_the_guard() -> None:
    """Test statique : chaque point d'entrée de ``StockService`` qui verrouille ou écrit des
    niveaux passe par ``_lock``, qui applique la garde de l'assortiment ; seules les
    annulations en sont exemptées (``apply_many``)."""
    import inspect

    from app.modules.stock.stock_service import StockService

    assert "self._ensure_in_assortment(" in inspect.getsource(StockService._lock)
    for name in ("lock_levels", "apply_many", "transfer", "transfer_lots", "lock_site_lots"):
        assert "self._lock(" in inspect.getsource(getattr(StockService, name)), name
    assert "self.lock_levels(" in inspect.getsource(StockService.consume)
    exempt = inspect.getsource(StockService.apply_many)
    assert "MovementType.CANCELLATION" in exempt
