import httpx
import psycopg
import pytest

from app.db import connexion
from app.spontanees import agent, contacts

PAGE = """<html><body><nav>menu</nav>
<p>Écrivez-nous : <a href="mailto:recrutement@acme.fr">recrutement@acme.fr</a></p>
<p>Presse : presse@acme.fr — Contact : contact@acme.fr — jean.dupont@acme.fr</p>
<p>RH groupe : rh@careers.acme.fr · prestataire : jobs@agence-externe.com · logo@2x.png</p>
<p>Talents : talents&#64;acme.fr</p></body></html>"""


def test_adresses_rh_du_domaine_seulement():
    assert contacts.adresses(PAGE, "acme.fr") == [
        "recrutement@acme.fr",
        "rh@careers.acme.fr",
        "talents@acme.fr",
    ]


@pytest.mark.parametrize(
    ("hote", "racine"),
    [("www.acme.fr", "acme.fr"), ("careers.acme.co.uk", "acme.co.uk"), ("acme.com", "acme.com")],
)
def test_domaine_racine(hote, racine):
    assert contacts.domaine_racine(hote) == racine


def test_site_normalise():
    assert contacts.site_normalise("www.Acme.fr/fr/accueil") == "https://www.acme.fr"
    with pytest.raises(ValueError):
        contacts.site_normalise("pas un site")


def _client(routes: dict[str, tuple[int, str]]) -> httpx.Client:
    vues = []

    def repondre(req: httpx.Request) -> httpx.Response:
        vues.append(req.url.path)
        code, corps = routes.get(req.url.path, (404, ""))
        return httpx.Response(code, text=corps, headers={"content-type": "text/html"})

    c = httpx.Client(transport=httpx.MockTransport(repondre))
    c.vues = vues
    return c


def test_trouver_respecte_robots_et_garde_la_source():
    c = _client(
        {
            "/robots.txt": (200, "User-agent: *\nDisallow: /contact\n"),
            "/": (200, "<p>Bienvenue</p>"),
            "/contact": (200, "<p>recrutement@acme.fr</p>"),
            "/recrutement": (200, "<p>jobs@acme.fr</p>"),
        }
    )
    trouves = contacts.trouver("acme.fr", client=c, pause=0)
    assert [(t.email, t.source_url) for t in trouves] == [
        ("jobs@acme.fr", "https://acme.fr/recrutement")
    ]
    assert "/contact" not in c.vues  # interdit par robots.txt : jamais téléchargé
    assert "/careers" not in c.vues  # adresse trouvée sur une page carrières : arrêt


def test_assembler_ajoute_signature_et_retire_les_jetons():
    res = {
        "salutation": "Madame, Monsieur,",
        "paragraphes": ["Je m'appelle [NOM] et je vous écris.", "Deuxième paragraphe."],
        "formule": "Bien cordialement,",
    }
    ident = {
        "nom": "Camille Test",
        "titre": "Analytics Engineer",
        "email": "c@test.fr",
        "telephone": "",
    }
    corps = agent.assembler(res, ident)
    assert "[NOM]" not in corps
    assert corps.endswith("Camille Test\nAnalytics Engineer\nc@test.fr")
    assert corps.startswith("Madame, Monsieur,\n\nJe m'appelle et je vous écris.")


@pytest.fixture
def conn():
    try:
        with connexion() as c:
            c.execute("SELECT 1 FROM spontanees LIMIT 1")
            with c.transaction(force_rollback=True):
                yield c
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")


def _brouillon(conn, eid):
    cid = conn.execute(
        "INSERT INTO contacts (entreprise_id, email, source_url) VALUES (%s, 'rh@acme-test.fr',"
        " 'https://acme-test.fr/recrutement') RETURNING id",
        (eid,),
    ).fetchone()["id"]
    return conn.execute(
        "INSERT INTO spontanees (entreprise_id, contact_id, destinataire, objet, corps)"
        " VALUES (%s, %s, 'rh@acme-test.fr', 'Objet', 'Corps') RETURNING id",
        (eid, cid),
    ).fetchone()["id"]


def test_ajouter_cible_et_garde_fous(conn):
    eid = agent.ajouter_cible(
        conn, "Acme Test Spontanée", "acme-test.fr", "Recrutement@Acme-Test.fr"
    )
    row = conn.execute("SELECT site_web FROM entreprises WHERE id = %s", (eid,)).fetchone()
    assert row["site_web"] == "https://acme-test.fr"
    assert any(c["id"] == eid for c in agent.cibles(conn))
    with pytest.raises(agent.SpontaneeImpossible):
        agent.ajouter_cible(conn, "Acme Test Spontanée", email="pas-une-adresse")
    _brouillon(conn, eid)
    assert "déjà préparée" in agent.deja_sollicitee(conn, eid)
    assert not any(c["id"] == eid for c in agent.cibles(conn))  # pas deux fois en 90 jours


def test_envoyee_cree_la_candidature(conn):
    eid = agent.ajouter_cible(conn, "Acme Test Envoi", "acme-test.fr")
    sid = _brouillon(conn, eid)
    cid = conn.execute("SELECT spontanee_envoyee(%s) AS c", (sid,)).fetchone()["c"]
    cand = conn.execute(
        "SELECT type, statut, contact_email FROM candidatures WHERE id = %s", (cid,)
    ).fetchone()
    assert cand == {"type": "spontanee", "statut": "postule", "contact_email": "rh@acme-test.fr"}
    assert agent.deja_sollicitee(conn, eid).startswith("candidature chez elle")
    with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
        conn.execute("SELECT spontanee_envoyee(%s)", (sid,))  # pas deux fois


def test_opposition_definitive(conn):
    eid = agent.ajouter_cible(conn, "Acme Test Opposition", "acme-test.fr")
    sid = _brouillon(conn, eid)
    conn.execute("SELECT spontanee_opposition(%s)", (sid,))
    assert (
        conn.execute("SELECT statut FROM spontanees WHERE id = %s", (sid,)).fetchone()["statut"]
        == "ignoree"
    )
    assert agent._contact(conn, {"id": eid, "site_web": "https://acme-test.fr"}) is None
    assert not any(c["id"] == eid for c in agent.cibles(conn))
