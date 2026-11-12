"""Fabrication d'un dossier de candidature : CV + LM adaptés, rendus, puis relus par le checker.

Étapes (une demande « fabriquer » exécutée par le PC) :
1. sources : CV structuré (lecture ATS en cache), texte du CV, LM de référence, coordonnées ;
2. adaptation du CV et rédaction de la LM (Gemini, textes masqués), garde-fou de véracité ;
3. rendu PDF + DOCX ;
4. relecture du PDF produit : compatibilité ATS (locale) et matching avec l'offre (Gemini) ;
5. « ATS friendly » si compatibilité ≥ seuil (90), matching ≥ celui du CV d'origine et aucun
   élément retiré par le garde-fou ; sinon « à revoir », avec les motifs.
Les fichiers vont en base (téléchargeables depuis le téléphone) et dans le dossier du poste.
"""

import re

import psycopg
from psycopg.types.json import Jsonb

from app import criteres, documents, dossiers
from app.ats import compatibilite, moteur
from app.ats import service as ats
from app.ats.anonymisation import verifier_masque
from app.config import RACINE
from app.generation import adaptation, identite, rendu
from app.notifications import notifier


class FabricationImpossible(ValueError):
    """Message montré tel quel à l'utilisateur."""


def _morceau(t: str, n: int = 30) -> str:
    from app.collecte.normalisation import sans_accents

    return re.sub(r"[^A-Za-z0-9]+", "_", sans_accents(t or "")).strip("_")[:n] or "x"


def _structure_cv(conn: psycopg.Connection) -> dict:
    """Lecture structurée du CV par défaut (analyse ATS en cache, refaite si le barème a changé)."""
    return ats.analyser_cv(conn)["resultat"]["cv"]


def fabriquer(conn: psycopg.Connection, offre_id: int, langue: str | None = None) -> dict:
    params = criteres.lire(conn)
    seuils = params["seuils"]
    faites = conn.execute(
        "SELECT count(*) AS n FROM fabrications WHERE cree_le::date = current_date AND statut <> 'erreur'"
    ).fetchone()["n"]
    if faites >= seuils["fabrications_par_jour"]:
        raise FabricationImpossible(
            f"Plafond de {seuils['fabrications_par_jour']} dossiers par jour atteint."
        )

    offre = conn.execute(
        "SELECT o.id, o.intitule, o.texte, o.extrait, o.fiche_complete, e.nom_affiche AS entreprise"
        " FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id WHERE o.id = %s",
        (offre_id,),
    ).fetchone()
    if offre is None:
        raise FabricationImpossible("Offre introuvable.")
    texte_offre = offre["texte"] or offre["extrait"] or ""
    if len(texte_offre) < 150:
        raise FabricationImpossible(
            "L'offre n'a presque pas de texte : colle d'abord la fiche complète."
        )
    texte_offre = (
        f"Intitulé : {offre['intitule']}\nEntreprise : {offre['entreprise']}\n\n{texte_offre}"
    )

    doc_cv = ats.cv_par_defaut(conn)
    doc_lm = conn.execute(
        "SELECT texte FROM documents WHERE type = 'lm' AND par_defaut AND langue = 'fr'"
    ).fetchone()
    ident = identite.lire(conn, doc_cv["texte"])
    if not ident.get("nom"):
        raise FabricationImpossible("Renseigne d'abord tes coordonnées (onglet « CV & lettre »).")
    langue = langue or adaptation.detecter_langue(texte_offre)
    cv_source = _structure_cv(conn)
    cv_masque = identite.masquer(doc_cv["texte"], ident)
    lm_masque = identite.masquer(doc_lm["texte"], ident) if doc_lm else ""
    sensibles = [ident.get("nom"), ident.get("email"), ident.get("telephone")]
    verifier_masque(cv_masque, sensibles)
    verifier_masque(lm_masque, sensibles)

    fab_id = conn.execute(
        "INSERT INTO fabrications (offre_id, langue) VALUES (%s, %s) RETURNING id",
        (offre_id, langue),
    ).fetchone()["id"]
    conn.commit()  # visible tout de suite dans l'appli (« en cours »)
    try:
        return _produire(
            conn, fab_id, offre, texte_offre, langue, ident, cv_source, cv_masque, lm_masque, seuils
        )
    except Exception as e:
        conn.rollback()
        conn.execute(
            "UPDATE fabrications SET statut = 'erreur', erreur = %s WHERE id = %s",
            (f"{type(e).__name__}: {e}"[:1000], fab_id),
        )
        conn.commit()
        raise


def _produire(
    conn, fab_id, offre, texte_offre, langue, ident, cv_source, cv_masque, lm_masque, seuils
) -> dict:
    offre_id = offre["id"]
    cv, verif_cv, modele = adaptation.adapter_cv(cv_source, cv_masque, texte_offre, langue)
    lm, verif_lm, _ = adaptation.rediger_lm(cv_masque, lm_masque, texte_offre, langue)

    nom = _morceau(ident["nom"], 25)
    societe = _morceau(offre["entreprise"], 25)
    fichiers = {
        ("cv", "pdf"): rendu.pdf(rendu.html_cv(cv, ident, langue)),
        ("cv", "docx"): rendu.docx_cv(cv, ident, langue),
        ("lm", "pdf"): rendu.pdf(rendu.html_lm(lm, ident, offre["entreprise"], langue)),
        ("lm", "docx"): rendu.docx_lm(lm, ident, offre["entreprise"], langue),
    }
    noms = {
        (t, f): f"{'CV' if t == 'cv' else 'LM'}_{nom}_{societe}_{langue.upper()}.{f}"
        for t, f in fichiers
    }

    # Relecture du CV produit, comme un ATS : texte extrait du PDF, compatibilité, matching.
    lecture = documents.lire(noms[("cv", "pdf")], fichiers[("cv", "pdf")])
    diag = documents.diagnostic(lecture, "cv")
    compat = compatibilite.score("pdf", lecture.texte, diag)
    cv_produit_masque = identite.masquer(lecture.texte, ident)
    verifier_masque(
        cv_produit_masque, [ident.get("nom"), ident.get("email"), ident.get("telephone")]
    )
    matching, _ = moteur.matcher(cv_produit_masque, texte_offre)
    origine = ats.matcher_offre(conn, offre_id)["score_matching"]  # en cache si déjà calculé

    motifs = []
    if compat["score"] < seuils["ats_friendly_min"]:
        motifs.append(f"compatibilité ATS {compat['score']}/100 (< {seuils['ats_friendly_min']})")
    if matching["score_matching"] < float(origine):
        motifs.append(
            f"matching {matching['score_matching']} inférieur au CV d'origine ({origine})"
        )
    retires = verif_cv["retires"] + verif_lm["retires"]
    if retires:
        motifs.append(
            f"{len(retires)} élément(s) retiré(s) par le garde-fou de véracité : à relire"
        )
    statut = "a_revoir" if motifs else "ats_friendly"

    dossier = dossiers.creer(conn, offre_id)
    for (t, f), contenu in fichiers.items():
        (RACINE / dossier / noms[(t, f)]).write_bytes(contenu)
        conn.execute(
            "INSERT INTO dossier_fichiers (fabrication_id, type, format, nom, contenu) VALUES (%s, %s, %s, %s, %s)",
            (fab_id, t, f, noms[(t, f)], contenu),
        )
    controle = {
        "compatibilite": compat,
        "diagnostic": diag,
        "matching": {
            k: matching.get(k)
            for k in ("score_matching", "detail_score", "mots_cles_manquants", "synthese")
        },
        "motifs": motifs,
        "dossier": dossier,
    }
    conn.execute(
        """
        UPDATE fabrications SET statut = %s, modele = %s, contenu = %s, verification = %s, controle = %s,
               score_ats = %s, score_matching = %s, score_matching_origine = %s WHERE id = %s
        """,
        (
            statut,
            modele,
            Jsonb({"cv": cv, "lm": lm}),
            Jsonb({"cv": verif_cv, "lm": verif_lm}),
            Jsonb(controle),
            compat["score"],
            matching["score_matching"],
            origine,
            fab_id,
        ),
    )
    if statut == "ats_friendly":
        conn.execute(
            "UPDATE offres SET etape = 'ats_friendly', etape_maj_le = now() WHERE id = %s"
            " AND etape IN ('reperee', 'qualifiee', 'fabrication')",
            (offre_id,),
        )
    notifier(
        conn,
        "dossier",
        f"Dossier {'prêt' if statut == 'ats_friendly' else 'à revoir'} — {offre['entreprise']}",
        f"{offre['intitule']} : ATS {compat['score']}/100, matching {matching['score_matching']}"
        f" (origine {origine}).",
        offre_id,
    )
    return {
        "fabrication_id": fab_id,
        "statut": statut,
        "score_ats": compat["score"],
        "score_matching": matching["score_matching"],
        "score_matching_origine": origine,
        "motifs": motifs,
        "dossier": dossier,
    }
