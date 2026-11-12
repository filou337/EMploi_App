"""Sauvegarde quelques mails d'alerte bruts par source, pour écrire et tester les parseurs.

Usage :  python -m app.collecte.echantillons [--jours 30] [--par-source 5]
Les fichiers .eml vont dans echantillons/<source>/ (ignoré par git : données personnelles).
"""

import argparse
import time

from app.collecte import gmail
from app.collecte.sources import est_alerte, requete_gmail, source_depuis_expediteur
from app.config import RACINE

DOSSIER = RACINE / "echantillons"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--jours", type=int, default=30)
    p.add_argument("--par-source", type=int, default=5)
    args = p.parse_args()

    svc = gmail.service()
    ids = list(gmail.ids_messages(svc, requete_gmail(args.jours)))
    par_source: dict[str, int] = {}
    for m in gmail.entetes(svc, ids):  # du plus récent au plus ancien
        if not est_alerte(m["From"]):
            continue
        source = source_depuis_expediteur(m["From"])
        if par_source.get(source, 0) >= args.par_source:
            continue
        dossier = DOSSIER / source
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / f"{m['id']}.eml").write_bytes(gmail.message_brut(svc, m["id"]))
        par_source[source] = par_source.get(source, 0) + 1
        time.sleep(0.2)

    for source, n in sorted(par_source.items()):
        print(f"{source:<16}{n:>3} mails -> {DOSSIER / source}")


if __name__ == "__main__":
    main()
