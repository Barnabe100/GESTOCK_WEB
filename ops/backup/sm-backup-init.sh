#!/usr/bin/env bash
# Initialise (ou contrôle) la racine des sauvegardes — idempotent, ne supprime jamais rien.
#
#   SM_BACKUP_DIR=/backups sm-backup-init.sh        (dans le conteneur db, utilisateur postgres)
#
# Crée l'arborescence et les droits décrits dans lib.sh, puis le marqueur .sm-backup-root.
# La racine doit déjà exister (répertoire de l'hôte monté) et appartenir à l'utilisateur
# courant : un montage absent n'est jamais « réparé » par la création d'un répertoire local.
set -euo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"
SM_OP=init

root="${SM_BACKUP_DIR:?SM_BACKUP_DIR doit être défini}"
[[ "$root" == /* ]] || sm_die "SM_BACKUP_DIR doit être un chemin absolu"
[[ -d "$root" && ! -L "$root" ]] || sm_die "$root absent ou lien symbolique : créer et monter le répertoire de l'hôte d'abord"
[[ "$(stat -c %u "$root")" == "$(id -u)" ]] || sm_die "$root n'appartient pas à l'utilisateur $(id -un) (uid $(id -u)) : chown sur l'hôte"
root="$(realpath "$root")"

chmod 0711 "$root"
for d in $(sm_subdirs); do
  if [[ -L "$root/$d" ]]; then sm_die "$root/$d est un lien symbolique : refus"; fi
  mkdir -p "$root/$d"
  chmod 0700 "$root/$d"
done
chmod 0755 "$root/status"
if [[ ! -f "$root/$SM_ROOT_MARKER" ]]; then
  printf 'stockmanager-backup-root\ncreated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$root/$SM_ROOT_MARKER"
  chmod 0600 "$root/$SM_ROOT_MARKER"
  echo "racine initialisée : $root"
else
  echo "racine déjà initialisée : $root (droits contrôlés)"
fi
SM_ROOT="$root"
sm_inventory_write
