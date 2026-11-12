"""Contrôles de validité des offres, puis déduplication.

Usage :  python -m app.validite
Sans réseau pour l'instant : fraîcheur et doublons. La recherche de la fiche chez
l'employeur (404, « pourvue ») viendra s'ajouter ici.
"""

from datetime import date, datetime, timedelta

import psycopg

from app.db import connexion
from app.dedup import dedupliquer

FRAICHEUR_MAX = timedelta(days=21)
# Statuts qu'un contrôle de fraîcheur peut encore changer.
_OUVERTS = ("a_verifier", "valide")


def trop_ancienne(date_publication: date | None, recu_le: datetime, aujourd_hui: date) -> bool:
    """Vrai si l'offre a plus de 21 jours.

    Date de publication inconnue : la date de l'alerte sert de borne, car une offre est
    publiée avant d'être annoncée. Une alerte de plus de 21 jours prouve donc une offre
    de plus de 21 jours ; une alerte récente ne prouve rien (l'offre reste à vérifier).
    """
    reference = date_publication or recu_le.date()
    return aujourd_hui - reference > FRAICHEUR_MAX


def marquer_anciennes(conn: psycopg.Connection, aujourd_hui: date | None = None) -> int:
    aujourd_hui = aujourd_hui or date.today()
    rows = conn.execute(
        "SELECT id, date_publication, recu_le FROM offres WHERE statut_validite = ANY(%s)",
        (list(_OUVERTS),),
    ).fetchall()
    ids = [r["id"] for r in rows if trop_ancienne(r["date_publication"], r["recu_le"], aujourd_hui)]
    if ids:
        conn.execute("UPDATE offres SET statut_validite = 'ancienne' WHERE id = ANY(%s)", (ids,))
    return len(ids)


def main() -> None:
    with connexion() as conn:
        doublons = dedupliquer(conn)  # avant la fraîcheur : un doublon reste un doublon
        anciennes = marquer_anciennes(conn)
        conn.execute(
            """
            INSERT INTO executions (tache, fin, doublons, rejetees)
            VALUES ('validite', now(), %s, %s)
            """,
            (doublons, anciennes),
        )
        bilan = conn.execute(
            "SELECT statut_validite, count(*) AS n FROM offres GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    print(f"{doublons} doublons, {anciennes} offres passées en « ancienne ».")
    for r in bilan:
        print(f"  {r['statut_validite']:<12}{r['n']:>5}")


if __name__ == "__main__":
    main()
