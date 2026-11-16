"""Critères de veille et seuils, modifiables dans l'appli (table `parametres`).

Le filtre est gratuit (Python, sans LLM) : seules les offres qui le passent consomment
du quota Gemini au matching.
"""

import hashlib
import re
from copy import deepcopy

import psycopg
from psycopg.types.json import Jsonb

from app import geo
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
        # Zone « ville + rayon » : si les deux sont remplis, elle remplace la liste de lieux.
        "zone_ville": "",
        "zone_rayon_km": 0,
        "niveaux_exclus": ["senior", "lead"],
        # Lieux acceptés quand aucune zone « ville + rayon » n'est réglée (Île-de-France et
        # télétravail). Lieu inconnu : proposé, « à vérifier ».
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
        "spontanees_auto_par_jour": 5,  # spontanées envoyées automatiquement au plus par jour
        "envois_par_jour": 10,
        "envois_auto_par_jour": 10,  # candidatures envoyées automatiquement au plus par jour
        "relances_auto_par_jour": 15,  # relances envoyées automatiquement au plus par jour
        "envois_auto_haut_par_jour": 5,  # dont score ≥ « fabrication » (85) ; les suivantes restent pour toi
        "envoi_auto_min": 50,  # score minimal pour une candidature automatique (sous le seuil « fabrication »)
        "candidatures_auto_par_passage": 3,  # dossiers préparés par passage (Gemini et Chrome sont lents)  # mails envoyés depuis Gmail au plus par jour (toujours sur ton clic)  # brouillons de candidature spontanée préparés au plus par jour  # un rapport récapitulatif tous les N jours
    },
    "veille": {"frequence_minutes": 60, "entreprises_par_defaut": 5, "initialisee": False},
    # Envoi en 1 clic depuis Gmail : désactivé tant que tu ne l'actives pas (onglet Spontanées).
    # relances_auto : relances dues envoyées sans ton clic (activé à ta demande du 14/11/2026).
    # spontanees_auto : spontanées envoyées sans ton clic (coupé tant que tu ne l'actives pas).
    "envoi": {"actif": False, "auto": False, "relances_auto": True, "spontanees_auto": False},
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


def refus(
    intitule: str, lieu: str, criteres: dict, conn: psycopg.Connection | None = None
) -> str | None:
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
    if lieu_accepte(lieu, criteres, conn) is False:
        return f"lieu hors zone ({lieu.strip()})"
    return None


# --- Zone ------------------------------------------------------------------------------------
# Statut du lieu d'une offre :
#   dans_zone   : dans le rayon autour de ta ville (ou dans la liste de lieux) ;
#   hors_zone   : refusée ;
#   teletravail : télétravail complet sans ville dans la zone : proposée, signalée ;
#   a_verifier  : lieu absent, vague (« Île-de-France ») ou introuvable : proposée, signalée.
# Seul « dans_zone » peut partir en candidature automatique.
DANS_ZONE, HORS_ZONE, TELETRAVAIL, A_VERIFIER = (
    "dans_zone",
    "hors_zone",
    "teletravail",
    "a_verifier",
)


def rayon(criteres: dict) -> tuple[str, float] | None:
    """(ville, rayon en km) si la zone « ville + rayon » est réglée."""
    ville = str(criteres.get("zone_ville") or "").strip()
    try:
        km = float(criteres.get("zone_rayon_km") or 0)
    except (TypeError, ValueError):
        km = 0
    return (ville, km) if ville and km > 0 else None


def signature_zone(criteres: dict) -> str:
    """Change quand la zone change : les offres déjà situées sont alors recalculées."""
    z = rayon(criteres)
    if z:
        return f"rayon:{geo.bas(z[0])}:{z[1]:g}"
    mots = ",".join(sorted(_bas(x).strip() for x in criteres.get("lieux", [])))
    return "mots:" + hashlib.sha1(mots.encode()).hexdigest()[:12]


def statut_lieu(
    lieu: str | None, criteres: dict, conn: psycopg.Connection | None = None
) -> tuple[str, float | None]:
    """(statut, distance en km si connue) du lieu d'une offre."""
    nom, remote = geo.commune(lieu or "")
    z = rayon(criteres)
    if z is None:  # liste de lieux
        if nom and any(
            f" {_bas(x).strip()} " in _bas(lieu or "") for x in _lieux_sans_remote(criteres)
        ):
            return DANS_ZONE, None
        if remote:
            return TELETRAVAIL, None
        return (HORS_ZONE if nom else A_VERIFIER), None
    if nom:
        origine, point = geo.coordonnees(z[0], conn), geo.coordonnees(nom, conn)
        if origine and point:
            d = round(geo.distance_km(origine, point), 1)
            if d <= z[1]:
                return DANS_ZONE, d
            return (TELETRAVAIL if remote else HORS_ZONE), d
    return (TELETRAVAIL if remote else A_VERIFIER), None


def _lieux_sans_remote(criteres: dict) -> list[str]:
    return [x for x in criteres.get("lieux", []) if not geo._TELETRAVAIL.search(geo.bas(x))]


def lieu_accepte(
    lieu: str | None, criteres: dict, conn: psycopg.Connection | None = None
) -> bool | None:
    """True si le lieu est dans la zone (ou télétravail complet), False s'il en est hors,
    None s'il est inconnu."""
    statut, _ = statut_lieu(lieu, criteres, conn)
    return {DANS_ZONE: True, TELETRAVAIL: True, HORS_ZONE: False}.get(statut)


def situer_offres(conn: psycopg.Connection, criteres: dict, limite: int = 500) -> int:
    """Note le statut du lieu (et la distance) des offres ouvertes pas encore situées pour la
    zone actuelle ; l'appli s'en sert pour ses étiquettes et ses filtres."""
    sig = signature_zone(criteres)
    rows = conn.execute(
        "SELECT id, ville FROM offres WHERE statut_validite IN ('a_verifier', 'valide')"
        " AND lieu_zone IS DISTINCT FROM %s ORDER BY recu_le DESC LIMIT %s",
        (sig, limite),
    ).fetchall()
    for r in rows:
        statut, km = statut_lieu(r["ville"], criteres, conn)
        conn.execute(
            "UPDATE offres SET lieu_statut = %s, distance_km = %s, lieu_zone = %s WHERE id = %s",
            (statut, km, sig, r["id"]),
        )
    return len(rows)
