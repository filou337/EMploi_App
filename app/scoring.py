"""Scoring V2 : grille pondérée sur 100, justifiée critère par critère, puis classe A / B / C.

| Critère                    | Poids | Pleine note                                       |
|----------------------------|-------|---------------------------------------------------|
| Compétences techniques     | 40    | tous les indispensables dans le CV ou le profil   |
| Missions                   | 25    | missions déjà menées (expérience ou projet)       |
| Séniorité                  | 15    | 0 à 2 ans demandés                                |
| Entreprise et secteur      | 10    | secteur visé                                      |
| Contrat, lieu, télétravail | 10    | CDI/CDD, Île-de-France ou full remote             |

Le calcul est fait ICI, par le code, à partir de l'analyse de matching (qui extrait de l'offre
compétences, missions, séniorité, contrat et secteur, preuves vérifiées dans le CV) et de
`profil/competences.yaml`. Aucun appel LLM : `--rescorer` recalcule tout gratuitement.

Classes : C si une contrainte obligatoire est violée (contrat, lieu, séniorité, secteur exclu)
ou score < 70 ; A si score ≥ seuil (85) et fiche complète ; B sinon. Une offre connue seulement
par l'extrait d'une alerte est plafonnée à 84.

Usage :
  python -m app.scoring --rescorer                 # recalcule les offres déjà analysées
  python -m app.scoring --exporter-calibrage       # profil/calibrage.csv : 20 offres à noter
  python -m app.scoring --calibrer                 # compare tes notes et propose des poids
"""

import argparse
import csv
import itertools
import re
from functools import lru_cache
from pathlib import Path

import psycopg
import yaml
from psycopg.types.json import Jsonb
from rapidfuzz import fuzz

from app import criteres as crit_mod
from app.ats.moteur import POIDS as POIDS_STATUT
from app.collecte.normalisation import contrat_depuis_intitule, niveau, sans_accents
from app.config import RACINE

PROFIL = RACINE / "profil" / "competences.yaml"  # personnel, ignoré par git
EXEMPLE = RACINE / "profil" / "competences.exemple.yaml"
CALIBRAGE = RACINE / "profil" / "calibrage.csv"
POIDS = {
    "competences": 40,
    "missions": 25,
    "seniorite": 15,
    "secteur": 10,
    "contrat_lieu": 10,
}
NIVEAU_PROFIL = {"maitrise": 1.0, "pratique": 0.8, "notions": 0.5}
STATUT_MISSION = {"realisee": 1.0, "proche": 0.6, "absente": 0.0}
SEUIL_B = 70
PLAFOND_EXTRAIT = 84
VERSION = 1


class ProfilInvalide(ValueError):
    pass


def _norm(t: str) -> str:
    return " " + re.sub(r"[^a-z0-9+#]+", " ", sans_accents(t or "").lower()).strip() + " "


@lru_cache(maxsize=4)
def _charger(chemin: Path, _mtime: float) -> dict:
    try:
        profil = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ProfilInvalide(f"{chemin.name} illisible : {e}") from e
    comp = profil.get("competences") or []
    if not comp:
        raise ProfilInvalide(f"{chemin.name} : aucune compétence déclarée.")
    for c in comp:
        if not isinstance(c, dict) or not c.get("nom"):
            raise ProfilInvalide(f"{chemin.name} : compétence sans nom ({c!r}).")
        if c.get("niveau", "pratique") not in NIVEAU_PROFIL:
            raise ProfilInvalide(f"{chemin.name} : niveau inconnu pour {c['nom']}.")
    poids = {**POIDS, **(profil.get("poids") or {})}
    if set(poids) != set(POIDS) or round(sum(poids.values())) != 100:
        raise ProfilInvalide(
            f"{chemin.name} : les poids doivent couvrir {list(POIDS)} et faire 100."
        )
    profil["poids"] = poids
    # Index des libellés normalisés -> facteur de niveau
    profil["_index"] = {
        _norm(x).strip(): NIVEAU_PROFIL[c.get("niveau", "pratique")]
        for c in comp
        for x in [c["nom"], *(c.get("alias") or [])]
    }
    return profil


def charger_profil(chemin: Path = PROFIL) -> dict:
    if not chemin.exists():
        raise ProfilInvalide(
            f"{chemin} introuvable : copie profil/competences.exemple.yaml en competences.yaml"
            " et remplis-le avec ton parcours."
        )
    return _charger(chemin, chemin.stat().st_mtime)


def faits(chemin: Path = PROFIL) -> list[str]:
    """Faits validés par toi (section « faits: » du profil), acceptés par le garde-fou de véracité.

    Ex. « 12 missions de data analyse », « méthode Agile Kanban ». Liste vide sans profil.
    """
    try:
        brut = charger_profil(chemin).get("faits") or []
    except ProfilInvalide:
        return []
    return [str(f).strip() for f in brut if str(f).strip()]


def niveau_profil(competence: str, profil: dict) -> float | None:
    """Facteur de niveau si la compétence est déclarée dans le profil (nom ou alias), sinon None."""
    n = _norm(competence)
    meilleur = None
    for libelle, facteur in profil["_index"].items():
        # « Power BI » couvre « Power BI Desktop » ; « SQL » couvre « SQL avancé ».
        if len(libelle) >= 2 and f" {libelle} " in n:
            meilleur = max(meilleur or 0, facteur)
    return meilleur


# --- Critères ------------------------------------------------------------------------------------
def _competences(m: dict, profil: dict) -> tuple[float, str]:
    hard = [c for c in m.get("competences", []) if c.get("type") == "hard"]
    if not hard:
        return 0.5, "aucune compétence technique extraite de l'offre"
    total = obtenu = 0.0
    manquants, via_profil, manquants_indisp = [], [], 0
    for c in hard:
        poids = 2.0 if c.get("obligatoire") else 1.0
        valeur = POIDS_STATUT.get(c.get("statut"), 0.0)
        if valeur == 0:
            facteur = niveau_profil(c.get("nom", ""), profil)
            if facteur:
                valeur = 0.9 * facteur
                via_profil.append(c["nom"])
            else:
                manquants.append(c["nom"] + (" (indispensable)" if c.get("obligatoire") else ""))
                manquants_indisp += bool(c.get("obligatoire"))
        total += poids
        obtenu += poids * valeur
    indisp = sum(1 for c in hard if c.get("obligatoire"))
    morceaux = [f"{indisp - manquants_indisp}/{indisp} indispensables couverts"] if indisp else []
    if via_profil:
        morceaux.append("via le profil : " + ", ".join(via_profil))
    if manquants:
        morceaux.append("manquent : " + ", ".join(manquants))
    return obtenu / total, " ; ".join(morceaux) or "toutes les compétences couvertes"


def _missions(m: dict, profil: dict, part_competences: float) -> tuple[float, str]:
    missions = m.get("missions")
    if not missions:
        return part_competences, "missions non extraites : reprise de la note des compétences"
    connues = [_norm(x) for x in profil.get("missions") or []]
    obtenu, absentes = 0.0, []
    for x in missions:
        valeur = STATUT_MISSION.get(x.get("statut"), 0.0)
        if valeur == 0 and connues:
            proche = max(fuzz.token_set_ratio(_norm(x.get("mission", "")), k) for k in connues)
            valeur = 0.6 if proche >= 70 else 0.0
        if valeur == 0:
            absentes.append(x.get("mission", "?"))
        obtenu += valeur
    faites = len(missions) - len(absentes)
    txt = f"{faites}/{len(missions)} missions déjà menées"
    if absentes:
        txt += " ; nouvelles : " + " ; ".join(absentes[:3])
    return obtenu / len(missions), txt


def _seniorite(offre: dict, m: dict, profil: dict) -> tuple[float, str, str | None]:
    niveaux = niveau(offre.get("intitule") or "")
    if niveaux & {"senior", "lead"}:
        return 0.0, "intitulé senior / lead", "niveau senior ou lead"
    mini = float((m.get("offre") or {}).get("experience_annees_min") or 0)
    maxi = float(profil.get("profil", {}).get("seniorite_max_annees", 2))
    if "junior" in niveaux and mini <= 3:
        return 1.0, "poste junior", None
    if mini <= maxi:
        return 1.0, f"{mini:g} an(s) demandé(s), dans la cible junior", None
    if mini >= 5:
        return 0.0, f"{mini:g} ans demandés", f"séniorité ({mini:g} ans demandés)"
    part = {3: 0.66, 4: 0.33}.get(int(mini), 0.33)
    return part, f"{mini:g} ans demandés pour un profil 0 à {maxi:g} an(s)", None


def _secteur(offre: dict, m: dict, profil: dict) -> tuple[float, str, str | None]:
    secteurs = profil.get("secteurs") or {}
    texte = _norm(f"{(m.get('offre') or {}).get('secteur', '')} {offre.get('entreprise') or ''}")
    for s in secteurs.get("exclus") or []:
        if _norm(s) in texte:
            return 0.0, f"secteur exclu ({s})", f"secteur exclu ({s})"
    for s in secteurs.get("vises") or []:
        if _norm(s) in texte:
            return 1.0, f"secteur visé ({s})", None
    nom = (m.get("offre") or {}).get("secteur") or "non précisé"
    return 0.7, f"secteur neutre ({nom})", None


def _contrat_lieu(offre: dict, m: dict, profil: dict, crit: dict) -> tuple[float, str, str | None]:
    o = m.get("offre") or {}
    contrat = (
        offre.get("contrat")
        or contrat_depuis_intitule(offre.get("intitule") or "")
        or o.get("contrat")
    )
    acceptes = [c.lower() for c in profil.get("profil", {}).get("contrats", ["CDI", "CDD"])]
    exclus = [c.lower() for c in crit["contrats_exclus"]]
    violation = None
    if not contrat:
        p_contrat, t_contrat = 0.7, "contrat non précisé"
    elif contrat.lower() in acceptes:
        p_contrat, t_contrat = 1.0, contrat.upper()
    elif contrat.lower() in exclus:
        p_contrat, t_contrat, violation = 0.0, f"{contrat} exclu", f"contrat exclu ({contrat})"
    else:
        p_contrat, t_contrat = 0.5, f"contrat {contrat}"
    lieu = offre.get("ville") or o.get("lieu") or ""
    remote = o.get("teletravail") == "total"
    zone = crit_mod.lieu_accepte(lieu, crit)
    if zone or remote:
        p_lieu, t_lieu = 1.0, "full remote" if remote and not zone else f"{lieu} (dans la zone)"
    elif zone is None:
        p_lieu, t_lieu = 0.7, "lieu non précisé"
    else:
        p_lieu, t_lieu = 0.0, f"{lieu} hors zone"
        violation = violation or f"lieu hors zone ({lieu})"
    if o.get("teletravail") == "hybride":
        t_lieu += ", hybride"
    return (p_contrat + p_lieu) / 2, f"{t_contrat} ; {t_lieu}", violation


# --- Grille --------------------------------------------------------------------------------------
def noter(offre: dict, matching: dict, profil: dict, crit: dict, seuil_a: float = 85) -> dict:
    """Grille V2 d'une offre. `offre` : intitule, ville, contrat, entreprise, fiche_complete."""
    poids = profil["poids"]
    parts, motifs = {}, []
    parts["competences"] = _competences(matching, profil)
    parts["missions"] = _missions(matching, profil, parts["competences"][0])
    for cle, fn in (
        ("seniorite", lambda: _seniorite(offre, matching, profil)),
        ("secteur", lambda: _secteur(offre, matching, profil)),
        ("contrat_lieu", lambda: _contrat_lieu(offre, matching, profil, crit)),
    ):
        part, txt, violation = fn()
        parts[cle] = (part, txt)
        if violation:
            motifs.append(violation)
    detail = {
        cle: {
            "note": round(poids[cle] * part, 1),
            "sur": poids[cle],
            "part": round(part, 3),
            "justification": txt,
        }
        for cle, (part, txt) in parts.items()
    }
    brut = round(sum(d["note"] for d in detail.values()), 1)
    complete = bool(offre.get("fiche_complete"))
    score = brut if complete else min(brut, PLAFOND_EXTRAIT)
    if motifs:
        classe, motif = "C", "contrainte violée : " + ", ".join(motifs)
    elif score < SEUIL_B:
        classe, motif = "C", f"score {score:g} < {SEUIL_B}"
    elif score >= seuil_a and complete:
        classe, motif = "A", None
    else:
        classe = "B"
        motif = "fiche incomplète (plafond 84)" if not complete and brut >= seuil_a else None
    return {
        "score": score,
        "score_brut": brut,
        "classe": classe,
        "motif": motif,
        "detail": detail,
        "cv_base": profil.get("profil", {}).get("cv_base"),
        "version": VERSION,
    }


def enregistrer(conn: psycopg.Connection, offre_id: int, grille: dict) -> None:
    conn.execute(
        "UPDATE offres SET score = %s, classe = %s, motif = %s, detail_score = %s, cv_base = %s"
        " WHERE id = %s",
        (
            grille["score"],
            grille["classe"],
            grille["motif"],
            Jsonb({k: grille[k] for k in ("detail", "score_brut", "version")}),
            grille["cv_base"],
            offre_id,
        ),
    )


def _offre(conn: psycopg.Connection, offre_id: int) -> dict | None:
    return conn.execute(
        "SELECT o.id, o.intitule, o.ville, o.contrat, o.fiche_complete, e.nom_affiche AS entreprise"
        " FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id WHERE o.id = %s",
        (offre_id,),
    ).fetchone()


def scorer(conn: psycopg.Connection, offre_id: int, matching: dict, params: dict) -> dict:
    """Calcule et enregistre la grille d'une offre à partir de son analyse de matching."""
    offre = _offre(conn, offre_id)
    grille = noter(
        offre, matching, charger_profil(), params["criteres"], params["seuils"]["fabrication"]
    )
    enregistrer(conn, offre_id, grille)
    return grille


def _analyses(conn: psycopg.Connection) -> list[dict]:
    """Dernière analyse de matching (CV par défaut) de chaque offre."""
    return conn.execute(
        """
        SELECT DISTINCT ON (a.offre_id) a.offre_id, a.resultat
        FROM analyses a JOIN documents d ON d.id = a.document_id AND d.par_defaut
        WHERE a.type = 'matching' AND a.offre_id IS NOT NULL
        ORDER BY a.offre_id, a.cree_le DESC
        """
    ).fetchall()


def rescorer(conn: psycopg.Connection) -> dict:
    from app import criteres

    params = criteres.lire(conn)
    bilan = {"A": 0, "B": 0, "C": 0}
    for a in _analyses(conn):
        g = scorer(conn, a["offre_id"], a["resultat"], params)
        bilan[g["classe"]] += 1
    return bilan


# --- Calibrage -----------------------------------------------------------------------------------
def exporter_calibrage(conn: psycopg.Connection, chemin: Path = CALIBRAGE, n: int = 20) -> int:
    rows = conn.execute(
        """
        SELECT o.id, e.nom_affiche AS entreprise, o.intitule, o.score, o.url_fiche, o.url_cle
        FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id
        WHERE o.detail_score ? 'detail' ORDER BY o.recu_le DESC LIMIT %s
        """,
        (n,),
    ).fetchall()
    with chemin.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["offre_id", "entreprise", "intitule", "lien", "score_outil", "ma_note"])
        for r in rows:
            w.writerow(
                [
                    r["id"],
                    r["entreprise"],
                    r["intitule"],
                    r["url_fiche"] or r["url_cle"],
                    r["score"],
                    "",
                ]
            )
    return len(rows)


def meilleurs_poids(echantillon: list[tuple[dict, float]], pas: int = 5) -> tuple[dict, float]:
    """Poids (multiples de `pas`, somme 100, chacun ≥ 5) qui minimisent l'écart moyen absolu.

    `echantillon` : liste de (parts par critère entre 0 et 1, note donnée par toi sur 100).
    """
    cles = list(POIDS)
    meilleur, ecart_min = dict(POIDS), float("inf")
    valeurs = range(pas, 100, pas)
    for combo in itertools.product(valeurs, repeat=len(cles) - 1):
        dernier = 100 - sum(combo)
        if dernier < pas:
            continue
        poids = dict(zip(cles, [*combo, dernier], strict=True))
        ecart = sum(
            abs(sum(poids[k] * parts[k] for k in cles) - note) for parts, note in echantillon
        ) / len(echantillon)
        if ecart < ecart_min - 1e-9:
            meilleur, ecart_min = poids, ecart
    return meilleur, round(ecart_min, 1)


def ecart_moyen(echantillon: list[tuple[dict, float]], poids: dict) -> float:
    return round(
        sum(abs(sum(poids[k] * p[k] for k in POIDS) - n) for p, n in echantillon)
        / len(echantillon),
        1,
    )


def calibrer(conn: psycopg.Connection, chemin: Path = CALIBRAGE) -> dict:
    notes = {}
    with chemin.open(encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if (r.get("ma_note") or "").strip():
                notes[int(r["offre_id"])] = float(r["ma_note"].replace(",", "."))
    if len(notes) < 5:
        raise ValueError(
            f"Seulement {len(notes)} note(s) dans {chemin.name} : il en faut au moins 5."
        )
    rows = conn.execute(
        "SELECT id, detail_score FROM offres WHERE id = ANY(%s)", (list(notes),)
    ).fetchall()
    echantillon = [
        ({k: v["part"] for k, v in r["detail_score"]["detail"].items()}, notes[r["id"]])
        for r in rows
        if r["detail_score"] and "detail" in r["detail_score"]
    ]
    actuels = charger_profil()["poids"]
    poids, ecart = meilleurs_poids(echantillon)
    return {
        "offres": len(echantillon),
        "ecart_actuel": ecart_moyen(echantillon, actuels),
        "poids_actuels": actuels,
        "poids_proposes": poids,
        "ecart_propose": ecart,
    }


def main() -> None:
    from app.db import connexion

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--rescorer", action="store_true")
    g.add_argument("--exporter-calibrage", action="store_true")
    g.add_argument("--calibrer", action="store_true")
    args = p.parse_args()
    with connexion() as conn:
        if args.rescorer:
            print("Offres rescorées :", rescorer(conn))
        elif args.exporter_calibrage:
            n = exporter_calibrage(conn)
            print(f"{n} offres dans {CALIBRAGE} : remplis la colonne « ma_note » (0 à 100).")
        else:
            r = calibrer(conn)
            print(f"{r['offres']} offres notées. Écart moyen actuel : {r['ecart_actuel']} points.")
            print(f"Poids proposés : {r['poids_proposes']} (écart {r['ecart_propose']}).")
            print("Pour les adopter, ajoute-les sous « poids: » dans profil/competences.yaml,")
            print("puis lance `python -m app.scoring --rescorer`.")


if __name__ == "__main__":
    main()
