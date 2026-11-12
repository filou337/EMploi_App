"""Sélections d'offres Welcome to the Jungle (marketing-campaigns@welcometothejungle.com).

WTTJ n'envoie pas d'alertes au fil de l'eau mais des « jobs du mois » : le texte brut liste
des fiches « **Entreprise** / Intitulé » ou « **Intitulé** » seul, chacune suivie de
« [ **Voir l'offre**](https://www.welcometothejungle.com/fr/companies/<ent>/jobs/<slug>_<ville>…) ».
Les autres campagnes (newsletter, « entreprises du mois ») n'ont pas ce lien : liste vide.

Quand l'entreprise n'est pas écrite, elle vient du lien ; la ville vient toujours du lien
(segment après le premier « _ »). Le lien est réduit à …/companies/<ent>/jobs/<slug>.
"""

import re
from email.message import EmailMessage

from app.collecte.normalisation import nettoyer
from app.collecte.parseurs import OffreAlerte

_LIEN = re.compile(
    r"\[\s*\**\s*Voir l.offre\s*\**\s*\]\(\s*"
    r"(https?://(?:www\.)?welcometothejungle\.com/(?:fr|en)/companies/([\w-]+)/jobs/([\w-]+))",
    re.IGNORECASE,
)
_GRAS = re.compile(r"^\*\*\s*(.*?)\s*\*\*$")
_BRUIT = re.compile(
    r"^[-=*_\s]*$|^\[|^🔵|^(?:Les (?:jobs|offres) du mois|SÉLECTION DU MOMENT)$", re.IGNORECASE
)


def _texte(message: EmailMessage) -> str:
    corps = message.get_body(preferencelist=("plain",))
    return corps.get_content() if corps is not None else ""


def _humain(slug: str) -> str:
    return " ".join(m.capitalize() if len(m) > 2 else m for m in slug.split("-"))


def ville(slug: str) -> str:
    """'data-engineer-f-h-x_paris' -> 'Paris' ; '…_paris-la-defense_SII_x1' -> 'Paris la Defense'."""
    morceaux = slug.split("_")
    return _humain(morceaux[1]) if len(morceaux) > 1 else ""


def _fiche(segment: str) -> list[str]:
    """Dernières lignes utiles avant un lien (au plus 2 : entreprise puis intitulé)."""
    lignes = []
    for brute in segment.splitlines():
        lg = nettoyer(brute)
        if lg and not _BRUIT.search(lg):
            lignes.append(lg)
    if not lignes:
        return []
    dernier = lignes[-1]
    if _GRAS.match(dernier):  # « **Intitulé** » seul : pas d'entreprise écrite
        return [_GRAS.match(dernier).group(1)]
    if len(lignes) >= 2 and _GRAS.match(lignes[-2]):
        return [_GRAS.match(lignes[-2]).group(1), dernier]
    return [dernier]


def extraire(message: EmailMessage) -> list[OffreAlerte]:
    texte = _texte(message)
    offres: list[OffreAlerte] = []
    vus: set[str] = set()
    debut = liens = 0
    for m in _LIEN.finditer(texte):
        liens += 1
        segment = texte[debut : m.start()]
        # Le segment s'arrête à la fin du lien précédent : « …"voir l'offre") **Capgemini** ».
        segment = segment.split(")", 1)[-1] if debut else segment
        debut = m.end()
        fiche = _fiche(segment)
        _, ent_slug, job_slug = m.groups()
        url = f"https://www.welcometothejungle.com/fr/companies/{ent_slug}/jobs/{job_slug}"
        if not fiche or url in vus:
            continue
        vus.add(url)
        intitule = fiche[-1]
        entreprise = fiche[0] if len(fiche) == 2 else _humain(ent_slug)
        offres.append(
            OffreAlerte(
                source="wttj",
                url_cle=url,
                intitule=intitule,
                entreprise=entreprise,
                lieu=ville(job_slug),
            )
        )
    if liens and not offres:
        raise ValueError(f"{liens} liens d'offre WTTJ mais aucune fiche lisible : format changé ?")
    return offres
