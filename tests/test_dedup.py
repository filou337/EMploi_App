"""Doublons flous, fraîcheur et garde-fou des 3 mois."""

from datetime import UTC, date, datetime

import psycopg
import pytest

from app.collecte.normalisation import cle_floue
from app.db import connexion
from app.dedup import candidature_recente, dedupliquer, meme_offre
from app.validite import trop_ancienne


@pytest.mark.parametrize(
    ("a", "b", "attendu"),
    [
        (
            ("Veepee", "Data Analyst (H/F)", "Paris"),
            ("VEEPEE SAS", "Data Analyst - CDI", "Ville de Paris"),
            True,
        ),
        (
            ("Hublo", "Junior Data Engineer", "Paris"),
            ("Hublo", "Data Engineer Junior (f/h/n)", "Paris"),
            True,
        ),
        (("Alten", "Data Analyst", "Paris"), ("Alten", "Data Analyst", "Toulouse"), False),
        (("Alten", "Data Analyst", "Paris"), ("Alten", "Data Engineer", "Paris"), False),
        (("Thales", "Ingénieur Data", "Vélizy"), ("Safran", "Ingénieur Data", "Vélizy"), False),
        (
            (
                "KPMG France",
                "Consultant Junior Transaction Services Deal Analytics F/H",
                "Courbevoie",
            ),
            (
                "KPMG France",
                "Consultant Senior Transaction Services Deal Analytics F/H",
                "Courbevoie",
            ),
            False,
        ),
        (("Acme", "Data Analyst - Stage", "Paris"), ("Acme", "Data Analyst", "Paris"), False),
    ],
)
def test_meme_offre(a, b, attendu):
    assert meme_offre(cle_floue(*a), cle_floue(*b), a[1], b[1]) is attendu


def test_contrats_differents():
    a, b = ("Acme", "CDD - Data Analyst F/H", "Paris"), ("Acme", "CDI - Data Analyst F/H", "Paris")
    assert not meme_offre(cle_floue(*a), cle_floue(*b), a[1], b[1])
    c = ("Acme", "Data analyst F/H", "Paris")  # contrat inconnu d'un côté : on ne tranche pas
    assert meme_offre(cle_floue(*a), cle_floue(*c), a[1], c[1])


AUJOURDHUI = date(2026, 11, 12)


def _dt(j: int, m: int = 11) -> datetime:
    return datetime(2026, m, j, 9, tzinfo=UTC)


def test_fraicheur_date_publication_connue():
    assert trop_ancienne(date(2026, 10, 20), _dt(11), AUJOURDHUI)  # 23 jours
    assert not trop_ancienne(date(2026, 10, 25), _dt(11), AUJOURDHUI)  # 18 jours


def test_fraicheur_alerte_ancienne_prouve_offre_ancienne():
    assert trop_ancienne(None, _dt(15, 10), AUJOURDHUI)  # alerte il y a 28 jours


def test_fraicheur_alerte_recente_ne_prouve_rien():
    assert not trop_ancienne(None, _dt(10), AUJOURDHUI)


@pytest.fixture
def conn():
    try:
        with connexion() as c, c.transaction(force_rollback=True):
            yield c
    except psycopg.OperationalError as e:
        pytest.skip(f"Base injoignable : {e}")


def _offre(conn, url, cle, recu_le):
    return conn.execute(
        "INSERT INTO offres (url_cle, cle_floue, source, recu_le)"
        " VALUES (%s, %s, 'linkedin', %s) RETURNING id",
        (url, cle, recu_le),
    ).fetchone()["id"]


def test_dedupliquer_garde_la_plus_ancienne(conn):
    cle = cle_floue("Zorglub", "Data Analyst (H/F)", "Paris")
    recent = _offre(conn, "t:2", cle_floue("ZORGLUB SAS", "Data Analyst", "Paris"), _dt(10))
    ancien = _offre(conn, "t:1", cle, _dt(1))
    autre = _offre(conn, "t:3", cle_floue("Zorglub", "Data Engineer", "Paris"), _dt(5))
    total = dedupliquer(conn)
    statuts = {
        r["id"]: (r["statut_validite"], r["doublon_de"])
        for r in conn.execute(
            "SELECT id, statut_validite, doublon_de FROM offres WHERE id = ANY(%s)",
            ([recent, ancien, autre],),
        )
    }
    assert statuts[recent] == ("doublon", ancien)
    assert statuts[ancien][0] != "doublon"
    assert statuts[autre][0] != "doublon"
    assert dedupliquer(conn) == total  # relancer donne le même résultat


def test_candidature_recente(conn):
    eid = conn.execute(
        "INSERT INTO entreprises (nom_norm) VALUES ('zorglub test') RETURNING id"
    ).fetchone()["id"]
    assert candidature_recente(conn, eid, AUJOURDHUI) is None
    conn.execute(
        "INSERT INTO candidatures (entreprise_id, postule_le, origine) VALUES (%s, %s, 'manuelle')",
        (eid, date(2026, 9, 1)),
    )
    assert candidature_recente(conn, eid, AUJOURDHUI) == date(2026, 9, 1)
    assert candidature_recente(conn, eid, date(2026, 12, 15)) is None  # plus de 3 mois
