import psycopg
import pytest

from app import activite
from app.config import database_url


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(database_url(), row_factory=psycopg.rows.dict_row)
        c.execute("SELECT 1 FROM activites LIMIT 1")
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")
    yield c
    c.rollback()
    c.execute("DELETE FROM activites WHERE agent = 'test_activite'")
    c.commit()
    c.close()


def test_debut_fin_visibles_tout_de_suite(conn):
    aid = activite.debut(
        "test_activite", "Prépare CV + LM + mail", entreprise="Acme", intitule="Data Analyst"
    )
    r = conn.execute("SELECT statut, action FROM v_activites WHERE id = %s", (aid,)).fetchone()
    assert r == {"statut": "en_cours", "action": "Prépare CV + LM + mail"}  # visible avant la fin
    activite.fin(aid, "Dossier prêt à envoyer", "contact publié trouvé")
    r = conn.execute(
        "SELECT statut, action, detail, fini_le IS NOT NULL f FROM activites WHERE id = %s", (aid,)
    ).fetchone()
    assert r == {
        "statut": "fait",
        "action": "Dossier prêt à envoyer",
        "detail": "contact publié trouvé",
        "f": True,
    }


def test_noter_et_erreur(conn):
    activite.noter("test_activite", "Candidature envoyée", entreprise="Acme", detail="à rh@acme.fr")
    aid = activite.debut("test_activite", "Analyse le poste")
    activite.fin(aid, "Analyse impossible", "texte trop court", erreur=True)
    statuts = [
        r["statut"]
        for r in conn.execute(
            "SELECT statut FROM activites WHERE agent = 'test_activite' ORDER BY id"
        )
    ]
    assert statuts == ["fait", "erreur"]


def test_une_panne_du_fil_n_arrete_pas_l_agent(monkeypatch):
    monkeypatch.setattr(
        activite, "connexion", lambda: (_ for _ in ()).throw(psycopg.OperationalError("hors ligne"))
    )
    assert activite.debut("test_activite", "x") is None
    activite.fin(None)  # sans erreur
