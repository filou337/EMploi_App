"""Importe les offres des mails d'alerte Gmail dans Postgres.

Usage :  python -m app.collecte.importer [--jours 30]
Relançable sans risque : une offre déjà en base (même URL) est comptée comme doublon.
Statut à l'arrivée : fiche incomplète, validité à vérifier (étape 2).
"""

import argparse
import email
import time
from email import policy
from email.utils import parsedate_to_datetime

from psycopg.types.json import Jsonb

from app import activite
from app.collecte import gmail
from app.collecte.enregistrement import enregistrer
from app.collecte.parseurs import parseur
from app.collecte.sources import est_alerte, requete_gmail, source_depuis_expediteur
from app.config import boites_gmail
from app.db import connexion


def _date(valeur: str):
    try:
        return parsedate_to_datetime(valeur)
    except (TypeError, ValueError):
        return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--jours", type=int, default=30)
    p.add_argument("--boite", action="append", help="boîte à lire (défaut : GMAIL_BOITES)")
    args = p.parse_args()
    for boite in args.boite or boites_gmail():
        print(f"[{boite}]")
        importer_boite(boite, args.jours)


def importer_boite(boite: str, jours: int) -> None:
    svc = gmail.service(boite)
    ids = list(gmail.ids_messages(svc, requete_gmail(jours)))
    mails = [m for m in gmail.entetes(svc, ids) if est_alerte(m["From"])]

    stats = {"mails_lus": 0, "offres_vues": 0, "doublons": 0}
    sans_parseur: dict[str, int] = {}
    erreurs: list[dict] = []
    with connexion() as conn:
        execution = conn.execute(
            "INSERT INTO executions (tache) VALUES ('collecte') RETURNING id"
        ).fetchone()
        conn.commit()
        for m in mails:
            source = source_depuis_expediteur(m["From"])
            extraire = parseur(source)
            if extraire is None:
                sans_parseur[source] = sans_parseur.get(source, 0) + 1
                continue
            try:
                brut = gmail.message_brut(svc, m["id"])
                message = email.message_from_bytes(brut, policy=policy.default)
                offres = extraire(message)
                with conn.transaction():
                    for offre in offres:
                        stats["offres_vues"] += 1
                        if not enregistrer(conn, offre, boite, m["id"], _date(m["Date"])):
                            stats["doublons"] += 1
                        else:
                            activite.noter(
                                "collecte",
                                "Offre récupérée",
                                entreprise=offre.entreprise,
                                intitule=offre.intitule,
                                detail=f"alerte {offre.source}",
                            )
                stats["mails_lus"] += 1
            except Exception as e:  # noqa: BLE001 — un mail illisible ne bloque pas les autres
                erreurs.append({"mail": m["id"], "erreur": f"{type(e).__name__}: {e}"})
            time.sleep(0.1)
        conn.execute(
            """
            UPDATE executions SET fin = now(), mails_lus = %s, offres_vues = %s,
                                  doublons = %s, erreurs = %s
            WHERE id = %s
            """,
            (*stats.values(), Jsonb(erreurs) if erreurs else None, execution["id"]),
        )

    nouvelles = stats["offres_vues"] - stats["doublons"]
    print(
        f"{stats['mails_lus']} mails lus, {stats['offres_vues']} offres vues, "
        f"{nouvelles} nouvelles, {stats['doublons']} déjà en base."
    )
    for source, n in sans_parseur.items():
        print(f"  {n} mails {source} ignorés : pas encore de parseur.")
    for e in erreurs[:10]:
        print(f"  ! {e['mail']} : {e['erreur'][:150]}")
    if len(erreurs) > 10:
        print(f"  ... et {len(erreurs) - 10} autres erreurs (détail dans la table executions).")


if __name__ == "__main__":
    main()
