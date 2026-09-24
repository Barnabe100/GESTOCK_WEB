"""RBAC : rôles de base, rôles personnalisés actifs/inactifs, jamais supprimés (ADR-0015).

- ``roles.is_active`` ; contrôle « rôle système ⇔ code de modèle » ;
- nom des rôles personnalisés unique par tenant **sans distinction de casse** ; un seul
  exemplaire de chaque rôle de base par tenant ;
- rôles de base : ``stock_manager`` devient ``manager`` (Gestionnaire), ``viewer`` est renommé
  Consultant, ``seller`` (Vendeur) est ajouté à chaque entreprise existante ;
- le rôle applicatif perd ``DELETE`` sur ``roles`` (désactivation au lieu de suppression).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-24 09:13:02.254998+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copie figée des noms et descriptions (une migration ne dépend pas du code applicatif). Les
# rôles de base affichent de toute façon le nom et la description de leur modèle à l'exécution.
MANAGER = (
    "Gestionnaire",
    "Catalogue, fournisseurs, stock (entrées, sorties, seuils) et alertes, sans annulation ni "
    "gestion des motifs de sortie.",
)
VIEWER = (
    "Consultant",
    "Consultation, sans accès aux utilisateurs, aux rôles, au journal d'audit ni aux modules.",
)
SELLER = (
    "Vendeur",
    "Consultation des articles, du stock et des alertes (droits de vente ajoutés avec le module "
    "Ventes).",
)
ADMINISTRATOR = (
    "Administrateur",
    "Administration complète de l'entreprise (tous les modules de l'offre).",
)
BASE_ROLES = {
    "administrator": ADMINISTRATOR,
    "manager": MANAGER,
    "seller": SELLER,
    "viewer": VIEWER,
}
OLD_MANAGER = (
    "Gestionnaire de stock",
    "Catalogue, fournisseurs, entrées/sorties et inventaires (sans annulation ni motifs de "
    "sortie).",
)
OLD_VIEWER = ("Consultation", "Accès en lecture seule.")


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _without_forced_rls(*tables: str) -> tuple[list[str], list[str]]:
    """La migration s'exécute en propriétaire des tables : le FORCE de la RLS est levé le temps
    des opérations de données, dans cette même transaction (le rôle applicatif n'est jamais
    concerné, et ne reçoit jamais BYPASSRLS)."""
    return (
        [f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY" for t in tables],
        [f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY" for t in tables],
    )


def _update_role(template_code: str, name_description: tuple[str, str], new_code: str) -> None:
    op.execute(
        sa.text(
            "UPDATE roles SET template_code = :new_code, name = :name, description = :description "
            "WHERE template_code = :code"
        ).bindparams(
            code=template_code,
            new_code=new_code,
            name=name_description[0],
            description=name_description[1],
        )
    )


def upgrade() -> None:
    disable, enable = _without_forced_rls("tenants", "roles", "membership_roles")
    for statement in disable:
        op.execute(statement)

    # Deux rôles personnalisés dont les noms ne diffèrent que par la casse : arrêt explicite
    # (aucun renommage silencieux des données d'un tenant).
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT tenant_id, lower(name) FROM roles WHERE NOT is_system "
                "GROUP BY tenant_id, lower(name) HAVING count(*) > 1"
            )
        )
        .all()
    )
    if duplicates:
        raise RuntimeError(
            "Rôles personnalisés en double (casse ignorée) ; renommez-les avant la migration : "
            + ", ".join(f"{tenant}:{name}" for tenant, name in duplicates)
        )

    op.add_column(
        "roles",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.drop_constraint(op.f("uq_roles_tenant_id_name"), "roles", type_="unique")
    op.create_index(
        "uq_roles_tenant_custom_name",
        "roles",
        ["tenant_id", sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("NOT is_system"),
    )
    op.create_index(
        "uq_roles_tenant_template",
        "roles",
        ["tenant_id", "template_code"],
        unique=True,
        postgresql_where=sa.text("template_code IS NOT NULL"),
    )
    op.create_check_constraint(
        op.f("ck_roles_system_has_template"), "roles", "is_system = (template_code IS NOT NULL)"
    )

    # Rôles de base : identifiants (donc attributions) inchangés.
    _update_role("stock_manager", MANAGER, "manager")
    _update_role("viewer", VIEWER, "viewer")
    # Chaque entreprise dispose des quatre rôles de base (Vendeur : nouveau ; Gestionnaire et
    # autres : entreprises créées avant l'apparition du modèle).
    for code, (name, description) in BASE_ROLES.items():
        op.execute(
            sa.text(
                "INSERT INTO roles (id, tenant_id, name, description, template_code, is_system, "
                "is_active, created_at, updated_at) "
                "SELECT gen_random_uuid(), t.id, :name, :description, :code, true, true, now(), "
                "now() FROM tenants t "
                "WHERE NOT EXISTS (SELECT 1 FROM roles r WHERE r.tenant_id = t.id "
                "AND r.template_code = :code)"
            ).bindparams(code=code, name=name, description=description)
        )

    for statement in enable:
        op.execute(statement)
    op.execute(f"REVOKE DELETE ON roles FROM {_app_role()}")


def downgrade() -> None:
    op.execute(f"GRANT DELETE ON roles TO {_app_role()}")
    disable, enable = _without_forced_rls("tenants", "roles", "membership_roles")
    for statement in disable:
        op.execute(statement)
    # Retour arrière (développement) : le rôle Vendeur et ses attributions disparaissent.
    op.execute(
        "DELETE FROM membership_roles WHERE role_id IN "
        "(SELECT id FROM roles WHERE template_code = 'seller')"
    )
    op.execute("DELETE FROM roles WHERE template_code = 'seller'")
    _update_role("manager", OLD_MANAGER, "stock_manager")
    _update_role("viewer", OLD_VIEWER, "viewer")
    for statement in enable:
        op.execute(statement)

    op.drop_constraint(op.f("ck_roles_system_has_template"), "roles", type_="check")
    op.drop_index(
        "uq_roles_tenant_template",
        table_name="roles",
        postgresql_where=sa.text("template_code IS NOT NULL"),
    )
    op.drop_index(
        "uq_roles_tenant_custom_name",
        table_name="roles",
        postgresql_where=sa.text("NOT is_system"),
    )
    op.create_unique_constraint(op.f("uq_roles_tenant_id_name"), "roles", ["tenant_id", "name"])
    op.drop_column("roles", "is_active")
