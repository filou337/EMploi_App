import base64
import email
from email import policy

import psycopg
import pytest

from app import criteres, envoi


def test_construire_mail_avec_pieces_jointes():
    m = envoi.construire(
        "camille@example.com",
        "Camille Exemple",
        "rh@acme.fr",
        "Candidature spontanée",
        "Madame, Monsieur,\n\nTexte.",
        [{"nom_fichier": "CV_Camille.pdf", "contenu": b"%PDF-1.4 cv"}],
    )
    lu = email.message_from_bytes(m.as_bytes(), policy=policy.default)
    assert lu["From"] == "Camille Exemple <camille@example.com>"
    assert lu["To"] == "rh@acme.fr" and lu["Subject"] == "Candidature spontanée"
    pj = [p for p in lu.iter_attachments()]
    assert [p.get_filename() for p in pj] == ["CV_Camille.pdf"]
    assert pj[0].get_content() == b"%PDF-1.4 cv"
    assert "Texte." in lu.get_body(preferencelist=("plain",)).get_content()


def test_email_du_jeton():
    charge = base64.urlsafe_b64encode(b'{"email": "Camille@Example.com"}').decode().rstrip("=")

    class Creds:
        id_token = f"x.{charge}.y"

    assert envoi._email_du_jeton(Creds()) == "camille@example.com"
    with pytest.raises(envoi.EnvoiImpossible):
        envoi._email_du_jeton(type("C", (), {"id_token": None})())


class FauxGmail:
    def __init__(self):
        self.envoyes = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        self.envoyes.append(body)
        return self

    def execute(self):
        return {"id": f"msg{len(self.envoyes)}", "threadId": "fil1"}


@pytest.fixture
def conn(monkeypatch, tmp_path):
    jeton = tmp_path / "token_envoi.json"
    jeton.write_text("{}")
    monkeypatch.setattr(envoi, "chemin_jeton", lambda: jeton)
    try:
        c = psycopg.connect(
            __import__("app.config", fromlist=["database_url"]).database_url(),
            row_factory=psycopg.rows.dict_row,
        )
        c.execute("SELECT 1 FROM envois LIMIT 1")
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")
    avant = criteres.lire(c)["envoi"]
    ids: dict = {}
    yield c, ids
    # Nettoyage : _envoyer valide (commit) avant d'appeler Gmail, on efface donc à la main.
    c.rollback()
    if ids.get("spontanee"):
        c.execute(
            "DELETE FROM envois WHERE type = 'spontanee' AND ref_id = %s", (ids["spontanee"],)
        )
        c.execute("DELETE FROM spontanees WHERE id = %s", (ids["spontanee"],))
    if ids.get("candidature"):
        c.execute("DELETE FROM candidatures WHERE id = %s", (ids["candidature"],))
    if ids.get("document"):
        c.execute("DELETE FROM documents WHERE id = %s", (ids["document"],))
    if ids.get("entreprise"):
        c.execute("DELETE FROM contacts WHERE entreprise_id = %s", (ids["entreprise"],))
        c.execute("DELETE FROM entreprises WHERE id = %s", (ids["entreprise"],))
    criteres.ecrire(c, "envoi", avant)
    c.commit()
    c.close()


def test_envoi_refuse_tant_que_desactive(conn):
    c, _ = conn
    criteres.ecrire(c, "envoi", {"actif": False})
    with pytest.raises(envoi.EnvoiImpossible, match="Autorise d'abord"):
        envoi.activer(c, True)
    criteres.ecrire(c, "envoi", {"actif": False, "autorise_pour": "camille@example.com"})
    with pytest.raises(envoi.EnvoiImpossible, match="désactivé"):
        envoi._controles(c, "rh@acme.fr")
    assert envoi.activer(c, True)["actif"] is True
    with pytest.raises(envoi.EnvoiImpossible, match="invalide"):
        envoi._controles(c, "pas-une-adresse")


def test_envoyer_spontanee_une_seule_fois(conn, monkeypatch):
    c, ids = conn
    faux = FauxGmail()
    monkeypatch.setattr(envoi, "_service", lambda: faux)
    criteres.ecrire(c, "envoi", {"actif": True, "autorise_pour": "camille@example.com"})
    ids["entreprise"] = eid = c.execute(
        "INSERT INTO entreprises (nom_norm, nom_affiche) VALUES ('test envoi acme', 'Acme Test Envoi') RETURNING id"
    ).fetchone()["id"]
    contact = c.execute(
        "INSERT INTO contacts (entreprise_id, email, source_url) VALUES (%s, 'rh@acme-envoi.fr', 'test') RETURNING id",
        (eid,),
    ).fetchone()["id"]
    ids["spontanee"] = sid = c.execute(
        "INSERT INTO spontanees (entreprise_id, contact_id, destinataire, objet, corps)"
        " VALUES (%s, %s, 'rh@acme-envoi.fr', 'Objet', 'Corps') RETURNING id",
        (eid, contact),
    ).fetchone()["id"]
    if not envoi._pieces_jointes(c):
        ids["document"] = c.execute(
            "INSERT INTO documents (type, langue, nom_fichier, format, taille, texte, par_defaut, contenu)"
            " VALUES ('cv', 'fr', 'CV_test.pdf', 'pdf', 10, 'cv', TRUE, %s) RETURNING id",
            (b"%PDF-1.4",),
        ).fetchone()["id"]
    c.commit()

    r = envoi.envoyer_spontanee(
        c, sid, "Candidature spontanée – test", "Madame, Monsieur,\n\nTexte."
    )
    ids["candidature"] = r["candidature_id"]
    assert len(faux.envoyes) == 1
    brut = base64.urlsafe_b64decode(faux.envoyes[0]["raw"])
    lu = email.message_from_bytes(brut, policy=policy.default)
    assert lu["To"] == "rh@acme-envoi.fr"
    assert lu["Subject"] == "Candidature spontanée – test"
    assert [p.get_content_type() for p in lu.iter_attachments()]  # CV joint
    cand = c.execute(
        "SELECT type, statut, gmail_thread_id FROM candidatures WHERE id = %s",
        (r["candidature_id"],),
    ).fetchone()
    assert cand == {"type": "spontanee", "statut": "postule", "gmail_thread_id": "fil1"}
    journal = c.execute(
        "SELECT statut, destinataire FROM envois WHERE id = %s", (r["envoi_id"],)
    ).fetchone()
    assert journal == {"statut": "envoye", "destinataire": "rh@acme-envoi.fr"}
    c.commit()
    with pytest.raises(envoi.EnvoiImpossible):  # déjà envoyée : plus un brouillon
        envoi.envoyer_spontanee(c, sid, "x", "y")
    assert len(faux.envoyes) == 1
