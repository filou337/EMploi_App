from app.generation.fabrique import appliquer_diplomes, attenuer, corriger_cv, meilleur_essai

PROFIL = {
    "profil": {"langues": {"francais": "C2", "anglais": "B2", "espagnol": "A2"}},
    "fin_experiences": {"Acme Industrie": "Août 2026"},
}


def test_langues_du_profil_font_foi():
    cv = corriger_cv({"langues": [{"langue": "Espagnol", "niveau": "B1"}]}, PROFIL, "")
    assert cv["langues"] == [
        {"langue": "Français", "niveau": "C2"},
        {"langue": "Anglais", "niveau": "B2"},
        {"langue": "Espagnol", "niveau": "A2"},
    ]


def test_stage_retire_et_fin_d_experience():
    cv = {
        "experiences": [
            {"poste": "Stage Data Analyst", "entreprise": "Acme Industrie", "fin": "Présent"},
            {"poste": "Data Consultant (stage)", "entreprise": "Globex", "fin": "02/2026"},
        ]
    }
    exp = corriger_cv(cv, PROFIL, "")["experiences"]
    assert [(e["poste"], e["fin"]) for e in exp] == [
        ("Data Analyst", "Août 2026"),
        ("Data Consultant", "02/2026"),
    ]


def test_niveaux_exageres_attenues_sauf_si_le_cv_le_dit():
    assert (
        attenuer("Expert en SQL. Profil confirmé et rigoureux.", "SQL")
        == "Maîtrisant SQL. Profil rigoureux."
    )
    assert attenuer("Expert en SQL.", "Expert en SQL depuis 2020") == "Expert en SQL."


def test_diplome_obtenu():
    cv = {"formations": [{"diplome": "Master Data & BI", "annee": "2025-En cours"}]}
    assert (
        appliquer_diplomes(cv, {"Master Data": "2025-2026"})["formations"][0]["annee"]
        == "2025-2026"
    )


def _essais(*scores):
    restants = list(scores)
    appels = []

    def rediger():
        appels.append(1)
        return {"matching": {"score_matching": restants.pop(0)}, "essais": 1}

    return rediger, appels


def test_seconde_redaction_si_le_cv_adapte_matche_moins_bien():
    rediger, appels = _essais(68.9, 79.0)
    e = meilleur_essai(75.3, rediger)
    assert len(appels) == 2 and e["matching"]["score_matching"] == 79.0 and e["essais"] == 2


def test_pas_de_seconde_redaction_si_c_est_bon():
    rediger, appels = _essais(74.0)
    assert meilleur_essai(75.3, rediger)["essais"] == 1 and len(appels) == 1  # écart < 2 points


def test_on_garde_le_premier_si_le_second_est_pire():
    rediger, _ = _essais(70.0, 65.0)
    assert meilleur_essai(75.3, rediger)["matching"]["score_matching"] == 70.0
