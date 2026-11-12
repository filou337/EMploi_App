import json
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from app import rapport
from app.db import connexion


@pytest.fixture
def conn():
    try:
        with connexion() as c:
            c.execute("SELECT 1 FROM rapports LIMIT 1")
            with c.transaction(force_rollback=True):
                yield c
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")


def _entreprise(conn, nom):
    return conn.execute(
        "INSERT INTO entreprises (nom_norm, nom_affiche) VALUES (%s, %s) RETURNING id",
        (f"test rapport {nom.lower()}", nom),
    ).fetchone()["id"]


def test_rapport_compte_la_periode(conn):
    maintenant = datetime.now(UTC)
    acme, globex = _entreprise(conn, "Acme"), _entreprise(conn, "Globex")
    conn.execute(
        "INSERT INTO offres (url_cle, cle_floue, source, intitule, pertinente, recu_le, classe, score)"
        " VALUES ('test:r1', 'a', 'manuelle', 'Data Analyst', TRUE, now(), 'B', 78),"
        "        ('test:r2', 'b', 'manuelle', 'Comptable', FALSE, now(), NULL, NULL),"
        "        ('test:r3', 'c', 'manuelle', 'Data Engineer', TRUE, now() - interval '10 days', NULL, NULL)"
    )
    c1 = conn.execute(
        "INSERT INTO candidatures (entreprise_id, poste, statut, postule_le) VALUES (%s, 'Data Analyst',"
        " 'postule', %s) RETURNING id",
        (acme, date.today() - timedelta(days=1)),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO candidatures (entreprise_id, poste, statut, postule_le) VALUES (%s, 'BI', 'postule', %s)",
        (globex, date.today() - timedelta(days=8)),  # hors période, mais relance J+7 due
    )
    conn.execute(
        "INSERT INTO reponses (candidature_id, gmail_message_id, boite, recu_le, classement)"
        " VALUES (%s, 'test-rapport-1', 'test', now(), 'positive')",
        (c1,),
    )
    debut = maintenant - timedelta(days=3)
    contenu = rapport.construire(conn, debut, maintenant + timedelta(seconds=1))
    n = contenu["chiffres"]
    assert n["offres_collectees"] >= 2 and n["offres_data"] >= 1
    assert n["reponses_positives"] >= 1
    assert any(x["entreprise"] == "Acme" for x in contenu["envoyees"])
    assert not any(x["entreprise"] == "Globex" for x in contenu["envoyees"])
    assert any(x["entreprise"] == "Globex" for x in contenu["relances"])
    assert any(x["intitule"] == "Data Analyst" for x in contenu["a_etudier"])
    assert "réponse(s) positive(s)" in rapport.resume(contenu)
    assert isinstance(
        next(x for x in contenu["a_etudier"] if x["intitule"] == "Data Analyst")["score"], float
    )
    json.dumps(contenu)  # sérialisable tel quel


def test_generer_seulement_quand_du(conn, monkeypatch):
    monkeypatch.setattr(rapport, "bureau", lambda *_a: None)
    premier = rapport.generer(conn, forcer=True, jours=3)
    assert "rapport" in premier
    second = rapport.generer(conn, jours=3)
    assert "prochain rapport" in second  # le précédent a moins de 3 jours
    conn.execute(
        "UPDATE rapports SET cree_le = now() - interval '4 days' WHERE id = %s",
        (premier["rapport"],),
    )
    assert "rapport" in rapport.generer(conn, jours=3)
    notif = conn.execute(
        "SELECT titre FROM notifications WHERE type = 'rapport' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert notif["titre"].startswith("Rapport du")


def test_resume_sans_activite():
    contenu = {
        "chiffres": {
            "reponses_positives": 0,
            "relances_dues": 0,
            "dossiers_prets": 0,
            "candidatures_envoyees": 0,
            "offres_data": 0,
            "refus": 0,
        }
    }
    assert rapport.resume(contenu) == "0 candidature(s) envoyée(s), 0 offre(s) data repérée(s)."
