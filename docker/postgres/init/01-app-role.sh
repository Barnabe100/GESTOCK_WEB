#!/usr/bin/env bash
# Crée le rôle SQL applicatif de StockManager Web.
#
# Ce rôle est utilisé par l'API : il n'est ni superutilisateur, ni propriétaire des tables,
# ni BYPASSRLS — la Row-Level Security s'applique donc à toutes ses requêtes.
# Les droits sur les tables sont accordés par les migrations Alembic.
#
# Exécuté automatiquement par l'image postgres au premier démarrage
# (/docker-entrypoint-initdb.d) ; réutilisable ailleurs (CI) avec PGHOST/PGPASSWORD.
set -euo pipefail

APP_ROLE="${POSTGRES_APP_USER:-stockmanager_app}"
APP_PASSWORD="${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD doit être défini}"

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER}" \
  --dbname "${POSTGRES_DB}" \
  -v app_role="${APP_ROLE}" \
  -v app_password="${APP_PASSWORD}" <<'EOSQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
  :'app_role', :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role')
\gexec
EOSQL
