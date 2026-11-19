import io
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from openpyxl import Workbook

from app import criteres, partenaires
from app.agents import veille
from app.db import connexion
from app.fiches.adzuna import Annonce
from app.fiches.ats import Poste


def _classeur(lignes) -> bytes:
    wb = Workbook()
    for ligne in lignes:
        wb.active.append(ligne)
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


def test_lecture_du_fichier_sans_doublons():
    contenu = _classeur(
        [
            ("Entreprise ", "Secteur ", "Site carrière "),
            (
                "CORAIL ANALYTICS ",
                "CONSEIL",
                "https://corail.example.com/jobs   / https://autre.example.com",
            ),
            ("Corail Analytics", "CONSEIL", "https://doublon.example.com"),
            ("Ernast & Young", "AUDIT", None),
            (None, None, None),
        ]
    )
    lus = partenaires.lire("liste.xlsx", contenu)
    assert [p.nom for p in lus] == ["CORAIL ANALYTICS", "EY"]
    assert lus[0].site == "https://corail.example.com/jobs" and lus[0].secteur == "Conseil"


def test_fichier_sans_colonne_entreprise_refuse():
    with pytest.raises(ValueError):
        partenaires.lire("liste.xlsx", _classeur([("Nom du poste",), ("x",)]))
    with pytest.raises(ValueError):
        partenaires.lire("liste.csv", b"")


@pytest.mark.parametrize(
    ("partenaire", "employeur", "attendu"),
    [
        ("CAP Gemini", "Capgemini", True),
        ("Capgemini", "Capgemini Invent", True),
        ("Orange", "Orange SA", True),
        ("Banque populaire Grand Ouest", "Banque Populaire", True),
        ("Crit", "Criteo", False),
        ("Spie", "Spiegel Conseil", False),
        ("Orange", "Jus d'orange bio", False),
    ],
)
def test_rapprochement_des_noms(partenaire, employeur, attendu):
    assert partenaires.meme_entreprise(partenaire, employeur) is attendu


def test_recherche_complete_limitee_aux_sites_inconnus():
    recent = datetime.now(UTC) - timedelta(days=1)
    assert veille._recherche_complete({"ats": None})
    assert veille._recherche_complete({"ats": "aucun", "ats_verifie_le": None})
    assert not veille._recherche_complete({"ats": "aucun", "ats_verifie_le": recent})
    assert not veille._recherche_complete({"ats": "lever", "ats_verifie_le": None})


@pytest.fixture
def conn():
    try:
        with connexion() as c:
            c.execute("SELECT liste FROM veille_entreprises LIMIT 1")
            with c.transaction(force_rollback=True):
                yield c
    except (psycopg.OperationalError, psycopg.errors.UndefinedColumn) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")


def test_import_puis_offres_adzuna_des_partenaires(conn, monkeypatch):
    lus = [
        partenaires.Partenaire("Zéphyr Données Test", "Conseil", "https://zephyr.example.com"),
        partenaires.Partenaire("Brume Industrie Test", None, None),
    ]
    r = partenaires.importer(conn, lus, "Liste test")
    assert r == {"liste": "Liste test", "ajoutees": 2, "deja_surveillees": 0}
    assert partenaires.importer(conn, lus, "Liste test")["deja_surveillees"] == 2

    annonces = [
        Annonce(
            Poste(
                "Data Analyst H/F",
                "Paris",
                "https://adzuna.example.com/ad/1?se=x",
                date.today(),
                "texte",
            ),
            "Zéphyr Données Test",
        ),
        Annonce(
            Poste("Data Analyst", "Paris", "https://adzuna.example.com/ad/2", date.today(), ""),
            "Une autre entreprise",
        ),
        Annonce(
            Poste("Comptable", "Paris", "https://adzuna.example.com/ad/3", date.today(), ""),
            "Brume Industrie Test",
        ),
    ]
    monkeypatch.setattr(veille.adzuna, "disponible", lambda: True)
    monkeypatch.setattr(veille.adzuna, "rechercher", lambda *a, **k: annonces)
    crit = {
        **criteres.lire(conn)["criteres"],
        "postes": ["data analyst"],
        "zone_ville": "",
        "zone_rayon_km": 0,
        "lieux": ["Paris"],
    }
    assert veille.partenaires_adzuna(conn, crit, forcer=True) == 1
    assert veille.partenaires_adzuna(conn, crit) == 0  # pause de 6 h
    assert veille.partenaires_adzuna(conn, crit, forcer=True) == 0  # déjà en base
    rows = conn.execute(
        "SELECT entreprise, intitule, liste, lien FROM v_offres_partenaires WHERE liste = 'Liste test'"
    ).fetchall()
    assert [(r["entreprise"], r["intitule"], r["lien"]) for r in rows] == [
        ("Zéphyr Données Test", "Data Analyst H/F", "https://adzuna.example.com/ad/1")
    ]


def test_stages_deja_reperes_sortis_du_pipeline(conn):
    from app.agents import matching
    from app.entreprises import entreprise_id

    eid = entreprise_id(conn, "Brume Industrie Test")
    ids = [
        conn.execute(
            "INSERT INTO offres (url_cle, cle_floue, entreprise_id, source, intitule,"
            " statut_validite, etape) VALUES (%s, %s, %s, %s, %s, 'valide', 'reperee') RETURNING id",
            (f"https://exemple.test/{i}", f"brume|{i}", eid, source, titre),
        ).fetchone()["id"]
        for i, (titre, source) in enumerate(
            [
                ("Apprenticeship - Risk Analyst", "linkedin"),
                ("Data Analyst (F/H)", "linkedin"),
                ("Stage - Data Analyst", "manuelle"),
            ]
        )
    ]
    crit = {"contrats_exclus": ["stage", "alternance"]}
    assert matching.retirer_contrats_exclus(conn, crit) >= 1
    etapes = [
        conn.execute("SELECT etape FROM offres WHERE id = %s", (i,)).fetchone()["etape"]
        for i in ids
    ]
    assert etapes == [None, "reperee", "reperee"]
