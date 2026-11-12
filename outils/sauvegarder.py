"""Sauvegarde la base Supabase (schéma public + données) dans sauvegardes/, compressée.

Usage :  python outils/sauvegarder.py
Le fichier contient tes données personnelles : il reste sur ce PC (sauvegardes/ est ignoré par git,
droits 600). Restauration : zcat fichier.sql.gz | psql "$SUPABASE_DATABASE_URL".
"""

import gzip
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

RACINE = Path(__file__).resolve().parent.parent
url = (dotenv_values(RACINE / ".env").get("SUPABASE_DATABASE_URL") or "").strip()
if not url.startswith("postgresql://"):
    sys.exit("SUPABASE_DATABASE_URL absente ou invalide dans .env")
dossier = RACINE / "sauvegardes"
dossier.mkdir(mode=0o700, exist_ok=True)
fichier = dossier / f"supabase_{datetime.now():%Y-%m-%d_%H%M%S}.sql.gz"
r = subprocess.run(
    ["docker", "run", "--rm", "--network", "host", "-e", "CIBLE", "postgres:17-alpine",
     "sh", "-c", 'pg_dump "$CIBLE" --schema=public --no-owner --no-privileges'],
    env={**os.environ, "CIBLE": url}, capture_output=True, check=False,
)
if r.returncode:
    sys.exit("Échec : " + r.stderr.decode(errors="replace").replace(url, "<URL>")[-500:])
with gzip.open(fichier, "wb") as f:
    f.write(r.stdout)
os.chmod(fichier, 0o600)
tables = [l.split()[1] for l in r.stdout.decode().splitlines() if l.startswith("COPY public.")]
print(f"Sauvegarde : {fichier.relative_to(RACINE)} ({fichier.stat().st_size // 1024} Ko, {len(tables)} tables)")
