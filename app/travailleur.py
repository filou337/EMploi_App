"""Travailleur du PC : exécute les demandes déposées par l'interface (téléphone, GitHub Pages).

Usage :  python -m app.travailleur        (tourne en continu ; service systemd via outils/installer_appli.sh)

L'interface ne fait pas tourner de Python : elle dépose une ligne dans `demandes` (analyser le CV,
matcher une offre, déposer un document, importer ou exporter un Excel, créer un dossier, lancer
les agents). Le travailleur les prend une à une, du plus ancien au plus récent, et écrit le résultat.
Il signale aussi sa présence (`parametres.pc`) : l'interface affiche « PC allumé » ou « éteint ».
"""

import argparse
import json
import time
import traceback
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from app import criteres, documents, dossiers, orchestrateur
from app.ats import service as ats
from app.db import connexion
from app.llm import LLMIndisponible
from app.migrations import appliquer

PAUSE = 5  # secondes entre deux coups d'œil à la file


def _json(obj) -> Jsonb:
    """Résultat stockable (dates et décimaux convertis en texte)."""
    return Jsonb(json.loads(json.dumps(obj, default=str)))


def _prendre(conn: psycopg.Connection) -> dict | None:
    return conn.execute(
        """
        UPDATE demandes SET statut = 'en_cours'
        WHERE id = (SELECT id FROM demandes WHERE statut = 'en_attente'
                    ORDER BY cree_le FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id, type, parametres, fichier, nom_fichier
        """
    ).fetchone()


def executer(conn: psycopg.Connection, d: dict) -> tuple[dict, bytes | None]:
    """Exécute une demande ; renvoie (résultat, fichier produit éventuel)."""
    p = d["parametres"] or {}
    t = d["type"]
    if t == "analyser_cv":
        return ats.analyser_cv(conn, forcer=bool(p.get("forcer"))), None
    if t == "matching":
        return ats.matcher_offre(conn, int(p["offre_id"]), bool(p.get("forcer"))), None
    if t == "fabriquer":
        from app.generation.fabrique import fabriquer

        return fabriquer(conn, int(p["offre_id"]), p.get("langue")), None
    if t == "creer_dossier":
        return {"dossier": dossiers.creer(conn, int(p["offre_id"]))}, None
    if t == "deposer_document":
        return documents.deposer(
            conn, p.get("type", "cv"), d["nom_fichier"] or "document", bytes(d["fichier"] or b"")
        ), None
    if t == "importer_candidatures":
        from app.serveur import NouvelleCandidature, _ajouter, lignes_import

        ajoutees = doublons = 0
        for ligne in lignes_import(d["nom_fichier"] or "", bytes(d["fichier"] or b"")):
            if _ajouter(conn, NouvelleCandidature(**ligne)) is None:
                doublons += 1
            else:
                ajoutees += 1
        return {"ajoutees": ajoutees, "deja_presentes": doublons}, None
    if t == "exporter_excel":
        from app.serveur import classeur_excel

        return {"nom": f"candidatures_{datetime.now():%Y-%m-%d}.xlsx"}, classeur_excel(conn)
    if t == "lancer_veille":
        conn.commit()  # libère la ligne « en cours » : le passage ouvre ses propres connexions
        etapes = (
            orchestrateur.ETAPES
            if p.get("gmail", True)
            else tuple(e for e in orchestrateur.ETAPES if e != "collecte")
        )
        return orchestrateur.passage(etapes), None
    if t == "preparer_spontanee":
        from app.spontanees import agent as spontanees

        eid = spontanees.ajouter_cible(conn, p["entreprise"], p.get("site_web"), p.get("email"))
        conn.commit()  # la cible reste enregistrée même si la rédaction échoue
        return spontanees.preparer(conn, eid), None
    if t == "envoyer_mail":
        from app import envoi

        if p.get("type") == "spontanee":
            return envoi.envoyer_spontanee(conn, int(p["id"]), p["objet"], p["corps"]), None
        if p.get("type") == "relance":
            return envoi.envoyer_relance(conn, int(p["id"]), p["objet"], p["corps"]), None
        raise ValueError("Type d'envoi inconnu.")
    raise ValueError(f"Type de demande inconnu : {t}")


def tour() -> bool:
    """Traite au plus une demande. Renvoie True s'il y en avait une."""
    with connexion() as conn:
        conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES ('pc', %s)"
            " ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, maj_le = now()",
            (_json({"vu_le": datetime.now(UTC).isoformat()}),),
        )
        conn.commit()
        d = _prendre(conn)
        conn.commit()
        if d is None:
            return False
        try:
            resultat, fichier = executer(conn, d)
            conn.execute(
                "UPDATE demandes SET statut = 'faite', resultat = %s, fichier_resultat = %s,"
                " fichier = NULL, traitee_le = now() WHERE id = %s",
                (_json(resultat), fichier, d["id"]),
            )
        except (
            ats.AnalyseImpossible,
            LLMIndisponible,
            documents.DocumentRefuse,
            ValueError,
            RuntimeError,
        ) as e:
            conn.rollback()
            conn.execute(
                "UPDATE demandes SET statut = 'erreur', erreur = %s, fichier = NULL, traitee_le = now()"
                " WHERE id = %s",
                (str(e), d["id"]),
            )
        except Exception as e:  # noqa: BLE001 — une demande en panne ne bloque pas les suivantes
            conn.rollback()
            conn.execute(
                "UPDATE demandes SET statut = 'erreur', erreur = %s, traitee_le = now() WHERE id = %s",
                (f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"[:2000], d["id"]),
            )
        return True


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--une-fois", action="store_true", help="traite la file puis s'arrête")
    args = p.parse_args()
    appliquer()
    with connexion() as conn:  # écrit les réglages par défaut pour que l'interface les lise
        for cle, valeur in criteres.lire(conn).items():
            conn.execute(
                "INSERT INTO parametres (cle, valeur) VALUES (%s, %s) ON CONFLICT (cle) DO NOTHING",
                (cle, Jsonb(valeur)),
            )
    print("Travailleur EMploi_App prêt : j'attends les demandes de l'appli (Ctrl+C pour arrêter).")
    while True:
        try:
            while tour():
                pass
        except psycopg.OperationalError as e:  # base injoignable (réseau) : on réessaie plus tard
            print(f"Base injoignable : {e}")
            time.sleep(30)
        if args.une_fois:
            return
        time.sleep(PAUSE)


if __name__ == "__main__":
    main()
