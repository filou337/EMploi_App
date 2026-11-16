"""Distance entre le lieu d'une offre et ta ville, pour la zone « ville + rayon ».

Géocodage de la commune par l'API publique de l'IGN (Géoplateforme, gratuite, sans clé).
Seul le nom de la commune part vers l'API, jamais une adresse. Les résultats sont gardés en
mémoire et dans la table `lieux_geo` (une commune n'est géocodée qu'une fois).
"""

import math
import re

import httpx
import psycopg

from app.collecte.normalisation import sans_accents

API = "https://data.geopf.fr/geocodage/search"
SCORE_MIN = 0.6

# Lieux trop vagues pour une distance (région, pays, département) : « lieu à vérifier ».
_VAGUES = {
    "france", "ile de france", "idf", "region parisienne", "paris region", "paris area",
    "grand paris", "europe", "emea", "hauts de seine", "seine saint denis", "val de marne",
    "yvelines", "essonne", "val d oise", "seine et marne", "plusieurs lieux", "multiple locations",
}  # fmt: skip
_ALIAS = {"la defense": "Puteaux", "paris la defense": "Puteaux"}
_TELETRAVAIL = re.compile(
    r"\b(full remote|100 ?% remote|remote|teletravail( total| complet)?|a distance|home office)\b"
)
_HYBRIDE = re.compile(r"\b(hybride?|hybrid)\b")

_cache: dict[str, tuple[float, float] | None] = {}


def bas(texte: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accents(texte or "").lower()).split())


def commune(lieu: str) -> tuple[str, bool]:
    """(commune à géocoder, télétravail mentionné). Commune vide : rien de géocodable."""
    texte = bas(lieu)
    remote = bool(_TELETRAVAIL.search(texte))
    premier = re.split(r"[,;/|•]| - | ou ", lieu or "")[0]
    premier = re.sub(r"[()]", " ", premier)
    nom = _HYBRIDE.sub(" ", _TELETRAVAIL.sub(" ", bas(premier)))
    nom = re.sub(r"^ville de ", "", " ".join(re.sub(r"\bcedex\b.*", "", nom).split()))
    if not nom or nom in _VAGUES or re.fullmatch(r"\d{1,3}", nom):
        return "", remote
    return _ALIAS.get(nom, nom), remote


def _choisir(cle: str, features: list) -> tuple[float, float] | None:
    """Premier résultat seulement s'il est sûr : bon score ET son nom figure dans la recherche
    (« France » ne doit pas donner Fort-de-France)."""
    if not features:
        return None
    p, (lon, lat) = features[0]["properties"], features[0]["geometry"]["coordinates"]
    nom = re.sub(r" \d+(e|er) arrondissement$", "", bas(p.get("name") or ""))
    if float(p.get("score") or 0) < SCORE_MIN or not nom or f" {nom} " not in f" {cle} ":
        return None
    return float(lat), float(lon)


def coordonnees(nom: str, conn: psycopg.Connection | None = None) -> tuple[float, float] | None:
    """(lat, lon) de la commune, ou None si introuvable ou API injoignable (pas mis en cache
    dans ce cas : on réessaiera au prochain passage)."""
    cle = bas(nom)
    if not cle:
        return None
    if cle in _cache:
        return _cache[cle]
    if conn is not None:
        row = conn.execute("SELECT lat, lon FROM lieux_geo WHERE cle = %s", (cle,)).fetchone()
        if row is not None:
            _cache[cle] = (row["lat"], row["lon"]) if row["lat"] is not None else None
            return _cache[cle]
    try:
        r = httpx.get(API, params={"q": nom, "type": "municipality", "limit": 1}, timeout=10)
        r.raise_for_status()
        point = _choisir(cle, r.json()["features"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None
    _cache[cle] = point
    if conn is not None:
        conn.execute(
            "INSERT INTO lieux_geo (cle, lat, lon) VALUES (%s, %s, %s) ON CONFLICT (cle) DO NOTHING",
            (cle, point and point[0], point and point[1]),
        )
    return point


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance à vol d'oiseau (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))
