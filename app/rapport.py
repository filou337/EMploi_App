"""Rapport périodique (tous les 3 jours) : où en est la recherche, et ce qu'il faut faire.

Contenu : offres repérées et classées sur la période, candidatures envoyées, réponses reçues,
relances dues aujourd'hui, dossiers prêts à envoyer, meilleures offres « à étudier » (classe B),
taux de réponse sur 60 jours. Enregistré dans la table `rapports` (lu dans l'appli, y compris
sur le téléphone) et signalé par une notification. Rien n'est envoyé par mail : l'accès Gmail
reste en lecture seule.

L'orchestrateur l'appelle à chaque passage ; un rapport n'est produit que si le dernier a
au moins `seuils.rapport_jours` jours (3 par défaut).

Usage :  python -m app.rapport [--forcer] [--jours 3]
"""

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from app import criteres
from app.notifications import bureau, notifier

TAUX_JOURS = 60


def _json(v):
    if isinstance(v, Decimal):
        return float(v)
    return v.isoformat() if hasattr(v, "isoformat") else v


def _liste(conn: psycopg.Connection, sql: str, params: tuple) -> list[dict]:
    return [{k: _json(v) for k, v in r.items()} for r in conn.execute(sql, params).fetchall()]


def construire(conn: psycopg.Connection, debut: datetime, fin: datetime) -> dict:
    p = (debut, fin)
    offres = conn.execute(
        """
        SELECT count(*) FILTER (WHERE statut_validite <> 'doublon') AS collectees,
               count(*) FILTER (WHERE pertinente AND statut_validite <> 'doublon') AS data
        FROM offres WHERE recu_le >= %s AND recu_le < %s
        """,
        p,
    ).fetchone()
    classes = {
        r["classe"]: r["n"]
        for r in conn.execute(
            """
            SELECT o.classe, count(DISTINCT o.id) AS n
            FROM offres o JOIN analyses a ON a.offre_id = o.id AND a.type = 'matching'
            WHERE a.cree_le >= %s AND a.cree_le < %s AND o.classe IS NOT NULL
            GROUP BY o.classe
            """,
            p,
        )
    }
    envoyees = _liste(
        conn,
        """
        SELECT c.postule_le, coalesce(e.nom_affiche, '?') AS entreprise,
               coalesce(c.poste, o.intitule, '') AS poste, c.type
        FROM candidatures c LEFT JOIN entreprises e ON e.id = c.entreprise_id
        LEFT JOIN offres o ON o.id = c.offre_id
        WHERE c.postule_le >= %s::date AND c.postule_le < %s::date + 1
        ORDER BY c.postule_le DESC
        """,
        p,
    )
    reponses = _liste(
        conn,
        """
        SELECT r.recu_le, r.classement, r.a_confirmer, coalesce(e.nom_affiche, r.expediteur) AS entreprise,
               coalesce(c.poste, '') AS poste, r.extrait
        FROM reponses r LEFT JOIN candidatures c ON c.id = r.candidature_id
        LEFT JOIN entreprises e ON e.id = c.entreprise_id
        WHERE r.recu_le >= %s AND r.recu_le < %s AND r.classement IS NOT NULL
        ORDER BY (r.classement = 'positive') DESC, r.recu_le DESC
        """,
        p,
    )
    relances = _liste(
        conn,
        """
        SELECT id, coalesce(entreprise, '?') AS entreprise, coalesce(poste, offre_intitule, '') AS poste,
               numero, due_le, contact_email
        FROM v_relances ORDER BY due_le
        """,
        (),
    )
    a_envoyer = _liste(
        conn,
        """
        SELECT o.id, coalesce(e.nom_affiche, '?') AS entreprise, o.intitule, o.score, o.etape
        FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id
        WHERE o.etape IN ('qualifiee', 'ats_friendly', 'a_envoyer')
        ORDER BY o.score DESC NULLS LAST LIMIT 10
        """,
        (),
    )
    a_etudier = _liste(
        conn,
        """
        SELECT o.id, coalesce(e.nom_affiche, '?') AS entreprise, o.intitule, o.score, o.motif
        FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id
        WHERE o.classe = 'B' AND o.statut_validite IN ('a_verifier', 'valide')
          AND NOT EXISTS (SELECT 1 FROM candidatures c WHERE c.offre_id = o.id)
        ORDER BY o.score DESC LIMIT 5
        """,
        (),
    )
    sans_suite = conn.execute(
        "SELECT count(*) AS n FROM candidatures WHERE statut = 'sans_suite'"
        " AND maj_le >= %s AND maj_le < %s",
        p,
    ).fetchone()["n"]
    taux = conn.execute(
        """
        SELECT count(*) AS envoyees,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM reponses r WHERE r.candidature_id = c.id
                   AND r.classement IN ('positive', 'negative'))) AS repondues,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM reponses r WHERE r.candidature_id = c.id
                   AND r.classement = 'positive')) AS positives
        FROM candidatures c WHERE c.postule_le >= %s::date - %s
        """,
        (fin, TAUX_JOURS),
    ).fetchone()
    par_classement = {"positive": 0, "negative": 0, "neutre": 0}
    for r in reponses:
        par_classement[r["classement"]] += 1
    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
        "chiffres": {
            "offres_collectees": offres["collectees"],
            "offres_data": offres["data"],
            "classe_a": classes.get("A", 0),
            "classe_b": classes.get("B", 0),
            "classe_c": classes.get("C", 0),
            "candidatures_envoyees": len(envoyees),
            "reponses_positives": par_classement["positive"],
            "refus": par_classement["negative"],
            "reponses_neutres": par_classement["neutre"],
            "relances_dues": len(relances),
            "dossiers_prets": len(a_envoyer),
            "passees_sans_suite": sans_suite,
        },
        "taux_reponse": {
            "jours": TAUX_JOURS,
            "envoyees": taux["envoyees"],
            "repondues": taux["repondues"],
            "positives": taux["positives"],
            "taux": round(100 * taux["repondues"] / taux["envoyees"], 1)
            if taux["envoyees"]
            else None,
        },
        "envoyees": envoyees,
        "reponses": reponses,
        "relances": relances,
        "a_envoyer": a_envoyer,
        "a_etudier": a_etudier,
    }


def resume(contenu: dict) -> str:
    """Une ligne pour la notification : le plus important d'abord."""
    c = contenu["chiffres"]
    morceaux = []
    if c["reponses_positives"]:
        morceaux.append(f"{c['reponses_positives']} réponse(s) positive(s)")
    if c["relances_dues"]:
        morceaux.append(f"{c['relances_dues']} relance(s) à faire")
    if c["dossiers_prets"]:
        morceaux.append(f"{c['dossiers_prets']} dossier(s) prêt(s)")
    morceaux.append(f"{c['candidatures_envoyees']} candidature(s) envoyée(s)")
    morceaux.append(f"{c['offres_data']} offre(s) data repérée(s)")
    if c["refus"]:
        morceaux.append(f"{c['refus']} refus")
    return ", ".join(morceaux) + "."


def dernier(conn: psycopg.Connection) -> dict | None:
    return conn.execute(
        "SELECT id, periode_debut, periode_fin, contenu, cree_le FROM rapports"
        " ORDER BY cree_le DESC LIMIT 1"
    ).fetchone()


def generer(conn: psycopg.Connection, forcer: bool = False, jours: int | None = None) -> dict:
    """Produit le rapport s'il est dû (ou si `forcer`). Renvoie un bilan pour le journal."""
    jours = jours or int(criteres.lire(conn)["seuils"].get("rapport_jours", 3))
    fin = datetime.now(UTC)
    precedent = dernier(conn)
    if precedent and not forcer and fin - precedent["cree_le"] < timedelta(days=jours):
        prochain = precedent["cree_le"] + timedelta(days=jours)
        return {"prochain rapport": f"{prochain.astimezone():%d/%m %H:%M}"}
    debut = precedent["periode_fin"] if precedent and not forcer else fin - timedelta(days=jours)
    contenu = construire(conn, debut, fin)
    row = conn.execute(
        "INSERT INTO rapports (periode_debut, periode_fin, contenu) VALUES (%s, %s, %s) RETURNING id",
        (debut, fin, Jsonb(contenu)),
    ).fetchone()
    texte = resume(contenu)
    notifier(conn, "rapport", f"Rapport du {fin.astimezone():%d/%m}", texte)
    bureau("EMploi_App — rapport", texte)
    return {"rapport": row["id"], **contenu["chiffres"]}


def main() -> None:
    from app.db import connexion

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--forcer", action="store_true", help="produit un rapport même s'il n'est pas dû"
    )
    p.add_argument("--jours", type=int, help="période couverte (défaut : seuils.rapport_jours)")
    args = p.parse_args()
    with connexion() as conn:
        bilan = generer(conn, args.forcer, args.jours)
        if "rapport" not in bilan:
            print(f"Pas encore dû ({bilan['prochain rapport']}). --forcer pour le produire.")
            return
        print(resume(dernier(conn)["contenu"]))


if __name__ == "__main__":
    main()
