"""Offres publiées sur le site carrières de l'employeur, au format standard schema.org JobPosting.

Beaucoup de sites carrières (pages maison, Workday, Taleo, SuccessFactors…) décrivent chaque
offre dans un bloc JSON-LD « JobPosting », destiné aux moteurs de recherche : intitulé, lieu,
date, description complète, employeur, date limite. On le lit comme Google le lit.

Méthode, à cadence lente et dans le respect de robots.txt :
1. pages d'offres listées dans les plans du site (sitemaps déclarés dans robots.txt, ou
   /sitemap.xml), sinon liens des pages carrières (/carrieres, /careers, /jobs…) ;
2. seules les pages d'offres « data » sont lues (mots-clés dans l'adresse), 25 au plus ;
3. chaque page donne 0, 1 ou plusieurs JobPosting ; une offre expirée (validThrough) est ignorée.

Résultat : un Tableau « site » comme ceux des ATS publics (recherche des fiches, veille). Il est
marqué incomplet : une offre absente de ce qu'on a lu n'est pas pour autant expirée.
"""

import gzip
import json
import re
import threading
import time
from collections import Counter
from datetime import date
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.collecte.normalisation import sans_accents
from app.fiches.ats import USER_AGENT, Poste, Tableau, _cadence, _date_iso, _pays
from app.texte import html_vers_texte

PAGES_CARRIERES = (
    "/carrieres",
    "/carriere",
    "/careers",
    "/jobs",
    "/recrutement",
    "/nous-rejoindre",
    "/rejoignez-nous",
    "/offres-emploi",
    "/emploi",
)
MAX_SITEMAPS = 6
MAX_PAGES = 25
BUDGET_SECONDES = 45  # par adresse lue : un grand site ne doit pas bloquer le passage
TAILLE_MAX = 8_000_000  # octets : au-delà, le plan du site est ignoré
# Les grands groupes publient leurs offres sur un sous-domaine dédié.
SOUS_DOMAINES = ("careers", "jobs", "recrutement", "carrieres", "emploi", "join")
_LD = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_HREF = re.compile(r"href=[\"']([^\"'#]+)[\"']", re.IGNORECASE)
# Fiche d'une offre précise (souvent un identifiant numérique) : on la suit depuis une page de liste.
_URL_FICHE = re.compile(
    r"/(?:jobs?|offres?|emplois?|careers?|postes?|vacanc\w*|positions?|job-?offers?)/"
    r".*(?:\d{4,}|[a-z]+-[a-z]+-[a-z]+)",
    re.IGNORECASE,
)
# Une adresse de page d'offre (et pas une page d'actualité ou de produit).
_URL_OFFRE = re.compile(
    r"/(?:jobs?|offres?|emplois?|careers?|carrieres?|postes?|vacanc\w*|positions?|recrutement"
    r"|job-?offers?|opportunit\w*)(?:/|-|$)",
    re.IGNORECASE,
)
# Une offre data, d'après les mots de son adresse.
_URL_DATA = re.compile(
    r"(?<![a-z])(?:data|donnees|analyst|analyste|analytics?|bi|business-intelligence|decisionnel"
    r"|dataviz|etl|sql|power-?bi|tableau|dbt|big-data|reporting)(?![a-z])",
    re.IGNORECASE,
)

_client = httpx.Client(
    timeout=20,
    follow_redirects=True,
    headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"},
)


_budget = threading.local()  # fin du budget de l'employeur en cours, propre à chaque fil


def _depasse() -> bool:
    fin = getattr(_budget, "fin", 0.0)
    return bool(fin) and time.monotonic() > fin


def _get(url: str) -> httpx.Response | None:
    if _depasse():
        return None
    verrou = _cadence.attendre(url)
    try:
        r = _client.get(url)
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        _cadence.fin(url, verrou)
    if int(r.headers.get("content-length") or 0) > TAILLE_MAX or len(r.content) > TAILLE_MAX:
        return None
    return r


def normaliser(site: str) -> str:
    """« acme.fr/fr » -> « https://acme.fr » ; ValueError si ce n'est pas un domaine."""
    site = (site or "").strip()
    if not re.match(r"^https?://", site, re.IGNORECASE):
        site = "https://" + site
    hote = (urlparse(site).hostname or "").lower()
    if "." not in hote:
        raise ValueError(f"Site invalide : {site}")
    return f"https://{hote}"


# --- Lecture des JobPosting ----------------------------------------------------------------------
def _objets(donnee) -> list[dict]:
    """Tous les objets d'un bloc JSON-LD (listes, @graph, ItemList imbriqués)."""
    if isinstance(donnee, list):
        return [o for x in donnee for o in _objets(x)]
    if not isinstance(donnee, dict):
        return []
    trouves = [donnee]
    for cle in ("@graph", "itemListElement", "item", "mainEntity"):
        if cle in donnee:
            trouves += _objets(donnee[cle])
    return trouves


def _est_offre(o: dict) -> bool:
    t = o.get("@type")
    return "JobPosting" in (t if isinstance(t, list) else [t])


def jobpostings(html: str) -> list[dict]:
    offres = []
    for bloc in _LD.findall(html or ""):
        try:
            donnee = json.loads(bloc.strip())
        except ValueError:
            try:  # sauts de ligne ou caractères de contrôle laissés dans les chaînes
                donnee = json.loads(re.sub(r"[\x00-\x1f]+", " ", bloc.strip()))
            except ValueError:
                continue
        offres += [o for o in _objets(donnee) if _est_offre(o)]
    return offres


def _texte(v) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or v.get("@value") or "")
    if isinstance(v, list):
        return ", ".join(t for t in (_texte(x) for x in v) if t)
    return str(v or "")


def _lieu(o: dict) -> str:
    lieux = o.get("jobLocation") or []
    morceaux = []
    for lieu in lieux if isinstance(lieux, list) else [lieux]:
        a = (lieu or {}).get("address") if isinstance(lieu, dict) else None
        if isinstance(a, str):
            morceaux.append(a)
        elif isinstance(a, dict):
            ville = a.get("addressLocality") or a.get("addressRegion") or ""
            cp = str(a.get("postalCode") or "")[:2]
            pays = _texte(a.get("addressCountry"))
            morceaux.append(
                ", ".join(x for x in (f"{ville} ({cp})" if cp and ville else ville, pays) if x)
            )
    lieu = " / ".join(dict.fromkeys(m for m in morceaux if m))
    if "TELECOMMUTE" in str(o.get("jobLocationType") or "").upper():
        lieu = f"{lieu} / Télétravail" if lieu else "Télétravail"
    return lieu


def poste(o: dict, url: str, aujourd_hui: date | None = None) -> tuple[Poste, str] | None:
    """(Poste, nom de l'employeur) d'un JobPosting, ou None s'il est illisible ou expiré."""
    titre = html_vers_texte(_texte(o.get("title") or o.get("name"))).strip()
    if not titre:
        return None
    fin = _date_iso(str(o.get("validThrough") or "")[:10] or None)
    if fin and fin < (aujourd_hui or date.today()):
        return None
    texte = html_vers_texte(str(o.get("description") or ""))
    contrat = _texte(o.get("employmentType"))
    if contrat and contrat.lower() not in texte.lower():
        texte = f"Type de contrat : {contrat}\n\n{texte}"
    lien = str(o.get("url") or "") or url
    return (
        Poste(titre, _lieu(o), lien, _date_iso(str(o.get("datePosted") or "")[:10] or None), texte),
        _texte(o.get("hiringOrganization")).strip(),
    )


# --- Où sont les offres ? -----------------------------------------------------------------------
def _robots(base: str) -> tuple[RobotFileParser, list[str]]:
    rp = RobotFileParser()
    r = _get(base + "/robots.txt")
    lignes = r.text.splitlines() if r is not None and r.status_code == 200 else []
    rp.parse(lignes)
    sitemaps = [
        urljoin(base + "/", lg.split(":", 1)[1].strip())
        for lg in lignes
        if lg.lower().startswith("sitemap:")
    ]
    return rp, sitemaps


def _contenu_xml(r: httpx.Response) -> str:
    if r.url.path.endswith(".gz") or r.content[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(r.content).decode("utf-8", "replace")
        except OSError:
            return ""
    return r.text


def _urls_sitemaps(base: str, sitemaps: list[str], rp: RobotFileParser) -> list[str]:
    a_lire, vus, pages = list(sitemaps) or [base + "/sitemap.xml"], set(), []
    while a_lire and len(vus) < MAX_SITEMAPS:
        sm = a_lire.pop(0)
        if sm in vus or not rp.can_fetch(USER_AGENT, sm):
            continue
        vus.add(sm)
        r = _get(sm)
        if r is None or r.status_code != 200:
            continue
        xml = _contenu_xml(r)
        locs = _LOC.findall(xml)
        if "<sitemapindex" in xml[:2000].lower():
            # Index : on lit d'abord les plans qui parlent d'offres (jobs, careers…).
            locs.sort(key=lambda u: not _URL_OFFRE.search(u))
            a_lire += [urljoin(sm, u) for u in locs]
        else:
            pages += [urljoin(sm, u) for u in locs]
    return pages


def _urls_pages_carrieres(base: str, rp: RobotFileParser) -> list[str]:
    hote = urlparse(base).hostname
    liens = []
    for chemin in PAGES_CARRIERES:
        url = base + chemin
        if not rp.can_fetch(USER_AGENT, url):
            continue
        r = _get(url)
        if r is None or r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            continue
        if jobpostings(r.text):
            liens.append(str(r.url))  # la page liste déjà les offres en JSON-LD
        for href in _HREF.findall(r.text):
            lien = urljoin(str(r.url), href)
            if urlparse(lien).hostname == hote:
                liens.append(lien)
        if len(liens) > 5:
            break
    return liens


def pages_offres(base: str) -> tuple[list[str], RobotFileParser]:
    """Pages d'offres data du site, d'après ses plans ou ses pages carrières (MAX_PAGES au plus)."""
    rp, sitemaps = _robots(base)
    candidates = _urls_sitemaps(base, sitemaps, rp) or _urls_pages_carrieres(base, rp)
    retenues = []
    for u in candidates:
        chemin = sans_accents(urlparse(u).path.lower())
        offre_data = bool(_URL_OFFRE.search(chemin) and _URL_DATA.search(chemin))
        liste = u.rstrip("/").endswith(PAGES_CARRIERES)  # page carrières qui contient les offres
        if (offre_data or liste) and u not in retenues and rp.can_fetch(USER_AGENT, u):
            retenues.append(u)
    return retenues[:MAX_PAGES], rp


def _bases(site: str) -> list[str]:
    """Le site, puis ses sous-domaines carrières habituels s'il s'agit d'un domaine nu."""
    base = normaliser(site)
    hote = base.removeprefix("https://").removeprefix("www.")
    if hote.count(".") != 1:
        return [base]  # déjà un sous-domaine (careers.acme.com) : on le lit tel quel
    # Sous-domaines carrières d'abord : le plan du site principal est souvent énorme.
    return [f"https://{sd}.{hote}" for sd in SOUS_DOMAINES] + [base]


def _fiches_liees(html: str, url: str, rp: RobotFileParser) -> list[str]:
    """Liens d'une page de liste vers des fiches d'offre du même site, les offres data d'abord."""
    hote = urlparse(url).hostname
    liens = []
    for href in _HREF.findall(html):
        lien = urljoin(url, href).split("#")[0]
        chemin = sans_accents(urlparse(lien).path.lower())
        if (
            urlparse(lien).hostname == hote
            and lien.rstrip("/") != url.rstrip("/")
            and _URL_FICHE.search(chemin)
            and lien not in liens
            and rp.can_fetch(USER_AGENT, lien)
        ):
            liens.append(lien)
    return sorted(liens, key=lambda u: not _URL_DATA.search(sans_accents(urlparse(u).path.lower())))


def _lire(base: str) -> tuple[dict[str, Poste], Counter[str]]:
    pages, rp = pages_offres(base)
    postes: dict[str, Poste] = {}
    employeurs: Counter[str] = Counter()
    a_lire, vus = list(pages), set()
    while a_lire and len(vus) < MAX_PAGES and not _depasse():
        url = a_lire.pop(0)
        if url in vus:
            continue
        vus.add(url)
        r = _get(url)
        if r is None or r.status_code != 200:
            continue
        offres = jobpostings(r.text)
        if not offres and url in pages:
            # Page de liste : on descend vers ses fiches d'offre (avant les autres pages de liste).
            a_lire = _fiches_liees(r.text, str(r.url), rp)[:10] + a_lire
        for o in offres:
            lu = poste(o, str(r.url))
            if lu:
                p, employeur = lu
                postes.setdefault(p.url, p)
                if employeur:
                    employeurs[employeur] += 1
    return postes, employeurs


def tableau(site: str, budget: float = BUDGET_SECONDES) -> Tableau | None:
    """Offres JobPosting du site de l'employeur, ou None si le site n'en publie pas."""
    try:
        bases = _bases(site)
    except ValueError:
        return None
    try:
        for base in bases:
            _budget.fin = (
                time.monotonic() + budget if budget else 0.0
            )  # budget propre à chaque adresse
            postes, employeurs = _lire(base)
            if postes:
                break
        else:
            return None
    finally:
        _budget.fin = 0.0
    liste = list(postes.values())
    return Tableau(
        "site",
        base,
        liste,
        nom_employeur=employeurs.most_common(1)[0][0] if employeurs else None,
        pays=_pays([p.lieu for p in liste]),
        complet=False,
    )
