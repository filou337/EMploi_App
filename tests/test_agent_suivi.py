"""Agent Suivi : un accusé de réception confirme l'envoi du dossier de la même entreprise."""

import psycopg
import pytest

from app.agents.suivi import rapprocher
from app.db import connexion


@pytest.fixture
def conn():
    try:
        with connexion() as c, c.transaction(force_rollback=True):
            yield c
    except psycopg.OperationalError as e:
        pytest.skip(f"Base injoignable : {e}")


def test_accuse_confirme_le_dossier(conn):
    eid = conn.execute(
        "INSERT INTO entreprises (nom_norm, nom_affiche) VALUES ('zz test suivi', 'ZZ Test')"
        " RETURNING id"
    ).fetchone()["id"]
    oid = conn.execute(
        "INSERT INTO offres (url_cle, cle_floue, source, entreprise_id, intitule, etape, etape_maj_le)"
        " VALUES ('t:suivi', 'zz|data analyst|paris', 'linkedin', %s, 'Data Analyst', 'a_envoyer', now())"
        " RETURNING id",
        (eid,),
    ).fetchone()["id"]
    autre = conn.execute(
        "INSERT INTO offres (url_cle, cle_floue, source, entreprise_id, intitule, etape, etape_maj_le)"
        " VALUES ('t:suivi2', 'zz|data engineer|paris', 'linkedin', %s, 'Data Engineer', 'reperee', now())"
        " RETURNING id",
        (eid,),
    ).fetchone()["id"]
    assert rapprocher(conn) == 0  # pas encore d'accusé
    cid = conn.execute(
        "INSERT INTO candidatures (entreprise_id, statut, postule_le, origine, boite, gmail_message_id)"
        " VALUES (%s, 'postule', current_date, 'manuelle', 'principale', 'msg-test') RETURNING id",
        (eid,),
    ).fetchone()["id"]
    assert rapprocher(conn) == 1
    assert (
        conn.execute("SELECT etape FROM offres WHERE id = %s", (oid,)).fetchone()["etape"]
        == "envoyee"
    )
    assert (
        conn.execute("SELECT offre_id FROM candidatures WHERE id = %s", (cid,)).fetchone()[
            "offre_id"
        ]
        == oid
    )
    assert (
        conn.execute("SELECT etape FROM offres WHERE id = %s", (autre,)).fetchone()["etape"]
        == "reperee"
    )
    assert rapprocher(conn) == 0  # rejouable sans effet


@pytest.mark.parametrize(
    ("adresse", "attendu"),
    [
        ("Hugues Test <h.test@tsg-solutions.com>", "h.test@tsg-solutions.com"),
        (
            '"Équipe RH de Datatorii" <datat-f7eb@candidates.welcomekit.co>',
            "datat-f7eb@candidates.welcomekit.co",
        ),
        ("AccorCorpo <notification@smartrecruiters.com>", None),
        ("EIFFAGE - Workday <eiffage@myworkday.com>", None),
        ("<noreply@konnevia.fr>", None),
        ("Lobellia <no-reply@lobellia.recruitee.com>", None),
    ],
)
def test_contact_pour_relance(adresse, attendu):
    from app.suivi.historique import contact

    assert contact(adresse) == attendu


def test_relances_dues(conn):
    eid = conn.execute(
        "INSERT INTO entreprises (nom_norm, nom_affiche) VALUES ('zz relance', 'ZZ') RETURNING id"
    ).fetchone()["id"]

    def cand(jours, statut="postule", relance=None, type_="offre"):
        return conn.execute(
            "INSERT INTO candidatures (entreprise_id, type, statut, postule_le, relance_le, origine)"
            " VALUES (%s, %s, %s, current_date - %s, %s, 'manuelle') RETURNING id",
            (eid, type_, statut, jours, relance),
        ).fetchone()["id"]

    trop_tot, due, trop_vieille, spont = cand(3), cand(8), cand(60), cand(10, type_="spontanee")
    seconde = cand(
        20,
        "relance",
        __import__("datetime").date.today() - __import__("datetime").timedelta(days=8),
    )
    dues = {
        r["id"]: r["numero"]
        for r in conn.execute("SELECT id, numero FROM v_relances WHERE entreprise = 'ZZ'")
    }
    assert dues == {due: 1, seconde: 2}
    assert conn.execute("SELECT marquer_relance(%s) AS n", (due,)).fetchone()["n"] == 1
    assert (
        conn.execute("SELECT statut FROM candidatures WHERE id = %s", (due,)).fetchone()["statut"]
        == "relance"
    )
    assert {trop_tot, trop_vieille, spont}.isdisjoint(dues)
