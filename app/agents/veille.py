"""Agent Veille : surveille le site carrières des entreprises choisies.

Pour chaque entreprise de `veille_entreprises` :
1. trouve son ATS public (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee) — sinon note
   qu'il faut s'abonner à ses alertes officielles, que l'agent Recherche lira dans Gmail ;
2. garde les postes qui passent tes critères (filtre Python, gratuit) ;
3. nouvelle offre -> ajoutée (fiche complète, étape « Repérée ») ; offre déjà connue par une
   alerte -> enrichie au lieu d'être dupliquée ; annonce dont le contenu change -> « modifiée » ;
   offre retirée du site -> « expirée ».
Premier passage sur une entreprise : un seul résumé, pas une notification par offre.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from app import criteres
from app.collecte.normalisation import cle_floue, contrat_depuis_intitule
from app.dedup import meme_offre
from app.fiches.ats import Poste, fiche_smartrecruiters
from app.fiches.recherche import _trouver_tableau, sites_employeur
from app.notifications import notifier

REESSAI_SANS_SITE = timedelta(
    days=7
)  # employeur sans site lisible : nouvelle recherche après 7 jours


def empreinte(p: Poste) -> str:
    return hashlib.sha1(f"{p.titre}|{p.lieu}|{p.texte}".encode()).hexdigest()


def initialiser(conn: psycopg.Connection) -> int:
    """Au tout premier passage : surveille par défaut les N derniers employeurs où tu as postulé."""
    params = criteres.lire(conn)
    if params["veille"].get("initialisee"):
        return 0
    n = conn.execute(
        """
        INSERT INTO veille_entreprises (entreprise_id, origine)
        SELECT entreprise_id, 'auto' FROM (
            SELECT entreprise_id, max(postule_le) AS derniere FROM candidatures
            WHERE entreprise_id IS NOT NULL AND statut <> 'ignore' GROUP BY entreprise_id
            ORDER BY derniere DESC NULLS LAST LIMIT %s) recentes
        ON CONFLICT (entreprise_id) DO NOTHING
        """,
        (params["veille"]["entreprises_par_defaut"],),
    ).rowcount
    criteres.ecrire(conn, "veille", {**params["veille"], "initialisee": True})
    return n


@dataclass
class Bilan:
    entreprises: int = 0
    sans_ats: list[str] = field(default_factory=list)
    nouvelles: int = 0
    enrichies: int = 0
    modifiees: int = 0
    retirees: int = 0
    hors_criteres: int = 0

    def resume(self) -> dict:
        return {
            "entreprises": self.entreprises,
            "sans ATS public": len(self.sans_ats),
            "nouvelles": self.nouvelles,
            "enrichies": self.enrichies,
            "modifiées": self.modifiees,
            "retirées": self.retirees,
            "hors critères": self.hors_criteres,
        }


def _surveiller(conn: psycopg.Connection, ent: dict, crit: dict, bilan: Bilan) -> str:
    nom = ent["nom_affiche"] or ent["nom_norm"]
    verifie = ent.get("ats_verifie_le")
    if ent["ats"] == "aucun" and verifie and datetime.now(UTC) - verifie < REESSAI_SANS_SITE:
        # Aucun site lisible trouvé récemment : on ne refait pas toute la recherche à chaque passage.
        bilan.sans_ats.append(nom)
        return "pas de site carrières lisible : abonne-toi à ses alertes officielles (Gmail)"
    tableau = _trouver_tableau(
        nom, ent["ats"], ent["ats_slug"], sites_employeur(conn, {**ent, "id": ent["entreprise_id"]})
    )
    conn.execute(
        "UPDATE entreprises SET ats = %s, ats_slug = %s, ats_verifie_le = now(),"
        " site_carrieres = CASE WHEN %s = 'site' THEN %s ELSE site_carrieres END WHERE id = %s",
        (
            tableau.ats if tableau else "aucun",
            tableau.slug if tableau else None,
            tableau.ats if tableau else None,
            tableau.slug if tableau else None,
            ent["entreprise_id"],
        ),
    )
    if tableau is None:
        bilan.sans_ats.append(nom)
        return (
            "pas d'ATS public : abonne-toi à ses alertes officielles (elles arriveront par Gmail)"
        )
    premier = ent["derniere_veille"] is None
    source = f"{tableau.ats}:{tableau.slug}"
    gardes = []
    for p in tableau.postes:
        if criteres.refus(p.titre, p.lieu, crit):
            bilan.hors_criteres += 1
        else:
            gardes.append(p)
    vus: set[str] = set()
    nouvelles_ici = 0
    for p in gardes:
        if tableau.ats == "smartrecruiters" and not p.texte:
            texte, url = fiche_smartrecruiters(tableau.slug, p.url)
            p = Poste(p.titre, p.lieu, url, p.publie_le, texte)
        vus.add(p.url)
        emp = empreinte(p)
        connue = conn.execute(
            "SELECT id, empreinte_contenu, etape FROM offres WHERE url_cle = %s OR url_fiche = %s"
            " ORDER BY id LIMIT 1",
            (p.url, p.url),
        ).fetchone()
        if connue is None:  # déjà reçue par une alerte sous un autre lien ?
            cle = cle_floue(nom, p.titre, p.lieu)
            for o in conn.execute(
                "SELECT id, cle_floue, intitule, etape FROM offres WHERE entreprise_id = %s"
                " AND statut_validite IN ('a_verifier', 'valide') AND url_fiche IS NULL",
                (ent["entreprise_id"],),
            ):
                if meme_offre(o["cle_floue"], cle, o["intitule"] or "", p.titre):
                    conn.execute(
                        """
                        UPDATE offres SET url_fiche = %s, texte = NULLIF(%s, ''), fiche_complete = %s,
                               statut_validite = 'valide', empreinte_contenu = %s, verifiee_le = now(),
                               date_publication = COALESCE(date_publication, %s),
                               note_validite = %s, etape = COALESCE(etape, 'reperee'),
                               etape_maj_le = COALESCE(etape_maj_le, now())
                        WHERE id = %s
                        """,
                        (
                            p.url,
                            p.texte,
                            bool(p.texte),
                            emp,
                            p.publie_le,
                            f"fiche trouvée par la veille sur {source}",
                            o["id"],
                        ),
                    )
                    bilan.enrichies += 1
                    connue = {"id": o["id"], "empreinte_contenu": emp, "etape": o["etape"]}
                    break
        if connue is None:
            oid = conn.execute(
                """
                INSERT INTO offres (url_cle, cle_floue, entreprise_id, source, intitule, ville, contrat,
                                    url_fiche, texte, fiche_complete, statut_validite, date_publication,
                                    empreinte_contenu, verifiee_le, note_validite, etape, etape_maj_le)
                VALUES (%s, %s, %s, 'site_carrieres', %s, %s, %s, %s, NULLIF(%s, ''), %s, 'valide', %s,
                        %s, now(), %s, 'reperee', now())
                ON CONFLICT (url_cle) DO NOTHING RETURNING id
                """,
                (
                    p.url,
                    cle_floue(nom, p.titre, p.lieu),
                    ent["entreprise_id"],
                    p.titre,
                    p.lieu or None,
                    contrat_depuis_intitule(p.titre),
                    p.url,
                    p.texte,
                    bool(p.texte),
                    p.publie_le,
                    emp,
                    f"publiée sur {source}",
                ),
            ).fetchone()
            if oid:
                bilan.nouvelles += 1
                nouvelles_ici += 1
                if not premier:
                    notifier(
                        conn,
                        "nouvelle_offre",
                        f"Nouvelle offre — {nom}",
                        f"{p.titre} · {p.lieu or 'lieu non précisé'}",
                        oid["id"],
                    )
        elif connue["empreinte_contenu"] and connue["empreinte_contenu"] != emp:
            conn.execute(
                "UPDATE offres SET texte = NULLIF(%s, ''), intitule = %s, ville = %s,"
                " empreinte_contenu = %s, modifiee_le = now() WHERE id = %s",
                (p.texte, p.titre, p.lieu or None, emp, connue["id"]),
            )
            bilan.modifiees += 1
            if connue["etape"]:
                notifier(
                    conn,
                    "modifiee",
                    f"Annonce modifiée — {nom}",
                    f"{p.titre} : l'annonce a changé, relance le matching.",
                    connue["id"],
                )
        elif not connue["empreinte_contenu"]:
            conn.execute(
                "UPDATE offres SET empreinte_contenu = %s WHERE id = %s", (emp, connue["id"])
            )
    if tableau.complet:
        retirees = conn.execute(
            """
            UPDATE offres SET statut_validite = 'expiree', verifiee_le = now(),
                   note_validite = %s
            WHERE entreprise_id = %s AND source = 'site_carrieres' AND statut_validite = 'valide'
              AND NOT (url_cle = ANY(%s)) RETURNING id, intitule, etape
            """,
            (f"retirée de {source}", ent["entreprise_id"], list(vus)),
        ).fetchall()
        bilan.retirees += len(retirees)
        for r in retirees:
            if r["etape"] in ("qualifiee", "fabrication", "ats_friendly", "a_envoyer"):
                notifier(
                    conn,
                    "retiree",
                    f"Offre retirée — {nom}",
                    f"{r['intitule']} n'est plus en ligne.",
                    r["id"],
                )
    if premier and nouvelles_ici:
        notifier(
            conn,
            "nouvelle_offre",
            f"Veille lancée sur {nom}",
            f"{nouvelles_ici} offre(s) correspondant à tes critères sur {source}.",
        )
    return f"{len(gardes)} poste(s) dans tes critères sur {len(tableau.postes)} ({source})"


def executer(conn: psycopg.Connection) -> dict:
    initialiser(conn)
    crit = criteres.lire(conn)["criteres"]
    bilan = Bilan()
    for ent in conn.execute(
        """
        SELECT v.id, v.entreprise_id, v.derniere_veille, e.nom_affiche, e.nom_norm, e.ats, e.ats_slug,
               e.site_carrieres, e.site_web, e.ats_verifie_le
        FROM veille_entreprises v JOIN entreprises e ON e.id = v.entreprise_id
        WHERE v.active ORDER BY v.derniere_veille NULLS FIRST
        """
    ).fetchall():
        bilan.entreprises += 1
        with conn.transaction():
            note = _surveiller(conn, ent, crit, bilan)
            conn.execute(
                "UPDATE veille_entreprises SET derniere_veille = now(), note = %s WHERE id = %s",
                (note, ent["id"]),
            )
    return bilan.resume()
