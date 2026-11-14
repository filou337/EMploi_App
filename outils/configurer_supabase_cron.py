"""Passage des agents 24 h/24 : Supabase déclenche GitHub Actions toutes les 30 minutes.

La planification de GitHub (cron du workflow) démarre mal ; ici, c'est la base Supabase qui appelle
l'API GitHub « lancer le workflow » (pg_cron + pg_net, inclus dans l'offre gratuite).

Prérequis : un jeton GitHub « fine-grained » limité au dépôt EMploi_App, droit « Actions : Read and
write » seulement, dans .env : GITHUB_DISPATCH_TOKEN=github_pat_…  (jamais versionné).
Le jeton est rangé CHIFFRÉ dans le coffre de Supabase (Vault) et n'est jamais affiché.

Utilisation :
  .venv/bin/python outils/configurer_supabase_cron.py            # installe et teste
  .venv/bin/python outils/configurer_supabase_cron.py --etat     # dernière exécution et réponse de GitHub
  .venv/bin/python outils/configurer_supabase_cron.py --retirer  # supprime la tâche
"""

import argparse
import sys
import time
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

RACINE = Path(__file__).resolve().parent.parent
DEPOT = "filou337/EMploi_App"
WORKFLOW = "agents.yml"
TACHE = "emploi-agents-30min"
SECRET = "github_dispatch_token"
FREQUENCE = "*/30 * * * *"

APPEL = f"""
SELECT net.http_post(
  url := 'https://api.github.com/repos/{DEPOT}/actions/workflows/{WORKFLOW}/dispatches',
  headers := jsonb_build_object(
    'Authorization', 'Bearer ' || (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = '{SECRET}'),
    'Accept', 'application/vnd.github+json',
    'X-GitHub-Api-Version', '2022-11-28',
    'User-Agent', 'emploi-app-supabase-cron'),
  body := jsonb_build_object('ref', 'main'),
  timeout_milliseconds := 10000
);
"""


def connexion() -> psycopg.Connection:
    env = dotenv_values(RACINE / ".env")
    url = env.get("DATABASE_URL") or ""
    if "supabase" not in url:
        sys.exit("DATABASE_URL ne pointe pas vers Supabase : rien n'est fait.")
    return psycopg.connect(url, row_factory=dict_row, autocommit=True)


def installer(c: psycopg.Connection) -> None:
    jeton = (dotenv_values(RACINE / ".env").get("GITHUB_DISPATCH_TOKEN") or "").strip()
    if not jeton.startswith(("github_pat_", "ghp_")):
        sys.exit(
            "Ajoute GITHUB_DISPATCH_TOKEN=github_pat_… dans .env (voir l'en-tête de ce script)."
        )
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions")
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_cron")
    existe = c.execute("SELECT id FROM vault.secrets WHERE name = %s", (SECRET,)).fetchone()
    if existe:
        c.execute("SELECT vault.update_secret(%s, %s)", (existe["id"], jeton))
    else:
        c.execute(
            "SELECT vault.create_secret(%s, %s, %s)", (jeton, SECRET, "Lancer le workflow agents")
        )
    print("✓ jeton GitHub rangé chiffré dans le coffre Supabase (Vault)")
    if c.execute("SELECT 1 FROM cron.job WHERE jobname = %s", (TACHE,)).fetchone():
        c.execute("SELECT cron.unschedule(%s)", (TACHE,))
    c.execute("SELECT cron.schedule(%s, %s, %s)", (TACHE, FREQUENCE, APPEL))
    print(f"✓ tâche « {TACHE} » planifiée : {FREQUENCE} (heure UTC)")
    tester(c)


def tester(c: psycopg.Connection) -> None:
    rid = c.execute(APPEL).fetchone()["http_post"]
    for _ in range(20):
        time.sleep(1)
        r = c.execute(
            "SELECT status_code, left(content, 200) AS contenu, error_msg FROM net._http_response"
            " WHERE id = %s",
            (rid,),
        ).fetchone()
        if r:
            if r["status_code"] == 204:
                print(
                    "✓ test : GitHub a accepté le lancement (un passage démarre, voir l'onglet Actions)"
                )
            else:
                print(
                    f"✗ test : réponse {r['status_code']} {r['error_msg'] or ''} {r['contenu'] or ''}"
                )
                print("  Vérifie le jeton : dépôt EMploi_App, droit « Actions : Read and write ».")
            return
    print("? test : pas encore de réponse de GitHub (réessaie avec --etat dans une minute)")


def etat(c: psycopg.Connection) -> None:
    job = c.execute(
        "SELECT jobid, schedule, active FROM cron.job WHERE jobname = %s", (TACHE,)
    ).fetchone()
    if not job:
        print("Aucune tâche installée.")
        return
    print(f"Tâche : {job['schedule']} · active : {job['active']}")
    for r in c.execute(
        "SELECT start_time, status, return_message FROM cron.job_run_details WHERE jobid = %s"
        " ORDER BY start_time DESC LIMIT 5",
        (job["jobid"],),
    ):
        print(
            f"  {r['start_time']:%d/%m %H:%M} UTC · {r['status']} · {(r['return_message'] or '')[:80]}"
        )
    for r in c.execute(
        "SELECT created, status_code, error_msg FROM net._http_response ORDER BY created DESC LIMIT 3"
    ):
        print(
            f"  réponse GitHub {r['created']:%d/%m %H:%M} UTC : {r['status_code']} {r['error_msg'] or ''}"
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--etat", action="store_true")
    p.add_argument("--retirer", action="store_true")
    a = p.parse_args()
    with connexion() as c:
        if a.retirer:
            if c.execute("SELECT 1 FROM cron.job WHERE jobname = %s", (TACHE,)).fetchone():
                c.execute("SELECT cron.unschedule(%s)", (TACHE,))
            print("Tâche retirée.")
        elif a.etat:
            etat(c)
        else:
            installer(c)


if __name__ == "__main__":
    main()
