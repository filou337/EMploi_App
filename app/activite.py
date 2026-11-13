"""Fil d'activité des agents (onglet Salon, en direct) : qui travaille sur quel poste.

Chaque écriture passe par sa propre connexion, validée tout de suite : le fil est visible pendant
qu'un agent travaille, même si sa propre transaction n'est pas encore terminée. Une panne du fil
n'arrête jamais un agent (les erreurs sont avalées).
"""

import psycopg

from app.db import connexion

CONSERVATION_JOURS = 7


def _ecrire(sql: str, params: tuple) -> dict | None:
    try:
        with connexion() as c:
            return c.execute(sql, params).fetchone()
    except psycopg.Error:
        return None


def debut(
    agent: str,
    action: str,
    offre_id: int | None = None,
    entreprise: str | None = None,
    intitule: str | None = None,
    detail: str | None = None,
) -> int | None:
    """Note qu'un agent commence un travail ; renvoie l'identifiant à passer à `fin`."""
    r = _ecrire(
        "INSERT INTO activites (agent, action, statut, offre_id, entreprise, intitule, detail)"
        " VALUES (%s, %s, 'en_cours', %s, %s, %s, %s) RETURNING id",
        (agent, action, offre_id, entreprise, intitule, (detail or "")[:500] or None),
    )
    return r["id"] if r else None


def fin(
    activite_id: int | None,
    action: str | None = None,
    detail: str | None = None,
    erreur: bool = False,
) -> None:
    if activite_id is None:
        return
    _ecrire(
        "UPDATE activites SET statut = %s, fini_le = now(), action = coalesce(%s, action),"
        " detail = coalesce(%s, detail) WHERE id = %s RETURNING id",
        ("erreur" if erreur else "fait", action, (detail or "")[:500] or None, activite_id),
    )


def noter(
    agent: str,
    action: str,
    offre_id: int | None = None,
    entreprise: str | None = None,
    intitule: str | None = None,
    detail: str | None = None,
) -> None:
    """Note un travail terminé d'un coup (offre récupérée, candidature envoyée…)."""
    _ecrire(
        "INSERT INTO activites (agent, action, statut, offre_id, entreprise, intitule, detail, fini_le)"
        " VALUES (%s, %s, 'fait', %s, %s, %s, %s, now()) RETURNING id",
        (agent, action, offre_id, entreprise, intitule, (detail or "")[:500] or None),
    )


def nettoyer() -> None:
    """Garde 7 jours d'historique ; clôt les « en cours » abandonnés (passage interrompu)."""
    _ecrire(
        "WITH a AS (DELETE FROM activites WHERE cree_le < now() - make_interval(days => %s)"
        " RETURNING 1) SELECT count(*) FROM a",
        (CONSERVATION_JOURS,),
    )
    _ecrire(
        "WITH a AS (UPDATE activites SET statut = 'erreur', fini_le = now(),"
        " detail = coalesce(detail, 'interrompu') WHERE statut = 'en_cours'"
        " AND cree_le < now() - interval '2 hours' RETURNING 1) SELECT count(*) FROM a",
        (),
    )
