"""Module C — compatibilité ATS sur 100, calculée localement (sans LLM).

On juge ce qu'un logiciel de recrutement peut tirer du fichier : texte extractible,
sections standard, coordonnées lisibles, mise en page simple, dates exploitables, longueur.
Chaque critère donne sa note, son maximum et l'explication ; le total est stable d'une
analyse à l'autre (même fichier, même score).
"""

import re
from dataclasses import asdict, dataclass

_MOIS = r"(?:janv?|f[ée]vr?|mars|avr|mai|juin|juil|ao[uû]t?|sept?|oct|nov|d[ée]c|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-zéû]*\.?"
_DATE = re.compile(
    rf"\b(?:(?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{{2}}|{_MOIS}\s+(?:19|20)\d{{2}}|(?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)
_PERIODE = re.compile(
    rf"((?:{_MOIS}\s+)?(?:19|20)\d{{2}})\s*(?:-|–|—|à|au|to)\s*((?:{_MOIS}\s+)?(?:19|20)\d{{2}}|aujourd'hui|présent|present|actuel|en cours|now)",
    re.IGNORECASE,
)


@dataclass
class Critere:
    nom: str
    note: float
    max: float
    detail: str


def _annee(texte: str) -> int | None:
    m = re.search(r"(?:19|20)\d{2}", texte)
    return int(m.group()) if m else None


def score(format_: str, texte: str, diag: dict) -> dict:
    sections = set(diag.get("sections", []))
    coord = diag.get("coordonnees", {})
    alertes = diag.get("alertes", [])
    mots, pages = diag.get("mots", 0), diag.get("pages", 0)
    criteres: list[Critere] = []

    scan = any("scanné" in a or "image" in a for a in alertes)
    criteres.append(
        Critere(
            "Texte extractible",
            0 if scan else 20,
            20,
            "Le texte est lisible par un ATS."
            if not scan
            else "Peu ou pas de texte lisible : PDF scanné ou image.",
        )
    )

    requises = ["experience", "formation", "competences"]
    pts = (
        sum(6 for s in requises if s in sections)
        + (2 if "langues" in sections else 0)
        + (2 if "profil" in sections else 0)
    )
    manquantes = [s for s in requises if s not in sections]
    criteres.append(
        Critere(
            "Sections standard",
            pts,
            22,
            "Sections reconnues : "
            + (", ".join(sorted(sections)) or "aucune")
            + (f". Manquantes : {', '.join(manquantes)}." if manquantes else "."),
        )
    )

    pts = (
        (6 if coord.get("email") else 0)
        + (5 if coord.get("telephone") else 0)
        + (2 if coord.get("linkedin") else 0)
    )
    absentes = [
        n
        for n, k in (("mail", "email"), ("téléphone", "telephone"), ("LinkedIn", "linkedin"))
        if not coord.get(k)
    ]
    criteres.append(
        Critere(
            "Coordonnées lisibles",
            pts,
            13,
            "Mail, téléphone et LinkedIn présents dans le texte."
            if not absentes
            else f"Introuvable(s) dans le texte lisible : {', '.join(absentes)}.",
        )
    )

    if format_ == "docx" or pages == 0:
        ok_pages = True
    else:
        ok_pages = 1 <= pages <= 2
    ok_mots = 300 <= mots <= 900
    criteres.append(
        Critere(
            "Longueur",
            (5 if ok_pages else 0) + (5 if ok_mots else 2 if 200 <= mots <= 1200 else 0),
            10,
            f"{pages or '?'} page(s), {mots} mots (repère : 1-2 pages, 300 à 900 mots).",
        )
    )

    problemes = [a for a in alertes if re.search(r"tableau|en-tête|icônes|Canva|images", a)]
    criteres.append(
        Critere(
            "Mise en page simple",
            max(0, 15 - 5 * len(problemes)),
            15,
            "Aucun élément gênant (tableau, en-tête, icônes, colonnes)."
            if not problemes
            else " ".join(problemes),
        )
    )

    dates = _DATE.findall(texte)
    periodes = _PERIODE.findall(texte)
    incoherentes = [
        f"{a} – {b}" for a, b in periodes if _annee(a) and _annee(b) and _annee(b) < _annee(a)
    ]
    pts = (6 if len(periodes) >= 2 else 3 if dates else 0) + (4 if not incoherentes else 0)
    criteres.append(
        Critere(
            "Dates exploitables",
            pts,
            10,
            f"{len(periodes)} période(s) datée(s) repérée(s)."
            + (f" Incohérentes : {', '.join(incoherentes)}." if incoherentes else "")
            + ("" if periodes else " Écris tes dates sous la forme « sept. 2025 – mars 2026 »."),
        )
    )

    criteres.append(
        Critere(
            "Format du fichier",
            5 if format_ in ("pdf", "docx") and not scan else 0,
            5,
            "DOCX ou PDF texte : les deux formats les mieux lus." if not scan else "PDF image.",
        )
    )

    titres = [lg.strip().lower() for lg in texte.splitlines() if 0 < len(lg.strip()) <= 30]
    doublons = len(titres) - len(set(titres))
    criteres.append(
        Critere(
            "Ordre de lecture",
            5 if doublons <= 3 else 2,
            5,
            "Lecture linéaire."
            if doublons <= 3
            else "Lignes courtes répétées : colonnes ou blocs lus dans le désordre ?",
        )
    )

    total = round(sum(c.note for c in criteres), 1)
    return {"score": total, "criteres": [asdict(c) for c in criteres]}
