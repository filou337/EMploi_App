"""Parseur des alertes LinkedIn, sur une alerte synthétique (et sur les vrais mails si présents)."""

import email
from email import policy
from pathlib import Path

import pytest

from app.collecte.parseurs.linkedin import extraire

RACINE = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_alerte.eml"


def _lire(chemin: Path):
    return email.message_from_bytes(chemin.read_bytes(), policy=policy.default)


@pytest.fixture(scope="module")
def offres():
    return extraire(_lire(FIXTURE))


def test_trois_offres_sans_doublon(offres):
    assert [o.url_cle for o in offres] == [
        "https://www.linkedin.com/jobs/view/1111111111/",
        "https://www.linkedin.com/jobs/view/2222222222/",
        "https://www.linkedin.com/jobs/view/3333333333/",
    ]


def test_champs_de_la_fiche(offres):
    acme = offres[0]
    assert (acme.intitule, acme.entreprise, acme.lieu) == (
        "Data Analyst (F/H)",
        "Acme SAS",
        "Ville de Paris",
    )
    assert acme.details == ("Cette entreprise recrute activement",)
    assert acme.source == "linkedin"


def test_mentions_annexes(offres):
    assert offres[1].details == ("1 relation",)  # espace insécable normalisé


def test_fiche_apres_titre_de_section(offres):
    """Le titre « Offres d'emploi : … » d'une autre alerte n'est pas pris pour l'intitulé."""
    initech = offres[2]
    assert (initech.intitule, initech.entreprise, initech.lieu) == (
        "Data Engineer - Stage",
        "Initech",
        "Île-de-France, France",
    )


def test_pied_de_mail_ignore(offres):
    assert all("destiné à" not in o.extrait for o in offres)


def _mail(corps: str):
    entete = "Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n"
    return email.message_from_bytes((entete + corps).encode(), policy=policy.default)


def test_mail_sans_offre():
    assert extraire(_mail("Souhaitez-vous toujours recevoir des alertes Emploi ?\n")) == []


def test_format_inconnu_signale():
    with pytest.raises(ValueError, match="format"):
        extraire(_mail("Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/42/\n"))


ECHANTILLONS = sorted((RACINE / "echantillons" / "linkedin").glob("*.eml"))


@pytest.mark.skipif(
    not ECHANTILLONS, reason="pas d'échantillons locaux (python -m app.collecte.echantillons)"
)
@pytest.mark.parametrize("chemin", ECHANTILLONS, ids=lambda p: p.stem)
def test_vrais_mails(chemin):
    """Chaque lien d'offre du mail donne une offre avec intitulé, entreprise et lieu."""
    message = _lire(chemin)
    texte = message.get_body(preferencelist=("plain",)).get_content()
    ids_attendus = set(__import__("re").findall(r"/jobs/view/(\d+)", texte))
    offres = extraire(message)
    assert {o.url_cle.split("/")[-2] for o in offres} == ids_attendus
    for o in offres:
        assert o.intitule and o.entreprise and o.lieu
        assert "http" not in o.extrait
