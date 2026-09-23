"""Isolation garantie par PostgreSQL lui-même (rôle applicatif, sans passer par l'API)."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.shared.ids import new_id

TENANT_TABLES = (
    "tenants",
    "sites",
    "tenant_modules",
    "tenant_memberships",
    "membership_sites",
    "membership_roles",
    "roles",
    "role_permissions",
    "subscriptions",
    "audit_logs",
)


def _count(db: Session, table: str) -> int:
    return int(db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_app_role_cannot_bypass_rls(db: Session) -> None:
    row = db.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    assert row == (False, False)


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_no_context_means_no_rows(db: Session, provision: Any, table: str) -> None:
    provision("alpha")
    assert _count(db, table) == 0


def _add_role_assignments(owner_db: Session, *tenants: Any) -> None:
    """Les propriétaires n'ont ni rôle ni site explicites : on en ajoute pour peupler
    membership_roles / membership_sites dans chaque tenant."""
    for t in tenants:
        owner_db.execute(
            text(
                "INSERT INTO membership_roles (id, tenant_id, membership_id, role_id, site_id) "
                "SELECT :id, m.tenant_id, m.id, r.id, :site FROM tenant_memberships m "
                "JOIN roles r ON r.tenant_id = m.tenant_id AND r.template_code = 'viewer' "
                "WHERE m.tenant_id = :tenant"
            ),
            {"id": new_id(), "tenant": t.tenant_id, "site": t.site_id},
        )
        owner_db.execute(
            text(
                "INSERT INTO membership_sites (tenant_id, membership_id, site_id) "
                "SELECT m.tenant_id, m.id, :site FROM tenant_memberships m "
                "WHERE m.tenant_id = :tenant"
            ),
            {"tenant": t.tenant_id, "site": t.site_id},
        )
    owner_db.commit()


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_context_limits_rows_to_active_tenant(
    db: Session, owner_db: Session, provision: Any, table: str
) -> None:
    a = provision("alpha")
    b = provision("beta")
    _add_role_assignments(owner_db, a, b)
    assert int(owner_db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()) >= 2
    set_db_context(db, tenant_id=a.tenant_id)
    rows = (
        db.execute(
            text(f"SELECT DISTINCT {'id' if table == 'tenants' else 'tenant_id'} FROM {table}")
        )
        .scalars()
        .all()
    )
    assert set(rows) == {a.tenant_id}


def test_cannot_insert_row_for_another_tenant(db: Session, provision: Any) -> None:
    a = provision("alpha")
    b = provision("beta")
    set_db_context(db, tenant_id=a.tenant_id)
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO sites (id, tenant_id, name, code, kind, is_active) "
                "VALUES (:id, :tenant, 'Intrus', 'X', 'store', true)"
            ),
            {"id": new_id(), "tenant": b.tenant_id},
        )


def test_cannot_update_rows_of_another_tenant(db: Session, provision: Any) -> None:
    a = provision("alpha")
    b = provision("beta")
    set_db_context(db, tenant_id=a.tenant_id)
    result = db.execute(text("UPDATE sites SET name = 'pirate' WHERE id = :id"), {"id": b.site_id})
    assert result.rowcount == 0  # type: ignore[attr-defined]


def test_user_sees_only_own_memberships_without_tenant(db: Session, provision: Any) -> None:
    a = provision("alpha")
    provision("beta")
    set_db_context(db, user_id=a.owner_user_id)
    tenants = db.execute(text("SELECT id FROM tenants")).scalars().all()
    assert tenants == [a.tenant_id]
    assert _count(db, "tenant_memberships") == 1
    assert _count(db, "sites") == 0  # pas de tenant actif : aucune donnée métier


def test_audit_log_is_append_only(db: Session, provision: Any) -> None:
    a = provision("alpha")
    set_db_context(db, tenant_id=a.tenant_id)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("UPDATE audit_logs SET action = 'x'"))
    db.rollback()
    set_db_context(db, tenant_id=a.tenant_id)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("DELETE FROM audit_logs"))


def test_catalog_is_read_only_for_app_role(db: Session) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("UPDATE plans SET grace_days = 999"))


def test_tenant_cannot_be_deleted_by_app_role(db: Session, provision: Any) -> None:
    a = provision("alpha")
    set_db_context(db, tenant_id=a.tenant_id)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("DELETE FROM tenants"))


def test_active_tenant_hides_own_memberships_elsewhere(db: Session, provision: Any) -> None:
    """Avec un tenant actif, l'utilisateur ne voit plus ses appartenances aux autres tenants."""
    a = provision("alpha", owner_email="multi@example.com")
    b = provision("beta", owner_email="multi@example.com")
    set_db_context(db, tenant_id=a.tenant_id, user_id=a.owner_user_id)
    assert db.execute(text("SELECT tenant_id FROM tenant_memberships")).scalars().all() == [
        a.tenant_id
    ]
    assert db.execute(text("SELECT id FROM tenants")).scalars().all() == [a.tenant_id]
    assert b.tenant_id != a.tenant_id


def test_orm_tenant_filter_works_without_rls(owner_db: Session, provision: Any) -> None:
    """Couche applicative : même avec une connexion qui ignore la RLS (superutilisateur),
    les requêtes ORM sont filtrées sur le tenant actif."""
    from sqlalchemy import select

    from app.platform.access.models import Role
    from app.platform.tenancy.models import Site

    a = provision("alpha")
    provision("beta")
    assert len(owner_db.scalars(select(Site)).all()) == 2  # pas de contexte : pas de filtre
    set_db_context(owner_db, tenant_id=a.tenant_id)
    assert {s.tenant_id for s in owner_db.scalars(select(Site))} == {a.tenant_id}
    assert {r.tenant_id for r in owner_db.scalars(select(Role))} == {a.tenant_id}
