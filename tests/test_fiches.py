"""Recherche de fiche chez l'employeur : identifiants, identité du site, rapprochement (sans réseau)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.fiches import recherche
from app.fiches.ats import Poste, Tableau
from app.fiches.recherche import est_son_site, similarite, slugs, traiter_entreprise
from app.pertinence import est_data


def test_slugs():
    assert slugs("BPCE Financement") == ["bpcefinancement", "bpce-financement", "bpce"]
    assert slugs("Datadog") == ["datadog"]
    assert "seb" in slugs("Groupe SEB")


def _poste(titre, lieu="Paris, France"):
    return Poste(
        titre, lieu, f"https://ats.example/{titre}", date(2026, 11, 1), "Missions : SQL, dbt"
    )


def test_est_son_site():
    t = Tableau("greenhouse", "sia", [_poste("x")], nom_employeur="Sia Partners", pays={"FR"})
    assert est_son_site(t, "Sia Partners")
    espagne = Tableau(
        "smartrecruiters", "thales", [_poste("x", "Madrid")], nom_employeur="Thales", pays={"ES"}
    )
    assert not est_son_site(espagne, "Thales")
    homonyme = Tableau(
        "greenhouse", "resilience", [_poste("x")], nom_employeur="Resilience", pays={"FR"}
    )
    assert not est_son_site(homonyme, "Resilience Care")
    t_us = Tableau(
        "greenhouse", "sia", [_poste("x", "Austin, TX")], nom_employeur="SIA Insurance Group"
    )
    assert not est_son_site(t_us, "Sia Partners France")
    assert est_son_site(Tableau("lever", "acme", [_poste("x")], pays={"FR"}), "Acme")
    assert not est_son_site(Tableau("lever", "acme", [_poste("x")], pays={"US"}), "Acme")


@pytest.mark.parametrize(
    ("intitule", "ville", "titre", "lieu", "mini", "maxi"),
    [
        ("Data Analyst (F/H)", "Paris", "Data Analyst - F/H", "Paris, France", 95, 100),
        ("Junior Data Engineer", "Paris", "Senior Data Engineer", "Paris", 0, 0),  # niveau
        ("Data Analyst", "Paris", "Data Analyst", "Lyon, France", 0, 0),  # ville
        ("Data Analyst", "Paris", "Head of Sales", "Paris", 0, 59),
    ],
)
def test_similarite(intitule, ville, titre, lieu, mini, maxi):
    assert mini <= similarite(intitule, ville, _poste(titre, lieu)) <= maxi


def test_traiter_entreprise(monkeypatch):
    autres = [_poste(f"Commercial {i}") for i in range(5)]
    tableau = Tableau(
        "lever",
        "acme",
        [_poste("Data Analyst - CDI"), _poste("Head of Sales"), *autres],
        pays={"FR"},
    )
    monkeypatch.setattr(
        recherche, "CONNECTEURS", {"lever": lambda slug: tableau if slug == "acme" else None}
    )
    vieux = datetime.now(UTC) - timedelta(days=10)
    offres = [
        {"id": 1, "intitule": "Data Analyst (H/F)", "ville": "Paris", "recu_le": vieux},
        {"id": 2, "intitule": "Data Engineer Python", "ville": "Paris", "recu_le": vieux},
        {
            "id": 3,
            "intitule": "Data Engineer Python",
            "ville": "Paris",
            "recu_le": datetime.now(UTC),
        },
    ]
    ent = {"id": 9, "nom_norm": "acme", "nom_affiche": "Acme", "ats": None, "ats_slug": None}
    res = traiter_entreprise(ent, offres)
    assert (res.ats, res.slug) == ("lever", "acme")
    verdicts = {v.offre_id: v for v in res.verdicts}
    assert verdicts[1].statut == "valide" and verdicts[1].url.endswith("Data Analyst - CDI")
    assert verdicts[2].statut == "expiree"  # absente d'un site sûr
    assert verdicts[3].statut is None  # trop récente pour conclure


def test_sans_ats(monkeypatch):
    monkeypatch.setattr(recherche, "CONNECTEURS", {"lever": lambda slug: None})
    ent = {
        "id": 9,
        "nom_norm": "inconnue",
        "nom_affiche": "Inconnue",
        "ats": None,
        "ats_slug": None,
    }
    res = traiter_entreprise(
        ent, [{"id": 1, "intitule": "Data Analyst", "ville": "", "recu_le": datetime.now(UTC)}]
    )
    assert res.ats == "aucun" and res.verdicts[0].statut is None


@pytest.mark.parametrize(
    ("intitule", "attendu"),
    [
        ("Data Analyst (F/H)", True),
        ("Chargé d'études statistiques", True),
        ("Consultant Power BI", True),
        ("Analyste de données RH", True),
        ("Ingénieur développement logiciel C", False),
        ("Contrôleur de Gestion Réseau", False),
    ],
)
def test_est_data(intitule, attendu):
    assert est_data(intitule) is attendu


def test_pas_d_expiree_sur_petit_site_ou_autre_ville(monkeypatch):
    petit = Tableau(
        "lever",
        "acme",
        [_poste("Head of Sales"), _poste("Data Scientist", "Casablanca")],
        pays={"FR"},
    )
    monkeypatch.setattr(recherche, "CONNECTEURS", {"lever": lambda slug: petit})
    vieux = datetime.now(UTC) - timedelta(days=10)
    ent = {"id": 9, "nom_norm": "acme", "nom_affiche": "Acme", "ats": None, "ats_slug": None}
    offres = [
        {"id": 1, "intitule": "Data Engineer", "ville": "Paris", "recu_le": vieux},
        {"id": 2, "intitule": "Data Scientist", "ville": "Paris", "recu_le": vieux},
    ]
    v = {x.offre_id: x for x in traiter_entreprise(ent, offres).verdicts}
    assert v[1].statut is None  # 2 postes seulement : on ne conclut pas
    assert v[2].statut is None and "Casablanca" in v[2].note


def test_adzuna_second_essai(monkeypatch):
    from app.fiches import adzuna

    monkeypatch.setattr(recherche, "CONNECTEURS", {"lever": lambda slug: None})
    monkeypatch.setattr(adzuna, "disponible", lambda: True)
    annonce = adzuna.Annonce(_poste("Data Analyst H/F", "Paris, Ile-de-France"), "ACME SAS")
    monkeypatch.setattr(adzuna, "chercher", lambda intitule, ville: [annonce])
    ent = {"id": 9, "nom_norm": "acme", "nom_affiche": "Acme", "ats": None, "ats_slug": None}
    offres = [
        {"id": 1, "intitule": "Data Analyst (F/H)", "ville": "Paris", "recu_le": datetime.now(UTC)},
        {"id": 2, "intitule": "Data Engineer", "ville": "Paris", "recu_le": datetime.now(UTC)},
    ]
    v = {x.offre_id: x for x in traiter_entreprise(ent, offres).verdicts}
    assert v[1].statut == "valide" and v[1].texte == "" and "Adzuna" in v[1].note
    assert v[2].statut is None


def test_adzuna_employeur():
    from app.fiches.adzuna import meme_employeur

    assert meme_employeur("Acme", "ACME SAS")
    assert meme_employeur("BPCE Financement", "BPCE FINANCEMENT")
    assert not meme_employeur("Sia", "Asia Pacific Recruit")
