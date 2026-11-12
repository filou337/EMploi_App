import pytest

from app.criteres import DEFAUTS, refus

C = DEFAUTS["criteres"]


@pytest.mark.parametrize(
    ("intitule", "lieu", "motif"),
    [
        ("Data Analyst (F/H)", "Paris (75)", None),
        ("Analytics Engineer", "Saint-Denis", None),
        ("Consultant Power BI", "", None),  # lieu inconnu : accepté
        ("Data Engineer", "Remote", None),
        ("Ingénieur développement C", "Paris", "intitulé"),
        ("Stage - Data Analyst", "Paris", "contrat"),
        ("Senior Data Engineer", "Paris", "niveau"),
        ("Data Analyst", "Lyon, France", "lieu"),
    ],
)
def test_refus(intitule, lieu, motif):
    r = refus(intitule, lieu, C)
    assert (r is None) if motif is None else (r is not None and r.startswith(motif))
