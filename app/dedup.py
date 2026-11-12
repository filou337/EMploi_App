"""Déduplication : même offre publiée plusieurs fois, et garde-fou des 3 mois par employeur.

Le doublon d'URL est déjà bloqué en base (url_cle UNIQUE). Ici on repère la même offre
republiée sous un autre lien : même entreprise, intitulé et ville similaires à 90 % ou plus.
"""

from datetime import date, timedelta

import psycopg
from rapidfuzz import fuzz

from app.collecte.normalisation import contrat_depuis_intitule, niveau

SEUIL = 90
DELAI_MEME_EMPLOYEUR = timedelta(days=90)


def meme_offre(cle_a: str, cle_b: str, intitule_a: str = "", intitule_b: str = "") -> bool:
    """Compare deux cle_floue 'entreprise|intitule|ville' champ par champ.

    Chaque champ doit atteindre le seuil : une similarité globale laisserait passer
    « Alten, Data Analyst, Paris » contre « Alten, Data Analyst, Toulouse ».
    L'ordre des mots de l'intitulé ne compte pas (« Data Engineer Junior » = « Junior Data Engineer »).
    Le niveau (junior, senior, stage…) doit être identique, et le contrat aussi quand les deux
    intitulés le donnent : sur un intitulé long, « Junior » contre « Senior » passerait les 90 %.
    Les intitulés bruts servent au contrat (la cle_floue l'a retiré) ; à défaut, la clé suffit.
    """
    ent_a, titre_a, ville_a = cle_a.split("|")
    ent_b, titre_b, ville_b = cle_b.split("|")
    if niveau(intitule_a or titre_a) != niveau(intitule_b or titre_b):
        return False
    contrat_a = contrat_depuis_intitule(intitule_a)
    contrat_b = contrat_depuis_intitule(intitule_b)
    if contrat_a and contrat_b and contrat_a != contrat_b:
        return False
    return (
        fuzz.ratio(ent_a, ent_b) >= SEUIL
        and fuzz.token_sort_ratio(titre_a, titre_b) >= SEUIL
        and fuzz.ratio(ville_a, ville_b) >= SEUIL
    )


def dedupliquer(conn: psycopg.Connection) -> int:
    """Recalcule les doublons de toute la base et renvoie leur nombre.

    La plus ancienne offre reste l'originale ; les suivantes pointent vers elle (doublon_de).
    Tout est recalculé à chaque passage : une règle corrigée s'applique aussi au passé.
    Une offre libérée repart en 'a_verifier' (la fraîcheur est recalculée juste après).
    """
    conn.execute(
        "UPDATE offres SET statut_validite = 'a_verifier', doublon_de = NULL"
        " WHERE statut_validite = 'doublon'"
    )
    rows = conn.execute(
        "SELECT id, cle_floue, intitule FROM offres ORDER BY recu_le, id"
    ).fetchall()
    references: dict[str, list[dict]] = {}  # entreprise -> offres originales
    doublons: list[tuple[int, int]] = []
    for row in rows:
        groupe = references.setdefault(row["cle_floue"].split("|")[0], [])
        original = next(
            (
                ref["id"]
                for ref in groupe
                if meme_offre(
                    ref["cle_floue"], row["cle_floue"], ref["intitule"] or "", row["intitule"] or ""
                )
            ),
            None,
        )
        if original is None:
            groupe.append(row)
        else:
            doublons.append((original, row["id"]))
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE offres SET statut_validite = 'doublon', doublon_de = %s WHERE id = %s",
            doublons,
        )
    return len(doublons)


def candidature_recente(
    conn: psycopg.Connection, entreprise_id: int, aujourd_hui: date | None = None
) -> date | None:
    """Date de la dernière candidature chez cet employeur si elle a moins de 3 mois, sinon None.

    Une offre concernée est signalée dans l'alerte, sans génération de dossier.
    """
    aujourd_hui = aujourd_hui or date.today()
    row = conn.execute(
        """
        SELECT GREATEST(
            (SELECT derniere_candidature FROM entreprises WHERE id = %(e)s),
            (SELECT max(postule_le) FROM candidatures
             WHERE entreprise_id = %(e)s AND statut <> 'ignore')
        ) AS derniere
        """,
        {"e": entreprise_id},
    ).fetchone()
    derniere = row["derniere"]
    if derniere and aujourd_hui - derniere < DELAI_MEME_EMPLOYEUR:
        return derniere
    return None
