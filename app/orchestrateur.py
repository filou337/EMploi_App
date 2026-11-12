"""Orchestrateur : enchaîne les agents, une fois (timer systemd, bouton de l'appli ou à la main).

Usage :  python -m app.orchestrateur [--sans-gmail] [--etapes collecte,validite,fiches,veille,matching]

Ordre : Recherche (alertes Gmail des 2 derniers jours) -> Suivi (réponses, accusés de réception :
valide les candidatures envoyées) -> Validité (doublons, fraîcheur)
-> Fiches (site carrières des nouveaux employeurs) -> Veille (entreprises surveillées)
-> Matching (qualification des offres à fiche complète) -> Spontanées (5 brouillons par jour au plus)
-> Rapport (tous les 3 jours).
Un verrou Postgres empêche deux passages simultanés. Chaque agent écrit son bilan dans le
journal (salon) ; une erreur d'un agent n'arrête pas les suivants.
"""

import argparse
import traceback
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from app import pertinence, rapport
from app.agents import matching, suivi, veille
from app.collecte.importer import importer_boite
from app.config import boites_gmail, chemin_token
from app.db import connexion
from app.dedup import dedupliquer
from app.fiches.recherche import executer as chercher_fiches
from app.migrations import appliquer
from app.notifications import bureau
from app.spontanees import agent as agent_spontanees
from app.validite import marquer_anciennes

VERROU = 8_765_001
ETAPES = (
    "collecte",
    "suivi",
    "validite",
    "fiches",
    "veille",
    "matching",
    "spontanees",
    "rapport",
)


def _journal(tache: str, debut: datetime, details: dict, erreur: str | None = None) -> None:
    with connexion() as conn:
        conn.execute(
            "INSERT INTO executions (tache, debut, fin, details, erreurs) VALUES (%s, %s, now(), %s, %s)",
            (tache, debut, Jsonb(details), Jsonb([{"erreur": erreur}]) if erreur else None),
        )


def _collecte() -> dict:
    lues = []
    for boite in boites_gmail():
        if not chemin_token(boite).exists():  # pas d'autorisation : impossible sans navigateur
            continue
        importer_boite(boite, jours=2)  # journalise lui-même son passage
        lues.append(boite)
    return {"boîtes lues": len(lues)}


def _spontanees() -> dict:
    with connexion() as conn:
        return agent_spontanees.executer(conn)


def _rapport() -> dict:
    with connexion() as conn:
        return rapport.generer(conn)  # produit seulement s'il est dû (tous les 3 jours)


def _validite() -> dict:
    with connexion() as conn:
        pertinence.marquer(conn)
        return {"doublons": dedupliquer(conn), "anciennes": marquer_anciennes(conn)}


def _veille() -> dict:
    with connexion() as conn:
        return veille.executer(conn)


def _matching() -> dict:
    with connexion() as conn:
        pertinence.marquer(conn)  # offres ajoutées par la veille juste avant
        return matching.executer(conn)


AGENTS = {
    "collecte": _collecte,
    "suivi": suivi.executer,
    "validite": _validite,
    "fiches": lambda: chercher_fiches(limite=40),
    "veille": _veille,
    "matching": _matching,
    "spontanees": _spontanees,
    "rapport": _rapport,
}
JOURNALISES_ICI = {
    "validite",
    "veille",
    "matching",
    "spontanees",
    "rapport",
}  # collecte et fiches écrivent leur propre bilan


def passage(etapes: tuple[str, ...] = ETAPES) -> dict:
    """Un passage complet ; renvoie le bilan par agent, ou {'occupe': True} si un passage tourne déjà."""
    with connexion() as verrou:
        if not verrou.execute("SELECT pg_try_advisory_lock(%s) AS ok", (VERROU,)).fetchone()["ok"]:
            return {"occupe": True}
        try:
            appliquer()
            bilan = {}
            for nom in etapes:
                debut = datetime.now(UTC)
                try:
                    bilan[nom] = AGENTS[nom]() or {}
                    if nom in JOURNALISES_ICI:
                        _journal(nom, debut, bilan[nom])
                except Exception as e:  # noqa: BLE001 — un agent en panne n'arrête pas les autres
                    bilan[nom] = {"erreur": f"{type(e).__name__}: {e}"}
                    _journal(nom, debut, {}, traceback.format_exc(limit=3))
            qualifiees = bilan.get("matching", {}).get("qualifiées", 0)
            if qualifiees:
                bureau(
                    "EMploi_App",
                    f"{qualifiees} nouvelle(s) offre(s) qualifiée(s) : dossier à préparer.",
                )
            return bilan
        finally:
            verrou.execute("SELECT pg_advisory_unlock(%s)", (VERROU,))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sans-gmail", action="store_true", help="saute la lecture des alertes Gmail")
    p.add_argument("--etapes", help="liste d'agents séparés par des virgules")
    args = p.parse_args()
    etapes = tuple(args.etapes.split(",")) if args.etapes else ETAPES
    if args.sans_gmail:
        etapes = tuple(e for e in etapes if e != "collecte")
    inconnues = set(etapes) - set(AGENTS)
    if inconnues:
        raise SystemExit(f"Agents inconnus : {', '.join(sorted(inconnues))}")
    bilan = passage(etapes)
    if bilan.get("occupe"):
        print("Un passage est déjà en cours : rien à faire.")
        return
    for nom, b in bilan.items():
        print(f"[{nom}] " + ", ".join(f"{k} {v}" for k, v in b.items()))


if __name__ == "__main__":
    main()
