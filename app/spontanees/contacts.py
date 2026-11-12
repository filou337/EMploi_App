"""Contact RH d'une entreprise, trouvé sur les pages publiques de son site.

Garde-fous (RGPD, respect des sites) :
- seules les adresses liées au recrutement sont gardées (recrutement@, rh@, jobs@, careers@…) ;
  jamais une adresse nominative (prenom.nom@) ni un simple contact@ ;
- seules les adresses du domaine de l'entreprise sont gardées ;
- quelques pages seulement (accueil, contact, carrières, mentions légales), robots.txt respecté,
  une requête par seconde ; la page où l'adresse a été trouvée est enregistrée.
"""

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.fiches.ats import USER_AGENT

PAGES = (
    "",
    "/contact",
    "/nous-contacter",
    "/contactez-nous",
    "/carrieres",
    "/carriere",
    "/recrutement",
    "/rejoignez-nous",
    "/careers",
    "/jobs",
    "/mentions-legales",
)
PAUSE = 1.0
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_RH = re.compile(
    r"^(?:recrut\w*|rh|drh|hr|jobs?|careers?|carrieres?|talents?|candidatures?|emploi|"
    r"join(?:us)?|rejoindre|rejoignez\w*|stage|people|humanresources|ressources\W?humaines)"
    r"(?:[._-]\w+)?$",
    re.IGNORECASE,
)
_FICHIER = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|css|js)$", re.IGNORECASE)


@dataclass(frozen=True)
class ContactTrouve:
    email: str
    source_url: str


def site_normalise(site: str) -> str:
    """« acme.fr/fr/ » -> « https://acme.fr »."""
    site = (site or "").strip()
    if not site:
        raise ValueError("Site web vide.")
    if not re.match(r"^https?://", site, re.IGNORECASE):
        site = "https://" + site
    p = urlparse(site)
    if not p.hostname or "." not in p.hostname:
        raise ValueError(f"Site web invalide : {site}")
    return f"{p.scheme.lower()}://{p.hostname.lower()}"


def domaine_racine(hote: str) -> str:
    """« www.careers.acme.co.uk » -> « acme.co.uk » (suffisant pour comparer deux domaines)."""
    morceaux = (hote or "").lower().strip(".").split(".")
    deux_niveaux = len(morceaux) >= 3 and morceaux[-2] in {"co", "com", "gouv", "org", "ac"}
    return ".".join(morceaux[-3:] if deux_niveaux else morceaux[-2:])


def est_adresse_rh(email: str) -> bool:
    local = email.split("@", 1)[0]
    return bool(_RH.match(local))


def adresses(html: str, domaine: str) -> list[str]:
    """Adresses RH du domaine présentes dans la page (texte ou liens mailto)."""
    texte = html.replace("&#64;", "@").replace("[at]", "@").replace("(at)", "@")
    vues = []
    for brute in _EMAIL.findall(texte):
        email = brute.strip(".").lower()
        if _FICHIER.search(email) or email in vues:
            continue
        if domaine_racine(email.rsplit("@", 1)[1]) != domaine:
            continue
        if est_adresse_rh(email):
            vues.append(email)
    return vues


def _robots(client: httpx.Client, base: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        r = client.get(urljoin(base, "/robots.txt"))
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        rp.parse([])
    return rp


def trouver(
    site: str, client: httpx.Client | None = None, pause: float = PAUSE
) -> list[ContactTrouve]:
    base = site_normalise(site)
    domaine = domaine_racine(urlparse(base).hostname or "")
    client = client or httpx.Client(
        timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
    )
    robots = _robots(client, base)
    trouves: dict[str, str] = {}
    for i, chemin in enumerate(PAGES):
        url = urljoin(base, chemin or "/")
        if not robots.can_fetch(USER_AGENT, url):
            continue
        if i and pause:
            time.sleep(pause)
        try:
            r = client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            continue
        for email in adresses(r.text, domaine):
            trouves.setdefault(email, str(r.url))
        if trouves and chemin in ("/recrutement", "/carrieres", "/careers", "/jobs"):
            break  # adresse de recrutement trouvée sur une page carrières : inutile d'aller plus loin
    # Les adresses « recrutement » passent avant « rh », « jobs »…
    ordre = sorted(trouves, key=lambda e: (not e.startswith("recrut"), len(e)))
    return [ContactTrouve(e, trouves[e]) for e in ordre]


def texte_accueil(site: str, client: httpx.Client | None = None, limite: int = 4000) -> str:
    """Présentation publique de l'entreprise (page d'accueil en texte) pour personnaliser le mail."""
    from app.texte import html_vers_texte

    base = site_normalise(site)
    client = client or httpx.Client(
        timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
    )
    if not _robots(client, base).can_fetch(USER_AGENT, base + "/"):
        return ""
    try:
        r = client.get(base + "/")
    except httpx.HTTPError:
        return ""
    if r.status_code != 200:
        return ""
    return html_vers_texte(re.sub(r"(?is)<(nav|footer|header)\b.*?</\1>", " ", r.text))[:limite]
