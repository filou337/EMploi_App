"""Pré-filtre par mots-clés : l'offre parle-t-elle de data ? (aucun appel réseau ni LLM)

Sert à ne pas dépenser de requêtes ni de tokens sur un poste de développeur C ou de
contrôleur de gestion. Le scoring (étape 3) affinera ; ici on écarte seulement l'évident.
"""

import re

from app.collecte.normalisation import sans_accents

_MOTS_DATA = re.compile(
    r"\b(?:data|donnees?|analyst|analyste|analytics?|bi|business intelligence|decisionnel"
    r"|dataviz|statistiques?|etl|elt|sql|power ?bi|tableau|looker|dbt|qlik|datawarehouse"
    r"|big data|reporting)\b"
)


def est_data(intitule: str) -> bool:
    return bool(_MOTS_DATA.search(sans_accents(intitule).lower()))


def marquer(conn) -> int:
    """Calcule `offres.pertinente` pour les offres qui ne l'ont pas encore (lu par l'interface)."""
    rows = conn.execute("SELECT id, intitule FROM offres WHERE pertinente IS NULL").fetchall()
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE offres SET pertinente = %s WHERE id = %s",
            [(est_data(r["intitule"] or ""), r["id"]) for r in rows],
        )
    return len(rows)
