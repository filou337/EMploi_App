#!/usr/bin/env bash
# Publie l'interface d'EMploi_App sur GitHub Pages (comme « à deux. ») :
#   1. vérifie la syntaxe JavaScript ;
#   2. écrit config.js (URL Supabase + clé publique « anon », lues dans .env) ;
#   3. change la version du service worker (les téléphones reçoivent la nouvelle interface) ;
#   4. génère le QR code de l'adresse publique ;
#   5. copie app/web/ dans le dépôt public, enregistre et pousse (après confirmation).
#
# Utilisation :  outils/publier.sh "Ce qui a changé"   (-y : sans confirmation, -n : essai à blanc)
# Le dépôt public ne contient QUE l'interface : aucune donnée, aucun secret.
set -euo pipefail
OUI=0; ESSAI=0
while getopts "yn" o; do case $o in y) OUI=1;; n) ESSAI=1;; *) exit 2;; esac; done
shift $((OPTIND-1))
MSG="${1:-Mise à jour de l’interface}"
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
lire() { "$RACINE/.venv/bin/python" -c 'import sys; from dotenv import dotenv_values; print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or "")' "$RACINE/.env" "$1"; }
SUPABASE_URL="$(lire SUPABASE_URL)"; ANON="$(lire SUPABASE_ANON_KEY)"
DEPOT="$(lire PAGES_DEPOT)"; DEPOT="${DEPOT:-https://github.com/filou337/emploi.git}"
SITE="$(lire PAGES_URL)"; SITE="${SITE:-https://filou337.github.io/emploi/}"
COPIE="${EMPLOI_SITE_DIR:-$HOME/.cache/emploi/site}"
etape(){ printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
stop(){ printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

etape "Vérifications"
[ -n "$SUPABASE_URL" ] && [ -n "$ANON" ] || stop "SUPABASE_URL et SUPABASE_ANON_KEY manquent dans .env."
case "$ANON" in *service_role*) stop "Ce n'est pas la clé « anon » : ne publie jamais la clé service_role.";; esac
python3 - "$ANON" <<'PY' || stop "La clé fournie n'est pas une clé « anon » (rôle différent) : publication annulée."
import base64, json, sys
cle = sys.argv[1]
if cle.count(".") == 2:  # ancien format JWT : on lit le rôle
    corps = cle.split(".")[1]; corps += "=" * (-len(corps) % 4)
    sys.exit(0 if json.loads(base64.urlsafe_b64decode(corps)).get("role") == "anon" else 1)
sys.exit(0 if cle.startswith("sb_publishable_") else 1)  # nouveau format de clé publique
PY
for f in app.js donnees.js sw.js; do node --check "$RACINE/app/web/$f" || stop "Erreur de syntaxe dans $f"; done
echo "Syntaxe JavaScript : OK"

etape "Préparation de la copie publique"
if [ -d "$COPIE/.git" ]; then git -C "$COPIE" pull -q --ff-only 2>/dev/null || true
else mkdir -p "$(dirname "$COPIE")"; git clone -q "$DEPOT" "$COPIE" || stop "Impossible de cloner $DEPOT (dépôt créé ?)"; fi
find "$COPIE" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -r "$RACINE/app/web/." "$COPIE/"
cat > "$COPIE/config.js" <<CONF
// Généré par outils/publier.sh. Valeurs publiques par conception (la RLS protège les données).
window.EMPLOI_CONFIG = {
  supabaseUrl: "$SUPABASE_URL",
  supabaseAnonKey: "$ANON",
  urlPublique: "$SITE",
};
CONF
VERSION="emploi-$(date +%Y%m%d-%H%M%S)"
sed -i "s/^const VERSION = \".*\";/const VERSION = \"$VERSION\";/" "$COPIE/sw.js"
"$RACINE/.venv/bin/python" - "$SITE" "$COPIE/qr.svg" "$RACINE/outils/qr_emploi.svg" <<'PY'
import sys
import qrcode
import qrcode.image.svg
site, *fichiers = sys.argv[1:]
for f in fichiers:
    qrcode.make(site, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2).save(f)
PY
touch "$COPIE/.nojekyll"
git -C "$COPIE" add -A
echo "Version : $VERSION · Adresse : $SITE"
git -C "$COPIE" status --short | head -20

if [ "$ESSAI" = 1 ]; then echo "(essai à blanc : rien n'est envoyé)"; exit 0; fi
if [ "$OUI" = 0 ]; then read -r -p "Publier sur $SITE ? [o/N] " R; [ "$R" = o ] || [ "$R" = O ] || stop "Annulé."; fi
etape "Envoi"
git -C "$COPIE" commit -q -m "$MSG" || echo "(rien de nouveau)"
git -C "$COPIE" branch -M main
git -C "$COPIE" push -q -u origin main
etape "Attente de la mise en ligne"
for i in $(seq 1 30); do
  if curl -fsS "$SITE" 2>/dev/null | grep -q "EMploi_App"; then echo "En ligne : $SITE"; exit 0; fi
  sleep 10
done
echo "Pas encore visible : vérifie Settings → Pages du dépôt (branche main, dossier racine)."
