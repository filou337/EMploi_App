from app.ats.anonymisation import masquer, nom_probable

CV = """Jean-Paul Martin
Analytics Engineer junior
jean.martin@example.com · +33 6 12 34 56 78 · 06.12.34.56.78 · linkedin.com/in/jpmartin
12 rue des Lilas, 69100 Villeurbanne — né le 01/02/2000
Expériences
Data Analyst chez Acme (2025-2026) : SQL, dbt. Martin a piloté 12 tableaux de bord.
"""


def test_nom_probable():
    assert nom_probable(CV) == "Jean-Paul Martin"
    assert nom_probable("Curriculum Vitae\nData Engineer\n") is None


def test_masquer():
    m = masquer(CV)
    for fuite in (
        "jean.martin",
        "12 34 56",
        "linkedin.com",
        "rue des Lilas",
        "Villeurbanne",
        "01/02/2000",
        "Martin",
    ):
        assert fuite not in m, fuite
    assert "[NOM]" in m and "[EMAIL]" in m and "[TÉLÉPHONE]" in m
    assert "Acme (2025-2026)" in m and "12 tableaux de bord" in m  # le contenu utile reste


def test_nom_probable_prenom_nom_et_faux_positifs():
    cv = "contact@example.com\nVilleurbanne\nPower BI\nDAX, Power Query\nCamille EXEMPLE\nAnalytics Engineer"
    assert nom_probable(cv) == "Camille EXEMPLE"
    assert nom_probable("Power BI\nAzure DevOps\nMaster BIG DATA") is None


def test_verifier_masque_bloque_une_fuite():
    import pytest

    from app.ats.anonymisation import FuiteDonnees, verifier_masque

    verifier_masque("[NOM] — [EMAIL]", ["Jean Exemple", "jean@example.com"])
    with pytest.raises(FuiteDonnees):
        verifier_masque("Signé : Jean EXEMPLE", ["Jean Exemple", "jean@example.com"])
