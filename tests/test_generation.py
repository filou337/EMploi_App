"""Agent Dossier sans réseau : garde-fou de véracité, assemblage du CV, coordonnées, rendu."""

import io

import docx
import pytest

from app import documents
from app.ats import compatibilite
from app.generation import adaptation, identite, rendu, verite

CV_TEXTE = """Camille EXEMPLE
camille.exemple@example.com · 06 11 22 33 44 · Paris (75011) · linkedin.com/in/camille-exemple
Expériences professionnelles
Analytics Engineer (alternance) — Société Alpha, sept. 2024 – sept. 2026
Modélisation de 40 modèles dbt sur Snowflake. Tableaux de bord Power BI pour 60 utilisateurs.
Compétences : SQL, Python, dbt, Snowflake, Power BI, Airflow
"""
CV = {
    "titre": "Analytics Engineer junior",
    "resume": "Analytics Engineer junior.",
    "experiences": [
        {
            "poste": "Analytics Engineer (alternance)",
            "entreprise": "Société Alpha",
            "debut": "sept. 2024",
            "fin": "sept. 2026",
            "missions": [
                "Modélisation de 40 modèles dbt sur Snowflake.",
                "Tableaux de bord Power BI pour 60 utilisateurs.",
            ],
        }
    ],
    "formations": [
        {"diplome": "Master Big Data", "etablissement": "Université Exemple", "annee": "2026"}
    ],
    "competences_techniques": ["SQL", "Python", "dbt", "Snowflake", "Power BI", "Airflow"],
    "soft_skills": ["Curieuse", "Rigoureuse"],
    "langues": [{"langue": "Français", "niveau": "C2"}, {"langue": "Anglais", "niveau": "C1"}],
}


def test_verite_termes_et_chiffres():
    assert verite.controler("Pipelines dbt sur Snowflake, 40 modèles", CV_TEXTE)["ok"]
    r = verite.controler("Déploiement Kubernetes et modèles Looker pour 500 utilisateurs", CV_TEXTE)
    assert r["termes_inventes"] == ["kubernetes", "looker"]
    assert r["nombres_inventes"] == ["500"]
    assert verite.controler("Power BI", CV_TEXTE)["ok"]  # terme composé reconnu dans la source


def test_libelle_dans_cv():
    assert verite.libelle_dans_cv("Snowflake", CV_TEXTE)
    assert verite.libelle_dans_cv("power bi", CV_TEXTE)
    assert not verite.libelle_dans_cv("BigQuery", CV_TEXTE)


def test_assembler_cv_garde_les_faits_et_retire_l_invente():
    res = {
        "titre": "Analytics Engineer",
        "accroche": "Analytics Engineer junior : dbt, Snowflake et Power BI au service des métiers.",
        "competences": [
            {
                "categorie": "Data",
                "elements": [
                    {"libelle": "dbt", "source": "dbt"},
                    {"libelle": "BigQuery", "source": "BigQuery"},  # absent du CV
                    {"libelle": "Looker", "source": "Power BI"},  # libellé inventé, source réelle
                ],
            }
        ],
        "experiences": [
            {
                "index": 0,
                "poste": "Lead Data Engineer",
                "puces": [
                    {"texte": "Conçu 40 modèles dbt sur Snowflake.", "source": 0},
                    {
                        "texte": "Migré l'entrepôt vers BigQuery pour 300 utilisateurs.",
                        "source": 1,
                    },  # inventé
                    {"texte": "Puce sans source.", "source": 9},
                ],
            }
        ],
        "formations": [{"index": 0, "diplome": "Master Big Data"}],
        "qualites": ["Curieuse"],
        "mots_cles_offre_repris": ["dbt"],
    }
    cv, verif = adaptation.assembler_cv(CV, res, CV_TEXTE)
    exp = cv["experiences"][0]
    assert (exp["entreprise"], exp["debut"], exp["fin"]) == (
        "Société Alpha",
        "sept. 2024",
        "sept. 2026",
    )
    assert exp["puces"] == ["Conçu 40 modèles dbt sur Snowflake."]
    # « Looker » cité avec la source « Power BI » : remplacé par le terme du CV ; les compétences
    # non placées par le LLM sont ajoutées à la fin (aucun mot-clé du CV n'est perdu).
    assert cv["competences"] == [
        {
            "categorie": "Data",
            "elements": ["dbt", "Power BI", "SQL", "Python", "Snowflake", "Airflow"],
        }
    ]
    assert not verif["ok"] and len(verif["retires"]) == 3
    assert cv["formations"][0]["etablissement"] == "Université Exemple"  # recopié, pas généré


def test_identite_extraire_et_masquer():
    ident = identite.extraire(CV_TEXTE)
    assert ident["email"] == "camille.exemple@example.com"
    assert ident["telephone"] == "06 11 22 33 44"
    assert "linkedin.com/in/camille-exemple" in ident["linkedin"]
    ident["nom"] = "Camille EXEMPLE"
    m = identite.masquer(CV_TEXTE + "\nSignature : Camille Exemple", ident)
    for fuite in ("camille", "Exemple", "06 11", "example.com"):
        assert fuite.lower() not in m.lower(), fuite
    assert "Snowflake" in m


def test_detecter_langue():
    assert (
        adaptation.detecter_langue("You will join our data team and work with the business") == "en"
    )
    assert (
        adaptation.detecter_langue(
            "Vous rejoindrez notre équipe data et travaillerez avec les métiers"
        )
        == "fr"
    )


CV_FINAL = {
    "titre": "Analytics Engineer junior",
    "accroche": "Analytics Engineer junior, je modélise des données fiables avec dbt et Snowflake et je les "
    "valorise dans Power BI pour les équipes métiers, avec un souci constant de qualité.",
    "competences": [
        {"categorie": "Data", "elements": ["SQL", "Python", "dbt", "Snowflake"]},
        {"categorie": "BI", "elements": ["Power BI"]},
    ],
    "experiences": [
        {
            "poste": "Analytics Engineer (alternance)",
            "entreprise": "Société Alpha",
            "debut": "sept. 2024",
            "fin": "sept. 2026",
            "puces": [
                "Conçu 40 modèles dbt sur Snowflake, testés et documentés pour les équipes data."
            ]
            * 4,
        }
    ],
    "formations": [
        {"diplome": "Master Big Data", "etablissement": "Université Exemple", "annee": "2026"}
    ],
    "langues": CV["langues"],
    "qualites": ["Curieuse", "Rigoureuse"],
}
IDENT = {
    "nom": "Camille EXEMPLE",
    "email": "camille.exemple@example.com",
    "telephone": "06 11 22 33 44",
    "ville": "Paris (75011)",
    "linkedin": "linkedin.com/in/camille-exemple",
}


def test_html_cv_echappe_et_titres_standard():
    html = rendu.html_cv(dict(CV_FINAL, accroche="<script>alert(1)</script>"), IDENT, "fr")
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    for titre in ("Compétences", "Expériences professionnelles", "Formation", "Langues"):
        assert titre in html
    assert "Professional Experience" in rendu.html_cv(CV_FINAL, IDENT, "en")


def test_docx_cv_lisible():
    d = docx.Document(io.BytesIO(rendu.docx_cv(CV_FINAL, IDENT, "fr")))
    texte = "\n".join(p.text for p in d.paragraphs)
    assert (
        "Camille EXEMPLE" in texte
        and "Société Alpha" in texte
        and "EXPÉRIENCES PROFESSIONNELLES" in texte
    )


@pytest.mark.skipif(
    not any(__import__("shutil").which(n) for n in ("google-chrome", "chromium")),
    reason="Chrome absent",
)
def test_pdf_cv_relu_par_le_checker():
    """Le PDF produit doit se relire comme un ATS : sections reconnues, coordonnées, bon score."""
    contenu = rendu.pdf(rendu.html_cv(CV_FINAL, IDENT, "fr"))
    lecture = documents.lire("cv.pdf", contenu)
    diag = documents.diagnostic(lecture, "cv")
    assert {"experience", "formation", "competences", "langues"} <= set(diag["sections"])
    assert diag["coordonnees"]["email"] and diag["coordonnees"]["telephone"]
    assert compatibilite.score("pdf", lecture.texte, diag)["score"] >= 85
