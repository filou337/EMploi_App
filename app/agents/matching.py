"""Agent Matching : place les offres dans le pipeline et qualifie les plus pertinentes.

1. Toute offre ouverte qui passe tes critères entre dans le pipeline à l'étape « Repérée ».
2. Celles qui ont une fiche complète passent au matching (Gemini), dans la limite du quota
   par passage ; une offre connue seulement par un extrait reste « Repérée » (plafond 84).
3. Le matching alimente la grille V2 (app/scoring.py) : score /100, classe A / B / C, motif.
4. Classe A et pas de candidature chez cet employeur depuis 3 mois -> « Qualifiée »,
   dans la limite de N dossiers par jour, avec notification.
"""

import psycopg

from app import activite, criteres, llm, scoring
from app.ats import service as ats
from app.dedup import candidature_recente
from app.notifications import notifier


def reperer(conn: psycopg.Connection, crit: dict) -> int:
    """Fait entrer dans le pipeline les offres ouvertes qui passent les critères."""
    rows = conn.execute(
        "SELECT id, intitule, ville FROM offres WHERE etape IS NULL"
        " AND statut_validite IN ('a_verifier', 'valide')"
    ).fetchall()
    ids = [
        r["id"] for r in rows if criteres.refus(r["intitule"] or "", r["ville"] or "", crit) is None
    ]
    if ids:
        conn.execute(
            "UPDATE offres SET etape = 'reperee', etape_maj_le = now() WHERE id = ANY(%s)", (ids,)
        )
    return len(ids)


def executer(conn: psycopg.Connection) -> dict:
    params = criteres.lire(conn)
    seuils = params["seuils"]
    bilan = {"repérées": reperer(conn, params["criteres"]), "analysées": 0, "qualifiées": 0}
    if not llm.disponible():
        bilan["note"] = "clé Gemini absente"
        return bilan
    try:
        ats.cv_par_defaut(conn)
        scoring.charger_profil()
    except (ats.AnalyseImpossible, scoring.ProfilInvalide) as e:
        bilan["note"] = str(e)
        return bilan
    deja = conn.execute(
        "SELECT count(*) AS n FROM offres WHERE etape = 'qualifiee' AND etape_maj_le::date = current_date"
    ).fetchone()["n"]
    candidates = conn.execute(
        """
        SELECT o.id, o.intitule, o.entreprise_id, e.nom_affiche AS entreprise
        FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id
        WHERE o.etape = 'reperee' AND o.fiche_complete AND o.statut_validite = 'valide'
          AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.offre_id = o.id
                          AND a.cree_le >= COALESCE(o.modifiee_le, o.recu_le))
        ORDER BY COALESCE(o.date_publication, o.recu_le::date) DESC
        LIMIT %s
        """,
        (seuils["matchings_par_passage"],),
    ).fetchall()
    for o in candidates:
        aid = activite.debut("matching", "Analyse le poste", offre_id=o["id"])
        try:
            with conn.transaction():
                res = ats.matcher_offre(conn, o["id"])
        except llm.LLMIndisponible as e:
            bilan["note"] = str(e)  # quota épuisé : on reprendra au prochain passage
            activite.fin(aid, "Analyse reportée", "quota Gemini atteint", erreur=True)
            break
        except ats.AnalyseImpossible as e:
            activite.fin(aid, "Analyse impossible", str(e), erreur=True)
            continue
        bilan["analysées"] += 1
        grille = res.get("grille") or scoring.scorer(conn, o["id"], res["resultat"], params)
        score = grille["score"]
        activite.fin(
            aid,
            "Poste analysé",
            f"{score:.0f}/100, classe {grille['classe']}"
            + (f" ({grille['motif']})" if grille.get("motif") else ""),
        )
        if grille["classe"] != "A":
            continue
        recente = o["entreprise_id"] and candidature_recente(conn, o["entreprise_id"])
        if recente:
            notifier(
                conn,
                "info",
                f"Bonne offre chez {o['entreprise']}, mais…",
                f"{o['intitule']} ({score:.0f}/100) : tu y as postulé le {recente:%d/%m/%Y}.",
                o["id"],
            )
            continue
        if deja + bilan["qualifiées"] >= seuils["dossiers_par_jour"]:
            bilan["note"] = f"plafond de {seuils['dossiers_par_jour']} dossiers par jour atteint"
            continue
        conn.execute(
            "UPDATE offres SET etape = 'qualifiee', etape_maj_le = now() WHERE id = %s",
            (o["id"],),
        )
        bilan["qualifiées"] += 1
        notifier(
            conn,
            "qualifiee",
            f"{score:.0f}/100 — {o['entreprise']}",
            f"{o['intitule']} : dossier à préparer.",
            o["id"],
        )
    return bilan
