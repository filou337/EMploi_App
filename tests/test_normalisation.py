import pytest

from app.collecte.normalisation import (
    cle_floue,
    contrat_depuis_intitule,
    norm_entreprise,
    norm_intitule,
    norm_ville,
)


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("AXA en France", "axa"),
        ("Groupe SEB", "seb"),
        ("Acme SAS", "acme"),
        ("Capgemini Invent", "capgemini invent"),
        ("France", "france"),  # on ne vide jamais complètement le nom
    ],
)
def test_norm_entreprise(brut, attendu):
    assert norm_entreprise(brut) == attendu


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("Data Analyst (F/H)", "data analyst"),
        ("Data Quality Analyst - CDI - F/H/X", "data quality analyst"),
        ("CDD - Analyste Risques Statistiques H/F", "analyste risques statistiques"),
        ("Ingénieur Data", "ingenieur data"),
    ],
)
def test_norm_intitule(brut, attendu):
    assert norm_intitule(brut) == attendu


def test_norm_ville():
    assert norm_ville("Ville de Paris") == "paris"
    assert norm_ville("Île-de-France, France") == "ile de france"
    assert norm_ville("L'Île-Saint-Denis") == "l ile saint denis"
    assert norm_ville("Saint-Denis (93)") == "saint denis"
    assert norm_ville("78280 Guyancourt") == "guyancourt"


def test_cle_floue_identique_entre_plateformes():
    assert cle_floue("Veepee", "Data Analyst (H/F)", "Paris") == cle_floue(
        "VEEPEE SAS", "Data Analyst - CDI - F/H", "Ville de Paris"
    )


@pytest.mark.parametrize(
    ("intitule", "attendu"),
    [
        ("Data Engineer - Stage", "stage"),
        ("Data Analyst, Revenue Operations Intern (M/F/X)", "stage"),
        ("ALTERNANCE - CHARGE ANALYSES (F/H)", "alternance"),
        ("CDD - Analyste Risques", "CDD"),
        ("Data Quality Analyst - CDI - F/H/X", "CDI"),
        ("Data Analyst (F/H)", None),
    ],
)
def test_contrat(intitule, attendu):
    assert contrat_depuis_intitule(intitule) == attendu
