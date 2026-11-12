import pytest

from app import scoring
from app.criteres import DEFAUTS

CRIT = DEFAUTS["criteres"]


@pytest.fixture(scope="module")
def profil():
    return scoring.charger_profil(scoring.EXEMPLE)  # le vrai profil n'est pas versionné


def _matching(**offre):
    return {
        "offre": {
            "experience_annees_min": 0,
            "contrat": "CDI",
            "lieu": "Paris",
            "teletravail": "hybride",
            "secteur": "BTP",
            **offre,
        },
        "competences": [
            {"nom": "SQL", "type": "hard", "obligatoire": True, "statut": "exacte"},
            {"nom": "Power BI", "type": "hard", "obligatoire": True, "statut": "exacte"},
            {"nom": "dbt", "type": "hard", "obligatoire": False, "statut": "absente"},
            {"nom": "Rigueur", "type": "soft", "obligatoire": True, "statut": "semantique"},
        ],
        "missions": [
            {"mission": "Construire des pipelines", "statut": "realisee"},
            {"mission": "Créer des dashboards Power BI", "statut": "realisee"},
        ],
    }


OFFRE = {
    "intitule": "Analytics Engineer",
    "ville": "Paris (75)",
    "contrat": None,
    "fiche_complete": True,
}


def test_profil_valide(profil):
    assert sum(profil["poids"].values()) == 100
    assert scoring.niveau_profil("Power BI Desktop", profil) == 1.0
    assert scoring.niveau_profil("Azure Data Factory (ADF)", profil) == 0.8
    assert scoring.niveau_profil("Snowflake", profil) is None


def test_bonne_offre_classe_a(profil):
    g = scoring.noter(OFFRE, _matching(), profil, CRIT)
    assert g["classe"] == "A", g
    assert g["score"] >= 85
    assert set(g["detail"]) == set(scoring.POIDS)
    assert all(d["justification"] for d in g["detail"].values())
    assert "dbt" in g["detail"]["competences"]["justification"]


def test_competence_du_profil_rattrapee(profil):
    m = _matching()
    m["competences"].append(
        {"nom": "Databricks", "type": "hard", "obligatoire": True, "statut": "absente"}
    )
    g = scoring.noter(OFFRE, m, profil, CRIT)
    assert "via le profil : Databricks" in g["detail"]["competences"]["justification"]


def test_fiche_incomplete_plafonnee(profil):
    g = scoring.noter({**OFFRE, "fiche_complete": False}, _matching(), profil, CRIT)
    assert g["score"] <= 84 and g["classe"] == "B"
    assert "plafond" in g["motif"]


@pytest.mark.parametrize(
    ("offre", "matching", "attendu"),
    [
        ({"ville": "Lyon"}, {"lieu": "Lyon", "teletravail": "aucun"}, "lieu"),
        ({"contrat": "stage"}, {}, "contrat"),
        ({}, {"experience_annees_min": 6}, "séniorité"),
        ({"intitule": "Senior Data Engineer"}, {}, "niveau"),
    ],
)
def test_contraintes_violees_classe_c(profil, offre, matching, attendu):
    g = scoring.noter({**OFFRE, **offre}, _matching(**matching), profil, CRIT)
    assert g["classe"] == "C"
    assert attendu in g["motif"]


def test_full_remote_hors_zone_accepte(profil):
    g = scoring.noter({**OFFRE, "ville": "Lyon"}, _matching(teletravail="total"), profil, CRIT)
    assert g["classe"] != "C" or "lieu" not in (g["motif"] or "")


def test_sans_missions_reprend_competences(profil):
    m = _matching()
    del m["missions"]
    g = scoring.noter(OFFRE, m, profil, CRIT)
    d = g["detail"]
    assert d["missions"]["part"] == d["competences"]["part"]


def test_meilleurs_poids_retrouve_une_grille():
    vrais = {"competences": 60, "missions": 10, "seniorite": 10, "secteur": 10, "contrat_lieu": 10}
    parts = [
        {"competences": 1.0, "missions": 0.2, "seniorite": 1, "secteur": 0.7, "contrat_lieu": 1},
        {"competences": 0.3, "missions": 0.9, "seniorite": 1, "secteur": 1.0, "contrat_lieu": 0.7},
        {"competences": 0.6, "missions": 0.5, "seniorite": 0, "secteur": 0.7, "contrat_lieu": 1},
        {"competences": 0.9, "missions": 1.0, "seniorite": 1, "secteur": 0.0, "contrat_lieu": 0.5},
        {"competences": 0.1, "missions": 0.3, "seniorite": 0.66, "secteur": 1, "contrat_lieu": 1},
        {"competences": 0.5, "missions": 0.0, "seniorite": 1, "secteur": 0.7, "contrat_lieu": 0},
    ]
    echantillon = [(p, sum(vrais[k] * p[k] for k in vrais)) for p in parts]
    poids, ecart = scoring.meilleurs_poids(echantillon)
    assert ecart == 0 and poids == vrais
