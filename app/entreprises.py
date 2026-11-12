"""Rattachement d'un nom d'entreprise (ou d'un domaine mail) à une ligne de `entreprises`.

Deux écritures du même employeur doivent tomber sur la même ligne : « Corail Analytics »
dans une alerte et corailanalytics.com dans un mail. On compare donc les noms normalisés
sans espaces.
"""

import psycopg

from app.collecte.normalisation import norm_entreprise

# Domaines qui n'identifient pas l'employeur : messageries, ATS, plateformes d'emploi.
DOMAINES_GENERIQUES = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "outlook.fr",
    "hotmail.com",
    "hotmail.fr",
    "live.com",
    "live.fr",
    "yahoo.com",
    "yahoo.fr",
    "icloud.com",
    "orange.fr",
    "free.fr",
    "wanadoo.fr",
    "laposte.net",
    "sfr.fr",
    "protonmail.com",
    "proton.me",
}
DOMAINES_ATS = {
    "smartrecruiters.com",
    "myworkday.com",
    "workday.com",
    "welcomekit.co",
    "welcometothejungle.com",
    "teamtailor-mail.com",
    "teamtailor.com",
    "softgarden.io",
    "greenhouse.io",
    "lever.co",
    "hire.lever.co",
    "recruitee.com",
    "personio.de",
    "personio.com",
    "join.com",
    "flatchr.io",
    "taleo.net",
    "successfactors.com",
    "successfactors.eu",
    "icims.com",
    "icims.eu",
    "profils.org",
    "hellowork.com",
    "reply.hellowork.com",
    "jobvite.com",
    "workablemail.com",
    "workable.com",
    "ashbyhq.com",
    "beetween.com",
    "digitalrecruiters.com",
    "talent-soft.com",
    "cornerstoneondemand.com",
    "konnevia.fr",
    "indeed.com",
    "linkedin.com",
    "mailinblack.com",
    "invitations.mailinblack.com",
}
_SUFFIXES_DOUBLES = {"co.uk", "com.au", "co.jp", "com.br"}
_ETIQUETTES_TECHNIQUES = {"mail", "email", "e", "news", "info", "noreply", "notification"}
# Administrations : le service est dans le sous-domaine (intradef.gouv.fr), pas dans « gouv ».
NOMS_ADMINISTRATIONS = {"intradef": "Ministère des Armées", "defense": "Ministère des Armées"}


def domaine_racine(domaine: str) -> str:
    """'mail.sia-partners.com' -> 'sia-partners.com'."""
    parties = domaine.lower().strip(".").split(".")
    n = 3 if ".".join(parties[-2:]) in _SUFFIXES_DOUBLES else 2
    return ".".join(parties[-n:])


def est_generique_ou_ats(domaine: str) -> bool:
    racine = domaine_racine(domaine)
    return racine in DOMAINES_GENERIQUES or racine in DOMAINES_ATS or domaine in DOMAINES_ATS


def nom_depuis_domaine(domaine: str) -> str | None:
    """'tsg-solutions.com' -> 'tsg solutions' ; None pour un domaine générique ou d'ATS.

    Exception : les sous-domaines Teamtailor portent le nom de l'employeur
    (gitguardian.teamtailor-mail.com -> 'gitguardian').
    """
    domaine = domaine.lower()
    if domaine_racine(domaine).startswith("teamtailor") and domaine.count(".") >= 2:
        return domaine.split(".")[0].replace("-", " ")
    parties = domaine.split(".")
    if domaine_racine(domaine) == "gouv.fr" and len(parties) >= 3:
        service = parties[-3]
        return NOMS_ADMINISTRATIONS.get(service, service.replace("-", " "))
    if est_generique_ou_ats(domaine):
        return None
    racine = domaine_racine(domaine).split(".")
    if racine[0] in _ETIQUETTES_TECHNIQUES and len(racine) >= 2:  # mail.acme -> acme
        return racine[1].replace("-", " ")
    return racine[0].replace("-", " ")


def compact(nom: str) -> str:
    return norm_entreprise(nom).replace(" ", "")


def entreprise_id(conn: psycopg.Connection, nom: str) -> int:
    """Id de l'entreprise, créée si besoin. « Corail Analytics » = « corailanalytics »."""
    cle = compact(nom)
    row = conn.execute(
        "SELECT id FROM entreprises WHERE replace(nom_norm, ' ', '') = %s ORDER BY id LIMIT 1",
        (cle,),
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO entreprises (nom_norm, nom_affiche) VALUES (%s, %s)"
        " ON CONFLICT (nom_norm) DO UPDATE SET nom_norm = EXCLUDED.nom_norm RETURNING id",
        (norm_entreprise(nom), nom),
    ).fetchone()["id"]
