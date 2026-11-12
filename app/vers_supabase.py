"""Migre la base locale (Docker) vers Supabase, applique la sécurité, puis bascule le PC dessus.

Usage :  python -m app.vers_supabase [--verifier] [--securite-seulement]

Prérequis dans .env : SUPABASE_DATABASE_URL (chaîne de connexion « Session pooler », secrète).
Étapes : 1. vérifie la connexion et que la base Supabase est vide ; 2. copie schéma + données
(pg_dump | psql) ; 3. applique sql/supabase/securite.sql ; 4. compare les comptes de lignes ;
5. dans .env, DATABASE_URL pointe désormais sur Supabase (l'ancienne valeur est gardée en
DATABASE_URL_LOCAL pour revenir en arrière). La chaîne de connexion n'est jamais affichée.
"""

import argparse
import os
import re
import subprocess
import sys

import psycopg
from dotenv import dotenv_values

from app.config import RACINE

ENV = RACINE / ".env"
TABLES = (
    "entreprises",
    "offres",
    "candidatures",
    "reponses",
    "contacts",
    "executions",
    "documents",
    "analyses",
    "veille_entreprises",
    "parametres",
    "notifications",
)


def _url_supabase() -> str:
    url = (dotenv_values(ENV).get("SUPABASE_DATABASE_URL") or "").strip()
    if url.startswith("sb_"):
        sys.exit(
            "SUPABASE_DATABASE_URL contient une clé d'API, pas la chaîne postgresql://… de la base."
        )
    if not url:
        sys.exit("SUPABASE_DATABASE_URL absente de .env (chaîne « Session pooler » de Supabase).")
    if "[YOUR-PASSWORD]" in url:
        sys.exit(
            "Remplace [YOUR-PASSWORD] par le mot de passe de la base dans SUPABASE_DATABASE_URL."
        )
    return url


def _comptes(url: str) -> dict[str, int]:
    with psycopg.connect(url, connect_timeout=15) as c:
        existantes = {
            r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        return {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in TABLES
            if t in existantes
        }


def _securite(url: str, proprietaire: str | None = None) -> None:
    """Applique la RLS ; si `proprietaire` (mail) est donné, ce compte seul aura accès aux données."""
    with psycopg.connect(url, autocommit=True) as c:
        c.execute((RACINE / "sql" / "supabase" / "securite.sql").read_text())
        if proprietaire:
            ligne = c.execute(
                "SELECT id, email_confirmed_at FROM auth.users WHERE lower(email) = lower(%s)",
                (proprietaire.strip(),),
            ).fetchone()
            if ligne is None:
                sys.exit("Aucun compte avec ce mail dans Authentication → Users : crée-le d'abord.")
            if ligne[1] is None:
                sys.exit(
                    "Ce compte n'est pas confirmé : coche « Auto Confirm User » ou confirme-le."
                )
            c.execute("DELETE FROM proprietaire")
            c.execute("INSERT INTO proprietaire (user_id) VALUES (%s)", (ligne[0],))
        n = c.execute("SELECT count(*) FROM proprietaire").fetchone()[0]
        autres = c.execute("SELECT count(*) FROM auth.users").fetchone()[0] - n
    print(
        f"Sécurité appliquée (RLS sur toutes les tables). Propriétaire désigné : {'oui' if n else 'non'}."
    )
    if not n:
        print(
            "  Personne n'a accès aux données via l'appli tant que le propriétaire n'est pas désigné :\n"
            "  python -m app.vers_supabase --securite-seulement --proprietaire ton@mail"
        )
    if autres > 0:
        print(
            f"  ⚠ {autres} autre(s) compte(s) dans Authentication → Users : ils n'ont accès à rien,"
            " mais supprime-les et ferme les inscriptions."
        )


def _basculer_env(url: str) -> None:
    texte = ENV.read_text()
    locale = dotenv_values(ENV).get("DATABASE_URL", "")
    if "DATABASE_URL_LOCAL=" not in texte:
        texte = re.sub(
            r"^DATABASE_URL=.*$",
            lambda _: f"DATABASE_URL_LOCAL={locale}",
            texte,
            flags=re.MULTILINE,
        )
        texte += f"\n# Base principale : Supabase (revenir en local : remettre DATABASE_URL_LOCAL)\nDATABASE_URL={url}\n"
        ENV.write_text(texte)
        os.chmod(ENV, 0o600)
    print(
        "`.env` : DATABASE_URL pointe maintenant sur Supabase (ancienne valeur : DATABASE_URL_LOCAL)."
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--verifier", action="store_true", help="teste seulement la connexion")
    p.add_argument("--securite-seulement", action="store_true", help="réapplique les règles RLS")
    p.add_argument("--proprietaire", help="mail du compte Supabase qui aura accès aux données")
    args = p.parse_args()
    url = _url_supabase()
    try:
        cibles = _comptes(url)
    except (
        psycopg.Error
    ) as e:  # message sans la chaîne de connexion (elle contient le mot de passe)
        msg = str(e).splitlines()[0].replace(url, "<SUPABASE_DATABASE_URL>")
        sys.exit(
            f"Connexion à Supabase impossible ({type(e).__name__}). "
            + (
                "Vérifie que SUPABASE_DATABASE_URL commence par postgresql://"
                if "connection info" in msg
                else msg[:200]
            )
        )
    print(f"Connexion à Supabase : OK ({len(cibles)} de nos tables déjà présentes).")
    if args.verifier:
        return
    if args.securite_seulement:
        _securite(url, args.proprietaire)
        return
    if cibles.get("offres"):
        sys.exit(
            "La base Supabase contient déjà des offres : migration annulée pour ne rien écraser."
        )

    print("Copie du schéma et des données (pg_dump | psql)…")
    # pg_dump 17 émet « SET transaction_timeout », inconnu des Postgres plus anciens : on le retire.
    # Export dans le conteneur de la base locale ; import par un conteneur client lancé sur le
    # réseau du PC (le DNS du conteneur de la base ne résout pas toujours les adresses internet).
    # pg_dump 17 émet « SET transaction_timeout », inconnu des Postgres plus anciens : on le retire.
    commande = (
        "docker compose exec -T db pg_dump -U emploi -d emploi --schema=public --no-owner"
        " --no-privileges --no-comments"
        " | sed -e '/^SET transaction_timeout/d' -e '/^CREATE SCHEMA public;/d'"
        " | docker run --rm -i --network host -e CIBLE postgres:17-alpine"
        " sh -c 'psql \"$CIBLE\" -q -1 -v ON_ERROR_STOP=1 >/dev/null'"
    )
    r = subprocess.run(
        ["bash", "-o", "pipefail", "-c", commande],
        check=False,
        cwd=RACINE,
        env={**os.environ, "CIBLE": url},
        capture_output=True,
        text=True,
    )
    if r.returncode:
        sys.exit("Échec de la copie :\n" + r.stderr.replace(url, "<SUPABASE_DATABASE_URL>")[-2000:])
    _securite(url, args.proprietaire)

    with psycopg.connect(dotenv_values(ENV)["DATABASE_URL"]) as c:
        sources = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    cibles = _comptes(url)
    ecarts = {t: (sources[t], cibles.get(t)) for t in TABLES if sources[t] != cibles.get(t)}
    for t in TABLES:
        print(f"  {t:<20}{sources[t]:>6} → {cibles.get(t, '—'):>6}")
    if ecarts:
        sys.exit(f"Écarts de lignes : {ecarts}. .env n'a pas été modifié.")
    _basculer_env(url)
    print("Migration terminée. Relance l'appli et le travailleur pour qu'ils utilisent Supabase.")


if __name__ == "__main__":
    main()
