"""Importe les candidatures passées depuis Gmail : envois, accusés de réception, réponses.

Usage :  python -m app.suivi.historique [--mois 24] [--boite principale]
Lecture seule côté Gmail. Relançable : un mail déjà traité n'est jamais compté deux fois.

- Candidature envoyée par mail (objet « Candidature… », hors « Re: ») : ligne créée, entreprise
  déduite du domaine du destinataire.
- Accusé de réception d'un ATS ou d'un employeur : ligne créée si aucune candidature n'existe déjà.
- Refus, invitation, mail non remis : rattaché à la candidature (même fil Gmail, sinon même
  entreprise) et statut mis à jour ; la réponse est gardée dans `reponses`.
Les mails sont traités du plus ancien au plus récent : le dernier mot fait le statut.
"""

import argparse
import email
import re
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from email import policy
from email.utils import parseaddr, parsedate_to_datetime

import psycopg
from psycopg.types.json import Jsonb

from app.collecte import gmail
from app.collecte.sources import filtres_alertes
from app.config import boites_gmail, chemin_token
from app.db import connexion
from app.entreprises import domaine_racine, entreprise_id, est_generique_ou_ats
from app.suivi.regles import (
    ACCUSE,
    AUTO,
    AUTRE,
    ENVOI,
    HORS_EMPLOI,
    NON_REMIS,
    POSITIF,
    REFUS,
    lire,
    parle_de_candidature,
    texte_utile,
)

# Expéditeurs jamais concernés : alertes d'offres et newsletters des plateformes.
_EXCLUS = filtres_alertes() + [
    "newsletters-noreply@linkedin.com",
    "marketing-campaigns@welcometothejungle.com",
    "newsletter@welcometothejungle.com",
    "jobalert.indeed.com",
    "match.indeed.com",
    "glassdoor.com",
    "emails.hellowork.com",
    "alertes.cadremploi.fr",
    "cadremploi.fr",
]
_MOTS = "(candidature OR application OR applying OR postuler OR postulé OR entretien OR interview)"
RATTACHEMENT_MAX = timedelta(
    days=180
)  # une réponse sans fil commun : candidature de moins de 6 mois


def requetes(periode: str) -> tuple[str, str]:
    """`periode` au format Gmail : « 24m » (mois) ou « 3d » (jours)."""
    envois = f"in:sent newer_than:{periode} subject:candidature"
    recus = f"-in:sent newer_than:{periode} -from:({' OR '.join(_EXCLUS)}) {_MOTS}"
    return envois, recus


def _date(valeur: str) -> datetime:
    try:
        d = parsedate_to_datetime(valeur)
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _canal(expediteur: str) -> str:
    domaine = parseaddr(expediteur)[1].rpartition("@")[2].lower()
    if domaine and est_generique_ou_ats(domaine):
        return domaine_racine(domaine).split(".")[0]  # smartrecruiters, myworkday, welcomekit...
    return "mail"


def _mes_adresses(boite_courante: str, svc) -> set[str]:
    """Adresses de toutes mes boîtes déjà autorisées : un mail entre elles n'est pas une réponse."""
    adresses = {gmail.adresse(svc)}
    for autre in boites_gmail():
        if autre != boite_courante and chemin_token(autre).exists():
            adresses.add(gmail.adresse(gmail.service(autre)))
    return adresses


# Adresses de robots (on ne relance pas un « noreply »). Les relais Welcomekit
# (xxx@candidates.welcomekit.co) transmettent au recruteur : ils sont gardés.
_ROBOT = re.compile(
    r"no-?reply|ne-?pas-?repondre|notification|donotreply|mailer|postmaster|"
    r"@.*(smartrecruiters|workday|teamtailor-mail|softgarden|recruitee|lever|greenhouse|join\.com)",
    re.IGNORECASE,
)


def contact(adresse_brute: str) -> str | None:
    """Adresse utilisable pour une relance (une personne ou une boîte RH), sinon None (robot d'ATS)."""
    adresse = parseaddr(adresse_brute or "")[1].lower()
    return adresse if adresse and "@" in adresse and not _ROBOT.search(adresse) else None


class Import:
    def __init__(self, conn: psycopg.Connection, boite: str):
        self.conn, self.boite = conn, boite
        self.stats: Counter[str] = Counter()
        self.sans_entreprise: list[str] = []

    # --- recherche de la candidature concernée ---------------------------------------------
    def _par_fil(self, thread: str) -> dict | None:
        return self.conn.execute(
            "SELECT id, statut FROM candidatures WHERE boite = %s AND gmail_thread_id = %s"
            " ORDER BY id LIMIT 1",
            (self.boite, thread),
        ).fetchone()

    def _par_entreprise(self, eid: int, date: datetime, fenetre: timedelta) -> dict | None:
        return self.conn.execute(
            """
            SELECT id, statut FROM candidatures
            WHERE entreprise_id = %s AND postule_le BETWEEN %s AND %s
            ORDER BY postule_le DESC, id DESC LIMIT 1
            """,
            (eid, (date - fenetre).date(), (date + timedelta(days=1)).date()),
        ).fetchone()

    def _creer(self, m: dict, eid: int, lu, statut: str, canal: str) -> int | None:
        type_ = "spontanee" if "spontan" in f"{m['Subject']} {lu.poste or ''}".lower() else "offre"
        row = self.conn.execute(
            """
            INSERT INTO candidatures (entreprise_id, type, statut, postule_le, origine, poste,
                                      canal, boite, gmail_message_id, gmail_thread_id, contact_email)
            VALUES (%s, %s, %s, %s, 'manuelle', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (boite, gmail_message_id) DO NOTHING
            RETURNING id
            """,
            (
                eid,
                type_,
                statut,
                m["date"].date(),
                lu.poste,
                canal,
                self.boite,
                m["id"],
                m["thread"],
                contact(m["To"] if canal == "mail" and m.get("envoi") else m["From"]),
            ),
        ).fetchone()
        if row:
            self.stats[f"candidatures créées ({type_})"] += 1
            return row["id"]
        return None

    def _repondre(self, cid: int, m: dict, classement: str, confiance: float) -> None:
        self.conn.execute(
            """
            INSERT INTO reponses (candidature_id, boite, gmail_message_id, expediteur, recu_le,
                                  classement, confiance, a_confirmer, extrait)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (boite, gmail_message_id) DO NOTHING
            """,
            (
                cid,
                self.boite,
                m["id"],
                m["From"],
                m["date"],
                classement,
                confiance,
                confiance < 0.9,
                m["Subject"][:300],
            ),
        )

    def _statut(self, cid: int, statut: str) -> None:
        self.conn.execute(
            "UPDATE candidatures SET statut = %s, maj_le = now() WHERE id = %s", (statut, cid)
        )

    # --- traitement d'un mail ---------------------------------------------------------------
    def traiter(self, m: dict, lu, texte: str) -> None:
        if lu.type == AUTO:
            self.stats["réponses automatiques ignorées"] += 1
            return
        if m.get("envoi") and lu.type == AUTRE:
            self.stats["mes propres réponses ignorées"] += (
                1  # « Re: » envoyé par moi : pas une réponse
            )
            return
        if lu.type == HORS_EMPLOI:
            self.stats["mails hors emploi ignorés (vie étudiante, concours…)"] += 1
            return
        trouvee = self._par_fil(m["thread"])
        eid = entreprise_id(self.conn, lu.entreprise) if lu.entreprise else None
        # Un mail neutre n'est rattaché par l'entreprise que s'il parle de candidature : sinon
        # tout le courrier d'un employeur (ou d'une école) finirait en « réponses ».
        par_entreprise = lu.type != AUTRE or parle_de_candidature(m["Subject"])
        if trouvee is None and eid is not None and lu.type != ENVOI and par_entreprise:
            # Un accusé d'ATS est une candidature à part, sauf s'il suit de près un envoi par mail.
            fenetre = timedelta(days=3) if lu.type == ACCUSE else RATTACHEMENT_MAX
            trouvee = self._par_entreprise(eid, m["date"], fenetre)

        if lu.type == ENVOI:
            if eid is None:
                self.sans_entreprise.append(f"{m['To'][:50]} | {m['Subject'][:60]}")
                return
            self._creer(m, eid, lu, "postule", "mail")
        elif lu.type == ACCUSE:
            if trouvee is None:
                if eid is None:
                    self.sans_entreprise.append(f"{m['From'][:50]} | {m['Subject'][:60]}")
                    return
                self._creer(m, eid, lu, "postule", _canal(m["From"]))
            else:
                self.stats["accusés rattachés"] += 1
        elif lu.type in (REFUS, POSITIF):
            if trouvee is None:
                if eid is None or not parle_de_candidature(m["Subject"]):
                    self.stats["réponses sans candidature identifiable"] += 1
                    return
                cid = self._creer(m, eid, lu, "postule", _canal(m["From"]))
                trouvee = (
                    {"id": cid}
                    if cid
                    else self.conn.execute(
                        "SELECT id FROM candidatures WHERE boite = %s AND gmail_message_id = %s",
                        (self.boite, m["id"]),
                    ).fetchone()
                )
            classement = "negative" if lu.type == REFUS else "positive"
            self._repondre(trouvee["id"], m, classement, lu.confiance)
            self._statut(trouvee["id"], "refus" if lu.type == REFUS else "reponse_positive")
            self.stats[f"réponses {classement}s"] += 1
        elif lu.type == NON_REMIS:
            if trouvee is not None:
                self._statut(trouvee["id"], "ignore")  # jamais arrivée : ne bloque pas l'employeur
                self.stats["envois non remis"] += 1
        elif lu.type == AUTRE and trouvee is not None:
            self._repondre(trouvee["id"], m, "neutre", lu.confiance)
            self.stats["réponses neutres"] += 1


def effacer_import(conn: psycopg.Connection, boite: str) -> int:
    """Efface ce qu'un import précédent a créé pour cette boîte (pour rejouer avec de meilleures règles).

    Ne touche qu'aux lignes issues d'un mail (gmail_message_id renseigné) : une candidature
    saisie à la main ou créée par l'outil reste en place.
    """
    # Une réponse confirmée à la main (onglet « Réponses ») et sa candidature sont gardées :
    # le nouvel import ne les recrée pas en double (même identifiant de mail).
    conn.execute(
        "DELETE FROM reponses WHERE confirme_le IS NULL AND (boite = %s OR candidature_id IN"
        " (SELECT id FROM candidatures WHERE boite = %s AND origine = 'manuelle'"
        "  AND gmail_message_id IS NOT NULL))",
        (boite, boite),
    )
    return conn.execute(
        "DELETE FROM candidatures c WHERE boite = %s AND origine = 'manuelle'"
        " AND gmail_message_id IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM reponses r WHERE r.candidature_id = c.id)",
        (boite,),
    ).rowcount


def importer_boite(
    boite: str, mois: int | None = 24, refaire: bool = False, jours: int | None = None
) -> None:
    if refaire:
        with connexion() as conn:
            print(f"  {effacer_import(conn, boite)} candidatures d'un import précédent effacées.")
    svc = gmail.service(boite)
    moi = _mes_adresses(boite, svc)
    q_envois, q_recus = requetes(f"{jours}d" if jours else f"{mois}m")
    noms = ("From", "To", "Subject", "Date")
    envois = gmail.entetes(svc, list(gmail.ids_messages(svc, q_envois)), noms=noms)
    recus = gmail.entetes(svc, list(gmail.ids_messages(svc, q_recus)), noms=noms)
    recus = [m for m in recus if parseaddr(m["From"])[1].lower() not in moi]
    for m in envois + recus:
        m["date"] = _date(m["Date"])
    envoi_ids = {m["id"] for m in envois}
    for m in envois:
        m["envoi"] = True
    mails = sorted({m["id"]: m for m in envois + recus}.values(), key=lambda m: m["date"])
    print(f"  {len(envois)} mails envoyés et {len(recus)} reçus à examiner.")

    erreurs: list[dict] = []
    with connexion() as conn:
        imp = Import(conn, boite)
        for m in mails:
            moi_envoie = m["id"] in envoi_ids
            texte = ""
            try:
                if not moi_envoie:
                    brut = gmail.message_brut(svc, m["id"])
                    texte = texte_utile(email.message_from_bytes(brut, policy=policy.default))
                    time.sleep(0.1)
                lu = lire(m["From"], m["To"], m["Subject"], texte, moi_envoie)
                with conn.transaction():
                    imp.traiter(m, lu, texte)
            except Exception as e:  # noqa: BLE001 — un mail illisible ne bloque pas les autres
                erreurs.append({"mail": m["id"], "erreur": f"{type(e).__name__}: {e}"[:300]})
        conn.execute(
            """
            UPDATE entreprises e SET derniere_candidature = c.derniere
            FROM (SELECT entreprise_id, max(postule_le) AS derniere FROM candidatures
                  WHERE statut <> 'ignore' GROUP BY entreprise_id) c
            WHERE e.id = c.entreprise_id
            """
        )
        conn.execute(
            "INSERT INTO executions (tache, fin, mails_lus, erreurs, details)"
            " VALUES ('suivi', now(), %s, %s, %s)",
            (len(mails), Jsonb(erreurs) if erreurs else None, Jsonb(dict(imp.stats))),
        )
    for nom, n in sorted(imp.stats.items()):
        print(f"  {nom:<42}{n:>5}")
    if imp.sans_entreprise:
        print(f"  {len(imp.sans_entreprise)} mails sans entreprise identifiable, par exemple :")
        for ligne in imp.sans_entreprise[:5]:
            print(f"    · {ligne}")
    for e in erreurs[:5]:
        print(f"  ! {e['mail']} : {e['erreur'][:150]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mois", type=int, default=24)
    p.add_argument("--jours", type=int, help="période en jours (prioritaire sur --mois)")
    p.add_argument("--boite", action="append", help="boîte à lire (défaut : GMAIL_BOITES)")
    p.add_argument("--refaire", action="store_true", help="efface l'import précédent de la boîte")
    args = p.parse_args()
    for boite in args.boite or boites_gmail():
        print(f"[{boite}]")
        importer_boite(boite, args.mois, args.refaire, args.jours)


if __name__ == "__main__":
    main()
