from email.message import EmailMessage

import pytest

from app.collecte.parseurs import parseur
from app.collecte.parseurs.wttj import extraire, ville

BASE = "https://www.welcometothejungle.com/fr/companies"

# Reproduit la structure des « jobs du mois » (texte brut) : entreprise en gras + intitulé,
# ou intitulé seul en gras ; deux fiches parfois collées sur la même ligne.
SELECTION = f"""[Profil](https://www.welcometothejungle.com/fr/me/profile)
**Bonjour Camille,**** **
Chaque mois, nous sélectionnons 6 jobs qui méritent votre attention.
🔵  SÉLECTION DU MOMENT
----------------------
Les jobs du mois
----------------
================
----------------
**Acme Data**
Data Analyst junior
[ **Voir l'offre**]({BASE}/acme-data/jobs/data-analyst-junior-h-f_paris "voir l'offre")
**Globex**
Analytics Engineer
[ **Voir l'offre**]({BASE}/globex/jobs/analytics-engineer_paris-la-defense_GLOBX_a1B2c3 "voir l'offre") **Initech**
Data Engineer (CDI)
[ **Voir l'offre**]({BASE}/initech/jobs/data-engineer-cdi_lille?utm_source=brevo "voir l'offre")
**Consultant BI**
[ **Voir l'offre**]({BASE}/hooli-conseil/jobs/consultant-bi_levallois-perret "voir l'offre")
**Acme Data**
Data Analyst junior
[ **Voir l'offre**]({BASE}/acme-data/jobs/data-analyst-junior-h-f_paris "voir l'offre")
[ **Affiner mes préférences**](https://www.welcometothejungle.com/en/jobs-matches "Mon profil")
"""

ENTREPRISES_DU_MOIS = f"""Les entreprises du mois
Cabinet de conseil spécialisé en cybersécurité
[ **Voir les offres**]({BASE}-v1/cyberlift/jobs?page=1 "voir les offres")
"""


def _mail(corps: str) -> EmailMessage:
    m = EmailMessage()
    m["From"] = "Welcome to the Jungle <marketing-campaigns@welcometothejungle.com>"
    m["Subject"] = "Les dernières opportunités Tech à découvrir"
    m.set_content(corps)
    return m


def test_parseur_enregistre():
    assert parseur("wttj") is extraire


def test_selection_du_mois():
    offres = extraire(_mail(SELECTION))
    assert [(o.entreprise, o.intitule, o.lieu) for o in offres] == [
        ("Acme Data", "Data Analyst junior", "Paris"),
        ("Globex", "Analytics Engineer", "Paris la Defense"),
        ("Initech", "Data Engineer (CDI)", "Lille"),
        ("Hooli Conseil", "Consultant BI", "Levallois Perret"),  # entreprise tirée du lien
    ]
    assert offres[2].url_cle == f"{BASE}/initech/jobs/data-engineer-cdi_lille"  # sans utm
    assert all(o.source == "wttj" for o in offres)


def test_campagne_sans_offre():
    assert extraire(_mail(ENTREPRISES_DU_MOIS)) == []
    assert extraire(_mail("Ce qui fait vraiment peur au travail...")) == []


def test_format_change_signale():
    with pytest.raises(ValueError):
        extraire(_mail(f"[ **Voir l'offre**]({BASE}/x/jobs/y_paris)"))


@pytest.mark.parametrize(
    ("slug", "attendu"),
    [
        ("data-engineer_paris", "Paris"),
        ("pmo_paris-la-defense_SII_x", "Paris la Defense"),
        ("sans-ville", ""),
    ],
)
def test_ville(slug, attendu):
    assert ville(slug) == attendu
