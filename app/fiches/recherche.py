"""Recherche de la fiche complète d'une offre chez l'employeur, via les ATS publics.

Usage :  python -m app.fiches [--limite 50] [--entreprise "Datadog"]

Pour chaque employeur qui a des offres data à vérifier :
1. retrouver son site carrières (ATS connu en base, sinon essai de quelques identifiants
   probables sur chaque ATS ; un employeur sans ATS n'est re-testé qu'au bout de 30 jours) ;
2. vérifier que ce site est bien le sien (nom renvoyé par l'API, ou postes en France) ;
3. rapprocher chaque offre des postes ouverts :
   - trouvée            -> 'valide', fiche complète, lien et date de publication réels ;
   - absente (site sûr) -> 'expiree' : l'employeur ne la publie plus ;
   - pas d'ATS trouvé   -> reste 'a_verifier' (fiche incomplète : score plafonné à 84).
Aucune page LinkedIn ou Indeed n'est consultée.
"""

import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb
from rapidfuzz import fuzz

from app.collecte.normalisation import _mots, niveau, norm_entreprise, norm_intitule, norm_ville
from app.db import connexion
from app.entreprises import compact
from app.fiches import adzuna, jobposting
from app.fiches.ats import (
    CONNECTEURS,
    Poste,
    Tableau,
    fiche_smartrecruiters,
    smartrecruiters_recherche,
)
from app.pertinence import est_data
from app.validite import marquer_anciennes

BUDGET_SITES_SECONDES = 120  # recherche sur les sites carrières, par employeur
SEUIL_TROUVEE = 85  # similarité d'intitulé pour dire « c'est la même offre »
SEUIL_ABSENTE = 60  # en dessous, aucun poste ne ressemble : l'offre n'est plus publiée
DELAI_NOUVEL_ESSAI = timedelta(days=30)
AGE_MIN_POUR_EXPIREE = timedelta(days=2)  # laisse à l'ATS le temps de publier
POSTES_MIN_POUR_EXPIREE = 5  # un petit site aux annonces génériques ne prouve pas une absence


def slugs(nom: str) -> list[str]:
    """Identifiants probables chez un ATS : 'BPCE Financement' -> bpcefinancement, bpce-financement, bpce."""
    propres = norm_entreprise(nom).split()
    bruts = _mots(nom)
    candidats = []
    for mots in (propres, bruts):
        if mots:
            candidats += ["".join(mots), "-".join(mots)]
    if len(propres) > 1 and len(propres[0]) >= 4:
        candidats.append(propres[0])
    vus: list[str] = []
    for c in candidats:
        if c and c not in vus:
            vus.append(c)
    return vus[:4]


def est_son_site(tableau: Tableau, nom: str) -> bool:
    """Le site carrières est-il celui de cet employeur en France ?

    Il faut des postes en France (« thales » sur SmartRecruiters est Thales Espagne) et, quand
    l'API donne le nom de l'employeur, qu'il corresponde (« Resilience » n'est pas « Resilience Care »).
    """
    if "FR" not in tableau.pays:
        return False
    if tableau.nom_employeur:
        a, b = compact(tableau.nom_employeur), compact(nom)
        court, long_ = sorted((a, b), key=len)
        return (
            a == b or fuzz.ratio(a, b) >= 85 or (court in long_ and len(court) >= 0.75 * len(long_))
        )
    return True


def similarite_titre(intitule: str, poste: Poste) -> int:
    """Similarité des intitulés (0 à 100) ; 0 si le niveau (junior, stage…) diffère."""
    if niveau(intitule) != niveau(poste.titre):
        return 0
    # Sites carrières : « Intitulé - Filière - Lieu » ; on compare aussi l'intitulé seul.
    variantes = {poste.titre, re.split(r"\s+[-–|]\s+", poste.titre)[0]}
    return max(
        round(fuzz.token_sort_ratio(norm_intitule(intitule), norm_intitule(v))) for v in variantes
    )


def meme_ville(ville: str, poste: Poste) -> bool:
    v_offre, v_poste = norm_ville(ville or ""), norm_ville(poste.lieu or "")
    return not (v_offre and v_poste) or fuzz.partial_ratio(v_offre, v_poste) >= 80


def similarite(intitule: str, ville: str, poste: Poste) -> int:
    """Similarité d'intitulé, ramenée à 0 si la ville ne colle pas."""
    return similarite_titre(intitule, poste) if meme_ville(ville, poste) else 0


@dataclass
class Verdict:
    offre_id: int
    statut: str | None  # 'valide', 'expiree' ou None (inchangé)
    note: str
    poste: Poste | None = None
    texte: str = ""
    url: str = ""


@dataclass
class Resultat:
    entreprise_id: int
    ats: str  # 'aucun' si rien trouvé
    slug: str | None
    verdicts: list[Verdict] = field(default_factory=list)


def sites_employeur(conn, ent: dict) -> list[tuple[str, bool]]:
    """Sites où chercher les offres JobPosting : (adresse, devinée ?), les sûres d'abord.

    Sûres : site carrières ou site web renseignés, domaine des adresses mail de l'employeur.
    Devinées : nom.fr et nom.com, acceptées seulement si les offres portent son nom.
    """
    from app.entreprises import domaine_racine, est_generique_ou_ats

    sures = [ent.get("site_carrieres"), ent.get("site_web")]
    for r in conn.execute(
        "SELECT email FROM contacts WHERE entreprise_id = %(e)s AND email IS NOT NULL"
        " UNION SELECT contact_email FROM candidatures WHERE entreprise_id = %(e)s"
        " AND contact_email IS NOT NULL",
        {"e": ent["id"]},
    ):
        domaine = r["email"].rpartition("@")[2].lower()
        if domaine and not est_generique_ou_ats(domaine):
            sures.append(domaine_racine(domaine))
    devinees = []
    nom = ent.get("nom_affiche") or ent.get("nom_norm") or ""
    for s in slugs(nom)[:1]:
        if "-" not in s and len(s) >= 3:
            devinees += [f"{s}.fr", f"{s}.com"]
    vus, sites = set(), []
    for site, devinee in [(x, False) for x in sures if x] + [(x, True) for x in devinees]:
        try:
            base = jobposting.normaliser(site)
        except ValueError:
            continue
        if base.removeprefix("https://www.") not in vus:
            vus.add(base.removeprefix("https://www."))
            sites.append((base, devinee))
    return sites[:4]


def _trouver_tableau(
    nom: str,
    ats: str | None,
    slug: str | None,
    sites: list[tuple[str, bool]] | None = None,
    indices: list[str] | None = None,
) -> Tableau | None:
    """`indices` : intitulés des offres recherchées, lus en premier sur un site carrières."""
    if ats == "site" and slug:
        t = jobposting.tableau(slug, indices=indices)
        if t is not None:
            return t
    elif ats and ats != "aucun" and slug and ats in CONNECTEURS:
        t = CONNECTEURS[ats](slug)
        if t is not None:
            return t  # site déjà identifié : on lui fait confiance
    for s in slugs(nom):
        for connecteur in CONNECTEURS.values():
            t = connecteur(s)
            if t is not None and t.postes and est_son_site(t, nom):
                return t
    # Pas d'ATS public : offres JobPosting publiées sur le site de l'employeur (2 min au plus).
    fin = time.monotonic() + BUDGET_SITES_SECONDES
    for site, devinee in sites or []:
        if time.monotonic() > fin:
            break
        t = jobposting.tableau(site, indices=indices)
        if t is None or not t.postes:
            continue
        if devinee and not t.nom_employeur:
            continue  # adresse devinée : il faut que les offres portent le nom de l'employeur
        if est_son_site(t, nom):
            return t
    return None


def verifier_adzuna(nom: str, offre: dict) -> Verdict | None:
    """Second essai : la même offre (employeur + intitulé + ville) est-elle en ligne sur Adzuna ?"""
    if not adzuna.disponible():
        return None
    for annonce in adzuna.chercher(offre["intitule"] or "", offre["ville"] or ""):
        if not adzuna.meme_employeur(nom, annonce.entreprise):
            continue
        score = similarite(offre["intitule"] or "", offre["ville"] or "", annonce.poste)
        if score >= SEUIL_TROUVEE:
            return Verdict(
                offre["id"],
                "valide",
                f"en ligne sur Adzuna (similarité {score}), fiche tronquée",
                annonce.poste,
                "",
                annonce.poste.url,
            )
    return None


def traiter_entreprise(ent: dict, offres: list[dict]) -> Resultat:
    res = _traiter_ats(ent, offres)
    if adzuna.disponible():
        nom = ent["nom_affiche"] or ent["nom_norm"]
        res.verdicts = [
            (verifier_adzuna(nom, o) or v) if v.statut is None else v
            for o, v in zip(offres, res.verdicts, strict=True)
        ]
    return res


def _traiter_ats(ent: dict, offres: list[dict]) -> Resultat:
    tableau = _trouver_tableau(
        ent["nom_affiche"] or ent["nom_norm"],
        ent["ats"],
        ent["ats_slug"],
        ent.get("sites"),
        [o["intitule"] for o in offres if o.get("intitule")],
    )
    if tableau is None:
        return Resultat(
            ent["id"],
            "aucun",
            None,
            [
                Verdict(o["id"], None, "aucun site carrières trouvé sur les ATS publics")
                for o in offres
            ],
        )
    source = f"{tableau.ats}:{tableau.slug}"
    res = Resultat(ent["id"], tableau.ats, tableau.slug)
    maintenant = datetime.now(UTC)
    for o in offres:
        intitule, ville = o["intitule"] or "", o["ville"] or ""
        postes = list(tableau.postes)
        if not tableau.complet and tableau.ats == "smartrecruiters":
            requete = " ".join(norm_intitule(intitule).split()[:4])
            postes += smartrecruiters_recherche(tableau.slug, requete)
        par_titre = sorted(
            ((similarite_titre(intitule, p), p) for p in postes), key=lambda x: -x[0]
        )
        titre_max, proche = par_titre[0] if par_titre else (0, None)
        trouve = next(
            ((s, p) for s, p in par_titre if s >= SEUIL_TROUVEE and meme_ville(ville, p)), None
        )
        if trouve:
            score, poste = trouve
            texte, url = poste.texte, poste.url
            if tableau.ats == "smartrecruiters":
                texte, url = fiche_smartrecruiters(tableau.slug, poste.url)
            res.verdicts.append(
                Verdict(
                    o["id"],
                    "valide",
                    f"trouvée sur {source} (similarité {score})",
                    poste,
                    texte,
                    url,
                )
            )
        elif (
            titre_max < SEUIL_ABSENTE
            and tableau.complet
            and len(tableau.postes) >= POSTES_MIN_POUR_EXPIREE
            and maintenant - o["recu_le"] > AGE_MIN_POUR_EXPIREE
        ):
            res.verdicts.append(
                Verdict(
                    o["id"],
                    "expiree",
                    f"absente de {source} ({len(tableau.postes)} postes ouverts)",
                )
            )
        elif proche is not None and titre_max >= SEUIL_TROUVEE:
            res.verdicts.append(
                Verdict(o["id"], None, f"même intitulé sur {source} mais à « {proche.lieu} »")
            )
        else:
            detail = f" : « {proche.titre} » ({titre_max})" if proche else ""
            res.verdicts.append(Verdict(o["id"], None, f"poste proche sur {source}{detail}"))
    return res


def _a_traiter(conn, limite: int | None, entreprise: str | None) -> list[tuple[dict, list[dict]]]:
    offres = conn.execute(
        """
        SELECT o.id, o.intitule, o.ville, o.recu_le, o.entreprise_id
        FROM offres o WHERE o.statut_validite = 'a_verifier' AND o.entreprise_id IS NOT NULL
        ORDER BY o.recu_le DESC
        """
    ).fetchall()
    par_ent: dict[int, list[dict]] = {}
    for o in offres:
        if est_data(o["intitule"] or ""):
            par_ent.setdefault(o["entreprise_id"], []).append(o)
    if not par_ent:
        return []
    ents = conn.execute(
        "SELECT id, nom_norm, nom_affiche, ats, ats_slug, ats_verifie_le, site_carrieres, site_web"
        " FROM entreprises WHERE id = ANY(%s)",
        (list(par_ent),),
    ).fetchall()
    limite_essai = datetime.now(UTC) - DELAI_NOUVEL_ESSAI
    choisies = [
        e
        for e in ents
        if not (e["ats"] == "aucun" and e["ats_verifie_le"] and e["ats_verifie_le"] > limite_essai)
        and (
            entreprise is None or compact(entreprise) in compact(e["nom_affiche"] or e["nom_norm"])
        )
    ]
    choisies.sort(
        key=lambda e: -len(par_ent[e["id"]])
    )  # les employeurs aux offres nombreuses d'abord
    lots = [(e, par_ent[e["id"]]) for e in choisies[:limite]]
    for e, _ in lots:
        e["sites"] = sites_employeur(conn, e)
    return lots


def _enregistrer(conn, res: Resultat) -> None:
    conn.execute(
        "UPDATE entreprises SET ats = %s, ats_slug = %s, ats_verifie_le = now(),"
        " site_carrieres = CASE WHEN %s = 'site' THEN %s ELSE site_carrieres END WHERE id = %s",
        (res.ats, res.slug, res.ats, res.slug, res.entreprise_id),
    )
    for v in res.verdicts:
        if v.statut == "valide":
            conn.execute(
                """
                UPDATE offres SET statut_validite = 'valide', fiche_complete = %s,
                       url_fiche = %s, texte = NULLIF(%s, ''),
                       date_publication = LEAST(COALESCE(date_publication, %s), %s),
                       verifiee_le = now(), note_validite = %s
                WHERE id = %s
                """,
                (
                    bool(v.texte),
                    v.url,
                    v.texte,
                    v.poste.publie_le,
                    v.poste.publie_le,
                    v.note,
                    v.offre_id,
                ),
            )
        else:
            conn.execute(
                "UPDATE offres SET statut_validite = COALESCE(%s, statut_validite),"
                " verifiee_le = now(), note_validite = %s WHERE id = %s",
                (v.statut, v.note, v.offre_id),
            )


def executer(
    limite: int | None = None, entreprise: str | None = None, refaire: bool = False
) -> dict[str, int]:
    """Cherche la fiche des offres à vérifier ; renvoie le bilan (utilisé par l'orchestrateur)."""
    with connexion() as conn:
        if refaire:
            n = conn.execute(
                """
                UPDATE offres SET statut_validite = 'a_verifier', fiche_complete = FALSE,
                       url_fiche = NULL, texte = NULL, note_validite = NULL, verifiee_le = NULL
                WHERE verifiee_le IS NOT NULL AND statut_validite IN ('valide', 'expiree', 'ancienne',
                                                                     'a_verifier')
                """
            ).rowcount
            conn.execute(
                "UPDATE entreprises SET ats = NULL, ats_slug = NULL, ats_verifie_le = NULL"
            )
            marquer_anciennes(conn)
            print(f"{n} offres remises à vérifier.")
        lots = _a_traiter(conn, limite, entreprise)
    print(
        f"{len(lots)} employeurs à vérifier ({sum(len(o) for _, o in lots)} offres data)."
        + ("" if adzuna.disponible() else " Adzuna inactif : clés absentes de .env.")
    )

    bilan: dict[str, int] = {}
    with connexion() as conn, ThreadPoolExecutor(max_workers=6) as pool:
        futurs = {pool.submit(traiter_entreprise, e, o): e for e, o in lots}
        for i, futur in enumerate(as_completed(futurs), 1):
            ent = futurs[futur]
            try:
                res = futur.result()
            except Exception as e:  # noqa: BLE001 — un employeur en erreur ne bloque pas les autres
                print(f"  ! {ent['nom_affiche']} : {type(e).__name__}: {e}")
                continue
            with conn.transaction():
                _enregistrer(conn, res)
            for v in res.verdicts:
                cle = v.statut or ("sans ATS" if res.ats == "aucun" else "incertaine")
                if v.statut == "valide" and "Adzuna" in v.note:
                    cle = "valide (Adzuna)"
                bilan[cle] = bilan.get(cle, 0) + 1
            if res.ats != "aucun":
                trouvees = sum(v.statut == "valide" for v in res.verdicts)
                print(
                    f"  [{i}/{len(lots)}] {ent['nom_affiche']:<30} {res.ats}:{res.slug}"
                    f"  {trouvees}/{len(res.verdicts)} offres trouvées"
                )
        anciennes = marquer_anciennes(conn)  # les vraies dates de publication peuvent en vieillir
        conn.execute(
            "INSERT INTO executions (tache, fin, offres_vues, rejetees, details)"
            " VALUES ('fiches', now(), %s, %s, %s)",
            (sum(bilan.values()), bilan.get("expiree", 0), Jsonb(bilan)),
        )
    print(
        "Bilan :",
        ", ".join(f"{k} {n}" for k, n in sorted(bilan.items())),
        f"| {anciennes} passées en « ancienne » d'après la vraie date de publication.",
    )
    return bilan


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limite", type=int, help="nombre maximum d'employeurs à traiter")
    p.add_argument("--entreprise", help="ne traiter que cet employeur (nom approché)")
    p.add_argument(
        "--refaire",
        action="store_true",
        help="oublie les sites trouvés et les verdicts précédents, puis recommence",
    )
    args = p.parse_args()
    from app.orchestrateur import VERROU

    # Même verrou que l'orchestrateur : jamais deux recherches de fiches en même temps.
    with connexion() as verrou:
        if not verrou.execute("SELECT pg_try_advisory_lock(%s) AS ok", (VERROU,)).fetchone()["ok"]:
            raise SystemExit("Un passage des agents est en cours : réessaie quand il sera terminé.")
        try:
            executer(args.limite, args.entreprise, args.refaire)
        finally:
            verrou.execute("SELECT pg_advisory_unlock(%s)", (VERROU,))
