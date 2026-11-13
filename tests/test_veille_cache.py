from datetime import UTC, datetime, timedelta

from app.agents import veille


def test_employeur_sans_site_pas_recherche_a_chaque_passage(monkeypatch):
    appels = []
    monkeypatch.setattr(veille, "_trouver_tableau", lambda *a, **k: appels.append(a) or None)
    bilan = veille.Bilan()
    ent = {
        "nom_affiche": "Acme",
        "nom_norm": "acme",
        "ats": "aucun",
        "ats_slug": None,
        "ats_verifie_le": datetime.now(UTC) - timedelta(days=2),
    }
    note = veille._surveiller(None, ent, {}, bilan)  # aucune requête, aucune écriture
    assert appels == [] and "site carrières" in note and bilan.sans_ats == ["Acme"]
