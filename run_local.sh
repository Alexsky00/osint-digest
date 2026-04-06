#!/usr/bin/env bash
# ── Test local du digest ───────────────────────────────────────────────────
# Usage :
#   chmod +x run_local.sh
#   ./run_local.sh            → pipeline complet, email désactivé
#   ./run_local.sh send       → pipeline complet + envoi email
#   ./run_local.sh alert      → alerte rapide, email désactivé
# ──────────────────────────────────────────────────────────────────────────

set -e

ENV_FILE="$(dirname "$0")/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "❌  Fichier .env introuvable."
  echo "    Copier .env.example → .env et remplir les clés API."
  exit 1
fi

# Charger le .env (ignore lignes vides et commentaires)
while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" == \#* ]] && continue
  export "$key=$value"
done < <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')

# Surcharge selon argument
case "${1:-}" in
  send)
    export INPUT_MAIL_ENABLED=true
    echo "📧  Mode : envoi email ACTIVÉ"
    ;;
  alert)
    export INPUT_TYPE=alert
    export INPUT_MAIL_ENABLED=false
    echo "⚡  Mode : alerte (email désactivé)"
    ;;
  *)
    export INPUT_MAIL_ENABLED=false
    echo "🧪  Mode : test — email DÉSACTIVÉ (passer 'send' pour envoyer)"
    ;;
esac

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python digest.py
