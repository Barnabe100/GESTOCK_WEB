#!/usr/bin/env bash
# Crée le rôle SQL de purge des notifications des membres (ADR-0049 D8, palier R4).
#
# Rôle distinct du rôle applicatif et de celui de la console : ni superutilisateur, ni
# propriétaire des tables, ni BYPASSRLS. Ses droits (minimaux : lire l'identifiant et la date des
# notifications de plus de 30 jours et les supprimer, rien d'autre) sont accordés par la
# migration Alembic 0045, qui vérifie sa présence.
#
# Exécuté automatiquement par l'image postgres au premier démarrage
# (/docker-entrypoint-initdb.d) ; réutilisable ailleurs (CI, volume existant) avec
# PGHOST/PGPASSWORD.
set -euo pipefail

PURGE_ROLE="${POSTGRES_PURGE_USER:-stockmanager_purge}"
PURGE_PASSWORD="${POSTGRES_PURGE_PASSWORD:?POSTGRES_PURGE_PASSWORD doit être défini}"

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER}" \
  --dbname "${POSTGRES_DB}" \
  -v purge_role="${PURGE_ROLE}" \
  -v purge_password="${PURGE_PASSWORD}" <<'EOSQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
  :'purge_role', :'purge_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'purge_role')
\gexec
EOSQL
