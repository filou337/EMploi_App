"""Alertes Indeed : résumés (donotreply@jobalert.indeed.com) et suggestions (match.indeed.com).

Résumé : fiches « intitulé / Entreprise - Ville (dépt) / mentions / extrait / ancienneté »,
chacune suivie d'un lien contenant jk=<identifiant>.
Suggestion : une seule offre ; le lien est un redirecteur cts.indeed.com dont le chemin est
du JSON compressé (gzip + base64) qui contient jk=<identifiant>. On le décode localement :
aucune page Indeed n'est téléchargée.
Le lien est réduit à https://fr.indeed.com/viewjob?jk=<id>.
Annonce sponsorisée : le redirecteur mène à la régie (pagead/clk?ad=...) sans identifiant
d'offre ; la clé devient indeed:sponsorise:<empreinte de entreprise|intitulé|ville>.
"""

import base64
import binascii
import gzip
import hashlib
import re
import zlib
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from app.collecte.normalisation import cle_floue, nettoyer
from app.collecte.parseurs import OffreAlerte

_JK = re.compile(r"[?&]jk=([0-9a-f]{16})")
_URL = re.compile(r"https?://\S+")
_AGE = re.compile(
    r"^(?:il y a (?P<jours>\d+)\+? jours?|il y a (?P<heures>\d+) heures?|publi[ée]e? (?:à l'instant|aujourd'hui)|aujourd'hui)$",
    re.IGNORECASE,
)
_ENTETE = re.compile(
    r"^(?:Alerte Emploi Indeed|\d+ nouveaux? emplois?|Emplois \d+-\d+ sur|Voir les résultats|Voir plus d)",
    re.IGNORECASE,
)
_MENTION = re.compile(
    r"€|\bpar (?:an|mois|heure|jour)\b|de l'heure|Employeur réactif|Candidature simplifiée"
    r"|Recrutement urgent|Plusieurs postes|Nouveau$|Télétravail|Temps (?:plein|partiel)",
    re.IGNORECASE,
)


def _texte(message: EmailMessage) -> str:
    corps = message.get_body(preferencelist=("plain",))
    return corps.get_content() if corps is not None else ""


def _date_mail(message: EmailMessage) -> date | None:
    try:
        return parsedate_to_datetime(message["Date"]).date()
    except (TypeError, ValueError):
        return None


def jk_du_redirecteur(url: str) -> str | None:
    """Identifiant d'offre caché dans un lien cts.indeed.com/v3/<gzip base64>."""
    m = _JK.search(url)
    if m:
        return m.group(1)
    for segment in urlparse(url).path.split("/"):
        if not segment.startswith("H4sI"):  # en-tête gzip encodé en base64
            continue
        try:
            brut = gzip.decompress(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        except (binascii.Error, OSError, EOFError, zlib.error):
            continue
        m = re.search(r"jk=([0-9a-f]{16})", brut.decode("utf-8", "replace"))
        if m:
            return m.group(1)
    return None


def _url(jk: str) -> str:
    return f"https://fr.indeed.com/viewjob?jk={jk}"


def _publiee(age: str, recu: date | None) -> date | None:
    m = _AGE.match(age)
    if not m or recu is None:
        return None
    return recu - timedelta(days=int(m.group("jours") or 0))


def _entreprise_lieu(ligne: str) -> tuple[str, str]:
    """'Caisse d'Epargne Ile de France - Paris (75)' -> ('Caisse d'Epargne Ile de France', 'Paris (75)')."""
    entreprise, sep, lieu = ligne.rpartition(" - ")
    return (entreprise, lieu) if sep else (ligne, "")


def _resume(lignes: list[str], recu: date | None) -> list[OffreAlerte]:
    offres: list[OffreAlerte] = []
    for i, ligne in enumerate(lignes):
        m = _JK.search(ligne)
        if not m or not _URL.fullmatch(ligne):
            continue
        fiche: list[str] = []
        j = i - 1
        while j >= 0 and not _URL.search(lignes[j]) and not _ENTETE.match(lignes[j]):
            fiche.insert(0, lignes[j])
            j -= 1
        if len(fiche) < 2:
            continue
        intitule, (entreprise, lieu) = fiche[0], _entreprise_lieu(fiche[1])
        reste = fiche[2:]
        age = reste.pop() if reste and _AGE.match(reste[-1]) else ""
        offres.append(
            OffreAlerte(
                source="indeed",
                url_cle=_url(m.group(1)),
                intitule=intitule,
                entreprise=entreprise,
                lieu=lieu,
                details=tuple(reste),
                publiee_le=_publiee(age, recu),
            )
        )
    return offres


def _suggestion(lignes: list[str]) -> list[OffreAlerte]:
    for i, ligne in enumerate(lignes):
        if not re.match(r"Voir l'emploi\s*:", ligne, re.IGNORECASE):
            continue
        url = _URL.search(ligne)
        jk = jk_du_redirecteur(url.group()) if url else None
        fiche: list[str] = []
        j = i - 1
        while j >= 0 and not re.search(r"pourrait correspondre|^Bonjour", lignes[j], re.IGNORECASE):
            fiche.insert(0, lignes[j])
            j -= 1
        details = [lg for lg in fiche[3:] if not lg.lower().startswith("type de poste")]
        mentions = [lg for lg in fiche[3:] if lg.lower().startswith("type de poste")]
        if len(fiche) < 3:
            return []
        if jk is None:  # annonce sponsorisée : pas d'identifiant d'offre dans le lien
            empreinte = hashlib.sha1(cle_floue(fiche[1], fiche[0], fiche[2]).encode()).hexdigest()
            cle, mentions = (
                f"indeed:sponsorise:{empreinte[:16]}",
                ["Annonce sponsorisée", *mentions],
            )
        else:
            cle = _url(jk)
        return [
            OffreAlerte(
                source="indeed",
                url_cle=cle,
                intitule=fiche[0],
                entreprise=fiche[1],
                lieu=fiche[2],
                details=tuple(mentions + details),
            )
        ]
    return []


def extraire(message: EmailMessage) -> list[OffreAlerte]:
    """Offres du mail ; ValueError si des liens d'offre sont présents mais illisibles."""
    lignes = [nettoyer(lg) for lg in _texte(message).splitlines()]
    lignes = [lg for lg in lignes if lg]
    if any(lg.startswith("Alerte Emploi Indeed") for lg in lignes[:3]):
        offres = _resume(lignes, _date_mail(message))
        liens = sum(1 for lg in lignes if _JK.search(lg))
    else:
        offres = _suggestion(lignes)
        liens = sum(1 for lg in lignes if re.match(r"Voir l'emploi\s*:", lg, re.IGNORECASE))
    vus: set[str] = set()
    uniques = [o for o in offres if not (o.url_cle in vus or vus.add(o.url_cle))]
    if liens and not uniques:
        raise ValueError(f"{liens} liens d'offre mais aucune fiche lisible : format changé ?")
    return uniques
