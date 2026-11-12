"""Un parseur par plateforme : mail d'alerte -> liste d'offres (extrait seulement)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage


@dataclass(frozen=True)
class OffreAlerte:
    """Ce qu'un mail d'alerte dit d'une offre. La fiche complète est cherchée à l'étape 2."""

    source: str
    url_cle: str  # URL stable de l'offre sans paramètres de suivi (ou clé indeed:sponsorise:…)
    intitule: str
    entreprise: str
    lieu: str
    details: tuple[str, ...] = field(default=())  # "Cette entreprise recrute activement"...
    publiee_le: date | None = None  # quand l'alerte donne l'ancienneté (« il y a 3 jours »)

    @property
    def extrait(self) -> str:
        return "\n".join((self.intitule, self.entreprise, self.lieu, *self.details))


Parseur = Callable[[EmailMessage], list[OffreAlerte]]


def parseur(source: str) -> Parseur | None:
    from app.collecte.parseurs import indeed, linkedin, wttj

    return {"linkedin": linkedin.extraire, "indeed": indeed.extraire, "wttj": wttj.extraire}.get(
        source
    )
