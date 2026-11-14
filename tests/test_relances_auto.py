import base64
from datetime import date, timedelta

import psycopg
import pytest

from app import criteres, envoi
from app.agents import relances
from app.config import database_url
from app.suivi.regles import relance_interdite


# --- Détection des réponses qui interdisent une relance --------------------------------------
@pytest.mark.parametrize(
    "texte",
    [
        "Merci de postuler directement sur notre site carrières, rubrique Data.",
        "Vous pouvez suivre l'avancement de votre candidature sur notre portail.",
        "Connectez-vous à votre espace candidat pour toute question.",
        "Please apply through our careers page so that we can review your profile.",
        "You can track your application status on our candidate portal.",
    ],
)
def test_renvoi_vers_le_site_officiel(texte):
    assert relance_interdite(texte) == "l'entreprise renvoie vers son site officiel"


@pytest.mark.parametrize(
    "texte",
    [
        "Merci de ne plus nous relancer au sujet de cette offre.",
        "Nous ne souhaitons pas être recontactés pour ce poste.",
        "Inutile de nous relancer, nous reviendrons vers vous.",
        "Please do not follow up on this application.",
    ],
)
def test_ne_plus_etre_contacte(texte):
    assert relance_interdite(texte) == "l'entreprise ne souhaite pas être recontactée"


@pytest.mark.parametrize(
    "texte",
    [
        "Nous avons bien reçu votre candidature et revenons vers vous rapidement.",
        "Merci pour votre intérêt, notre équipe étudie votre profil.",
        "",
    ],
)
def test_reponses_neutres_n_interdisent_rien(texte):
    assert relance_interdite(texte) is None


def test_texte_relance_et_langue():
    r = {
        "numero": 1,
        "poste": "Data Analyst",
        "entreprise": "Acme",
        "postule_le": date(2026, 11, 3),
    }
    objet, corps = relances.texte_relance(r, {"nom": "Camille Exemple", "telephone": "06"}, "fr")
    assert objet == "Relance – candidature au poste de Data Analyst"
    assert "envoyée le 03/11/2026" in corps and "rejoindre Acme" in corps
    assert corps.endswith("Camille Exemple\n06")
    sans_poste = {"numero": 1, "type": "offre", "entreprise": "Acme", "postule_le": None}
    assert relances.texte_relance(sans_poste, {"nom": "C"})[0] == "Relance – candidature"
    domaine = {"numero": 1, "entreprise": "werecruit", "poste": "Data Analyst", "postule_le": None}
    assert "rejoindre votre équipe" in relances.texte_relance(domaine, {"nom": "C"})[1]
    assert relances.langue({"poste": "Senior Data Engineer"}) == "en"
    assert relances.langue({"poste": "Ingénieur données"}) == "fr"
    assert not relances.adresse_humaine("no-reply@acme.fr")
    assert relances.adresse_humaine("cc-2dbb@candidates.welcomekit.co")
    assert relances.adresse_humaine("marie.durand@acme.fr")


# --- Agent : envoi automatique ---------------------------------------------------------------
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
        return {"id": f"m{len(self.envoyes)}", "threadId": "fil"}


@pytest.fixture
def base(monkeypatch, tmp_path):
    jeton = tmp_path / "token_envoi.json"
    jeton.write_text("{}")
    monkeypatch.setattr(envoi, "chemin_jeton", lambda: jeton)
    try:
        c = psycopg.connect(database_url(), row_factory=psycopg.rows.dict_row)
        c.execute("SELECT relance_motif FROM candidatures LIMIT 1")
    except (psycopg.OperationalError, psycopg.errors.UndefinedColumn) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")
    avant = {k: criteres.lire(c)[k] for k in ("envoi", "seuils", "identite")}
    ids: list[int] = []
    yield c, ids
    c.rollback()
    c.execute("DELETE FROM envois WHERE type = 'relance' AND ref_id = ANY(%s)", (ids,))
    c.execute("DELETE FROM candidatures WHERE id = ANY(%s)", (ids,))
    for k, v in avant.items():
        criteres.ecrire(c, k, v)
    c.commit()
    c.close()


def _candidature(c, ids, jours, contact, ignoree=False, statut="postule"):
    cid = c.execute(
        "INSERT INTO candidatures (type, statut, postule_le, origine, poste, canal, contact_email,"
        " relance_ignoree) VALUES ('offre', %s, %s, 'manuelle', 'Data Analyst', 'mail', %s, %s)"
        " RETURNING id",
        (statut, date.today() - timedelta(days=jours), contact, ignoree),
    ).fetchone()["id"]
    ids.append(cid)
    return cid


def test_relances_envoyees_automatiquement(base, monkeypatch):
    c, ids = base
    faux = FauxGmail()
    monkeypatch.setattr(envoi, "_service", lambda: faux)
    criteres.ecrire(
        c, "envoi", {"autorise_pour": "camille@example.com", "actif": False, "auto": False}
    )
    criteres.ecrire(c, "identite", {"nom": "Camille Exemple"})
    criteres.ecrire(c, "seuils", {**criteres.lire(c)["seuils"], "relances_auto_par_jour": 15})
    due = _candidature(c, ids, 8, "marie.durand@acme-relance.fr")
    robot = _candidature(c, ids, 8, "no-reply@acme-relance.fr")
    bloquee = _candidature(c, ids, 8, "rh@acme-relance.fr", ignoree=True)
    trop_tot = _candidature(c, ids, 3, "rh2@acme-relance.fr")
    c.commit()
    # Une relance automatique ne passe pas par l'interrupteur « envoi en 1 clic » (actif = False).
    bilan = relances.executer(c)
    envoyees = {
        r["ref_id"]
        for r in c.execute(
            "SELECT ref_id FROM envois WHERE type = 'relance' AND automatique AND ref_id = ANY(%s)",
            (ids,),
        )
    }
    assert envoyees == {due}
    assert bilan["envoyées"] == 1 and bilan["sans adresse utilisable"] == 1
    assert robot not in envoyees and bloquee not in envoyees and trop_tot not in envoyees
    statut = c.execute("SELECT statut FROM candidatures WHERE id = %s", (due,)).fetchone()
    assert statut["statut"] == "relance"
    brut = base64.urlsafe_b64decode(faux.envoyes[0]["raw"]).decode(errors="ignore")
    assert "marie.durand@acme-relance.fr" in brut
    c.commit()
    assert relances.executer(c)["envoyées"] == 0  # pas deux fois la même relance


def test_relances_desactivables(base):
    c, _ = base
    criteres.ecrire(c, "envoi", {"autorise_pour": "camille@example.com", "relances_auto": False})
    assert relances.executer(c)["note"] == "relances automatiques désactivées"


def test_reponse_vers_le_site_bloque_la_relance(base):
    from datetime import UTC, datetime

    from app.suivi.historique import Import
    from app.suivi.regles import lire

    c, ids = base
    cid = _candidature(c, ids, 8, "rh@acme-site.fr")
    c.execute(
        "UPDATE candidatures SET boite = 'test', gmail_thread_id = 'fil-site' WHERE id = %s", (cid,)
    )
    texte = "Bonjour, merci de postuler directement sur notre site carrières. Cordialement."
    m = {
        "id": "msg-site",
        "thread": "fil-site",
        "From": "RH Acme <rh@acme-site.fr>",
        "To": "camille@example.com",
        "Subject": "Re: Candidature – Data Analyst",
        "date": datetime.now(UTC),
    }
    imp = Import(c, "test")
    imp.traiter(m, lire(m["From"], m["To"], m["Subject"], texte, False), texte)
    row = c.execute(
        "SELECT relance_ignoree, relance_motif FROM candidatures WHERE id = %s", (cid,)
    ).fetchone()
    assert row == {
        "relance_ignoree": True,
        "relance_motif": "l'entreprise renvoie vers son site officiel",
    }
    c.execute("DELETE FROM reponses WHERE candidature_id = %s", (cid,))
