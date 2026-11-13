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

from app import criteres, documents, dossiers, scoring
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


TOLERANCE_MATCHING = (
    2  # points : l'écart du matching entre deux lectures (bruit) n'est pas une baisse
)


def _structure_cv(conn: psycopg.Connection) -> dict:
    """Lecture structurée du CV par défaut (analyse ATS en cache, refaite si le barème a changé).

    Les années des diplômes obtenus depuis (profil, « diplomes_obtenus ») remplacent « En cours ».
    """
    from copy import deepcopy

    cv = deepcopy(ats.analyser_cv(conn)["resultat"]["cv"])
    return appliquer_diplomes(cv, scoring.diplomes_obtenus())


_NOMS_LANGUES = {
    "francais": "Français",
    "anglais": "Anglais",
    "espagnol": "Espagnol",
    "allemand": "Allemand",
    "italien": "Italien",
    "portugais": "Portugais",
}
_STAGE = re.compile(
    r"^\s*(?:stage|stagiaire|alternance|alternant|apprenti)\s*[-–:]?\s*"
    r"|\s*[-–(]\s*(?:stage|stagiaire|alternance)\s*\)?\s*$",
    re.IGNORECASE,
)
# Niveaux qui surévaluent un profil junior : retirés s'ils ne figurent pas dans le CV.
_NIVEAUX = (
    (re.compile(r"\b(?:[Ee]xpert|[Ee]xperte|[Ss]pécialiste)s? (?:en|de|du|des)\s+"), "maîtrisant "),
    (
        re.compile(
            r"\s*\b(?:senior|confirmée?|chevronnée?|aguerrie?|expérimentée?)\b(?:\s+et\b)?",
            re.IGNORECASE,
        ),
        "",
    ),
)


def corriger_cv(cv: dict, profil: dict, texte_cv: str) -> dict:
    """Corrections par le code, d'après ton profil qui fait foi : langues, intitulés, dates, niveaux."""
    langues = (profil.get("profil") or {}).get("langues") or {}
    if langues:
        cv["langues"] = [
            {"langue": _NOMS_LANGUES.get(k.lower(), k.capitalize()), "niveau": str(v)}
            for k, v in langues.items()
        ]
    fins = {k.lower(): v for k, v in (profil.get("fin_experiences") or {}).items()}
    for e in cv.get("experiences", []):
        e["poste"] = _STAGE.sub("", e.get("poste") or "").strip() or e.get("poste", "")
        fin = next((v for k, v in fins.items() if k in (e.get("entreprise") or "").lower()), None)
        if fin and (
            not e.get("fin") or re.match(r"(?i)pr[ée]sent|aujourd|en cours|current", e["fin"])
        ):
            e["fin"] = fin
    cv["accroche"] = attenuer(cv.get("accroche", ""), texte_cv)
    return cv


def attenuer(texte: str, texte_cv: str) -> str:
    """Retire « expert », « senior », « confirmé »… quand ton CV ne le dit pas."""
    bas = (texte_cv or "").lower()
    for motif, remplacement in _NIVEAUX:
        if motif.search(bas):
            continue  # le CV le dit : on le garde

        def remplacer(m: re.Match, r: str = remplacement, avant: str = texte) -> str:
            debut_phrase = m.start() == 0 or avant[: m.start()].rstrip().endswith((".", "!", "?"))
            return r.capitalize() if r and debut_phrase else r

        texte = motif.sub(remplacer, texte)
    return re.sub(r"\s{2,}", " ", texte).strip()


def _profil() -> dict:
    try:
        return scoring.charger_profil()
    except scoring.ProfilInvalide:
        return {}


def appliquer_diplomes(cv: dict, diplomes: dict[str, str]) -> dict:
    """{« Master Big Data » : « 2024-2026 »} -> année corrigée pour la formation qui le contient."""
    for f in cv.get("formations", []):
        for nom, annee in diplomes.items():
            if nom.lower() in (f.get("diplome") or "").lower():
                f["annee"] = annee
    return cv


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
    faits = scoring.faits()
    if faits:  # faits validés par toi : guident la rédaction et passent le garde-fou de véracité
        cv_masque += "\n\nFaits validés par le candidat :\n" + identite.masquer(
            "\n".join(f"- {x}" for x in faits), ident
        )
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


def meilleur_essai(origine: float, rediger) -> dict:
    """Gemini ne rédige pas deux fois pareil : si le CV adapté matche moins bien que l'original,
    on refait une rédaction et on garde la meilleure des deux."""
    essai = rediger()
    if essai["matching"]["score_matching"] < origine - TOLERANCE_MATCHING:
        second = rediger()
        if second["matching"]["score_matching"] > essai["matching"]["score_matching"]:
            essai = second
        essai["essais"] = 2
    return essai


def _rediger_et_relire(offre, texte_offre, langue, ident, cv_source, cv_masque, lm_masque) -> dict:
    """Une rédaction complète (CV + LM), rendue en PDF/DOCX puis relue comme un ATS."""
    cv, verif_cv, modele = adaptation.adapter_cv(cv_source, cv_masque, texte_offre, langue)
    cv = corriger_cv(cv, _profil(), cv_masque)
    lm, verif_lm, _ = adaptation.rediger_lm(cv_masque, lm_masque, texte_offre, langue)
    lm["paragraphes"] = [
        attenuer(p, cv_masque + "\n" + lm_masque) for p in lm.get("paragraphes", [])
    ]
    nom = _morceau(ident["nom"], 25)
    societe = _morceau(offre["entreprise"], 25)
    # Le CV tient toujours sur une page : resserrage par paliers (en-tête d'abord) si besoin.
    cv_pdf, compact = rendu.pdf_cv_une_page(cv, ident, langue)
    fichiers = {
        ("cv", "pdf"): cv_pdf,
        ("cv", "docx"): rendu.docx_cv(cv, ident, langue, compact),
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
    return {
        "cv": cv,
        "verif_cv": verif_cv,
        "modele": modele,
        "lm": lm,
        "verif_lm": verif_lm,
        "fichiers": fichiers,
        "noms": noms,
        "diag": diag,
        "compat": compat,
        "matching": matching,
        "essais": 1,
        "compact": compact,
    }


def _produire(
    conn, fab_id, offre, texte_offre, langue, ident, cv_source, cv_masque, lm_masque, seuils
) -> dict:
    offre_id = offre["id"]
    origine = ats.matcher_offre(conn, offre_id)["score_matching"]  # en cache si déjà calculé
    essai = meilleur_essai(
        float(origine),
        lambda: _rediger_et_relire(
            offre, texte_offre, langue, ident, cv_source, cv_masque, lm_masque
        ),
    )
    cv, verif_cv, modele = essai["cv"], essai["verif_cv"], essai["modele"]
    lm, verif_lm = essai["lm"], essai["verif_lm"]
    fichiers, noms, diag = essai["fichiers"], essai["noms"], essai["diag"]
    compat, matching = essai["compat"], essai["matching"]

    motifs = []
    if compat["score"] < seuils["ats_friendly_min"]:
        motifs.append(f"compatibilité ATS {compat['score']}/100 (< {seuils['ats_friendly_min']})")
    if matching["score_matching"] < float(origine) - TOLERANCE_MATCHING:
        motifs.append(
            f"matching {matching['score_matching']} inférieur au CV d'origine ({origine})"
        )
    # Une compétence non prouvée retirée laisse un document juste : on le note sans bloquer.
    # Une phrase retirée (accroche, puce, paragraphe) peut casser le texte : à relire.
    retires = [r for r in verif_cv["retires"] + verif_lm["retires"] if r.get("ou") != "compétence"]
    if retires:
        motifs.append(
            f"{len(retires)} phrase(s) retirée(s) par le garde-fou de véracité : à relire"
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
        "essais": essai["essais"],
        "palier_une_page": essai.get("compact", 0),
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
