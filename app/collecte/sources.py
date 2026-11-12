"""Plateformes d'alerte reconnues et rattachement d'un expéditeur à sa source."""

from email.utils import parseaddr

# Domaines expéditeurs par source (liste blanche). Les sous-domaines sont acceptés.
DOMAINES_PAR_SOURCE: dict[str, tuple[str, ...]] = {
    "wttj": ("welcometothejungle.com", "welcometothejungle.co"),
    "france_travail": ("francetravail.fr", "pole-emploi.fr"),
    "linkedin": ("linkedin.com",),
    "apec": ("apec.fr",),
    "jobrapido": ("jobrapido.com",),
    "indeed": ("indeed.com",),
}


# Adresses qui envoient vraiment des offres, quand on les connaît. Pour ces sources,
# les autres expéditeurs du domaine (newsletters, invitations, marketing) sont ignorés.
# Une source absente d'ici est acceptée sur son seul domaine, en attendant d'avoir vu ses alertes.
ADRESSES_ALERTE: dict[str, tuple[str, ...]] = {
    "linkedin": ("jobalerts-noreply@linkedin.com",),
    "indeed": ("donotreply@jobalert.indeed.com", "donotreply@match.indeed.com"),
}


def domaine(expediteur: str) -> str:
    """'WTTJ <alertes@mail.welcometothejungle.com>' -> 'mail.welcometothejungle.com'."""
    adresse = parseaddr(expediteur)[1].lower()
    return adresse.rpartition("@")[2]


def source_depuis_expediteur(expediteur: str) -> str | None:
    """Renvoie la source ('wttj', 'linkedin'...) ou None si l'expéditeur n'est pas reconnu."""
    dom = domaine(expediteur)
    if not dom:
        return None
    for source, domaines in DOMAINES_PAR_SOURCE.items():
        if any(dom == d or dom.endswith("." + d) for d in domaines):
            return source
    return None


def est_alerte(expediteur: str) -> bool:
    """Vrai si le mail vient d'un expéditeur d'offres (et pas d'une newsletter de la plateforme)."""
    source = source_depuis_expediteur(expediteur)
    if source is None:
        return False
    adresses = ADRESSES_ALERTE.get(source)
    return adresses is None or parseaddr(expediteur)[1].lower() in adresses


def filtres_alertes() -> list[str]:
    """Adresses (ou domaines, à défaut) des expéditeurs d'alertes, pour une requête Gmail."""
    return [
        a
        for source, domaines in DOMAINES_PAR_SOURCE.items()
        for a in ADRESSES_ALERTE.get(source, domaines)
    ]


def requete_gmail(jours: int) -> str:
    """Requête de recherche Gmail : alertes des plateformes reconnues sur les N derniers jours."""
    return f"newer_than:{jours}d from:({' OR '.join(filtres_alertes())})"
