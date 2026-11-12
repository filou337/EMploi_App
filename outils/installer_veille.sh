#!/usr/bin/env bash
# Installe la veille automatique : l'orchestrateur tourne toutes les 60 min tant que le PC est
# allumé, même appli fermée (timer systemd de l'utilisateur, sans droits administrateur).
# Désinstaller : systemctl --user disable --now emploi-veille.timer
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
CIBLE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$CIBLE"
cat > "$CIBLE/emploi-veille.service" <<SERVICE
[Unit]
Description=EMploi_App - passage des agents (collecte, validité, fiches, veille, matching)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$RACINE
ExecStart=$RACINE/.venv/bin/python -m app.orchestrateur
TimeoutStartSec=45min
Nice=10
SERVICE
cat > "$CIBLE/emploi-veille.timer" <<TIMER
[Unit]
Description=EMploi_App - veille toutes les 60 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=60min
Persistent=true

[Install]
WantedBy=timers.target
TIMER
systemctl --user daemon-reload
systemctl --user enable --now emploi-veille.timer
systemctl --user list-timers emploi-veille.timer --no-pager
echo "Journal : journalctl --user -u emploi-veille.service -n 50"
