"""Adzuna (API gratuite, clé sur developer.adzuna.com) : l'offre est-elle encore en ligne ?

Sert de second essai pour les employeurs sans ATS public. Adzuna agrège les offres et
donne leur date de création ; sa description est tronquée, donc la fiche reste « incomplète »
(score plafonné à 84) mais la validité et la date de publication deviennent sûres.
"""

from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from app.collecte.normalisation import norm_intitule, norm_ville
from app.config import adzuna
from app.entreprises import compact
from app.fiches.ats import Poste, _date_iso, get_json

URL = "https://api.adzuna.com/v1/api/jobs/fr/search/1"


@dataclass(frozen=True)
class Annonce:
    poste: Poste
    entreprise: str


def disponible() -> bool:
    return adzuna() is not None


def chercher(intitule: str, ville: str) -> list[Annonce]:
    """Annonces Adzuna de moins de 30 jours proches de cet intitulé (une requête)."""
    cles = adzuna()
    if cles is None:
        return []
    params = {
        "app_id": cles[0],
        "app_key": cles[1],
        "what": " ".join(norm_intitule(intitule).split()[:5]),
        "results_per_page": 50,
        "max_days_old": 30,
        "content-type": "application/json",
    }
    if norm_ville(ville or ""):
        params["where"] = ville.split(",")[0].split("(")[0].strip()
    j = get_json(URL, params)
    if not isinstance(j, dict):
        return []
    return [
        Annonce(
            Poste(
                r.get("title", ""),
                (r.get("location") or {}).get("display_name", ""),
                r.get("redirect_url", ""),
                _date_iso(r.get("created")),
                r.get("description", ""),
            ),
            (r.get("company") or {}).get("display_name", ""),
        )
        for r in j.get("results", [])
    ]


def meme_employeur(a: str, b: str) -> bool:
    x, y = compact(a), compact(b)
    return bool(x and y) and (
        x == y or fuzz.ratio(x, y) >= 85 or (min(len(x), len(y)) >= 4 and (x in y or y in x))
    )


def publiee_le(annonce: Annonce) -> date | None:
    return annonce.poste.publie_le
