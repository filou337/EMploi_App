#!/usr/bin/env bash
# Envoie la configuration du PC dans les « Secrets » chiffrés du dépôt GitHub, pour que les agents
# tournent 24 h/24 avec GitHub Actions (.github/workflows/agents.yml).
#
# Ce qui part (chiffré, jamais affiché, illisible même depuis GitHub une fois enregistré) :
#   - DATABASE_URL (Supabase), GEMINI_API_KEY, GEMINI_MODELE, GMAIL_BOITES, clés Adzuna ;
#   - secrets/client_secret.json et les jetons Gmail (lecture des boîtes, droit d'envoi) ;
#   - profil/competences.yaml (ton profil, qui n'est pas dans le dépôt).
# À relancer après chaque nouvelle autorisation Gmail (jeton expiré) ou changement de profil.
#
# Utilisation :  outils/configurer_github.sh        (-n : liste ce qui serait envoyé, sans rien envoyer)
set -euo pipefail
ESSAI=0; [ "${1:-}" = "-n" ] && ESSAI=1
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RACINE"
command -v gh >/dev/null || { echo "gh (GitHub CLI) introuvable."; exit 1; }
DEPOT="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
lire() { .venv/bin/python -c 'import sys; from dotenv import dotenv_values; print(dotenv_values(".env").get(sys.argv[1]) or "")' "$1"; }

envoyer() {  # nom, valeur (lue sur l'entrée standard, jamais en argument visible)
  local nom="$1" valeur
  valeur="$(cat)"
  if [ -z "$valeur" ]; then echo "  - $nom : vide, ignoré"; return; fi
  if [ "$ESSAI" = 1 ]; then echo "  - $nom : prêt (${#valeur} caractères)"; return; fi
  printf '%s' "$valeur" | gh secret set "$nom" --repo "$DEPOT" >/dev/null && echo "  ✓ $nom"
}
fichier() {  # nom, chemin -> base64 sur une ligne
  if [ -f "$2" ]; then base64 -w0 "$2" | envoyer "$1"; else echo "  - $1 : $2 absent, ignoré"; fi
}

echo "Dépôt : $DEPOT"
echo "Variables :"
lire DATABASE_URL | envoyer DATABASE_URL
lire GEMINI_API_KEY | envoyer GEMINI_API_KEY
lire GEMINI_MODELE | envoyer GEMINI_MODELE
lire GMAIL_BOITES | envoyer GMAIL_BOITES
lire ADZUNA_APP_ID | envoyer ADZUNA_APP_ID
lire ADZUNA_APP_KEY | envoyer ADZUNA_APP_KEY
echo "Fichiers :"
fichier GOOGLE_CLIENT_SECRET_B64 secrets/client_secret.json
fichier GMAIL_TOKEN_PRINCIPALE_B64 secrets/token.json
fichier GMAIL_TOKEN_ANCIENNE_B64 secrets/token_ancienne.json
fichier GMAIL_TOKEN_ENVOI_B64 secrets/token_envoi.json
fichier PROFIL_YAML_B64 profil/competences.yaml
case "$(lire DATABASE_URL)" in *supabase*) ;; *) echo "⚠ DATABASE_URL ne pointe pas vers Supabase : GitHub ne pourra pas joindre ta base locale.";; esac
[ "$ESSAI" = 1 ] && echo "(essai : rien n'a été envoyé)" || echo "Terminé. Lancer un passage : gh workflow run agents.yml"
