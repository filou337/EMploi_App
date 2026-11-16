import pytest

from app import criteres, geo
from app.criteres import DEFAUTS, refus

C = DEFAUTS["criteres"]


@pytest.mark.parametrize(
    ("intitule", "lieu", "motif"),
    [
        ("Data Analyst (F/H)", "Paris (75)", None),
        ("Analytics Engineer", "Saint-Denis", None),
        ("Consultant Power BI", "", None),  # lieu inconnu : accepté
        ("Data Engineer", "Remote", None),
        ("Ingénieur développement C", "Paris", "intitulé"),
        ("Stage - Data Analyst", "Paris", "contrat"),
        ("Senior Data Engineer", "Paris", "niveau"),
        ("Data Analyst", "Lyon, France", "lieu"),
    ],
)
def test_refus(intitule, lieu, motif):
    r = refus(intitule, lieu, C)
    assert (r is None) if motif is None else (r is not None and r.startswith(motif))


# --- Zone « ville + rayon » (géocodage simulé, sans réseau) -------------------------------

POINTS = {
    "boulogne billancourt": (48.837, 2.243),
    "paris": (48.857, 2.352),
    "paris 8e arrondissement": (48.874, 2.312),
    "puteaux": (48.884, 2.238),
    "versailles": (48.805, 2.120),
    "cergy": (49.036, 2.063),
    "lyon": (45.758, 4.835),
}


@pytest.fixture
def zone(monkeypatch):
    monkeypatch.setattr(
        geo, "coordonnees", lambda nom, conn=None: POINTS.get(geo.bas(nom).rstrip(" 0123456789"))
    )
    return {**C, "zone_ville": "Boulogne-Billancourt", "zone_rayon_km": 15}


@pytest.mark.parametrize(
    ("lieu", "statut"),
    [
        ("Paris, Île-de-France, France", "dans_zone"),
        ("Paris 8e Arrondissement", "dans_zone"),
        ("La Défense", "dans_zone"),  # alias -> Puteaux
        ("Versailles (78)", "dans_zone"),
        ("Cergy", "hors_zone"),  # ~25 km
        ("Lyon, France", "hors_zone"),
        ("Lyon (télétravail complet)", "teletravail"),
        ("Remote", "teletravail"),
        ("Île-de-France, France", "a_verifier"),
        ("", "a_verifier"),
        ("Ville inconnue", "a_verifier"),
    ],
)
def test_statut_lieu_rayon(zone, lieu, statut):
    assert criteres.statut_lieu(lieu, zone)[0] == statut


def test_rayon_distance_et_refus(zone):
    statut, km = criteres.statut_lieu("Paris", zone)
    assert statut == "dans_zone" and 8 < km < 11
    assert refus("Data Analyst", "Cergy", zone).startswith("lieu")
    assert refus("Data Analyst", "Île-de-France", zone) is None  # vague : proposée, signalée


def test_liste_de_lieux_sans_rayon():
    assert criteres.statut_lieu("Courbevoie, Île-de-France", C)[0] == "dans_zone"
    assert criteres.statut_lieu("Remote", C)[0] == "teletravail"
    assert criteres.statut_lieu("Lyon", C)[0] == "hors_zone"
    assert criteres.statut_lieu(None, C)[0] == "a_verifier"


def test_signature_change_avec_la_zone(zone):
    assert criteres.signature_zone(zone) != criteres.signature_zone({**zone, "zone_rayon_km": 20})
    assert criteres.signature_zone(C).startswith("mots:")


def test_choix_du_geocodage_sur():
    def f(nom, score, lat=1.0, lon=2.0):
        return [
            {"properties": {"name": nom, "score": score}, "geometry": {"coordinates": [lon, lat]}}
        ]

    assert geo._choisir("paris 8e", f("Paris 8e Arrondissement", 0.86)) == (1.0, 2.0)
    assert geo._choisir("france", f("Fort-de-France", 0.68)) is None
    assert geo._choisir("massy", f("Massy", 0.4)) is None
