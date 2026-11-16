"""Spontanées : envoi automatique, réponses (redirection, « pas pour l'instant »), sites."""

import base64
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app import criteres, envoi
from app.agents import relances
from app.config import database_url
from app.spontanees import agent, contacts, suite
from app.suivi.regles import pas_maintenant, redirection

MOI = "camille@example.com"


# --- Règles (sans base) ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "texte",
    [
        "Merci pour votre message. Nous n'avons pas de poste ouvert pour le moment.",
        "Nous ne recrutons pas actuellement mais conservons votre CV.",
        "Pour l'instant, nous n'avons pas de besoin sur ce profil.",
        "We are not hiring at the moment, we will keep your CV on file.",
    ],
)
def test_pas_maintenant(texte):
    assert pas_maintenant(texte)


@pytest.mark.parametrize(
    "texte",
    [
        "Nous ne prenons pas de candidatures spontanées, pas de poste pour le moment.",
        "Merci, nous revenons vers vous rapidement pour un entretien.",
        "Malheureusement votre profil ne correspond pas.",
    ],
)
def test_pas_maintenant_faux(texte):
    assert not pas_maintenant(texte)


def test_redirection_avec_adresse():
    t = "Bonjour, merci d'adresser votre candidature à Paul Martin : paul.martin@acme.fr. Bien à vous"
    assert redirection(t, {MOI, "rh@acme.fr"}).email == "paul.martin@acme.fr"
    t2 = "Je vous invite à contacter notre filiale data : recrutement@acme-data.fr"
    assert redirection(t2, {MOI}).email == "recrutement@acme-data.fr"


def test_redirection_sans_adresse_ou_formule_de_politesse():
    assert redirection("Il faut vous adresser au service RH de votre région.", {MOI}).email is None
    assert redirection("N'hésitez pas à nous contacter. rh@acme.fr", {MOI, "rh@acme.fr"}) is None
    assert redirection("Merci, bonne journée.", {MOI}) is None


def test_renvoi_vers_le_site_pas_d_insistance():
    t = "Merci de postuler directement sur notre site carrières ou d'écrire à jobs@acme.fr."
    assert redirection(t, {MOI}) is None


def test_corps_redirige():
    corps = (
        "Madame, Monsieur,\n\nJe vous écris via votre adresse de contact : pourriez-vous transmettre"
        " ce message à la personne en charge du recrutement ? Merci.\n\nParagraphe 1.\n\nSignature"
    )
    r = suite.corps_redirige(corps, "Marie (Acme)")
    assert r.startswith("Madame, Monsieur,\n\nSur les conseils de Marie (Acme)")
    assert "transmettre ce message" not in r and "Paragraphe 1." in r


def test_phrase_pieces_et_portfolio():
    res = {"salutation": "Madame, Monsieur,", "paragraphes": ["P."], "formule": "Cordialement,"}
    corps = agent.assembler(res, {"nom": "Camille", "portfolio": "https://exemple.org/portfolio/"})
    assert "ci-joint mon CV et ma lettre de motivation, ainsi que mon portfolio en ligne :" in corps
    assert corps.count("https://exemple.org/portfolio/") == 1  # pas répété dans la signature
    html = envoi.en_html(corps)
    assert '<a href="https://exemple.org/portfolio/">' in html


def test_adresse_nominative_seulement_sur_la_page_carrieres():
    page = "<p>Votre contact : marie.durand@acme.fr</p>"
    assert contacts.adresses(page, "acme.fr") == []
    assert contacts.adresses(page, "acme.fr", nominatives=True) == ["marie.durand@acme.fr"]


def test_site_probable():
    assert (
        agent.site_probable(
            "SNCF Voyageurs",
            ["https://jobs.lever.co/x", "https://www.sncf-connect.com/a", "rh@sncf.fr"],
        )
        == "https://sncf.fr"
    )
    assert agent.site_probable("Acme", ["jean@gmail.com", "https://fr.indeed.com/x"]) is None


# --- Base locale ------------------------------------------------------------------------------
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
        return {"id": f"m{len(self.envoyes)}", "threadId": f"t{len(self.envoyes)}"}


@pytest.fixture
def base(monkeypatch, tmp_path):
    jeton = tmp_path / "token_envoi.json"
    jeton.write_text("{}")
    monkeypatch.setattr(envoi, "chemin_jeton", lambda: jeton)
    try:
        c = psycopg.connect(database_url(), row_factory=psycopg.rows.dict_row)
        c.execute("SELECT a_valider FROM spontanees LIMIT 1")
    except (psycopg.OperationalError, psycopg.errors.UndefinedColumn) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")
    avant = {k: criteres.lire(c)[k] for k in ("envoi", "seuils", "identite")}
    pdf = c.execute(
        "INSERT INTO documents (type, nom_fichier, chemin, format, taille, empreinte, texte,"
        " contenu, par_defaut, langue) VALUES ('cv', 'CV_test.pdf', 'test/CV_test.pdf', 'pdf', 10,"
        " 'test-spont', 'cv', %s, FALSE, 'fr') RETURNING id",
        (b"%PDF-1.4",),
    ).fetchone()["id"]
    defauts = [
        r["id"] for r in c.execute("SELECT id FROM documents WHERE par_defaut AND type = 'cv'")
    ]
    c.execute("UPDATE documents SET par_defaut = FALSE WHERE id = ANY(%s)", (defauts,))
    c.execute("UPDATE documents SET par_defaut = TRUE WHERE id = %s", (pdf,))
    eid = c.execute(
        "INSERT INTO entreprises (nom_norm, nom_affiche) VALUES ('acme spont test', 'Acme Spont Test')"
        " RETURNING id"
    ).fetchone()["id"]
    faux = FauxGmail()
    monkeypatch.setattr(envoi, "_service", lambda: faux)
    criteres.ecrire(c, "envoi", {"autorise_pour": MOI, "spontanees_auto": True})
    criteres.ecrire(c, "identite", {"nom": "Camille Exemple"})
    c.commit()
    yield c, eid, faux
    c.rollback()
    c.execute(
        "DELETE FROM envois WHERE type IN ('spontanee', 'relance') AND ref_id IN"
        " (SELECT id FROM spontanees WHERE entreprise_id = %(e)s UNION"
        "  SELECT id FROM candidatures WHERE entreprise_id = %(e)s)",
        {"e": eid},
    )
    c.execute("DELETE FROM notifications WHERE titre LIKE '%%Acme Spont Test%%'")
    c.execute(
        "DELETE FROM reponses WHERE candidature_id IN (SELECT id FROM candidatures WHERE entreprise_id = %s)",
        (eid,),
    )
    c.execute("DELETE FROM spontanees WHERE entreprise_id = %s", (eid,))
    c.execute("DELETE FROM candidatures WHERE entreprise_id = %s", (eid,))
    c.execute("DELETE FROM contacts WHERE entreprise_id = %s", (eid,))
    c.execute("DELETE FROM entreprises WHERE id = %s", (eid,))
    c.execute("DELETE FROM documents WHERE id = %s", (pdf,))
    c.execute("UPDATE documents SET par_defaut = TRUE WHERE id = ANY(%s)", (defauts,))
    for k, v in avant.items():
        criteres.ecrire(c, k, v)
    c.commit()
    c.close()


def _brouillon(c, eid, email="rh@acme-spont.fr", ok=True, a_valider=None):
    cid = c.execute(
        "INSERT INTO contacts (entreprise_id, email, source_url) VALUES (%s, %s, 'test')"
        " ON CONFLICT (entreprise_id, email) DO UPDATE SET email = EXCLUDED.email RETURNING id",
        (eid, email),
    ).fetchone()["id"]
    sid = c.execute(
        "INSERT INTO spontanees (entreprise_id, contact_id, destinataire, objet, corps, verification,"
        " a_valider) VALUES (%s, %s, %s, 'Candidature spontanée – Data', %s, %s, %s) RETURNING id",
        (eid, cid, email, "Madame, Monsieur,\n\nTexte.", Jsonb({"ok": ok}), a_valider),
    ).fetchone()["id"]
    c.commit()
    return sid


def test_envoi_automatique_et_garde_fous(base):
    c, eid, faux = base
    sur = _brouillon(c, eid)
    a_relire = _brouillon(c, eid, "jobs@acme-spont.fr", ok=False)
    redirige = _brouillon(c, eid, "paul@acme-spont.fr", a_valider="redirection : à valider")
    bilan = {"envoyées": 0}
    agent._envois_auto(c, bilan)
    assert bilan["envoyées"] == 1 and len(faux.envoyes) == 1
    statuts = {
        r["id"]: (r["statut"], r["a_valider"])
        for r in c.execute(
            "SELECT id, statut, a_valider FROM spontanees WHERE entreprise_id = %s", (eid,)
        )
    }
    assert statuts[sur][0] == "envoyee"
    assert statuts[a_relire] == ("brouillon", "paragraphe retiré par le garde-fou : à relire")
    assert statuts[redirige][0] == "brouillon"
    brut = base64.urlsafe_b64decode(faux.envoyes[0]["raw"]).decode(errors="ignore")
    assert "CV_test.pdf" in brut and "text/html" in brut
    assert envoi.etat(c)["spontanees_auto_aujourdhui"] == 1


def test_interrupteur_coupe(base):
    c, eid, faux = base
    criteres.ecrire(c, "envoi", {"autorise_pour": MOI, "spontanees_auto": False})
    sid = _brouillon(c, eid)
    with pytest.raises(envoi.EnvoiImpossible):
        agent.envoyer_auto(c, sid)
    c.rollback()
    assert not faux.envoyes


def _envoyee(c, eid):
    sid = _brouillon(c, eid)
    cid = c.execute("SELECT spontanee_envoyee(%s) AS c", (sid,)).fetchone()["c"]
    c.execute(
        "UPDATE candidatures SET boite = 'test', gmail_thread_id = %s, postule_le = %s WHERE id = %s",
        (f"fil-{cid}", date.today() - timedelta(days=40), cid),
    )
    c.commit()
    return cid


def _reponse(c, cid, texte, de="Marie Durand <marie.durand@acme-spont.fr>"):
    from app.suivi.historique import Import
    from app.suivi.regles import lire

    m = {
        "id": f"msg-{cid}-{len(texte)}",
        "thread": f"fil-{cid}",
        "From": de,
        "To": MOI,
        "Subject": "Re: Candidature spontanée – Data",
        "date": datetime.now(UTC) - timedelta(days=31),
    }
    imp = Import(c, "test")
    imp.traiter(m, lire(m["From"], m["To"], m["Subject"], texte, False), texte)
    c.commit()
    return imp


def test_redirection_prepare_un_mail_a_valider(base):
    c, eid, _ = base
    cid = _envoyee(c, eid)
    _reponse(c, cid, "Bonjour, merci d'adresser votre candidature à paul.martin@acme-spont.fr.")
    nouveau = c.execute(
        "SELECT destinataire, statut, a_valider, corps FROM spontanees WHERE entreprise_id = %s"
        " AND destinataire = 'paul.martin@acme-spont.fr'",
        (eid,),
    ).fetchone()
    assert nouveau["statut"] == "brouillon" and nouveau["a_valider"].startswith("redirection")
    assert "Sur les conseils de Marie Durand" in nouveau["corps"]
    cand = c.execute("SELECT relance_ignoree FROM candidatures WHERE id = %s", (cid,)).fetchone()
    assert cand["relance_ignoree"]


def test_pas_maintenant_une_seule_relance_a_j30(base, monkeypatch):
    c, eid, faux = base
    monkeypatch.setattr(relances, "interdite_dans_le_fil", lambda boite, fil: None)
    cid = _envoyee(c, eid)
    _reponse(c, cid, "Merci, nous n'avons pas de poste ouvert pour le moment.")
    cand = c.execute(
        "SELECT relance_prevue_le, contact_email, statut FROM candidatures WHERE id = %s", (cid,)
    ).fetchone()
    assert cand["relance_prevue_le"] == date.today() - timedelta(days=1)
    assert cand["contact_email"] == "marie.durand@acme-spont.fr"
    due = c.execute("SELECT numero, motif FROM v_relances WHERE id = %s", (cid,)).fetchone()
    assert due == {"numero": 3, "motif": "plus_tard"}
    criteres.ecrire(c, "envoi", {"autorise_pour": MOI, "relances_auto": True})
    c.commit()
    assert relances.executer(c)["envoyées"] >= 1
    brut = base64.urlsafe_b64decode(faux.envoyes[-1]["raw"]).decode(errors="ignore")
    assert "marie.durand@acme-spont.fr" in brut
    assert c.execute("SELECT 1 FROM v_relances WHERE id = %s", (cid,)).fetchone() is None
