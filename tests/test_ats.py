"""Moteur ATS sans réseau : score de compatibilité et calcul du matching (preuves vérifiées)."""

from app.ats.compatibilite import score
from app.ats.moteur import preuve_verifiee, score_matching

TEXTE = (
    """Jean Test
jean@example.com 06 12 34 56 78 linkedin.com/in/jt
Expériences
Data Analyst — Acme, sept. 2025 – mars 2026 : tableaux de bord Power BI, modèles dbt sur Snowflake.
Stage BI — Globex, 2024 – 2025 : requêtes SQL, automatisation Python.
Formation
Master Big Data, 2026
Compétences
SQL, Python, dbt, Power BI
"""
    + "mot " * 300
)

DIAG = {
    "pages": 1,
    "mots": 330,
    "sections": ["competences", "experience", "formation"],
    "coordonnees": {"email": True, "telephone": True, "linkedin": True},
    "alertes": [],
}


def test_compatibilite_bon_cv():
    r = score("pdf", TEXTE, DIAG)
    assert r["score"] >= 90
    assert {c["nom"] for c in r["criteres"]} >= {
        "Texte extractible",
        "Sections standard",
        "Dates exploitables",
    }
    assert sum(c["max"] for c in r["criteres"]) == 100


def test_compatibilite_cv_scanne():
    diag = dict(
        DIAG,
        mots=12,
        sections=[],
        coordonnees={},
        alertes=["Très peu de texte lisible : PDF scanné ou en image ?"],
    )
    assert score("pdf", "Jean Test", diag)["score"] < 30


def test_dates_incoherentes():
    r = score("pdf", TEXTE + "\nAlternance, 2026 – 2024", DIAG)
    dates = next(c for c in r["criteres"] if c["nom"] == "Dates exploitables")
    assert "Incohérentes" in dates["detail"]


def test_preuve_verifiee():
    assert preuve_verifiee("modèles dbt sur Snowflake", TEXTE)
    assert preuve_verifiee("modeles  DBT sur snowflake", TEXTE)  # accents, casse, espaces
    assert not preuve_verifiee("5 ans de Kubernetes en production", TEXTE)


def test_score_matching_retrograde_les_preuves_inventees():
    res = {
        "offre": {"experience_annees_min": 2},
        "competences": [
            {
                "nom": "dbt",
                "type": "hard",
                "obligatoire": True,
                "statut": "exacte",
                "preuve": "modèles dbt sur Snowflake",
            },
            {
                "nom": "Kubernetes",
                "type": "hard",
                "obligatoire": True,
                "statut": "exacte",
                "preuve": "déploie sur Kubernetes",
            },
            {
                "nom": "Power BI",
                "type": "hard",
                "obligatoire": False,
                "statut": "synonyme",
                "preuve": "tableaux de bord Power BI",
            },
            {
                "nom": "Autonomie",
                "type": "soft",
                "obligatoire": False,
                "statut": "absente",
                "preuve": "",
            },
        ],
        "experience": {"annees_pertinentes": 1},
        "formation_ok": True,
        "criteres_obligatoires_ok": [{"critere": "CDI", "respecte": True, "justification": ""}],
    }
    r = score_matching(res, TEXTE)
    kube = next(c for c in r["competences"] if c["nom"] == "Kubernetes")
    assert kube["statut"] == "absente" and kube["statut_modele"] == "exacte"
    # hard : (2*1 + 2*0 + 1*0.9) / 5 = 0.58 -> 29 ; exp 1/2 -> 10 ; formation 10 ; soft 0 ; critères 10
    assert r["detail_score"] == {
        "competences_techniques": 29.0,
        "experience": 10.0,
        "formation": 10.0,
        "soft_skills": 0.0,
        "criteres_obligatoires": 10.0,
    }
    assert r["score_matching"] == 59.0
    assert r["mots_cles_manquants"] == ["Kubernetes", "Autonomie"]
