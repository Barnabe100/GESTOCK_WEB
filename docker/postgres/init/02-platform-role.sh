#!/usr/bin/env bash
# Crée le rôle SQL de la console d'administration TechNova (ADR-0031).
#
# Rôle distinct du rôle applicatif des tenants : ni superutilisateur, ni propriétaire des
# tables, ni BYPASSRLS. Ses droits (minimaux, sans accès aux données des tenants) sont
# accordés par les migrations Alembic (0017).
#
# Exécuté automatiquement par l'image postgres au premier démarrage
# (/docker-entrypoint-initdb.d) ; réutilisable ailleurs (CI, volume existant) avec
# PGHOST/PGPASSWORD.
set -euo pipefail

PLATFORM_ROLE="${POSTGRES_PLATFORM_USER:-stockmanager_platform}"
PLATFORM_PASSWORD="${POSTGRES_PLATFORM_PASSWORD:?POSTGRES_PLATFORM_PASSWORD doit être défini}"

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER}" \
  --dbname "${POSTGRES_DB}" \
  -v platform_role="${PLATFORM_ROLE}" \
  -v platform_password="${PLATFORM_PASSWORD}" <<'EOSQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
  :'platform_role', :'platform_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'platform_role')
\gexec
EOSQL
