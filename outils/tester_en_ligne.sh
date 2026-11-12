#!/usr/bin/env bash
# Teste le mode en ligne SANS rien publier : sert une copie de l'interface sur ce PC
# (http://127.0.0.1:8899/), branchée sur Supabase avec la clé publique de .env.
# Connecte-toi avec ton compte pour vérifier lecture, création et modification. Ctrl+C pour arrêter.
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
lire() { "$RACINE/.venv/bin/python" -c 'import sys; from dotenv import dotenv_values; print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or "")' "$RACINE/.env" "$1"; }
COPIE="$(mktemp -d)"; trap 'rm -rf "$COPIE"' EXIT
cp -r "$RACINE/app/web/." "$COPIE/"
cat > "$COPIE/config.js" <<CONF
window.EMPLOI_CONFIG = { supabaseUrl: "$(lire SUPABASE_URL)", supabaseAnonKey: "$(lire SUPABASE_ANON_KEY)", urlPublique: "http://127.0.0.1:8899/" };
CONF
"$RACINE/.venv/bin/python" -c "import qrcode, qrcode.image.svg; qrcode.make('http://127.0.0.1:8899/', image_factory=qrcode.image.svg.SvgPathImage).save('$COPIE/qr.svg')"
echo "Test du mode en ligne : http://127.0.0.1:8899/  (Ctrl+C pour arrêter)"
cd "$COPIE" && exec python3 -m http.server 8899 --bind 127.0.0.1
