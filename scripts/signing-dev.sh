#!/usr/bin/env bash
# Signing Service de DÉVELOPPEMENT / TEST (ADR-0034) — jamais en production.
#
# Prépare une paire de clés Ed25519 ÉPHÉMÈRE et un secret HMAC HORS DU DÉPÔT, démarre le service
# local, attend qu'il SIGNE réellement (pas seulement qu'il démarre), puis affiche les variables
# à donner à la console TechNova et à l'API.
#
#   scripts/signing-dev.sh start      # prépare (si besoin) et démarre ; écrit $DIR/signing.env
#   scripts/signing-dev.sh check      # contrôle de bout en bout (stockmanager signing check)
#   scripts/signing-dev.sh stop       # arrête le service
#   scripts/signing-dev.sh reset      # arrête et efface les clés éphémères (nouvelle paire)
#
# Variables : SM_SIGNING_DEV_DIR (défaut : ${XDG_RUNTIME_DIR:-/tmp}/sm-signing-dev),
#             SM_SIGNING_DEV_PORT (défaut : 8100), SM_SIGNING_DEV_KEY_ID (défaut : dev-ed25519-local).
# Les clés produites ici ne servent qu'au développement et aux tests : le trousseau versionné
# (backend/app/platform/licensing/data/public_keys.toml) ne contient JAMAIS leur clé publique.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${SM_SIGNING_DEV_DIR:-${XDG_RUNTIME_DIR:-/tmp}/sm-signing-dev}"
PORT="${SM_SIGNING_DEV_PORT:-8100}"
KEY_ID="${SM_SIGNING_DEV_KEY_ID:-dev-ed25519-local}"
URL="http://127.0.0.1:${PORT}"

refuse_repository() {
  local target
  target="$(mkdir -p "$1" && cd "$1" && pwd)"
  case "$target/" in
    "$ROOT"/*) echo "refus : $target est dans le dépôt (les clés ne s'y écrivent jamais)" >&2; exit 2 ;;
  esac
  if git -C "$target" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "refus : $target est dans un dépôt Git" >&2; exit 2
  fi
}

prepare() {
  refuse_repository "$DIR"
  chmod 700 "$DIR"
  if [[ ! -f "$DIR/private.pem" ]]; then
    (cd "$ROOT/signing-service" && uv run --frozen signing-service-keygen \
      --key-id "$KEY_ID" --out "$DIR/private.pem") | grep -v '^#' > "$DIR/public_keys.toml"
  fi
  if [[ ! -f "$DIR/client-secret" ]]; then
    (umask 077 && head -c 48 /dev/urandom | base64 | tr -d '\n' > "$DIR/client-secret")
  fi
  chmod 600 "$DIR/private.pem" "$DIR/client-secret" "$DIR/public_keys.toml"
  # Fichier d'environnement de la console et de l'API (développement / tests seulement).
  (umask 077 && cat > "$DIR/signing.env" <<ENV
SM_SIGNING_SERVICE_URL=$URL
SM_SIGNING_CLIENT_SECRET=$(cat "$DIR/client-secret")
SM_LICENSE_PUBLIC_KEYS_FILE=$DIR/public_keys.toml
ENV
  )
}

running() { [[ -f "$DIR/service.pid" ]] && kill -0 "$(cat "$DIR/service.pid")" 2>/dev/null; }

start() {
  prepare
  if running; then echo "déjà démarré ($URL)"; else
    # Processus entièrement détaché (aucun descripteur hérité) : l'appelant rend la main.
    nohup env SIGNING_KEY_ID="$KEY_ID" SIGNING_PRIVATE_KEY_FILE="$DIR/private.pem" \
      SIGNING_CLIENT_SECRET_FILE="$DIR/client-secret" \
      uv run --directory "$ROOT/signing-service" --frozen uvicorn \
        --factory signing_service.app:create_app --host 127.0.0.1 --port "$PORT" \
        < /dev/null > "$DIR/service.log" 2>&1 &
    echo $! > "$DIR/service.pid"
    disown
  fi
  for _ in $(seq 1 60); do
    if curl -fsS "$URL/health" >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
  curl -fsS "$URL/health" >/dev/null || { echo "service injoignable ; voir $DIR/service.log" >&2; exit 1; }
  check
  echo "Variables (console TechNova, API) : set -a; . $DIR/signing.env; set +a"
}

check() {
  # Vraie signature + vérification par le trousseau : prouve service, secret et clés cohérents.
  (cd "$ROOT/backend" && set -a && . "$DIR/signing.env" && set +a && uv run stockmanager signing check)
}

stop() {
  if running; then kill "$(cat "$DIR/service.pid")" && rm -f "$DIR/service.pid" && echo "arrêté"; else echo "non démarré"; fi
}

case "${1:-}" in
  start) start ;;
  check) check ;;
  stop) stop ;;
  reset) stop; refuse_repository "$DIR"; rm -f "$DIR"/{private.pem,client-secret,public_keys.toml,signing.env,service.log}; echo "clés éphémères effacées" ;;
  *) sed -n '2,16p' "$0"; exit 64 ;;
esac
