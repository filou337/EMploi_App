"""Suite donnée à une réponse à une candidature spontanée (appelé par l'agent Suivi).

- Renvoi vers le site officiel ou « ne plus contacter » : rien (le Suivi bloque déjà la relance).
- « Écrivez plutôt à X » avec l'adresse : mail préparé pour X, qui attend TON clic
  (`spontanees.a_valider`) ; la candidature d'origine n'est plus relancée.
  Sans adresse écrite : notification, à toi de trouver le bon contact.
- « Pas de recrutement pour l'instant » : une seule relance 30 jours après la réponse
  (`candidatures.relance_prevue_le`), envoyée par l'agent Relances, puis plus rien.
"""

import re
from datetime import date, timedelta
from email.utils import parseaddr

import psycopg
from psycopg.types.json import Jsonb

from app.notifications import notifier
from app.suivi.regles import pas_maintenant, redirection, relance_interdite

DELAI_PLUS_TARD = 30
_ROBOT = re.compile(
    r"no-?reply|ne-?pas-?repondre|donotreply|notification|mailer|postmaster", re.IGNORECASE
)


def _signataire(expediteur: str) -> str:
    nom, adresse = parseaddr(expediteur or "")
    return nom.strip('" ') or adresse


def corps_redirige(corps: str, conseil: str) -> str:
    """Mail d'origine, introduit par « Sur les conseils de … » (salutation et demande de
    transmission retirées : elles ne valent plus pour la nouvelle personne)."""
    paragraphes = [p for p in re.split(r"\n\s*\n", corps or "") if p.strip()]
    if paragraphes and re.match(r"(?i)\s*(madame|monsieur|bonjour|hello)\b", paragraphes[0]):
        paragraphes = paragraphes[1:]
    paragraphes = [p for p in paragraphes if "transmettre ce message" not in p]
    intro = (
        f"Sur les conseils de {conseil}, qui m'a invité à vous écrire, je me permets de vous"
        " adresser ma candidature spontanée."
    )
    return "\n\n".join(["Madame, Monsieur,", intro, *paragraphes])


def traiter(
    conn: psycopg.Connection, cand: dict, expediteur: str, moi: str, recu: date, texte: str
) -> str | None:
    """Applique la règle à une réponse reçue pour la candidature spontanée `cand` ; renvoie ce
    qui a été fait (pour les statistiques du Suivi) ou None."""
    if relance_interdite(texte):
        return None
    emetteur = parseaddr(expediteur or "")[1].lower()
    red = redirection(texte, {moi, emetteur, cand.get("contact_email") or ""})
    if red is not None:
        return _rediriger(conn, cand, expediteur, red.email)
    if pas_maintenant(texte):
        humain = emetteur if emetteur and not _ROBOT.search(emetteur) else None
        n = conn.execute(
            "UPDATE candidatures SET relance_prevue_le = %s,"
            " contact_email = coalesce(%s, contact_email), maj_le = now()"
            " WHERE id = %s AND relance_prevue_le IS NULL",
            (recu + timedelta(days=DELAI_PLUS_TARD), humain, cand["id"]),
        ).rowcount
        return "spontanées « pas pour l'instant » (relance à J+30)" if n else None
    return None


def _rediriger(
    conn: psycopg.Connection, cand: dict, expediteur: str, email: str | None
) -> str | None:
    ent = conn.execute(
        "SELECT nom_affiche FROM entreprises WHERE id = %s", (cand["entreprise_id"],)
    ).fetchone()
    nom_ent = ent["nom_affiche"] if ent else "l'entreprise"
    qui = _signataire(expediteur)
    if email is None:
        notifier(
            conn,
            "reponse",
            f"Spontanée — {nom_ent} : écrire à un autre service",
            f"{qui} t'invite à t'adresser à un autre service, sans donner d'adresse :"
            " trouve le bon contact et ajoute-le dans l'onglet Spontanées.",
        )
        return "spontanées redirigées (adresse à trouver)"
    deja = conn.execute(
        "SELECT 1 FROM spontanees WHERE entreprise_id = %s AND lower(destinataire) = %s",
        (cand["entreprise_id"], email),
    ).fetchone()
    if deja:
        return None
    contact = conn.execute(
        "INSERT INTO contacts (entreprise_id, email, fonction, source_url) VALUES (%s, %s,"
        " 'recrutement', %s) ON CONFLICT (entreprise_id, email) DO UPDATE SET email = EXCLUDED.email"
        " RETURNING id, opposition",
        (cand["entreprise_id"], email, f"réponse de {qui}"),
    ).fetchone()
    if contact["opposition"]:
        return None
    origine = conn.execute(
        "SELECT objet, corps FROM spontanees WHERE candidature_id = %s ORDER BY id DESC LIMIT 1",
        (cand["id"],),
    ).fetchone()
    objet = (origine and origine["objet"]) or "Candidature spontanée"
    conseil = f"{qui} ({nom_ent})" if qui and nom_ent not in qui else qui or nom_ent
    conn.execute(
        "INSERT INTO spontanees (entreprise_id, contact_id, destinataire, objet, corps,"
        " verification, a_valider) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            cand["entreprise_id"],
            contact["id"],
            email,
            objet,
            corps_redirige(origine["corps"] if origine else "", conseil),
            Jsonb({"ok": True, "retires": [], "redirection_de": qui}),
            f"redirection : {qui} conseille d'écrire à {email}",
        ),
    )
    conn.execute(
        "UPDATE candidatures SET relance_ignoree = TRUE, relance_motif = %s, maj_le = now()"
        " WHERE id = %s",
        (f"redirigée vers {email}", cand["id"]),
    )
    notifier(
        conn,
        "spontanee",
        f"Spontanée — {nom_ent} : écrire à {email}",
        f"{qui} conseille d'écrire à {email}. Le mail est prêt dans l'onglet Spontanées :"
        " relis-le et clique « Envoyer ».",
    )
    return "spontanées redirigées (mail prêt à valider)"
