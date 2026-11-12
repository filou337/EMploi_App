"""Lance l'appli locale :  python -m app  [--port 8765] [--sans-navigateur]

1. démarre la base Postgres (docker compose) si elle ne répond pas ;
2. applique les migrations en attente ;
3. lance le serveur sur 127.0.0.1 et ouvre le dashboard dans le navigateur.
"""

import argparse
import shutil
import subprocess
import threading
import time
import webbrowser

import psycopg
import uvicorn

from app.config import RACINE
from app.db import connexion
from app.migrations import appliquer


def base_joignable() -> bool:
    try:
        with connexion() as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.OperationalError:
        return False


def demarrer_base() -> None:
    if base_joignable():
        return
    if shutil.which("docker") is None:
        raise SystemExit("Base injoignable et Docker absent : lance Postgres puis réessaie.")
    print("Démarrage de la base Postgres (docker compose up -d)…")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=RACINE, check=True)
    for _ in range(30):
        if base_joignable():
            return
        time.sleep(1)
    raise SystemExit("La base ne répond toujours pas après 30 s (docker compose ps).")


def main() -> None:
    p = argparse.ArgumentParser(description="Appli locale EMploi_App")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--sans-navigateur", action="store_true")
    args = p.parse_args()

    demarrer_base()
    for nom in appliquer():
        print(f"Migration appliquée : {nom}")
    url = f"http://127.0.0.1:{args.port}/"
    if not args.sans_navigateur:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"EMploi_App : {url}   (Ctrl+C pour arrêter)")
    uvicorn.run("app.serveur:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
