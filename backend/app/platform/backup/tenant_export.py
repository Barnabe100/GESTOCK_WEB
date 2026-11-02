"""Export des données d'UNE entreprise depuis une copie restaurée (restauration par tenant).

Usage prévu (procédure ``docs/exploitation/SAUVEGARDE_RESTAURATION.md`` §6) : la base de
production n'est JAMAIS restaurée en place pour une seule entreprise. On restaure le cluster à
l'instant voulu dans une instance À PART (``ops/backup/sm-restore-pitr.sh``), puis on extrait les
lignes de l'entreprise avec cette commande, branchée sur la COPIE (``--database-url``).

- Lecture seule (transaction ``READ ONLY``), rôle propriétaire de la copie.
- Toutes les tables portant ``tenant_id`` (découvertes dans le schéma : aucune table oubliée
  quand un module en ajoute), sauf le journal de la console TechNova (``platform_audit_logs``,
  données de la plateforme) ; la ligne ``tenants`` de l'entreprise ; les identités de ses membres
  SANS ``password_hash`` (jamais exporté).
- Un fichier CSV par table (en-tête, ordre stable par clé primaire), ``manifest.json`` :
  révision Alembic, instant, nombre de lignes et empreinte SHA-256 de chaque fichier.
- Répertoire de sortie neuf (refus s'il existe et n'est pas vide), droits 0700 / 0600.

La RÉINTÉGRATION dans la production n'est pas automatisée : elle se décide et se fait au cas par
cas (conflits d'identifiants, séquences, documents numérotés), sous contrôle humain.
"""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

#: Tables portant ``tenant_id`` qui n'appartiennent pas aux données de l'entreprise.
PLATFORM_TABLES = frozenset({"platform_audit_logs"})
#: Colonnes jamais exportées (secrets), quelle que soit la table.
SECRET_COLUMNS = frozenset({"password_hash"})
USER_COLUMNS = ("id", "email", "full_name", "created_at", "updated_at")


class TenantExportError(Exception):
    pass


@dataclass(frozen=True)
class ExportedTable:
    name: str
    rows: int
    sha256: str


def _tenant_tables(conn: Any) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT c.table_name FROM information_schema.columns c "
            "JOIN information_schema.tables t ON t.table_schema = c.table_schema "
            "AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE' "
            "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' "
            "ORDER BY c.table_name"
        )
    ).scalars()
    return [name for name in rows if name not in PLATFORM_TABLES]


def _columns(conn: Any, table: str) -> list[str]:
    names = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
        ),
        {"t": table},
    ).scalars()
    return [n for n in names if n not in SECRET_COLUMNS]


def _primary_key(conn: Any, table: str) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = CAST(:t AS regclass) "
                "AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum)"
            ),
            {"t": f"public.{table}"},
        ).scalars()
    )


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise TenantExportError(f"identifiant SQL inattendu : {name!r}")
    return f'"{name}"'


def _copy(conn: Any, query: str, params: dict[str, Any], target: Path) -> ExportedTable:
    # COPY ne prend pas de paramètres liés : la requête est rendue avec des littéraux sûrs
    # (identifiants validés, UUID de l'entreprise typé).
    rendered = str(text(query).bindparams(**params).compile(compile_kwargs={"literal_binds": True}))
    raw = conn.connection.driver_connection
    digest = hashlib.sha256()
    count = 0
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    sql = f"COPY ({rendered}) TO STDOUT WITH (FORMAT csv, HEADER true)"
    with os.fdopen(fd, "wb") as out, raw.cursor() as cursor, cursor.copy(sql) as copy:
        for chunk in copy:
            data = bytes(chunk)
            out.write(data)
            digest.update(data)
            count += data.count(b"\n")
    # En-tête compris : une ligne de moins (champ multiligne : comptage indicatif, l'empreinte
    # fait foi).
    return ExportedTable(target.stem, max(count - 1, 0), digest.hexdigest())


def export_tenant(engine: Engine, tenant_id: uuid.UUID, out_dir: Path) -> dict[str, Any]:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise TenantExportError(f"{out_dir} n'est pas vide")
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_dir, 0o700)
    tables: list[ExportedTable] = []
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        exists = conn.execute(
            text("SELECT name FROM tenants WHERE id = :t"), {"t": tenant_id}
        ).scalar_one_or_none()
        if exists is None:
            raise TenantExportError(f"entreprise {tenant_id} absente de cette base")
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tid = {"t": str(tenant_id)}
        tables.append(
            _copy(
                conn,
                "SELECT * FROM tenants WHERE id = CAST(:t AS uuid)",
                tid,
                out_dir / "tenants.csv",
            )
        )
        user_cols = ", ".join(_ident(c) for c in USER_COLUMNS)
        tables.append(
            _copy(
                conn,
                # Identifiants validés par _ident, entreprise liée en paramètre.
                f"SELECT {user_cols} FROM users WHERE id IN (SELECT user_id FROM "  # nosec B608
                "tenant_memberships WHERE tenant_id = CAST(:t AS uuid)) ORDER BY id",
                tid,
                out_dir / "users.csv",
            )
        )
        for table in _tenant_tables(conn):
            cols = ", ".join(_ident(c) for c in _columns(conn, table))
            order = ", ".join(_ident(c) for c in _primary_key(conn, table)) or "1"
            tables.append(
                _copy(
                    conn,
                    # Tables et colonnes du schéma, validées par _ident ; entreprise liée.
                    f"SELECT {cols} FROM {_ident(table)} WHERE tenant_id = CAST(:t AS uuid) "  # nosec B608
                    f"ORDER BY {order}",
                    tid,
                    out_dir / f"{table}.csv",
                )
            )
        conn.rollback()
    manifest = {
        "tenant_id": str(tenant_id),
        "tenant_name": exists,
        "alembic_revision": revision,
        "exported_at": datetime.now(UTC).isoformat(),
        "excluded": sorted(PLATFORM_TABLES),
        "secret_columns_excluded": sorted(SECRET_COLUMNS),
        "tables": [{"name": t.name, "rows": t.rows, "sha256": t.sha256} for t in tables],
    }
    path = out_dir / "manifest.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return manifest
