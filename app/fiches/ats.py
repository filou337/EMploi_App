"""Connecteurs vers les API publiques des ATS (gratuites, sans clé).

Chaque connecteur lit la liste des postes ouverts d'un employeur, désigné par son
identifiant chez l'ATS (« slug »). Ce sont les API qui alimentent les pages carrières :
elles sont prévues pour être lues. Une requête par employeur, à cadence lente.

Réponse : None si l'employeur n'a pas de site chez cet ATS, sinon un Tableau (souvent non
vide ; vide si l'ATS ne distingue pas « inconnu » de « aucun poste »).
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import quote, urlparse

import httpx

from app.texte import html_vers_texte

USER_AGENT = "EMploi_App/0.1 (outil personnel de recherche d'emploi)"
PAUSE_PAR_HOTE = 0.4  # secondes entre deux requêtes vers le même hébergeur
PAUSES_PARTICULIERES = {"api.adzuna.com": 2.6}  # offre gratuite : 25 requêtes par minute


@dataclass(frozen=True)
class Poste:
    titre: str
    lieu: str
    url: str
    publie_le: date | None
    texte: str


@dataclass
class Tableau:
    ats: str
    slug: str
    postes: list[Poste]
    nom_employeur: str | None = None  # quand l'API le donne (Greenhouse, SmartRecruiters)
    pays: set[str] = field(default_factory=set)
    complet: bool = True  # False si l'API n'a pas tout renvoyé : une absence ne prouve rien


class _Cadence:
    """Une requête à la fois par hébergeur, espacées d'au moins PAUSE_PAR_HOTE."""

    def __init__(self) -> None:
        self._verrous: dict[str, threading.Lock] = {}
        self._derniere: dict[str, float] = {}
        self._global = threading.Lock()

    def attendre(self, url: str) -> threading.Lock:
        hote = urlparse(url).hostname or ""
        with self._global:
            verrou = self._verrous.setdefault(hote, threading.Lock())
        verrou.acquire()
        pause = PAUSES_PARTICULIERES.get(hote, PAUSE_PAR_HOTE)
        attente = self._derniere.get(hote, 0) + pause - time.monotonic()
        if attente > 0:
            time.sleep(attente)
        return verrou

    def fin(self, url: str, verrou: threading.Lock) -> None:
        self._derniere[urlparse(url).hostname or ""] = time.monotonic()
        verrou.release()


_cadence = _Cadence()
_client = httpx.Client(
    timeout=20,
    follow_redirects=True,
    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
)


def get_json(url: str, params: dict | None = None):
    """JSON de la réponse, ou None si 404 / 400 / réponse illisible. Reprend sur 429 et 5xx."""
    for essai in range(4):
        verrou = _cadence.attendre(url)
        try:
            r = _client.get(url, params=params)
        except httpx.HTTPError:
            r = None
        finally:
            _cadence.fin(url, verrou)
        if r is not None and r.status_code in (400, 401, 403, 404, 410):
            return None
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        time.sleep(2 ** (essai + 1))  # 429, 5xx ou coupure : on réessaie plus tard
    return None


def _date_iso(valeur) -> date | None:
    if not valeur:
        return None
    if isinstance(valeur, int | float):  # Lever : millisecondes
        return datetime.fromtimestamp(valeur / 1000, UTC).date()
    try:
        return datetime.fromisoformat(str(valeur)).date()
    except ValueError:
        return None


def _pays(lieux: list[str]) -> set[str]:
    trouves = set()
    for lieu in lieux:
        bas = lieu.lower()
        if any(
            m in bas for m in ("france", "paris", ", fr", "île-de-france", "ile-de-france", "lyon")
        ):
            trouves.add("FR")
    return trouves


# --- Greenhouse -----------------------------------------------------------------------------
def greenhouse(slug: str) -> Tableau | None:
    j = get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs", {"content": "true"}
    )
    if not isinstance(j, dict) or "jobs" not in j:
        return None
    postes = [
        Poste(
            titre=p.get("title", ""),
            lieu=(p.get("location") or {}).get("name", ""),
            url=p.get("absolute_url", ""),
            publie_le=_date_iso(p.get("first_published") or p.get("updated_at")),
            texte=html_vers_texte(p.get("content") or ""),
        )
        for p in j["jobs"]
    ]
    nom = next((p.get("company_name") for p in j["jobs"] if p.get("company_name")), None)
    return Tableau("greenhouse", slug, postes, nom, _pays([p.lieu for p in postes]))


# --- Lever (instances US et UE) ---------------------------------------------------------------
def _lever(hote: str, nom_ats: str, slug: str) -> Tableau | None:
    j = get_json(f"https://{hote}/v0/postings/{quote(slug)}", {"mode": "json"})
    if not isinstance(j, list):
        return None
    postes = []
    for p in j:
        cat = p.get("categories") or {}
        listes = "\n\n".join(
            f"{x.get('text', '')}\n{html_vers_texte(x.get('content', ''))}"
            for x in p.get("lists") or []
        )
        texte = "\n\n".join(
            t for t in (p.get("descriptionPlain"), listes, p.get("additionalPlain")) if t
        )
        postes.append(
            Poste(
                p.get("text", ""),
                cat.get("location", ""),
                p.get("hostedUrl", ""),
                _date_iso(p.get("createdAt")),
                texte.strip(),
            )
        )
    pays = {p.get("country") for p in j if p.get("country")} | _pays([p.lieu for p in postes])
    return Tableau(nom_ats, slug, postes, None, pays)


def lever(slug: str) -> Tableau | None:
    return _lever("api.lever.co", "lever", slug)


def lever_eu(slug: str) -> Tableau | None:
    return _lever("api.eu.lever.co", "lever_eu", slug)


# --- SmartRecruiters --------------------------------------------------------------------------
def _postes_sr(resumes: list[dict], slug: str) -> list[Poste]:
    postes = []
    for p in resumes:
        lieu = p.get("location") or {}
        texte_lieu = ", ".join(x for x in (lieu.get("city"), lieu.get("country")) if x)
        postes.append(
            Poste(
                p.get("name", ""),
                texte_lieu,
                f"https://jobs.smartrecruiters.com/{slug}/{p.get('id')}",
                _date_iso(p.get("releasedDate")),
                "",  # texte chargé à la demande (fiche_smartrecruiters)
            )
        )
    return postes


def smartrecruiters(slug: str) -> Tableau | None:
    """L'API répond 200 avec une liste vide pour un employeur inconnu : vide = pas trouvé.

    Lecture limitée à 500 postes ; au-delà le tableau est marqué incomplet et la recherche
    se fait par intitulé (smartrecruiters_recherche).
    """
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings"
    resumes, offset, total = [], 0, 0
    while offset < 500:
        j = get_json(base, {"limit": 100, "offset": offset})
        if not isinstance(j, dict):
            return None
        resumes += j.get("content", [])
        total = j.get("totalFound", 0)
        offset += 100
        if offset >= total:
            break
    if not resumes:
        return None
    nom = (resumes[0].get("company") or {}).get("name")
    pays = {((p.get("location") or {}).get("country") or "").upper() for p in resumes} - {""}
    return Tableau(
        "smartrecruiters", slug, _postes_sr(resumes, slug), nom, pays, complet=len(resumes) >= total
    )


def smartrecruiters_recherche(slug: str, requete: str) -> list[Poste]:
    """Postes d'un employeur SmartRecruiters dont l'intitulé correspond à la requête."""
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings"
    j = get_json(base, {"limit": 100, "q": requete})
    return _postes_sr(j.get("content", []), slug) if isinstance(j, dict) else []


def fiche_smartrecruiters(slug: str, url_poste: str) -> tuple[str, str]:
    """Texte complet et lien public d'un poste SmartRecruiters (une requête de plus)."""
    pid = url_poste.rstrip("/").rsplit("/", 1)[-1]
    j = get_json(f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings/{pid}")
    if not isinstance(j, dict):
        return "", url_poste
    sections = (j.get("jobAd") or {}).get("sections") or {}
    morceaux = [
        f"{s.get('title', '')}\n{html_vers_texte(s.get('text', ''))}"
        for s in sections.values()
        if isinstance(s, dict) and s.get("text")
    ]
    return "\n\n".join(morceaux).strip(), j.get("postingUrl") or url_poste


# --- Ashby ------------------------------------------------------------------------------------
def ashby(slug: str) -> Tableau | None:
    j = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}")
    if not isinstance(j, dict) or "jobs" not in j:
        return None
    postes = [
        Poste(
            p.get("title", ""),
            p.get("location", ""),
            p.get("jobUrl", ""),
            _date_iso(p.get("publishedAt")),
            p.get("descriptionPlain") or html_vers_texte(p.get("descriptionHtml") or ""),
        )
        for p in j["jobs"]
        if p.get("isListed", True)
    ]
    return Tableau("ashby", slug, postes, None, _pays([p.lieu for p in postes]))


# --- Recruitee --------------------------------------------------------------------------------
def recruitee(slug: str) -> Tableau | None:
    j = get_json(f"https://{quote(slug)}.recruitee.com/api/offers/")
    if not isinstance(j, dict) or "offers" not in j:
        return None
    postes = [
        Poste(
            p.get("title", ""),
            ", ".join(x for x in (p.get("city"), p.get("country")) if x),
            p.get("careers_url", ""),
            _date_iso(p.get("published_at")),
            html_vers_texte((p.get("description") or "") + (p.get("requirements") or "")),
        )
        for p in j["offers"]
    ]
    pays = {(p.get("country_code") or "").upper() for p in j["offers"]} - {""}
    nom = next((p.get("company_name") for p in j["offers"] if p.get("company_name")), None)
    return Tableau("recruitee", slug, postes, nom, pays | _pays([p.lieu for p in postes]))


CONNECTEURS: dict[str, Callable[[str], Tableau | None]] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "lever_eu": lever_eu,
    "smartrecruiters": smartrecruiters,
    "ashby": ashby,
    "recruitee": recruitee,
}
