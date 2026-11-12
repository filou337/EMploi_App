"""Vérifie la sécurité Supabase (RLS) comme l'API la vit, sans rien modifier.

Usage :  python outils/verifier_rls.py

Pour chaque compte de Authentication → Users, et pour un visiteur anonyme, le script se met
dans la peau de l'API (rôle `authenticated` ou `anon` + identité du jeton) et essaie de lire
les 11 tables, les vues et les fonctions, puis de créer et modifier des données.
Tout se passe dans une transaction ANNULÉE à la fin : aucune donnée n'est touchée.
La RLS n'est jamais désactivée.
"""

import json
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

RACINE = Path(__file__).resolve().parent.parent
TABLES = (
    "entreprises",
    "offres",
    "candidatures",
    "reponses",
    "contacts",
    "executions",
    "documents",
    "analyses",
    "veille_entreprises",
    "parametres",
    "notifications",
    "demandes",
    "fabrications",
    "dossier_fichiers",
    "rapports",
    "spontanees",
    "envois",
    "candidatures_auto",
)
VUES = (
    "v_offres",
    "v_offre_detail",
    "v_candidatures",
    "v_reponses",
    "v_documents",
    "v_analyses",
    "v_veille_entreprises",
    "v_journal",
    "v_fabrications",
    "v_relances",
    "v_spontanees",
    "v_candidatures_auto",
)


def en_tant_que(c: psycopg.Connection, uid: str | None) -> None:
    if uid is None:
        c.execute("SET LOCAL ROLE anon")
        c.execute(
            "SELECT set_config('request.jwt.claims', %s, true)", (json.dumps({"role": "anon"}),)
        )
    else:
        c.execute("SET LOCAL ROLE authenticated")
        c.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": uid, "role": "authenticated"}),),
        )


def essai(c: psycopg.Connection, sql: str, params=()):
    """Exécute dans un point de sauvegarde ; renvoie (ok, valeur ou message d'erreur)."""
    try:
        with c.transaction():
            cur = c.execute(sql, params)
            ligne = cur.fetchone() if cur.description else None
            return True, (ligne[0] if ligne else cur.rowcount)
    except psycopg.Error as e:
        return False, type(e).__name__


def audit(c: psycopg.Connection, uid: str | None, cible: dict) -> dict:
    r: dict = {}
    for t in TABLES + VUES:
        r[t] = essai(c, f"SELECT count(*) FROM {t}")
    r["candidatures non ignorées"] = essai(
        c, "SELECT count(*) FROM candidatures WHERE statut <> 'ignore'"
    )
    r["contenu du CV lisible"] = essai(
        c, "SELECT count(*) FROM documents WHERE octet_length(contenu) > 0"
    )
    r["resume() offres"] = essai(c, "SELECT (resume()->'offres'->>'total')::int")
    ok_o, offre = essai(c, "SELECT min(id) FROM offres WHERE etape = 'reperee'")
    ok_c, cand = essai(c, "SELECT min(id) FROM candidatures")
    offre, cand = (offre if ok_o else None), (cand if ok_c else None)
    r["création : ajouter_candidature"] = essai(
        c,
        "SELECT ajouter_candidature(%s::jsonb)",
        (json.dumps({"entreprise": "Test RLS Entreprise", "poste": "Contrôle d'accès"}),),
    )
    r["création : demande au PC"] = essai(
        c, "INSERT INTO demandes (type) VALUES ('exporter_excel') RETURNING id"
    )
    r["création : notification"] = essai(
        c, "INSERT INTO notifications (type, titre) VALUES ('test', 'RLS') RETURNING id"
    )
    r["modification : notes candidature"] = (
        essai(c, "UPDATE candidatures SET notes = coalesce(notes, '') WHERE id = %s", (cand,))
        if cand
        else essai(c, "UPDATE candidatures SET notes = notes")
    )
    r["modification : étape d'une offre"] = essai(
        c, "SELECT changer_etape(%s, 'qualifiee')", (offre or cible["offre"],)
    )
    r["attaque : « J'ai postulé » sur une offre du propriétaire"] = essai(
        c, "SELECT j_ai_postule(%s)", (cible["offre"],)
    )
    r["attaque : modifier une candidature du propriétaire"] = essai(
        c, "UPDATE candidatures SET statut = 'refus' WHERE id = %s", (cible["candidature"],)
    )
    r["attaque : supprimer une offre du propriétaire"] = essai(
        c, "DELETE FROM offres WHERE id = %s", (cible["offre"],)
    )
    r["modification : paramètres"] = essai(c, "UPDATE parametres SET maj_le = now()")
    r["lecture : table proprietaire"] = essai(c, "SELECT count(*) FROM proprietaire")
    r["modification : se déclarer propriétaire"] = essai(
        c, "INSERT INTO proprietaire VALUES (%s)", (uid or "00000000-0000-0000-0000-000000000000",)
    )
    return r


def main() -> None:
    url = (dotenv_values(RACINE / ".env").get("SUPABASE_DATABASE_URL") or "").strip()
    if not url.startswith("postgresql://"):
        sys.exit("SUPABASE_DATABASE_URL absente ou invalide dans .env")
    with psycopg.connect(url) as c:
        proprio = {r[0] for r in c.execute("SELECT user_id::text FROM proprietaire")}
        # Une vraie offre et une vraie candidature du propriétaire, visées directement par les autres.
        cible = {
            "offre": c.execute("SELECT min(id) FROM offres WHERE etape = 'reperee'").fetchone()[0],
            "candidature": c.execute("SELECT min(id) FROM candidatures").fetchone()[0],
        }
        comptes = c.execute("SELECT id::text, email FROM auth.users ORDER BY created_at").fetchall()
        identites = [
            (
                f"{e.split('@')[0][:4]}…@{e.split('@')[1]}"
                + (" (PROPRIÉTAIRE)" if u in proprio else " (autre compte)"),
                u,
            )
            for u, e in comptes
        ]
        identites.append(("visiteur anonyme (clé publique)", None))
        resultats = {}
        for nom, uid in identites:
            with c.transaction(force_rollback=True):
                en_tant_que(c, uid)
                resultats[nom] = audit(c, uid, cible)
    noms = list(resultats)
    lignes = list(next(iter(resultats.values())))
    larg = 40
    print(f"{'contrôle':<{larg}}" + "".join(f"{n[:30]:>32}" for n in noms))
    for ligne in lignes:
        cellules = []
        for n in noms:
            ok, val = resultats[n][ligne]
            cellules.append(f"{(str(val) if ok else '✗ ' + val)[:30]:>32}")
        print(f"{ligne:<{larg}}" + "".join(cellules))
    print("\nTransaction annulée : aucune donnée modifiée.")


if __name__ == "__main__":
    main()
