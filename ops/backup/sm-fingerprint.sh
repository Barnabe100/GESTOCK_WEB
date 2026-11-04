#!/usr/bin/env bash
# Empreinte des données (R11, ADR-0058) — preuve qu'une restauration ne touche QUE son entreprise.
#
#   sm-fingerprint.sh  (variables PGHOST PGPORT PGUSER PGDATABASE : superutilisateur d'une COPIE)
#
# Une ligne par table et par entreprise : « table|entreprise|lignes|md5 » (tables d'entreprise,
# regroupées par tenant_id), « table|-|lignes|md5 » pour les autres tables du schéma public. Le
# md5 est celui de la concaténation TRIÉE des md5 de chaque ligne (indépendant de l'ordre
# physique). TOUTES les tables (plateforme comprise). Lecture seule, REPEATABLE READ. Utilisé sur les copies isolées seulement (superutilisateur de la copie).
set -euo pipefail
psql -X -w -v ON_ERROR_STOP=1 -Atq -c "
SELECT c.relname,
       EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
  FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
 ORDER BY 1" | {
  echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"
  while IFS='|' read -r t has_tenant; do
    if [[ "$has_tenant" == t ]]; then
      echo "SELECT '$t', coalesce(tenant_id::text, '-'), count(*), md5(string_agg(md5(x::text), '' ORDER BY md5(x::text))) FROM \"$t\" x GROUP BY tenant_id;"
    else
      echo "SELECT '$t', '-', count(*), coalesce(md5(string_agg(md5(x::text), '' ORDER BY md5(x::text))), '') FROM \"$t\" x;"
    fi
  done
  echo "COMMIT;"
} | psql -X -w -v ON_ERROR_STOP=1 -Atq -F '|' | sort
