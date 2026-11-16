"""Récap du jour par mail à toi-même : ce qui est parti, ce qui attend, ce qui a coincé.

Envoyé une fois par jour, au premier passage après `HEURE` (20 h, heure de Paris), depuis ta
boîte d'envoi vers elle-même (aucun recruteur). Le jour du dernier récap est noté dans
`parametres.envoi.recap_le`.

Usage :  python -m app.recap [--forcer]
"""

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app import criteres, envoi

PARIS = ZoneInfo("Europe/Paris")
HEURE = 20
TYPES = {"candidature": "Candidature", "spontanee": "Spontanée", "relance": "Relance"}


def _heure(t: datetime | None) -> str:
    return t.astimezone(PARIS).strftime("%H:%M") if t else "--:--"


def contenu(conn: psycopg.Connection, jour: date) -> tuple[str, str]:
    """(objet, texte) du récap de `jour` (heure de Paris)."""
    debut = datetime(jour.year, jour.month, jour.day, tzinfo=PARIS)
    fin = debut + timedelta(days=1)
    envois = conn.execute(
        "SELECT * FROM v_envois WHERE cree_le >= %s AND cree_le < %s ORDER BY cree_le",
        (debut, fin),
    ).fetchall()
    partis = [e for e in envois if e["statut"] == "envoye"]
    erreurs = [e for e in envois if e["statut"] == "erreur"]
    pretes = conn.execute(
        "SELECT entreprise, intitule, destinataire, depart_le FROM v_candidatures_auto"
        " WHERE statut = 'pret' ORDER BY depart_le"
    ).fetchall()
    cote = conn.execute(
        "SELECT entreprise, intitule, motif FROM v_candidatures_auto"
        " WHERE statut = 'mise_de_cote' AND cree_le >= %s AND cree_le < %s ORDER BY cree_le",
        (debut, fin),
    ).fetchall()
    sur_site = conn.execute(
        "SELECT entreprise, intitule, coalesce(kit->>'lien', url_fiche, url_cle) AS lien, cree_le"
        " FROM v_candidatures_auto WHERE statut = 'mise_de_cote'"
        " AND (kit IS NOT NULL OR motif LIKE 'aucun contact publié%%') ORDER BY cree_le"
    ).fetchall()
    attente = conn.execute(
        "SELECT count(*) AS n, count(*) FILTER (WHERE NOT fiche_complete"
        " AND envoi_auto_note LIKE 'annonce complète introuvable%%') AS a_coller"
        " FROM offres WHERE envoi_auto_demande_le IS NOT NULL"
    ).fetchone()

    lignes = [f"Récap EMploi_App du {jour:%d/%m/%Y}", ""]
    lignes.append(f"PARTIS AUJOURD'HUI ({len(partis)})")
    for e in partis:
        qui = " — ".join(x for x in (e["entreprise"], e["poste"]) if x) or "?"
        pj = f" [{', '.join(e['pieces'])}]" if e["pieces"] else ""
        mode = "auto" if e["automatique"] else "par toi"
        copie = ", copie reçue" if e["copie_a"] else ""
        lignes.append(
            f"- {_heure(e['envoye_le'])} · {TYPES.get(e['type'], e['type'])} · {qui}"
            f" → {e['destinataire']} ({mode}{copie}){pj}"
        )
    if not partis:
        lignes.append("- rien n'est parti aujourd'hui")
    if pretes:
        lignes += ["", f"PRÊTES À PARTIR ({len(pretes)}) — bloquables dans l'onglet Envois auto"]
        lignes += [
            f"- départ {_heure(p['depart_le'])} · {p['entreprise'] or '?'} — {p['intitule']}"
            f" → {p['destinataire']}"
            for p in pretes
        ]
    if sur_site:
        lignes += [
            "",
            (
                f"À POSTULER SUR LEUR SITE ({len(sur_site)}) — onglet Envois auto, bouton"
                " « Postuler sur le site » puis ton marque-page « Remplir »"
            ),
        ]
        lignes += [
            f"- {x['entreprise'] or '?'} — {x['intitule']} (depuis le {x['cree_le']:%d/%m})"
            + (f" : {x['lien']}" if x["lien"] else "")
            for x in sur_site
        ]
    cote = [c for c in cote if not (c["motif"] or "").startswith("aucun contact publié")]
    if cote:
        lignes += ["", f"MISES DE CÔTÉ POUR TOI AUJOURD'HUI ({len(cote)})"]
        lignes += [f"- {c['entreprise'] or '?'} — {c['intitule']} : {c['motif']}" for c in cote]
    if attente["n"]:
        lignes += [
            "",
            f"DEMANDES « ENVOI AUTO » EN ATTENTE : {attente['n']}"
            + (f" (dont {attente['a_coller']} annonce(s) à coller)" if attente["a_coller"] else ""),
        ]
    if erreurs:
        lignes += ["", f"ERREURS D'ENVOI ({len(erreurs)})"]
        lignes += [
            f"- {_heure(e['cree_le'])} · {e['entreprise'] or '?'} → {e['destinataire']} :"
            f" {(e['erreur'] or '')[:160]}"
            for e in erreurs
        ]
    lignes += ["", "Chaque envoi automatique t'arrive aussi en copie cachée dans ta boîte."]
    objet = (
        f"Récap du {jour:%d/%m} : {len(partis)} envoi(s)"
        + (f", {len(sur_site)} à postuler sur leur site" if sur_site else "")
        + (f", {len(erreurs)} erreur(s)" if erreurs else "")
    )
    return objet, "\n".join(lignes)


def executer(conn: psycopg.Connection, forcer: bool = False) -> dict:
    maintenant = datetime.now(PARIS)
    params = criteres.lire(conn)["envoi"]
    jour = maintenant.date()
    if not forcer:
        if maintenant.hour < HEURE:
            return {"note": f"récap après {HEURE} h"}
        if params.get("recap_le") == jour.isoformat():
            return {"note": "récap déjà envoyé aujourd'hui"}
    objet, texte = contenu(conn, jour)
    if not envoi.prevenir_moi(conn, objet, texte):
        return {"note": "envoi impossible (Gmail non autorisé ?)"}
    criteres.ecrire(conn, "envoi", {**criteres.lire(conn)["envoi"], "recap_le": jour.isoformat()})
    return {"envoyé": objet}


def main() -> None:
    from app.db import connexion

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--forcer", action="store_true", help="envoie le récap tout de suite")
    args = p.parse_args()
    with connexion() as conn:
        print(executer(conn, args.forcer))


if __name__ == "__main__":
    main()
