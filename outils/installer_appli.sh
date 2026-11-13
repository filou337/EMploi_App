#!/usr/bin/env bash
# Installe le travailleur du PC en service permanent (systemd utilisateur, sans droits admin) :
# il démarre avec ta session, exécute les demandes de l'appli (analyses ATS, matching, dépôt de CV,
# Excel, passage des agents) et redémarre seul s'il plante.
# Installe aussi l'appli locale en service permanent (http://127.0.0.1:8765, sans rien lancer).
# Désinstaller : systemctl --user disable --now emploi-travailleur.service emploi-app.service
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
CIBLE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$CIBLE"
# Appli locale (tableau de bord sur http://127.0.0.1:8765), toujours disponible.
cat > "$CIBLE/emploi-app.service" <<SERVICE
[Unit]
Description=EMploi_App - appli locale (http://127.0.0.1:8765)
After=network-online.target

[Service]
WorkingDirectory=$RACINE
ExecStart=$RACINE/.venv/bin/python -m app --sans-navigateur
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
SERVICE
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
systemctl --user enable --now emploi-travailleur.service emploi-app.service
systemctl --user restart emploi-app.service  # recharge le code après une mise à jour
systemctl --user --no-pager status emploi-travailleur.service | head -5
systemctl --user --no-pager status emploi-app.service | head -5
echo "Appli : http://127.0.0.1:8765"
echo "Journal : journalctl --user -u emploi-travailleur.service -f"
