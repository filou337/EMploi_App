"""Envoi en 1 clic depuis Gmail : spontanées et relances, toujours déclenché par TON clic.

Garde-fous :
- droit séparé `gmail.send` dans son propre jeton (secrets/token_envoi.json) : la lecture des
  boîtes reste en lecture seule ; l'adresse autorisée est vérifiée (celle de la boîte d'envoi) ;
- interrupteur `parametres.envoi.actif`, désactivé par défaut (onglet « Spontanées ») ;
- un envoi = un clic sur un brouillon précis ; jamais deux fois le même (index unique) ;
- plafond quotidien (`seuils.envois_par_jour`, 10) ; contact en opposition refusé ;
- chaque envoi est journalisé dans `envois` (destinataire, objet, identifiant Gmail).

Autoriser une fois (sur le PC) :  python -m app.envoi --autoriser
"""

import argparse
import base64
import json
import os
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr

import psycopg
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app import criteres
from app.config import _chemin, chemin_client_secret

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class EnvoiImpossible(RuntimeError):
    pass


def boite_envoi() -> str:
    """Boîte d'où partent les mails (celle de ton CV par défaut)."""
    return os.getenv("GMAIL_ENVOI_BOITE", "ancienne").strip() or "ancienne"


def chemin_jeton():
    return _chemin("GOOGLE_TOKEN_ENVOI", "secrets/token_envoi.json")


# --- Autorisation --------------------------------------------------------------------------------
def _email_du_jeton(creds: Credentials) -> str:
    """Adresse du compte autorisé, lue dans le jeton d'identité renvoyé par Google."""
    jeton = getattr(creds, "id_token", None) or ""
    if jeton.count(".") != 2:
        raise EnvoiImpossible(
            "Google n'a pas renvoyé l'adresse du compte : recommence l'autorisation."
        )
    corps = jeton.split(".")[1]
    corps += "=" * (-len(corps) % 4)
    return json.loads(base64.urlsafe_b64decode(corps)).get("email", "").lower()


def autoriser(conn: psycopg.Connection) -> str:
    """Ouvre le navigateur, vérifie que c'est bien le compte de la boîte d'envoi, enregistre."""
    from app.collecte import gmail

    attendu = (
        gmail.service(boite_envoi())
        .users()
        .getProfile(userId="me")
        .execute()["emailAddress"]
        .lower()
    )
    # Google laisse cocher les droits un par un : on accepte la réponse, puis on vérifie nous-mêmes
    # que le droit d'envoi a bien été coché (sinon message clair, rien n'est enregistré).
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    flow = InstalledAppFlow.from_client_secrets_file(str(chemin_client_secret()), SCOPES)
    print(f"Autorisation d'ENVOI : choisis le compte {attendu} dans le navigateur,", flush=True)
    print("puis COCHE la case « Envoyer des e-mails en votre nom ».", flush=True)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="select_account consent")
    if SCOPES[0] not in (creds.granted_scopes or creds.scopes or []):
        raise EnvoiImpossible(
            "La case « Envoyer des e-mails en votre nom » n'a pas été cochée : rien n'est enregistré."
            " Relance la commande et coche-la sur l'écran de Google."
        )
    email = _email_du_jeton(creds)
    if email != attendu:
        raise EnvoiImpossible(
            f"Compte autorisé : {email}, attendu : {attendu}. Rien n'est enregistré : recommence"
            " en choisissant le bon compte."
        )
    chemin = chemin_jeton()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(creds.to_json())
    os.chmod(chemin, 0o600)
    params = criteres.lire(conn)["envoi"]
    criteres.ecrire(
        conn,
        "envoi",
        {**params, "autorise_pour": email, "autorise_le": datetime.now(UTC).isoformat()},
    )
    return email


def _service():
    chemin = chemin_jeton()
    if not chemin.exists():
        raise EnvoiImpossible(
            "Envoi non autorisé : lance `python -m app.envoi --autoriser` sur le PC."
        )
    creds = Credentials.from_authorized_user_file(str(chemin), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise EnvoiImpossible(
                "Autorisation d'envoi expirée ou révoquée : relance `python -m app.envoi --autoriser`."
            ) from e
        chemin.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def etat(conn: psycopg.Connection) -> dict:
    p = criteres.lire(conn)
    e, s = p["envoi"], p["seuils"]
    jour = conn.execute(
        "SELECT count(*) FILTER (WHERE NOT automatique) AS manuels,"
        " count(*) FILTER (WHERE automatique) AS auto FROM envois"
        " WHERE cree_le::date = current_date AND statut = 'envoye'"
    ).fetchone()
    return {
        "autorise": bool(e.get("autorise_pour")) and chemin_jeton().exists(),
        "adresse": e.get("autorise_pour"),
        "actif": bool(e.get("actif")),
        "envoyes_aujourdhui": jour["manuels"],
        "plafond": int(s.get("envois_par_jour", 10)),
        "auto": bool(e.get("auto")),
        "auto_aujourdhui": jour["auto"],
        "plafond_auto": int(s.get("envois_auto_par_jour", 10)),
    }


def activer(conn: psycopg.Connection, actif: bool, cle: str = "actif") -> dict:
    """`cle` = « actif » (envoi en 1 clic) ou « auto » (candidatures automatiques)."""
    if cle not in ("actif", "auto"):
        raise EnvoiImpossible("Réglage inconnu.")
    params = criteres.lire(conn)["envoi"]
    if actif and not params.get("autorise_pour"):
        raise EnvoiImpossible(
            "Autorise d'abord l'envoi sur le PC : python -m app.envoi --autoriser"
        )
    criteres.ecrire(conn, "envoi", {**params, cle: bool(actif)})
    return etat(conn)


# --- Construction et envoi -----------------------------------------------------------------------
def construire(
    expediteur: str, nom: str, destinataire: str, objet: str, corps: str, pieces: list[dict]
) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((nom, expediteur)) if nom else expediteur
    m["To"] = destinataire
    m["Subject"] = objet
    m.set_content(corps)
    for p in pieces:
        m.add_attachment(
            bytes(p["contenu"]), maintype="application", subtype="pdf", filename=p["nom_fichier"]
        )
    return m


def _pieces_jointes(conn: psycopg.Connection) -> list[dict]:
    """CV et LM par défaut, en PDF (ceux déposés dans l'onglet « CV & lettre »)."""
    return conn.execute(
        "SELECT nom_fichier, contenu FROM documents WHERE par_defaut AND format = 'pdf'"
        " AND contenu IS NOT NULL AND type IN ('cv', 'lm') AND coalesce(langue, 'fr') = 'fr'"
        " ORDER BY type"
    ).fetchall()


def _controles(conn: psycopg.Connection, destinataire: str | None, auto: bool = False) -> dict:
    e = etat(conn)
    if not e["autorise"]:
        raise EnvoiImpossible(
            "Envoi non autorisé : lance `python -m app.envoi --autoriser` sur le PC."
        )
    if auto:
        if not e["auto"]:
            raise EnvoiImpossible("Les candidatures automatiques sont désactivées.")
        if e["auto_aujourdhui"] >= e["plafond_auto"]:
            raise EnvoiImpossible(
                f"Plafond de {e['plafond_auto']} envois automatiques par jour atteint."
            )
    else:
        if not e["actif"]:
            raise EnvoiImpossible(
                "L'envoi depuis Gmail est désactivé (interrupteur de l'onglet Spontanées)."
            )
        if e["envoyes_aujourdhui"] >= e["plafond"]:
            raise EnvoiImpossible(f"Plafond de {e['plafond']} envois par jour atteint.")
    if not destinataire or not _EMAIL.fullmatch(destinataire.strip()):
        raise EnvoiImpossible("Adresse du destinataire absente ou invalide.")
    return e


def _envoyer(
    conn: psycopg.Connection,
    type_: str,
    ref_id: int,
    numero: int,
    destinataire: str,
    objet: str,
    corps: str,
    pieces: list[dict],
    fil: str | None = None,
    auto: bool = False,
) -> dict:
    from app.generation import identite

    e = _controles(conn, destinataire, auto)
    if not objet.strip() or not corps.strip():
        raise EnvoiImpossible("Objet ou message vide.")
    try:
        with conn.transaction():
            envoi_id = conn.execute(
                "INSERT INTO envois (type, ref_id, numero, destinataire, objet, automatique)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (type_, ref_id, numero, destinataire, objet, auto),
            ).fetchone()["id"]
    except psycopg.errors.UniqueViolation as err:
        raise EnvoiImpossible("Ce mail a déjà été envoyé (ou est en cours d'envoi).") from err
    conn.commit()  # trace avant l'appel à Gmail : un double clic ne peut pas envoyer deux fois
    ident = identite.lire(conn)
    message = construire(e["adresse"], ident.get("nom", ""), destinataire, objet, corps, pieces)
    corps_api = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
    if fil:
        corps_api["threadId"] = fil
    try:
        r = _service().users().messages().send(userId="me", body=corps_api).execute()
    except Exception as err:
        conn.rollback()
        conn.execute(
            "UPDATE envois SET statut = 'erreur', erreur = %s WHERE id = %s",
            (f"{type(err).__name__}: {err}"[:1000], envoi_id),
        )
        conn.commit()
        raise EnvoiImpossible(f"Gmail a refusé l'envoi : {err}") from err
    conn.execute(
        "UPDATE envois SET statut = 'envoye', envoye_le = now(), gmail_message_id = %s,"
        " gmail_thread_id = %s WHERE id = %s",
        (r.get("id"), r.get("threadId"), envoi_id),
    )
    return {"envoi_id": envoi_id, "gmail_message_id": r.get("id"), "thread_id": r.get("threadId")}


def envoyer_spontanee(conn: psycopg.Connection, sid: int, objet: str, corps: str) -> dict:
    s = conn.execute(
        "SELECT s.statut, s.destinataire, c.opposition FROM spontanees s"
        " LEFT JOIN contacts c ON c.id = s.contact_id WHERE s.id = %s",
        (sid,),
    ).fetchone()
    if s is None:
        raise EnvoiImpossible("Brouillon introuvable.")
    if s["statut"] != "brouillon":
        raise EnvoiImpossible("Ce brouillon n'est plus à envoyer.")
    if s["opposition"]:
        raise EnvoiImpossible("Ce contact a demandé à ne plus être sollicité.")
    pieces = _pieces_jointes(conn)
    if not pieces:
        raise EnvoiImpossible(
            "Aucun CV en PDF par défaut : dépose-le dans l'onglet « CV & lettre »."
        )
    conn.execute("UPDATE spontanees SET objet = %s, corps = %s WHERE id = %s", (objet, corps, sid))
    conn.commit()
    r = _envoyer(conn, "spontanee", sid, 1, s["destinataire"], objet, corps, pieces)
    cid = conn.execute("SELECT spontanee_envoyee(%s) AS c", (sid,)).fetchone()["c"]
    # Le fil Gmail rattache les réponses à cette candidature (agent Suivi).
    conn.execute(
        "UPDATE candidatures SET boite = %s, gmail_message_id = %s, gmail_thread_id = %s,"
        " canal = 'gmail' WHERE id = %s",
        (boite_envoi(), r["gmail_message_id"], r["thread_id"], cid),
    )
    return {**r, "candidature_id": cid}


def envoyer_relance(conn: psycopg.Connection, cid: int, objet: str, corps: str) -> dict:
    rel = conn.execute(
        "SELECT r.numero, r.contact_email, c.boite, c.gmail_thread_id FROM v_relances r"
        " JOIN candidatures c ON c.id = r.id WHERE r.id = %s",
        (cid,),
    ).fetchone()
    if rel is None:
        raise EnvoiImpossible("Aucune relance due pour cette candidature.")
    if not rel["contact_email"]:
        raise EnvoiImpossible("Adresse du recruteur inconnue : relance depuis ta messagerie.")
    opposee = conn.execute(
        "SELECT 1 FROM contacts WHERE lower(email) = lower(%s) AND opposition",
        (rel["contact_email"],),
    ).fetchone()
    if opposee:
        raise EnvoiImpossible("Ce contact a demandé à ne plus être sollicité.")
    fil = rel["gmail_thread_id"] if rel["boite"] == boite_envoi() else None
    r = _envoyer(conn, "relance", cid, rel["numero"], rel["contact_email"], objet, corps, [], fil)
    conn.execute("SELECT marquer_relance(%s)", (cid,))
    return {**r, "numero": rel["numero"]}


def envoyer_candidature(
    conn: psycopg.Connection,
    ca_id: int,
    objet: str | None = None,
    corps: str | None = None,
    auto: bool = False,
) -> dict:
    """Envoie une candidature préparée (CV + LM adaptés joints) ; l'offre passe « Envoyée »."""
    ca = conn.execute(
        "SELECT a.*, o.entreprise_id, o.intitule FROM candidatures_auto a"
        " JOIN offres o ON o.id = a.offre_id WHERE a.id = %s",
        (ca_id,),
    ).fetchone()
    if ca is None:
        raise EnvoiImpossible("Candidature préparée introuvable.")
    if ca["statut"] not in ("pret", "mise_de_cote"):
        raise EnvoiImpossible("Cette candidature n'est plus à envoyer.")
    objet, corps = (objet or ca["objet"] or "").strip(), (corps or ca["corps"] or "").strip()
    opposee = conn.execute(
        "SELECT 1 FROM contacts WHERE lower(email) = lower(%s) AND opposition",
        (ca["destinataire"] or "",),
    ).fetchone()
    if opposee:
        raise EnvoiImpossible("Ce contact a demandé à ne plus être sollicité.")
    pieces = conn.execute(
        "SELECT nom AS nom_fichier, contenu FROM dossier_fichiers WHERE fabrication_id = %s"
        " AND format = 'pdf' ORDER BY type",
        (ca["fabrication_id"],),
    ).fetchall()
    if not pieces:
        raise EnvoiImpossible("CV et LM adaptés introuvables pour cette offre.")
    conn.execute(
        "UPDATE candidatures_auto SET objet = %s, corps = %s WHERE id = %s", (objet, corps, ca_id)
    )
    conn.commit()
    r = _envoyer(
        conn, "candidature", ca["offre_id"], 1, ca["destinataire"], objet, corps, pieces, None, auto
    )
    cid = conn.execute(
        """
        INSERT INTO candidatures (offre_id, entreprise_id, type, statut, postule_le, origine, poste,
                                  canal, boite, gmail_message_id, gmail_thread_id, contact_email)
        VALUES (%s, %s, 'offre', 'postule', current_date, 'agent', %s, 'gmail', %s, %s, %s, %s)
        ON CONFLICT (offre_id) DO UPDATE SET statut = 'postule', postule_le = current_date
        RETURNING id
        """,
        (
            ca["offre_id"],
            ca["entreprise_id"],
            ca["intitule"],
            boite_envoi(),
            r["gmail_message_id"],
            r["thread_id"],
            ca["destinataire"],
        ),
    ).fetchone()["id"]
    conn.execute(
        "UPDATE candidatures_auto SET statut = 'envoyee', envoyee_le = now(), envoi_id = %s,"
        " candidature_id = %s, motif = NULL WHERE id = %s",
        (r["envoi_id"], cid, ca_id),
    )
    conn.execute(
        "UPDATE offres SET etape = 'envoyee', etape_maj_le = now() WHERE id = %s", (ca["offre_id"],)
    )
    conn.execute(
        "UPDATE entreprises SET derniere_candidature = GREATEST(derniere_candidature, current_date)"
        " WHERE id = %s",
        (ca["entreprise_id"],),
    )
    conn.execute(
        "UPDATE contacts SET contacte_le = current_date WHERE lower(email) = lower(%s)",
        (ca["destinataire"],),
    )
    return {**r, "candidature_id": cid}


def main() -> None:
    from app.db import connexion

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--autoriser", action="store_true", help="autorise l'envoi (navigateur)")
    p.add_argument("--etat", action="store_true", help="affiche l'état de l'envoi")
    args = p.parse_args()
    with connexion() as conn:
        if args.autoriser:
            try:
                email = autoriser(conn)
            except EnvoiImpossible as e:
                raise SystemExit(str(e)) from e
            print(f"Envoi autorisé depuis {email}. Active-le dans l'onglet Spontanées.")
        print(json.dumps(etat(conn), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
