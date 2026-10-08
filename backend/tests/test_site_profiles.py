"""Profils / modules par site, palier A : profil d'activité porté par le site (migration 0039).

Le profil est une colonne de ``sites`` (catalogue GLOBAL ``business_profiles``, aucun profil
propre à un tenant) : initialisé avec le profil de l'entreprise, jamais déduit de ``kind``,
isolé par la RLS de ``sites``. À ce palier, il ne pilote encore ni les modules ni les capacités.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.platform.tenancy.models import Site
from app.shared.ids import new_id
from tests.conftest import OWNER_URL, add_site

COLUMN = "business_profile_code"


def _alembic() -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = OWNER_URL
    return config


@pytest.fixture
def at_head(owner_engine: Engine) -> Iterator[None]:
    """Toute descente de migration est suivie d'une remontée à ``head`` (base partagée)."""
    try:
        yield
    finally:
        owner_engine.dispose()
        command.upgrade(_alembic(), "head")


def _owner(slug: str) -> str:
    """E-mail du propriétaire créé par la fixture ``provision``."""
    return f"owner@{slug}.example.com"


def _site_profiles(owner_db: Session, tenant_id: uuid.UUID) -> dict[str, str]:
    rows = owner_db.execute(
        text(f"SELECT code, {COLUMN} FROM sites WHERE tenant_id = :t"), {"t": tenant_id}
    ).all()
    return {code: profile for code, profile in rows}


def _has_column(owner_db: Session) -> bool:
    return (
        owner_db.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'sites' AND column_name = :c"
            ),
            {"c": COLUMN},
        ).scalar_one()
        == 1
    )


# --- Modèle et création des sites -----------------------------------------------------------


def test_site_model_declares_a_mandatory_catalogue_profile() -> None:
    column = Site.__table__.c[COLUMN]
    assert column.nullable is False
    (fk,) = column.foreign_keys
    assert fk.target_fullname == "business_profiles.code"
    assert fk.ondelete == "RESTRICT"


def test_new_sites_receive_the_tenant_profile_never_derived_from_kind(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    alpha = provision("prof-alpha", profile="retail.alimentation")
    beta = provision("prof-beta", profile="restaurant.maquis")
    owner = _owner("prof-alpha")
    # Premier site (provisioning) : profil de l'entreprise.
    assert set(_site_profiles(owner_db, alpha.tenant_id).values()) == {"retail.alimentation"}
    assert set(_site_profiles(owner_db, beta.tenant_id).values()) == {"restaurant.maquis"}
    # Nouveaux sites par l'API, quelle que soit leur nature : profil de l'entreprise.
    api = api_for(owner)
    for code, kind in (("DEP", "warehouse"), ("RES", "restaurant"), ("AUT", "other")):
        assert add_site(api, f"Site {code}", code, kind).status_code == 201
    owner_db.expire_all()
    profiles = _site_profiles(owner_db, alpha.tenant_id)
    assert len(profiles) == 4
    assert set(profiles.values()) == {"retail.alimentation"}


# --- Migration 0039 : montée, descente, remontée ----------------------------------------------


def test_migration_backfills_every_site_with_its_tenant_current_profile(
    provision: Any, api_for: Any, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    alpha = provision("mig-alpha", profile="retail.alimentation")
    beta = provision("mig-beta", profile="restaurant.maquis")
    api = api_for(_owner("mig-alpha"))
    assert add_site(api, "Dépôt", "DEP", "warehouse").status_code == 201
    assert add_site(api, "Salle", "SAL", "restaurant").status_code == 201
    owner_db.commit()
    owner_db.close()

    owner_engine.dispose()
    command.downgrade(_alembic(), "0038")
    with Session(owner_engine) as session:
        assert not _has_column(session)
        # Profil ACTUEL de l'entreprise au moment de la migration (pas celui d'origine).
        session.execute(
            text("UPDATE tenants SET business_profile_code = 'retail.quincaillerie' WHERE id = :t"),
            {"t": alpha.tenant_id},
        )
        session.commit()

    owner_engine.dispose()
    command.upgrade(_alembic(), "head")
    with Session(owner_engine) as session:
        assert _has_column(session)
        alpha_sites = _site_profiles(session, alpha.tenant_id)
        assert len(alpha_sites) == 3
        # Site « salle de restauration » d'une entreprise de quincaillerie : profil de
        # l'entreprise, jamais déduit de la nature du site.
        assert set(alpha_sites.values()) == {"retail.quincaillerie"}
        assert set(_site_profiles(session, beta.tenant_id).values()) == {"restaurant.maquis"}
        # Aucun site sans profil, dans toute la base.
        assert (
            session.execute(text(f"SELECT count(*) FROM sites WHERE {COLUMN} IS NULL")).scalar_one()
            == 0
        )
        # Contraintes : NOT NULL, clé étrangère ON DELETE RESTRICT, index.
        nullable = session.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'sites' AND column_name = :c"
            ),
            {"c": COLUMN},
        ).scalar_one()
        assert nullable == "NO"
        fk = session.execute(
            text(
                "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
                "WHERE conname = 'fk_sites_business_profile_code_business_profiles'"
            )
        ).one()
        assert tuple(fk) == ("business_profiles", "r")
        assert (
            session.execute(
                text("SELECT count(*) FROM pg_indexes WHERE indexname = :i"),
                {"i": "ix_sites_business_profile_code"},
            ).scalar_one()
            == 1
        )
        # RLS de ``sites`` intacte après la reprise (ENABLE + FORCE).
        rls = session.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'sites'")
        ).one()
        assert tuple(rls) == (True, True)


def test_downgrade_refuses_to_lose_a_site_specific_profile(
    provision: Any, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    alpha = provision("mig-diverge", profile="retail.alimentation")
    owner_db.execute(
        text(f"UPDATE sites SET {COLUMN} = 'restaurant.maquis' WHERE id = :s"),
        {"s": alpha.site_id},
    )
    owner_db.commit()
    owner_db.close()
    owner_engine.dispose()
    try:
        with pytest.raises(RuntimeError, match="Retour arrière refusé"):
            command.downgrade(_alembic(), "0038")
        with Session(owner_engine) as session:
            # Descente annulée en entier : colonne, valeur et FORCE conservés.
            assert _has_column(session)
            kept = session.execute(
                text(f"SELECT {COLUMN} FROM sites WHERE id = :s"), {"s": alpha.site_id}
            ).scalar_one()
            assert kept == "restaurant.maquis"
            force = session.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'sites'")
            ).scalar_one()
            assert force is True
    finally:
        with owner_engine.begin() as conn:
            conn.execute(
                text(f"UPDATE sites SET {COLUMN} = 'retail.alimentation' WHERE id = :s"),
                {"s": alpha.site_id},
            )


# --- Isolation et intégrité ---------------------------------------------------------------------


def test_rls_isolates_site_profiles_between_tenants(db: Session, provision: Any) -> None:
    alpha = provision("rls-prof-alpha", profile="retail.alimentation")
    beta = provision("rls-prof-beta", profile="restaurant.maquis")
    set_db_context(db, tenant_id=alpha.tenant_id)
    visible = db.execute(text(f"SELECT id, {COLUMN} FROM sites")).all()
    assert [(row.id, row.business_profile_code) for row in visible] == [
        (alpha.site_id, "retail.alimentation")
    ]
    # Le profil d'un site d'une autre entreprise n'est ni visible ni modifiable.
    result = db.execute(
        text(f"UPDATE sites SET {COLUMN} = 'retail.alimentation' WHERE id = :s"),
        {"s": beta.site_id},
    )
    assert result.rowcount == 0  # type: ignore[attr-defined]
    db.commit()
    set_db_context(db, tenant_id=beta.tenant_id)
    assert (
        db.execute(
            text(f"SELECT {COLUMN} FROM sites WHERE id = :s"), {"s": beta.site_id}
        ).scalar_one()
        == "restaurant.maquis"
    )


def test_site_profile_must_exist_in_the_catalogue_and_is_mandatory(
    db: Session, provision: Any
) -> None:
    alpha = provision("int-prof", profile="retail.alimentation")
    set_db_context(db, tenant_id=alpha.tenant_id)
    with pytest.raises(IntegrityError, match="fk_sites_business_profile_code_business_profiles"):
        db.execute(
            text(f"UPDATE sites SET {COLUMN} = 'inconnu.profil' WHERE id = :s"),
            {"s": alpha.site_id},
        )
    db.rollback()
    set_db_context(db, tenant_id=alpha.tenant_id)
    with pytest.raises(IntegrityError, match=COLUMN):
        db.execute(
            text(
                "INSERT INTO sites (id, tenant_id, name, code, kind, is_active) "
                "VALUES (:id, :t, 'Sans profil', 'NOP', 'store', true)"
            ),
            {"id": new_id(), "t": alpha.tenant_id},
        )
    db.rollback()


def test_site_profiles_are_independent_and_drive_their_own_capabilities(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    alpha = provision("ind-prof", profile="retail.alimentation")
    api = api_for(_owner("ind-prof"))
    created = add_site(api, "Dépôt", "DEP", "warehouse")
    assert created.status_code == 201
    other_site = uuid.UUID(created.json()["id"])
    # Profil d'UN site modifié (hors API à ce palier) : ni l'autre site ni l'entreprise.
    owner_db.execute(
        text(f"UPDATE sites SET {COLUMN} = 'distribution.entrepot' WHERE id = :s"),
        {"s": other_site},
    )
    owner_db.commit()
    profiles = _site_profiles(owner_db, alpha.tenant_id)
    assert profiles.pop("DEP") == "distribution.entrepot"
    assert set(profiles.values()) == {"retail.alimentation"}
    tenant_profile = owner_db.execute(
        text("SELECT business_profile_code FROM tenants WHERE id = :t"), {"t": alpha.tenant_id}
    ).scalar_one()
    assert tenant_profile == "retail.alimentation"
    # Palier B : les capacités d'un site suivent SON profil (Entrepôt : ni point de vente).
    api.site_id = other_site
    caps = api.get("/me/capabilities")
    assert caps.status_code == 200, caps.text
    assert caps.json()["profile"]["code"] == "distribution.entrepot"
    assert "pos" not in {m["code"] for m in caps.json()["modules"]}


def test_console_role_reads_site_profiles_but_cannot_change_them(
    platform_engine: Engine, provision: Any
) -> None:
    alpha = provision("console-prof", profile="retail.alimentation")
    with platform_engine.connect() as conn:
        profile = conn.execute(
            text(f"SELECT {COLUMN} FROM sites WHERE tenant_id = :t"), {"t": alpha.tenant_id}
        ).scalar_one()
        assert profile == "retail.alimentation"
    with platform_engine.connect() as conn, pytest.raises(Exception, match="permission denied"):
        conn.execute(
            text(f"UPDATE sites SET {COLUMN} = 'restaurant.maquis' WHERE tenant_id = :t"),
            {"t": alpha.tenant_id},
        )
