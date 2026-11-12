"""Normalisation des textes d'offre : clés anti-doublon et contrat déduit de l'intitulé."""

import re
import unicodedata

# Formes juridiques et mots génériques retirés du nom d'entreprise ("Groupe SEB" -> "seb").
_MOTS_ENTREPRISE = {
    "sa",
    "sas",
    "sasu",
    "sarl",
    "eurl",
    "se",
    "inc",
    "ltd",
    "llc",
    "gmbh",
    "group",
    "groupe",
    "france",
    "en",
}
# Mentions de genre et de contrat retirées de l'intitulé pour comparer deux annonces.
_BRUIT_INTITULE = re.compile(
    r"\(?\b[hfmx](?:\s*/\s*[hfmx]){1,2}\b\)?|\b(?:cdi|cdd|stage|alternance|freelance)\b",
    re.IGNORECASE,
)
_VILLE_PREFIXE = re.compile(r"^ville de\s+", re.IGNORECASE)


def sans_accents(texte: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _mots(texte: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", sans_accents(texte).lower())


def nettoyer(texte: str) -> str:
    """Espaces insécables et multiples ramenés à un seul espace."""
    return re.sub(r"\s+", " ", texte.replace("\xa0", " ")).strip()


def norm_entreprise(nom: str) -> str:
    """'AXA en France' -> 'axa' ; 'Capgemini Invent' -> 'capgemini invent'."""
    mots = _mots(nom)
    gardes = [m for m in mots if m not in _MOTS_ENTREPRISE]
    return " ".join(gardes or mots)


def norm_intitule(intitule: str) -> str:
    """'Data Analyst (F/H) - CDI' -> 'data analyst'."""
    return " ".join(_mots(_BRUIT_INTITULE.sub(" ", intitule)))


def norm_ville(lieu: str) -> str:
    """'Ville de Paris' -> 'paris' ; 'Saint-Denis (93)' -> 'saint denis'."""
    ville = _VILLE_PREFIXE.sub("", lieu.split(",")[0])
    ville = re.sub(r"\([^)]*\)|\d+", " ", ville)  # « Saint-Denis (93) », « 78280 Guyancourt »
    return " ".join(_mots(ville))


def cle_floue(entreprise: str, intitule: str, lieu: str) -> str:
    return f"{norm_entreprise(entreprise)}|{norm_intitule(intitule)}|{norm_ville(lieu)}"


def contrat_depuis_intitule(intitule: str) -> str | None:
    """Type de contrat quand l'intitulé le dit ; None sinon (à lire dans la fiche)."""
    mots = set(_mots(intitule))
    if mots & {"stage", "stagiaire", "intern", "internship"}:
        return "stage"
    if mots & {"alternance", "alternant", "apprenti", "apprentissage"}:
        return "alternance"
    for contrat in ("cdi", "cdd", "freelance"):
        if contrat in mots:
            return contrat.upper() if contrat != "freelance" else contrat
    return None


_NIVEAUX = {
    "junior": "junior",
    "debutant": "junior",
    "jeune": "junior",
    "senior": "senior",
    "confirme": "senior",
    "experimente": "senior",
    "expert": "senior",
    "lead": "lead",
    "principal": "lead",
    "staff": "lead",
    "manager": "lead",
    "head": "lead",
    "stage": "stage",
    "stagiaire": "stage",
    "intern": "stage",
    "internship": "stage",
    "alternance": "alternance",
    "alternant": "alternance",
    "apprenti": "alternance",
}


def niveau(intitule: str) -> frozenset[str]:
    """Niveaux annoncés dans l'intitulé : 'Consultant Junior' -> {'junior'}."""
    return frozenset(_NIVEAUX[m] for m in _mots(intitule) if m in _NIVEAUX)
