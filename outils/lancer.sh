#!/usr/bin/env bash
# Lance EMploi_App : base Postgres, migrations, serveur local, puis ouvre le navigateur.
cd "$(dirname "$0")/.." || exit 1
# Appli déjà servie (service emploi-app) : on ouvre seulement le navigateur.
if curl -fsS -o /dev/null --max-time 2 http://127.0.0.1:8765/ 2>/dev/null; then
  exec xdg-open http://127.0.0.1:8765/
fi
exec .venv/bin/python -m app "$@"
