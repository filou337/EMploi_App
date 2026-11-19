"""Listes d'entreprises partenaires : un Excel (Entreprise, Secteur, Site carrière) importé
dans la veille, sous le nom de la liste.

Usage :  python -m app.partenaires fichier.xlsx --liste "Nom de la liste"
Le fichier reste sur le PC (non versionné) ; seule la base garde les entreprises et leur liste.
Chaque entreprise est surveillée comme les autres (site carrières), et l'agent Veille cherche
en plus ses offres sur Adzuna : voir app.agents.veille.partenaires_adzuna.
"""

import argparse
import io
import re
from dataclasses import dataclass

import psycopg
from openpyxl import load_workbook

from app.collecte.normalisation import norm_entreprise, sans_accents
from app.entreprises import compact, entreprise_id

# Noms mal orthographiés ou trop vagues dans les listes, ramenés au nom employé par les annonces.
CORRECTIONS = {
    "ernastyoung": "EY",
    "ernstyoung": "EY",
    "capfmcreditagricolt": "Crédit Agricole",
    "freeilliad": "Free",
    "microsoft": "Microsoft",
}
_URL = re.compile(r"https?://[^\s,;]+")


@dataclass(frozen=True)
class Partenaire:
    nom: str
    secteur: str | None
    site: str | None


def _colonne(entetes: list[str], *mots: str) -> int | None:
    return next((i for i, h in enumerate(entetes) if any(m in h for m in mots)), None)


def lire(nom_fichier: str, contenu: bytes) -> list[Partenaire]:
    """Lignes du classeur, sans doublons (même nom compact : on garde le premier site lu)."""
    if not nom_fichier.lower().endswith(".xlsx"):
        raise ValueError("Fichier .xlsx attendu.")
    feuille = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True).active
    lignes = [list(r) for r in feuille.iter_rows(values_only=True)]
    if not lignes:
        return []
    entetes = [sans_accents(str(h or "")).lower().strip() for h in lignes[0]]
    i_nom = _colonne(entetes, "entreprise", "societe", "employeur", "raison sociale")
    if i_nom is None:
        raise ValueError("Colonne « Entreprise » introuvable dans la première ligne.")
    i_secteur = _colonne(entetes, "secteur", "activite")
    i_site = _colonne(entetes, "site", "carriere", "url", "lien")
    vus: dict[str, Partenaire] = {}
    for brut in lignes[1:]:

        def cellule(i: int | None, ligne: list = brut) -> str:
            return str(ligne[i] or "").strip() if i is not None and i < len(ligne) else ""

        nom = " ".join(cellule(i_nom).split())
        if len(nom) < 2:
            continue
        nom = CORRECTIONS.get(compact(nom), nom)
        cle = compact(nom)
        site = next(iter(_URL.findall(cellule(i_site))), None)
        if cle not in vus:
            vus[cle] = Partenaire(nom, cellule(i_secteur).capitalize() or None, site)
    return list(vus.values())


def importer(conn: psycopg.Connection, partenaires: list[Partenaire], liste: str) -> dict:
    """Ajoute chaque entreprise à la veille sous `liste` ; complète son secteur et son site."""
    liste = " ".join(liste.split())[:80]
    if not liste:
        raise ValueError("Donne un nom à la liste.")
    ajoutees = deja = 0
    for p in partenaires:
        eid = entreprise_id(conn, p.nom)
        conn.execute(
            "UPDATE entreprises SET secteur = COALESCE(secteur, %s),"
            " site_carrieres = COALESCE(site_carrieres, %s) WHERE id = %s",
            (p.secteur, p.site, eid),
        )
        nouvelle = conn.execute(
            """
            INSERT INTO veille_entreprises (entreprise_id, origine, liste) VALUES (%s, 'manuelle', %s)
            ON CONFLICT (entreprise_id) DO UPDATE SET liste = EXCLUDED.liste
            RETURNING (xmax = 0) AS inseree
            """,
            (eid, liste),
        ).fetchone()["inseree"]
        if nouvelle:
            ajoutees += 1
        else:
            deja += 1
    return {"liste": liste, "ajoutees": ajoutees, "deja_surveillees": deja}


def meme_entreprise(partenaire: str, employeur: str) -> bool:
    """« Capgemini » = « CAP Gemini » = « Capgemini Invent » ; « Crit » ≠ « Criteo ».

    Mots entiers seulement : le nom le plus court doit ouvrir le plus long.
    """
    a, b = norm_entreprise(partenaire).split(), norm_entreprise(employeur).split()
    if not a or not b:
        return False
    if "".join(a) == "".join(b):
        return True
    court, long_ = sorted((a, b), key=len)
    return long_[: len(court)] == court


def main() -> None:
    from pathlib import Path

    from app.db import connexion

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("fichier", type=Path)
    p.add_argument("--liste", required=True, help="nom affiché dans l'appli")
    args = p.parse_args()
    partenaires = lire(args.fichier.name, args.fichier.read_bytes())
    with connexion() as conn:
        r = importer(conn, partenaires, args.liste)
    print(
        f"{len(partenaires)} entreprise(s) lue(s) : {r['ajoutees']} ajoutée(s) à la veille,"
        f" {r['deja_surveillees']} déjà surveillée(s) — liste « {r['liste']} »."
    )


if __name__ == "__main__":
    main()
