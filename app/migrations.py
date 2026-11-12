"""Applique les migrations SQL de sql/migrations/ qui ne l'ont pas encore été.

Usage :  python -m app.migrations
schema.sql crée la base de départ (premier démarrage Docker) ; chaque fichier NNN_*.sql
le fait évoluer. Les migrations passées sont notées dans la table schema_migrations.
"""

from app.config import RACINE
from app.db import connexion

DOSSIER = RACINE / "sql" / "migrations"


def appliquer() -> list[str]:
    appliquees: list[str] = []
    with connexion() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations"
            " (nom TEXT PRIMARY KEY, appliquee_le TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        deja = {r["nom"] for r in conn.execute("SELECT nom FROM schema_migrations")}
        for fichier in sorted(DOSSIER.glob("*.sql")):
            if fichier.name in deja:
                continue
            with conn.transaction():
                conn.execute(fichier.read_text())
                conn.execute("INSERT INTO schema_migrations (nom) VALUES (%s)", (fichier.name,))
            appliquees.append(fichier.name)
    return appliquees


def main() -> None:
    faites = appliquer()
    print("\n".join(f"appliquée : {n}" for n in faites) or "Base à jour.")


if __name__ == "__main__":
    main()
