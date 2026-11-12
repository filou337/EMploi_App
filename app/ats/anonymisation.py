"""Masquage des coordonnées d'un CV avant tout envoi à un LLM.

Remplacés : adresses mail, téléphones, liens (LinkedIn, GitHub, portfolio), adresse postale,
date de naissance, et le nom (repéré en tête de CV ou fourni). L'analyse n'en a pas besoin :
la présence des coordonnées est vérifiée localement (module de compatibilité).
"""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TEL = re.compile(r"(?<!\d)(?:\+\d{2,3}[\s.-]?|0)\d(?:[\s.-]?\d{2}){4}(?!\d)")
_URL = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|[\w-]+\.(?:dev|me|io|fr|com))/\S*",
    re.IGNORECASE,
)
_ADRESSE = re.compile(
    r"\b\d{1,4}(?:\s?(?:bis|ter))?,?\s+(?:rue|avenue|av\.|boulevard|bd|allée|impasse|chemin|place|quai|route)\b[^\n,]{0,60}"
    r"(?:,?\s*\d{5}\s+[A-ZÉ][\w' -]+)?",
    re.IGNORECASE,
)
_CP_VILLE = re.compile(r"\b\d{5}\s+[A-ZÉ][\w'-]+(?:[ -][A-ZÉ][\w'-]+)*")
_NAISSANCE = re.compile(
    r"(?:n[ée]e? le|date de naissance\s*:?)\s*\d{1,2}[/. -]\d{1,2}[/. -]\d{2,4}", re.IGNORECASE
)
_LIGNE_NOM = re.compile(r"^\s*([A-ZÉÈ][\w'éèëï-]+(?:\s+[A-ZÉÈ][\w'éèëï-]+){1,3})\s*$")


_PRENOM_NOM = re.compile(
    r"^\s*([A-ZÉÈ][a-zéèëïç]+(?:[- ][A-ZÉÈ][a-zéèëïç]+)?)\s+([A-ZÉÈ][A-ZÉÈ'-]{2,}(?:\s+[A-ZÉÈ][A-ZÉÈ'-]{2,})?)\s*$"
)
_PAS_UN_NOM = re.compile(
    r"(?i)curriculum|\bcv\b|data|engineer|analyst|ing[ée]nieur|consultant|power|azure|google|"
    r"microsoft|madame|monsieur|objet|stage|master|licence|bachelor|universit|[ée]cole"
)


def nom_probable(texte: str) -> str | None:
    """Nom du candidat : « Prénom NOM » (nom de famille en capitales, 3 lettres ou plus) sur une
    ligne seule parmi les 60 premières, à défaut une ligne « Prénom Nom » parmi les 3 premières.
    Les termes techniques (« Power BI ») et mots courants des CV sont exclus."""
    lignes = [lg for lg in (texte or "").splitlines() if lg.strip()]
    for ligne in lignes[:60]:
        m = _PRENOM_NOM.match(ligne)
        if m and not _PAS_UN_NOM.search(ligne):
            return f"{m.group(1)} {m.group(2)}"
    for ligne in lignes[:3]:
        m = _LIGNE_NOM.match(ligne)
        if m and not _PAS_UN_NOM.search(ligne):
            return m.group(1)
    return None


class FuiteDonnees(ValueError):
    """Une coordonnée connue est encore présente dans un texte destiné au LLM : envoi refusé."""


def verifier_masque(texte: str, sensibles: list[str]) -> None:
    """Contrôle bloquant avant tout envoi au LLM : aucune valeur sensible ne doit subsister."""
    bas = (texte or "").lower()
    for valeur in sensibles:
        v = (valeur or "").strip().lower()
        morceaux = [v] + [m for m in v.split() if len(m) >= 4]
        if v and any(m in bas for m in morceaux):
            raise FuiteDonnees(
                "Coordonnée encore présente dans le texte à envoyer : envoi au LLM annulé."
            )


def masquer(texte: str, nom: str | None = None) -> str:
    nom = nom or nom_probable(texte)
    texte = _EMAIL.sub("[EMAIL]", texte)
    texte = _URL.sub("[LIEN]", texte)
    texte = _NAISSANCE.sub("[DATE DE NAISSANCE]", texte)
    texte = _ADRESSE.sub("[ADRESSE]", texte)
    texte = _TEL.sub("[TÉLÉPHONE]", texte)
    if nom:
        for partie in sorted({nom, *nom.split()}, key=len, reverse=True):
            if len(partie) >= 3:
                texte = re.sub(rf"\b{re.escape(partie)}\b", "[NOM]", texte, flags=re.IGNORECASE)
        texte = re.sub(r"\[NOM\](?:\s+\[NOM\])+", "[NOM]", texte)
    return texte
