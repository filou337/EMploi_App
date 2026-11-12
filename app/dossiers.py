"""Dossier local d'un poste : Candidatures/<date>_<entreprise>_<poste>/ avec offre.txt.

Le CV et la LM adaptés y seront déposés par l'agent Dossier (génération, prochaine étape).
"""

import re
from datetime import date

import psycopg

from app.collecte.normalisation import sans_accents
from app.config import RACINE

RACINE_DOSSIERS = RACINE / "Candidatures"


def _morceau(texte: str, n: int) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", sans_accents(texte or "")).strip("-")[:n] or "x"


def creer(conn: psycopg.Connection, offre_id: int) -> str:
    o = conn.execute(
        """
        SELECT o.*, e.nom_affiche AS entreprise FROM offres o
        LEFT JOIN entreprises e ON e.id = o.entreprise_id WHERE o.id = %s
        """,
        (offre_id,),
    ).fetchone()
    if o is None:
        raise ValueError("Offre introuvable")
    if o["dossier"] and (RACINE / o["dossier"]).exists():
        return o["dossier"]
    nom = f"{date.today():%Y-%m-%d}_{_morceau(o['entreprise'], 30)}_{_morceau(o['intitule'], 50)}"
    dossier = RACINE_DOSSIERS / nom
    dossier.mkdir(parents=True, exist_ok=True)
    score = conn.execute(
        "SELECT score_matching FROM analyses WHERE offre_id = %s ORDER BY cree_le DESC LIMIT 1",
        (offre_id,),
    ).fetchone()
    lien = o["url_fiche"] or (o["url_cle"] if o["url_cle"].startswith("http") else "")
    entete = [
        f"Entreprise : {o['entreprise'] or ''}",
        f"Poste : {o['intitule'] or ''}",
        f"Lieu : {o['ville'] or ''}",
        f"Contrat : {o['contrat'] or 'non précisé'}",
        f"Lien : {lien}",
        f"Source : {o['source']}",
        f"Publiée le : {o['date_publication'] or 'inconnue'}",
        f"Matching : {score['score_matching'] if score else 'non calculé'}/100",
        f"Dossier créé le : {date.today():%d/%m/%Y}",
        "",
        "=" * 72,
        "",
    ]
    (dossier / "offre.txt").write_text(
        "\n".join(entete) + (o["texte"] or o["extrait"] or ""), "utf-8"
    )
    chemin = str(dossier.relative_to(RACINE))
    conn.execute("UPDATE offres SET dossier = %s WHERE id = %s", (chemin, offre_id))
    return chemin
