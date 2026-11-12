"""Agent Suivi : relit les mails récents et fait avancer les candidatures.

À chaque passage de l'orchestrateur :
1. import des 3 derniers jours de chaque boîte autorisée (envois, accusés, refus, invitations) ;
2. validation automatique : un accusé de réception d'une entreprise dont un dossier est
   « À envoyer » (ou prêt) confirme l'envoi -> l'offre passe « Envoyée » et la candidature
   lui est rattachée ;
3. notification de chaque nouvelle réponse (positive ou refus) ;
4. relances : « sans suite » 30 jours après la dernière relance, et rappel quotidien des
   relances dues (J+7, J+14 ; textes prêts dans l'onglet Candidatures).
"""

import psycopg

from app.config import boites_gmail, chemin_token
from app.db import connexion
from app.notifications import notifier
from app.suivi.historique import importer_boite

JOURS = 3


def rapprocher(conn: psycopg.Connection) -> int:
    """Rattache les candidatures détectées dans Gmail aux offres prêtes à envoyer de la même entreprise."""
    confirmees = 0
    for o in conn.execute(
        """
        SELECT o.id, o.intitule, o.entreprise_id, e.nom_affiche AS entreprise, o.etape_maj_le
        FROM offres o JOIN entreprises e ON e.id = o.entreprise_id
        WHERE o.etape IN ('a_envoyer', 'ats_friendly', 'fabrication')
          AND NOT EXISTS (SELECT 1 FROM candidatures c WHERE c.offre_id = o.id)
        """
    ).fetchall():
        c = conn.execute(
            """
            SELECT id FROM candidatures
            WHERE entreprise_id = %s AND offre_id IS NULL AND origine = 'manuelle'
              AND gmail_message_id IS NOT NULL AND postule_le >= %s::date - 1
            ORDER BY postule_le DESC LIMIT 1
            """,
            (o["entreprise_id"], o["etape_maj_le"]),
        ).fetchone()
        if c is None:
            continue
        conn.execute(
            "UPDATE candidatures SET offre_id = %s, maj_le = now() WHERE id = %s",
            (o["id"], c["id"]),
        )
        conn.execute(
            "UPDATE offres SET etape = 'envoyee', etape_maj_le = now() WHERE id = %s", (o["id"],)
        )
        notifier(
            conn,
            "envoyee",
            f"Candidature confirmée — {o['entreprise']}",
            f"{o['intitule']} : accusé de réception reçu, l'offre passe « Envoyée ».",
            o["id"],
        )
        confirmees += 1
    return confirmees


def executer() -> dict:
    with connexion() as conn:
        dernier = conn.execute("SELECT coalesce(max(id), 0) AS n FROM reponses").fetchone()["n"]
    lues = 0
    for boite in boites_gmail():
        if chemin_token(boite).exists():
            importer_boite(boite, jours=JOURS)  # journalise lui-même son passage (tâche « suivi »)
            lues += 1
    with connexion() as conn:
        confirmees = rapprocher(conn)
        nouvelles = conn.execute(
            """
            SELECT r.classement, e.nom_affiche AS entreprise, c.poste, c.offre_id FROM reponses r
            JOIN candidatures c ON c.id = r.candidature_id LEFT JOIN entreprises e ON e.id = c.entreprise_id
            WHERE r.id > %s AND r.classement IN ('positive', 'negative')
            """,
            (dernier,),
        ).fetchall()
        for r in nouvelles:
            positive = r["classement"] == "positive"
            notifier(
                conn,
                "qualifiee" if positive else "reponse",
                f"{'🎉 Réponse positive' if positive else 'Refus'} — {r['entreprise']}",
                r["poste"] or "",
                r["offre_id"],
            )
        sans_suite = conn.execute("SELECT classer_sans_suite() AS n").fetchone()["n"]
        relances = conn.execute("SELECT count(*) AS n FROM v_relances").fetchone()["n"]
        deja = conn.execute(
            "SELECT 1 FROM notifications WHERE type = 'relances' AND cree_le::date = current_date"
        ).fetchone()
        if relances and not deja:
            notifier(
                conn,
                "relances",
                f"{relances} relance(s) à faire",
                "Textes prêts dans l'onglet Candidatures.",
            )
    return {
        "boîtes lues": lues,
        "candidatures confirmées": confirmees,
        "nouvelles réponses": len(nouvelles),
        "relances à faire": relances,
        "passées sans suite": sans_suite,
    }
