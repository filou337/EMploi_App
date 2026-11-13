"""Coordonnées du candidat : extraites du CV, modifiables dans l'appli, jamais envoyées au LLM.

Elles servent à deux choses : imprimer l'en-tête des CV et LM générés (sur le PC), et masquer
de façon fiable ces mêmes valeurs dans tout texte envoyé à Gemini.
"""

import re

import psycopg

from app import criteres
from app.ats.anonymisation import masquer as masquer_generique
from app.ats.anonymisation import nom_probable

CHAMPS = (
    "nom",
    "titre",
    "email",
    "telephone",
    "ville",
    "linkedin",
    "portfolio",
    "permis",
    "mobilite",
)


def extraire(texte: str) -> dict:
    """Coordonnées repérées dans le texte du CV (à vérifier par l'utilisateur)."""
    t = texte or ""
    # Les liens coupés en fin de ligne par l'extraction PDF sont recollés (« …/in/camill\ne-exemple… »).
    recolle = re.sub(r"(linkedin\.com/in/[\w-]*)\n([\w-]+/?)", r"\1\2", t)
    trouve = lambda motif, source=t: (
        m.group(0).strip() if (m := re.search(motif, source, re.IGNORECASE)) else ""
    )
    nom = nom_probable(t) or trouve(r"^[A-ZÉ][a-zéèëï-]+ [A-ZÉ]{2,}(?: [A-ZÉ]{2,})?$", t)
    if not nom:
        m = re.search(
            r"^([A-ZÉ][a-zéèëï-]+(?: [A-ZÉ][\w'-]+)? [A-ZÉ]{2,}[A-ZÉ-]*)\s*$", t, re.MULTILINE
        )
        nom = m.group(1) if m else ""
    ville = trouve(r"[A-ZÉ][\w' -]+ \(\d{5}\)") or trouve(r"\d{5}\s+[A-ZÉ][\w' -]+")
    return {
        "nom": nom,
        "titre": "",
        "email": trouve(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        "telephone": trouve(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}"),
        "ville": ville,
        "linkedin": trouve(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+/?", recolle),
        "portfolio": trouve(r"(?:https?://)?[\w-]+\.github\.io/?[\w/-]*") or "",
        "permis": "Permis B" if re.search(r"permis b", t, re.IGNORECASE) else "",
        "mobilite": trouve(r"[Tt][ée]l[ée]travail ou pr[ée]sentiel") or "",
    }


def lire(conn: psycopg.Connection, texte_cv: str | None = None) -> dict:
    """Coordonnées enregistrées ; à défaut, extraites du CV puis enregistrées."""
    ident = criteres.lire(conn).get("identite") or {}
    if not ident.get("nom") and texte_cv:
        ident = {**extraire(texte_cv), **{k: v for k, v in ident.items() if v}}
        criteres.ecrire(conn, "identite", ident)
    lu = {c: ident.get(c, "") for c in CHAMPS}
    # Valeurs à masquer en plus (ancienne adresse encore présente dans le CV déposé…).
    lu["masques"] = [str(m) for m in ident.get("masques") or [] if str(m).strip()]
    return lu


def masquer(texte: str, ident: dict) -> str:
    """Retire du texte toutes les coordonnées connues, puis applique le masquage générique."""
    t = texte or ""
    remplacements = {
        "email": "[EMAIL]",
        "telephone": "[TÉLÉPHONE]",
        "linkedin": "[LIEN]",
        "portfolio": "[LIEN]",
        "ville": "[VILLE]",
    }
    for champ, jeton in remplacements.items():
        valeur = (ident.get(champ) or "").strip()
        if len(valeur) >= 4:
            t = re.sub(re.escape(valeur), jeton, t, flags=re.IGNORECASE)
    for valeur in ident.get("masques") or []:
        valeur = str(valeur).strip()
        if len(valeur) >= 4:
            t = re.sub(rf"\b{re.escape(valeur)}\b", "[VILLE]", t, flags=re.IGNORECASE)
    # Identifiant du profil LinkedIn, y compris ses morceaux quand l'extraction PDF a coupé le lien.
    slug = re.search(r"linkedin\.com/in/([\w-]+)", ident.get("linkedin") or "")
    if slug:
        for morceau in sorted({slug.group(1), *slug.group(1).split("-")}, key=len, reverse=True):
            if len(morceau) >= 5:
                t = re.sub(re.escape(morceau), "[LIEN]", t, flags=re.IGNORECASE)
    nom = (ident.get("nom") or "").strip()
    if nom:
        for partie in sorted({nom, *nom.split()}, key=len, reverse=True):
            if len(partie) >= 3:
                t = re.sub(rf"\b{re.escape(partie)}\b", "[NOM]", t, flags=re.IGNORECASE)
        t = re.sub(r"\[NOM\](?:\s+\[NOM\])+", "[NOM]", t)
    return masquer_generique(t, nom or None)
