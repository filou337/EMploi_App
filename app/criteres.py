"""Critères de veille et seuils, modifiables dans l'appli (table `parametres`).

Le filtre est gratuit (Python, sans LLM) : seules les offres qui le passent consomment
du quota Gemini au matching.
"""

import re
from copy import deepcopy

import psycopg
from psycopg.types.json import Jsonb

from app.collecte.normalisation import contrat_depuis_intitule, niveau, sans_accents
from app.pertinence import est_data

DEFAUTS: dict = {
    "criteres": {
        # Intitulés recherchés : l'offre doit contenir l'un de ces termes (ou un mot data).
        "postes": [
            "data analyst",
            "analytics engineer",
            "data engineer",
            "analyste de donnees",
            "business intelligence",
            "bi",
            "consultant data",
            "power bi",
        ],
        "contrats_exclus": ["stage", "alternance", "freelance"],
        "niveaux_exclus": ["senior", "lead"],
        # Lieux acceptés (Île-de-France et télétravail). Lieu inconnu : accepté.
        "lieux": [
            "paris",
            "ile de france",
            "ile-de-france",
            "remote",
            "teletravail",
            "75",
            "77",
            "78",
            "91",
            "92",
            "93",
            "94",
            "95",
            "hauts de seine",
            "seine saint denis",
            "val de marne",
            "yvelines",
            "essonne",
            "val d oise",
            "seine et marne",
            "boulogne",
            "nanterre",
            "courbevoie",
            "puteaux",
            "issy",
            "levallois",
            "neuilly",
            "saint denis",
            "montrouge",
            "clichy",
            "velizy",
            "massy",
            "rueil",
            "la defense",
            "saint ouen",
            "ivry",
            "vincennes",
            "cergy",
            "guyancourt",
            "versailles",
        ],
    },
    "seuils": {
        "fabrication": 85,
        "dossiers_par_jour": 10,
        "matchings_par_passage": 15,
        "fabrications_par_jour": 25,
        "ats_friendly_min": 90,
        "relance_jours": 7,  # 1re relance J+7 après la candidature
        "relance2_jours": 7,  # 2de relance 7 jours après la 1re (J+14)
        "sans_suite_jours": 30,  # sans réponse 30 jours après la dernière relance : « sans suite »
        "relancer_spontanees": False,
        "rapport_jours": 3,
        "spontanees_par_jour": 5,
        "envois_par_jour": 10,
        "envois_auto_par_jour": 10,  # candidatures envoyées automatiquement au plus par jour
        "envois_auto_haut_par_jour": 5,  # dont score ≥ « fabrication » (85) ; les suivantes restent pour toi
        "envoi_auto_min": 50,  # score minimal pour une candidature automatique (sous le seuil « fabrication »)
        "candidatures_auto_par_passage": 3,  # dossiers préparés par passage (Gemini et Chrome sont lents)  # mails envoyés depuis Gmail au plus par jour (toujours sur ton clic)  # brouillons de candidature spontanée préparés au plus par jour  # un rapport récapitulatif tous les N jours
    },
    "veille": {"frequence_minutes": 60, "entreprises_par_defaut": 5, "initialisee": False},
    # Envoi en 1 clic depuis Gmail : désactivé tant que tu ne l'actives pas (onglet Spontanées).
    "envoi": {"actif": False, "auto": False},
    # Coordonnées imprimées sur les CV et LM générés (jamais envoyées au LLM).
    "identite": {},
    # Réponses des formulaires de candidature (salaire, disponibilité…) : jamais imprimées
    # sur les CV, seulement proposées dans le kit formulaire.
    "formulaire": {},
}


def lire(conn: psycopg.Connection) -> dict:
    """Paramètres en base, complétés par les valeurs par défaut."""
    valeurs = deepcopy(DEFAUTS)
    for r in conn.execute("SELECT cle, valeur FROM parametres"):
        if r["cle"] in valeurs and isinstance(r["valeur"], dict):
            valeurs[r["cle"]].update(r["valeur"])
    return valeurs


def ecrire(conn: psycopg.Connection, cle: str, valeur: dict) -> None:
    if cle not in DEFAUTS:
        raise ValueError(f"Paramètre inconnu : {cle}")
    conn.execute(
        "INSERT INTO parametres (cle, valeur) VALUES (%s, %s)"
        " ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, maj_le = now()",
        (cle, Jsonb(valeur)),
    )


def _bas(t: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", sans_accents(t or "").lower()) + " "


def refus(intitule: str, lieu: str, criteres: dict) -> str | None:
    """Motif pour lequel l'offre ne correspond pas aux critères, ou None si elle passe."""
    titre = _bas(intitule)
    if not (est_data(intitule) or any(f" {_bas(p).strip()} " in titre for p in criteres["postes"])):
        return "intitulé hors cible"
    contrat = contrat_depuis_intitule(intitule)
    if contrat and contrat.lower() in [c.lower() for c in criteres["contrats_exclus"]]:
        return f"contrat exclu ({contrat})"
    exclus = set(criteres["niveaux_exclus"]) & set(niveau(intitule))
    if exclus:
        return f"niveau exclu ({', '.join(sorted(exclus))})"
    if lieu_accepte(lieu, criteres) is False:
        return f"lieu hors zone ({lieu.strip()})"
    return None


def lieu_accepte(lieu: str | None, criteres: dict) -> bool | None:
    """True si le lieu est dans la zone, False sinon, None s'il est inconnu."""
    if not lieu or not lieu.strip():
        return None
    bas = _bas(lieu)
    return any(f" {_bas(x).strip()} " in bas for x in criteres["lieux"])
