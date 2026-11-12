#!/usr/bin/env bash
# Installe le travailleur du PC en service permanent (systemd utilisateur, sans droits admin) :
# il démarre avec ta session, exécute les demandes de l'appli (analyses ATS, matching, dépôt de CV,
# Excel, passage des agents) et redémarre seul s'il plante.
# Désinstaller : systemctl --user disable --now emploi-travailleur.service
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
CIBLE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$CIBLE"
systemctl --user disable --now emploi-app.service 2>/dev/null || true   # ancienne version
cat > "$CIBLE/emploi-travailleur.service" <<SERVICE
[Unit]
Description=EMploi_App - travailleur du PC (demandes de l'appli)
After=network-online.target

[Service]
WorkingDirectory=$RACINE
ExecStart=$RACINE/.venv/bin/python -m app.travailleur
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
SERVICE
systemctl --user daemon-reload
systemctl --user enable --now emploi-travailleur.service
systemctl --user --no-pager status emploi-travailleur.service | head -5
echo "Journal : journalctl --user -u emploi-travailleur.service -f"
