#!/usr/bin/env bash
# Lance EMploi_App : base Postgres, migrations, serveur local, puis ouvre le navigateur.
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python -m app "$@"
