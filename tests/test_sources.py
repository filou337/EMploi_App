"""Rattachement des expéditeurs aux sources (sans réseau)."""

import pytest

from app.collecte.sources import domaine, est_alerte, requete_gmail, source_depuis_expediteur


@pytest.mark.parametrize(
    ("expediteur", "attendu"),
    [
        ("Welcome to the Jungle <alerts@mail.welcometothejungle.com>", "wttj"),
        ("France Travail <ne-pas-repondre@francetravail.fr>", "france_travail"),
        ("Pôle emploi <info@pole-emploi.fr>", "france_travail"),
        ("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "linkedin"),
        ("APEC <offres@apec.fr>", "apec"),
        ("Jobrapido <alert@jobrapido.com>", "jobrapido"),
        ("Indeed <donotreply@jobalert.indeed.com>", "indeed"),
        ("Arnaque <offres@linkedin.com.example.net>", None),  # faux domaine
        ("Ami <jean@gmail.com>", None),
        ("", None),
    ],
)
def test_source_depuis_expediteur(expediteur, attendu):
    assert source_depuis_expediteur(expediteur) == attendu


def test_domaine_minuscules():
    assert domaine("X <A@Mail.WelcomeToTheJungle.com>") == "mail.welcometothejungle.com"


def test_requete_gmail():
    q = requete_gmail(30)
    assert q.startswith("newer_than:30d from:(")
    assert "apec.fr" in q and " OR " in q
    assert "jobalerts-noreply@linkedin.com" in q
    assert "linkedin.com" not in q.split("(")[1].split(" OR ")  # pas tout le domaine LinkedIn


@pytest.mark.parametrize(
    ("expediteur", "attendu"),
    [
        ("LinkedIn <jobalerts-noreply@linkedin.com>", True),
        ("LinkedIn <newsletters-noreply@linkedin.com>", False),
        ("LinkedIn <invitations@linkedin.com>", False),
        ("APEC <offres@apec.fr>", True),
        ("Indeed <donotreply@match.indeed.com>", True),
        (
            "Indeed Apply <indeedapply@indeed.com>",
            False,
        ),  # confirmation, pas une alerte  # adresses APEC pas encore connues : domaine suffit
        ("Ami <jean@gmail.com>", False),
    ],
)
def test_est_alerte(expediteur, attendu):
    assert est_alerte(expediteur) is attendu
