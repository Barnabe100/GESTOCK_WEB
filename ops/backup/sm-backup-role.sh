#!/usr/bin/env bash
# Rôle PostgreSQL dédié aux sauvegardes — idempotent, à lancer par un SUPERUTILISATEUR une fois.
#
#   sm-backup-role.sh [--password-file FICHIER]        (conteneur db : -u postgres, socket locale)
#
# Crée ou corrige stockmanager_backup (SM_BACKUP_PGUSER) avec les droits STRICTEMENT nécessaires :
#   LOGIN REPLICATION       pg_basebackup (protocole de réplication) ;
#   NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS, CONNECTION LIMIT 5 ;
#   EXECUTE pg_switch_wal() test de restauration : clôturer le segment courant pour mesurer la
#                           perte (aucune donnée écrite) ;
#   SELECT alembic_version  comparer la révision du schéma avec la copie restaurée.
# Aucun droit sur les données des entreprises (la RLS ne lui accorde rien).
#
# Crée aussi (R11, ADR-0058) les deux rôles des sauvegardes LOGIQUES, sans mot de passe
# (connexion par la socket locale du conteneur seulement), sans superutilisateur ni BYPASSRLS :
#   stockmanager_tenant_backup   lecture d'UNE entreprise sous RLS (SM_TENANT_BACKUP_PGUSER) ;
#   stockmanager_tenant_restore  restauration d'UNE entreprise sous RLS (SM_TENANT_RESTORE_PGUSER),
#                                SET session_replication_role accordé ici (superutilisateur).
# Leurs droits sur les tables sont accordés par la migration 0063 (à jouer APRÈS ce script si le
# propriétaire du schéma n'est pas superutilisateur). Le mot de passe (si
# l'authentification n'est pas la socket locale) est lu dans un fichier 0600, transmis par
# l'entrée standard de psql (jamais en argument), journalisation des requêtes coupée.
set -euo pipefail
ROLE="${SM_BACKUP_PGUSER:-stockmanager_backup}"
READER="${SM_TENANT_BACKUP_PGUSER:-stockmanager_tenant_backup}"
RESTORER="${SM_TENANT_RESTORE_PGUSER:-stockmanager_tenant_restore}"
DB="${SM_BACKUP_PGDATABASE:-stockmanager}"
PWFILE=""
while (( $# )); do
  case "$1" in
    --password-file) PWFILE="$2"; shift 2 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
[[ "$ROLE" =~ ^[a-z_][a-z0-9_]{0,62}$ && "$DB" =~ ^[a-z_][a-z0-9_]{0,62}$ && "$READER" =~ ^[a-z_][a-z0-9_]{0,62}$ && "$RESTORER" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "nom de rôle ou de base invalide" >&2; exit 2; }
SUPER="${SM_SUPERUSER:-${POSTGRES_USER:-postgres}}"
q() { psql -X -w -v ON_ERROR_STOP=1 -Atq -U "$SUPER" -d "$DB" "$@"; }
[[ "$(q -c "SELECT rolsuper FROM pg_roles WHERE rolname = current_user")" == t ]] \
  || { echo "à lancer avec un superutilisateur (SM_SUPERUSER)" >&2; exit 1; }
q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$ROLE') THEN CREATE ROLE $ROLE; END IF;
END \$\$;
ALTER ROLE $ROLE LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS CONNECTION LIMIT 5;
GRANT CONNECT ON DATABASE $DB TO $ROLE;
GRANT USAGE ON SCHEMA public TO $ROLE;
GRANT SELECT ON TABLE public.alembic_version TO $ROLE;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_switch_wal() TO $ROLE;
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$READER') THEN CREATE ROLE $READER; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$RESTORER') THEN CREATE ROLE $RESTORER; END IF;
END \$\$;
ALTER ROLE $READER LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION CONNECTION LIMIT 3;
ALTER ROLE $RESTORER LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION CONNECTION LIMIT 2;
GRANT CONNECT ON DATABASE $DB TO $READER, $RESTORER;
GRANT SET ON PARAMETER session_replication_role TO $RESTORER;
SQL
if [[ -n "$PWFILE" ]]; then
  [[ -f "$PWFILE" ]] || { echo "fichier de mot de passe absent" >&2; exit 1; }
  [[ "$(stat -c %a "$PWFILE")" =~ ^[46]00$ ]] || { echo "le fichier de mot de passe doit être 0600 ou 0400" >&2; exit 1; }
  pw="$(head -n 1 "$PWFILE")"
  (( ${#pw} >= 16 )) || { echo "mot de passe trop court (16 caractères minimum)" >&2; exit 1; }
  [[ "$pw" != *'$smpw$'* ]] || { echo "mot de passe refusé" >&2; exit 1; }
  printf "SET log_statement = 'none';\nSET log_min_duration_statement = -1;\nALTER ROLE %s PASSWORD \$smpw\$%s\$smpw\$;\n" "$ROLE" "$pw" | q
  unset pw
fi
q -c "SELECT 'rôle ' || rolname || ' : replication=' || rolreplication || ', superuser=' || rolsuper || ', bypassrls=' || rolbypassrls || ', login=' || rolcanlogin FROM pg_roles WHERE rolname IN ('$ROLE', '$READER', '$RESTORER') ORDER BY 1"
