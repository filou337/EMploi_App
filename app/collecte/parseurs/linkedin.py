"""Alertes emploi LinkedIn (jobalerts-noreply@linkedin.com).

Le texte brut du mail liste des fiches de 3 lignes (intitulé, entreprise, lieu), parfois
suivies de mentions ("1 relation"), puis "Voir l'offre d'emploi : <lien /jobs/view/ID>".
On part de chaque lien d'offre et on remonte jusqu'à la ligne de rupture précédente.
Le lien est réduit à https://www.linkedin.com/jobs/view/ID/ : la même offre revient dans
plusieurs alertes avec des paramètres de suivi différents.
"""

import re
from email.message import EmailMessage

from app.collecte.normalisation import nettoyer
from app.collecte.parseurs import OffreAlerte

_LIEN_OFFRE = re.compile(r"^Voir l.offre d.emploi\s*:\s*\S*?/jobs/view/(\d+)", re.IGNORECASE)
# Lignes qui ne font pas partie d'une fiche : liens, séparateurs, titres de section.
_RUPTURE = re.compile(
    r"https?://|^-{5,}|^Offres d.emploi\s*:|^Nouvelles offres d.emploi|^De nouvelles offres"
    r"|^Votre alerte Emploi|^Voir toutes les offres|^Modifier l.alerte",
    re.IGNORECASE,
)


def _texte(message: EmailMessage) -> str:
    corps = message.get_body(preferencelist=("plain",))
    return corps.get_content() if corps is not None else ""


def extraire(message: EmailMessage) -> list[OffreAlerte]:
    """Offres du mail. Liste vide pour un mail sans offre (ex. rappel de désactivation).

    Lève ValueError si le mail contient des liens d'offre mais qu'aucune fiche n'est lisible :
    signe que LinkedIn a changé son format.
    """
    lignes = [nettoyer(lg) for lg in _texte(message).splitlines()]
    lignes = [lg for lg in lignes if lg]
    offres: list[OffreAlerte] = []
    vus: set[str] = set()
    liens = 0
    for i, ligne in enumerate(lignes):
        m = _LIEN_OFFRE.match(ligne)
        if not m:
            continue
        liens += 1
        fiche: list[str] = []
        j = i - 1
        while j >= 0 and not _RUPTURE.search(lignes[j]):
            fiche.insert(0, lignes[j])
            j -= 1
        if len(fiche) < 2:
            continue  # fiche illisible : on ne devine pas
        url = f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
        if url in vus:
            continue
        vus.add(url)
        offres.append(
            OffreAlerte(
                source="linkedin",
                url_cle=url,
                intitule=fiche[0],
                entreprise=fiche[1],
                lieu=fiche[2] if len(fiche) > 2 else "",
                details=tuple(fiche[3:]),
            )
        )
    if liens and not offres:
        raise ValueError(f"{liens} liens d'offre mais aucune fiche lisible : format changé ?")
    return offres
