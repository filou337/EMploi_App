"""Orchestration du moteur ATS : analyse du CV par défaut, matching avec une offre, historique."""

import psycopg
from psycopg.types.json import Jsonb

from app.ats import compatibilite, moteur
from app.ats.anonymisation import verifier_masque


class AnalyseImpossible(ValueError):
    """Message montré tel quel à l'utilisateur."""


def texte_masque(conn: psycopg.Connection, texte: str) -> str:
    """Texte du CV sans aucune coordonnée (fiche d'identité), vérifié avant tout envoi au LLM."""
    from app.generation import identite

    ident = identite.lire(conn, texte)
    masque = identite.masquer(texte, ident)
    verifier_masque(masque, [ident.get("nom"), ident.get("email"), ident.get("telephone")])
    return masque


def cv_par_defaut(conn: psycopg.Connection) -> dict:
    doc = conn.execute(
        "SELECT id, format, texte, diagnostic, nom_fichier FROM documents"
        " WHERE type = 'cv' AND par_defaut AND langue = 'fr'"
    ).fetchone()
    if doc is None:
        raise AnalyseImpossible("Dépose d'abord ton CV dans l'onglet « CV & lettre ».")
    if not (doc["texte"] or "").strip():
        raise AnalyseImpossible("Ton CV n'a pas de texte lisible (PDF scanné ?) : rien à analyser.")
    return doc


def _en_cache(
    conn: psycopg.Connection,
    document_id: int,
    cle_version: str,
    version: int,
    offre_id: int | None = None,
) -> dict | None:
    """Analyse déjà faite sur ce même fichier avec ce même barème : on la renvoie telle quelle."""
    row = conn.execute(
        f"""
        SELECT a.id, a.cree_le, a.modele, a.score_ats, a.score_qualite, a.score_matching, a.resultat,
               d.nom_fichier AS document
        FROM analyses a JOIN documents d ON d.id = a.document_id
        LEFT JOIN offres o ON o.id = a.offre_id
        WHERE a.document_id = %s AND a.offre_id IS NOT DISTINCT FROM %s
          AND ({cle_version})::int = %s
          AND (o.id IS NULL OR a.cree_le >= COALESCE(o.modifiee_le, o.recu_le))
        ORDER BY a.cree_le DESC LIMIT 1
        """,
        (document_id, offre_id, version),
    ).fetchone()
    if row:
        row["depuis_cache"] = True
    return row


def analyser_cv(
    conn: psycopg.Connection, document_id: int | None = None, forcer: bool = False
) -> dict:
    """Compatibilité ATS (locale) + lecture et qualité (Gemini). Enregistre l'analyse.

    Même fichier + même barème = même résultat : l'analyse en cache est renvoyée sans rappeler
    Gemini, sauf si `forcer`.
    """
    if document_id is None:
        doc = cv_par_defaut(conn)
    else:
        doc = conn.execute(
            "SELECT id, format, texte, diagnostic, nom_fichier FROM documents WHERE id = %s AND type = 'cv'",
            (document_id,),
        ).fetchone()
        if doc is None:
            raise AnalyseImpossible("CV introuvable.")
    if not forcer:
        cache = _en_cache(
            conn, doc["id"], "a.resultat->'fiabilite'->>'version_bareme'", moteur.VERSION_CV
        )
        if cache:
            return cache
    ats = compatibilite.score(doc["format"], doc["texte"] or "", doc["diagnostic"] or {})
    lecture, modele = moteur.analyser_cv(texte_masque(conn, doc["texte"]))
    resultat = {"compatibilite": ats, **lecture}
    row = conn.execute(
        """
        INSERT INTO analyses (document_id, type, modele, score_ats, score_qualite, resultat)
        VALUES (%s, 'cv', %s, %s, %s, %s) RETURNING id, cree_le
        """,
        (doc["id"], modele, ats["score"], lecture["score_qualite"], Jsonb(resultat)),
    ).fetchone()
    return {
        "id": row["id"],
        "cree_le": row["cree_le"],
        "document": doc["nom_fichier"],
        "modele": modele,
        "score_ats": ats["score"],
        "score_qualite": lecture["score_qualite"],
        "resultat": resultat,
    }


def matcher_offre(conn: psycopg.Connection, offre_id: int, forcer: bool = False) -> dict:
    doc = cv_par_defaut(conn)
    if not forcer:
        cache = _en_cache(
            conn, doc["id"], "a.resultat->>'version'", moteur.VERSION_MATCHING, offre_id
        )
        if cache:
            return cache
    offre = conn.execute(
        "SELECT o.id, o.intitule, o.texte, o.extrait, o.fiche_complete, e.nom_affiche AS entreprise"
        " FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id WHERE o.id = %s",
        (offre_id,),
    ).fetchone()
    if offre is None:
        raise AnalyseImpossible("Offre introuvable.")
    texte_offre = offre["texte"] or offre["extrait"] or ""
    if len(texte_offre) < 40:
        raise AnalyseImpossible(
            "Cette offre n'a presque pas de texte : colle d'abord la fiche complète."
        )
    entete = f"Intitulé : {offre['intitule']}\nEntreprise : {offre['entreprise']}\n\n"
    res, modele = moteur.matcher(texte_masque(conn, doc["texte"]), entete + texte_offre)
    res["fiche_complete"] = offre["fiche_complete"]
    if not offre["fiche_complete"]:
        res["avertissement"] = (
            "Analyse sur l'extrait de l'alerte seulement : colle la fiche complète "
            "pour un matching fiable."
        )
    row = conn.execute(
        """
        INSERT INTO analyses (document_id, offre_id, type, modele, score_matching, resultat)
        VALUES (%s, %s, 'matching', %s, %s, %s) RETURNING id, cree_le
        """,
        (doc["id"], offre_id, modele, res["score_matching"], Jsonb(res)),
    ).fetchone()
    grille = _noter_offre(conn, offre_id, res)
    return {
        "id": row["id"],
        "cree_le": row["cree_le"],
        "modele": modele,
        "score_matching": res["score_matching"],
        "resultat": res,
        "grille": grille,
    }


def _noter_offre(conn: psycopg.Connection, offre_id: int, res: dict) -> dict | None:
    """Grille V2 (score, classe A/B/C) à partir du matching ; None si le profil est invalide."""
    from app import criteres, scoring

    try:
        return scoring.scorer(conn, offre_id, res, criteres.lire(conn))
    except scoring.ProfilInvalide:
        return None


def derniere(conn: psycopg.Connection, type_: str, offre_id: int | None = None) -> dict | None:
    return conn.execute(
        """
        SELECT a.id, a.cree_le, a.modele, a.score_ats, a.score_qualite, a.score_matching, a.resultat,
               d.nom_fichier AS document
        FROM analyses a JOIN documents d ON d.id = a.document_id
        WHERE a.type = %s AND (%s::int IS NULL OR a.offre_id = %s) AND d.par_defaut
        ORDER BY a.cree_le DESC LIMIT 1
        """,
        (type_, offre_id, offre_id),
    ).fetchone()


def historique(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        SELECT a.id, a.type, a.cree_le, a.score_ats, a.score_qualite, a.score_matching,
               d.nom_fichier AS document, o.intitule, e.nom_affiche AS entreprise
        FROM analyses a JOIN documents d ON d.id = a.document_id
        LEFT JOIN offres o ON o.id = a.offre_id LEFT JOIN entreprises e ON e.id = o.entreprise_id
        ORDER BY a.cree_le DESC LIMIT 100
        """
    ).fetchall()
