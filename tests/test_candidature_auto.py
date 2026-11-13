import psycopg
import pytest

from app import criteres, envoi
from app.agents import candidature_auto as ca
from app.config import database_url


def test_contact_publie_dans_la_fiche():
    offre = {
        "texte": "Envoyez CV et lettre à no-reply@acme.fr ou à recrutement.data@acme.fr avant le 30.",
        "url_fiche": "https://acme.fr/carrieres/123",
        "entreprise_id": None,
    }
    assert ca.contact_publie(None, offre) == (
        "recrutement.data@acme.fr",
        "https://acme.fr/carrieres/123",
    )
    assert (
        ca.contact_publie(
            None, {"texte": "Postulez via jobs@smartrecruiters.com", "entreprise_id": None}
        )
        is None
    )


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
        c.execute("SELECT 1 FROM candidatures_auto LIMIT 1")
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as e:
        pytest.skip(f"Base locale injoignable ou non migrée : {e}")
    avant = {k: criteres.lire(c)[k] for k in ("envoi", "seuils")}
    ids: dict = {"offres": []}
    yield c, ids
    c.rollback()
    for oid in ids["offres"]:
        c.execute("DELETE FROM envois WHERE type = 'candidature' AND ref_id = %s", (oid,))
        c.execute("DELETE FROM candidatures WHERE offre_id = %s", (oid,))
        c.execute(
            "DELETE FROM offres WHERE id = %s", (oid,)
        )  # cascade : fabrications, candidatures_auto
    if ids.get("entreprise"):
        c.execute("DELETE FROM contacts WHERE entreprise_id = %s", (ids["entreprise"],))
        c.execute("DELETE FROM entreprises WHERE id = %s", (ids["entreprise"],))
    for k, v in avant.items():
        criteres.ecrire(c, k, v)
    c.commit()
    c.close()


def _offre(c, ids, eid, score, motif=None, url="test:auto"):
    oid = c.execute(
        "INSERT INTO offres (url_cle, cle_floue, source, intitule, entreprise_id, texte, fiche_complete,"
        " statut_validite, score, classe, motif, etape) VALUES (%s, 'x', 'manuelle', 'Data Analyst', %s,"
        " 'Missions SQL et Power BI. Candidature à talents@acme-auto.fr', TRUE, 'valide', %s, 'B', %s,"
        " 'reperee') RETURNING id",
        (url, eid, score, motif),
    ).fetchone()["id"]
    ids["offres"].append(oid)
    return oid


def _faux_dossier(c):
    def fabriquer(conn, offre_id, langue=None):
        fid = conn.execute(
            "INSERT INTO fabrications (offre_id, langue, statut, contenu) VALUES (%s, 'fr', 'ats_friendly',"
            ' \'{"lm": {"paragraphes": ["Lettre."]}}\') RETURNING id',
            (offre_id,),
        ).fetchone()["id"]
        for t in ("cv", "lm"):
            conn.execute(
                "INSERT INTO dossier_fichiers (fabrication_id, type, format, nom, contenu)"
                " VALUES (%s, %s, 'pdf', %s, %s)",
                (fid, t, f"{t.upper()}_test.pdf", b"%PDF-1.4"),
            )
        conn.commit()
        return {"fabrication_id": fid, "statut": "ats_friendly", "motifs": []}

    return fabriquer


def test_rien_ne_part_si_desactive(base):
    c, _ = base
    criteres.ecrire(c, "envoi", {"autorise_pour": "camille@example.com", "auto": False})
    assert ca.executer(c)["note"] == "candidatures automatiques désactivées"


def test_envoi_sous_le_seuil_et_mise_de_cote_au_dessus(base, monkeypatch):
    c, ids = base
    faux = FauxGmail()
    monkeypatch.setattr(envoi, "_service", lambda: faux)
    monkeypatch.setattr("app.generation.fabrique.fabriquer", _faux_dossier(c))
    monkeypatch.setattr(
        ca, "rediger", lambda conn, o, lm: ("Candidature – Data Analyst", "Bonjour,\n\nTexte.", {})
    )
    monkeypatch.setattr(ca.llm, "disponible", lambda: True)
    monkeypatch.setattr(ca, "candidature_recente", lambda conn, eid: None)
    criteres.ecrire(
        c, "envoi", {"autorise_pour": "camille@example.com", "auto": True, "actif": False}
    )
    seuils = criteres.lire(c)["seuils"]
    criteres.ecrire(
        c, "seuils", {**seuils, "candidatures_auto_par_passage": 10, "envoi_auto_min": 50}
    )
    ids["entreprise"] = eid = c.execute(
        "INSERT INTO entreprises (nom_norm, nom_affiche) VALUES ('test auto acme', 'Acme Auto') RETURNING id"
    ).fetchone()["id"]
    bas = _offre(c, ids, eid, 72, url="test:auto:72")
    haut = _offre(c, ids, eid, 90, url="test:auto:90")
    exclue = _offre(
        c, ids, eid, 75, motif="contrainte violée : contrat exclu (stage)", url="test:auto:stage"
    )
    c.commit()

    bilan = ca.executer(c)
    assert bilan["envoyées"] == 1 and bilan["mises de côté"] == 1
    lignes = {
        r["offre_id"]: r
        for r in c.execute(
            "SELECT offre_id, statut, motif, destinataire FROM candidatures_auto WHERE offre_id = ANY(%s)",
            (ids["offres"],),
        )
    }
    assert (
        lignes[bas]["statut"] == "envoyee" and lignes[bas]["destinataire"] == "talents@acme-auto.fr"
    )
    assert lignes[haut]["statut"] == "mise_de_cote" and "≥ 85" in lignes[haut]["motif"]
    assert exclue not in lignes  # contrainte violée : jamais traitée
    assert len(faux.envoyes) == 1
    cand = c.execute(
        "SELECT statut, origine FROM candidatures WHERE offre_id = %s", (bas,)
    ).fetchone()
    assert cand == {"statut": "postule", "origine": "agent"}
    etapes = {
        r["id"]: r["etape"]
        for r in c.execute("SELECT id, etape FROM offres WHERE id = ANY(%s)", ([bas, haut],))
    }
    assert etapes == {bas: "envoyee", haut: "a_envoyer"}
    c.commit()
    assert ca.executer(c)["envoyées"] == 0  # rien ne repart une seconde fois


def test_faits_valides_acceptes_par_le_garde_fou(monkeypatch):
    monkeypatch.setattr(
        ca.ats, "cv_par_defaut", lambda conn: {"texte": "SQL, Power BI, méthodes Agile."}
    )
    monkeypatch.setattr(
        ca.identite,
        "lire",
        lambda conn, texte=None: {"nom": "Camille Test", "portfolio": "exemple.github.io/p"},
    )
    monkeypatch.setattr(
        ca.scoring, "faits", lambda: ["12 missions de data analyse menées", "travail en Kanban"]
    )
    reponse = {
        "objet": "Candidature – Data Analyst",
        "salutation": "Bonjour,",
        "paragraphes": [
            "J'ai mené 12 missions de data analyse, en Kanban, avec SQL et Power BI.",
            "J'ai aussi déployé Snowflake sur 40 projets.",  # ni dans le CV ni dans les faits
        ],
        "formule": "Au plaisir de vous lire,",
    }
    monkeypatch.setattr(
        ca, "generer_json", lambda consigne, contenu, schema: (reponse, "modele-test")
    )
    offre = {"intitule": "Data Analyst", "entreprise": "Acme", "texte": "Missions SQL."}
    objet, corps, verif = ca.rediger(None, offre, "")
    assert objet == "Candidature – Data Analyst"
    assert "12 missions" in corps and "Kanban" in corps
    assert "Snowflake" not in corps and len(verif["retires"]) == 1
    assert "https://exemple.github.io/p" in corps and corps.rstrip().endswith("Camille Test")
