"""Inventaire des mails d'alerte : combien par source et par expéditeur.

Usage :  python -m app.collecte.inventaire [--jours 30] [--sujets]
Lecture seule : seuls les en-têtes (expéditeur, objet, date) sont lus.
"""

import argparse
from collections import Counter, defaultdict
from email.utils import parseaddr

from app.collecte import gmail
from app.collecte.sources import requete_gmail, source_depuis_expediteur


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--jours", type=int, default=30)
    p.add_argument("--sujets", action="store_true", help="affiche 3 objets par expéditeur")
    args = p.parse_args()

    svc = gmail.service()
    requete = requete_gmail(args.jours)
    ids = list(gmail.ids_messages(svc, requete))
    print(f"{len(ids)} mails trouvés sur {args.jours} jours.\n")
    if not ids:
        return

    par_expediteur: Counter[tuple[str, str]] = Counter()
    sujets: dict[str, list[str]] = defaultdict(list)
    for m in gmail.entetes(svc, ids):
        adresse = parseaddr(m["From"])[1].lower()
        source = source_depuis_expediteur(m["From"]) or "?"
        par_expediteur[(source, adresse)] += 1
        if len(sujets[adresse]) < 3:
            sujets[adresse].append(m["Subject"])

    par_source = Counter()
    for (source, _), n in par_expediteur.items():
        par_source[source] += n

    print(f"{'SOURCE':<16}{'MAILS':>6}")
    for source, n in par_source.most_common():
        print(f"{source:<16}{n:>6}")

    print(f"\n{'SOURCE':<16}{'MAILS':>6}  EXPÉDITEUR")
    for (source, adresse), n in sorted(par_expediteur.items(), key=lambda x: (x[0][0], -x[1])):
        print(f"{source:<16}{n:>6}  {adresse}")
        if args.sujets:
            for s in sujets[adresse]:
                print(f"{'':<24}· {s[:90]}")


if __name__ == "__main__":
    main()
