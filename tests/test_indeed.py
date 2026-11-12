"""Parseur Indeed : résumé d'alerte et suggestion à redirecteur compressé."""

import base64
import email
import gzip
from datetime import date
from email import policy
from pathlib import Path

import pytest

from app.collecte.parseurs.indeed import extraire, jk_du_redirecteur

RACINE = Path(__file__).resolve().parent.parent


def _mail(corps: str, date_mail: str = "Wed, 11 Nov 2026 09:00:00 +0000"):
    entete = (
        "From: Indeed <donotreply@jobalert.indeed.com>\n"
        f"Date: {date_mail}\nContent-Type: text/plain; charset=utf-8\n"
        "Content-Transfer-Encoding: 8bit\n\n"
    )
    return email.message_from_bytes((entete + corps).encode(), policy=policy.default)


RESUME = """Alerte Emploi Indeed
20 nouveaux emplois analyste - Paris (75)
Emplois 1-20 sur 20 nouveaux emplois
Voir les résultats correspondants sur Indeed : https://fr.indeed.com/jobs?q=analyste
Data Quality Analyst - CDI - F/H/X
Acme - Saint-Denis (93)
Candidature simplifiée
Au sein de l'équipe data quality, vous serez en charge de…
Publié à l'instant
https://fr.indeed.com/rc/clk/dl?jk=dce7cbbad021c4a2&from=ja&tk=abc
Stage - Data Analyste H/F
Caisse d'Epargne Ile de France - Paris (75)
De 1 200 € à 1 500 € par mois
Mise en place d'une base de données sur Alteryx…
il y a 3 jours
https://fr.indeed.com/rc/clk/dl?jk=2f5ce4fe03b65608&from=ja
Data Quality Analyst - CDI - F/H/X
Acme - Saint-Denis (93)
Doublon dans le même mail.
il y a 1 jour
https://fr.indeed.com/rc/clk/dl?jk=dce7cbbad021c4a2&from=ja
Ne partagez pas cet email
Gérer les alertes Emploi: https://fr.indeed.com/alerts
"""


def test_resume():
    offres = extraire(_mail(RESUME))
    assert [o.url_cle for o in offres] == [
        "https://fr.indeed.com/viewjob?jk=dce7cbbad021c4a2",
        "https://fr.indeed.com/viewjob?jk=2f5ce4fe03b65608",
    ]
    acme, cepa = offres
    assert (acme.intitule, acme.entreprise, acme.lieu) == (
        "Data Quality Analyst - CDI - F/H/X",
        "Acme",
        "Saint-Denis (93)",
    )
    assert acme.publiee_le == date(2026, 11, 11)
    assert cepa.entreprise == "Caisse d'Epargne Ile de France"  # tiret final seulement
    assert cepa.publiee_le == date(2026, 11, 8)
    assert "De 1 200 € à 1 500 € par mois" in cepa.details


def _redirecteur(jk: str) -> str:
    charge = gzip.compress(f'{{"u":"https://fr.indeed.com/viewjob?jk={jk}&from=match"}}'.encode())
    return "https://cts.indeed.com/v3/" + base64.urlsafe_b64encode(charge).decode().rstrip("=")


def test_jk_du_redirecteur():
    assert jk_du_redirecteur(_redirecteur("3af94bb8239db59a")) == "3af94bb8239db59a"
    assert jk_du_redirecteur("https://cts.indeed.com/v3/pas-du-gzip") is None


def test_suggestion():
    corps = (
        "Bonjour Jean,\n"
        "Votre parcours pourrait correspondre pour l'offre d'emploi suivante : Analyste Power BI.\n"
        "Alternance - Analyste Power BI F/H\nBouygues Construction\n78280 Guyancourt\n"
        "Type de poste: Temps plein\n"
        f"Voir l'emploi: {_redirecteur('3af94bb8239db59a')}\n"
        f"Postuler maintenant: {_redirecteur('3af94bb8239db59a')}\n"
    )
    (offre,) = extraire(_mail(corps))
    assert offre.url_cle == "https://fr.indeed.com/viewjob?jk=3af94bb8239db59a"
    assert (offre.intitule, offre.entreprise, offre.lieu) == (
        "Alternance - Analyste Power BI F/H",
        "Bouygues Construction",
        "78280 Guyancourt",
    )


ECHANTILLONS = sorted((RACINE / "echantillons" / "indeed").glob("*.eml"))


@pytest.mark.skipif(not ECHANTILLONS, reason="pas d'échantillons locaux")
@pytest.mark.parametrize("chemin", ECHANTILLONS, ids=lambda p: p.stem)
def test_vrais_mails(chemin):
    offres = extraire(email.message_from_bytes(chemin.read_bytes(), policy=policy.default))
    assert offres
    for o in offres:
        assert o.intitule and o.entreprise and o.lieu
        assert "http" not in o.extrait


def test_suggestion_sponsorisee():
    charge = gzip.compress(b'{"u":"https://fr.indeed.com/pagead/clk?ad=abc&from=email"}')
    lien = "https://cts.indeed.com/v3/" + base64.urlsafe_b64encode(charge).decode().rstrip("=")
    corps = (
        "Bonjour Jean,\nVotre parcours pourrait correspondre pour l'offre suivante : X.\n"
        "Chef de Projet IA & Data (H/F)\nGalileo Global Education\n75011 Paris\n"
        f"Type de poste: Contrat d'apprentissage\nVoir l'emploi: {lien}\n"
    )
    (offre,) = extraire(_mail(corps))
    assert offre.url_cle.startswith("indeed:sponsorise:")
    assert offre.details[0] == "Annonce sponsorisée"
    (encore,) = extraire(_mail(corps))
    assert encore.url_cle == offre.url_cle  # même annonce, même clé
