"""Règles de lecture des mails de candidature, sur des cas tirés de vrais mails (personnes fictives)."""

import email
from email import policy

import pytest

from app.suivi.regles import (
    ACCUSE,
    AUTO,
    AUTRE,
    ENVOI,
    NON_REMIS,
    POSITIF,
    REFUS,
    classer,
    entreprise_destinataire,
    entreprise_du_mail,
    lire,
    poste_du_mail,
    texte_utile,
)

SPONT = "Candidature spontanée - Analystics Engineer - Jean Test"


@pytest.mark.parametrize(
    ("sujet", "texte", "moi", "attendu"),
    [
        (SPONT, "Bonjour, je me permets...", True, ENVOI),
        ("Re: " + SPONT, "Merci", True, AUTRE),
        ("Projet M2 : remise documents", "", True, AUTRE),
        (
            "Votre candidature pour le poste Business Analyst Junior F/H - JR2026-3",
            "Bonjour, Nous accusons réception de votre candidature pour l'offre de Business Analyst",
            False,
            ACCUSE,
        ),
        (
            "Re: " + SPONT,
            "Bonjour Jean, Malheureusement, ta candidature n'a pas été retenue",
            False,
            REFUS,
        ),
        (
            "BearingPoint - Votre candidature",
            "Après une étude approfondie de votre candidature et malgré la qualité de votre profil",
            False,
            REFUS,
        ),
        (
            "RE: " + SPONT,
            "Merci pour votre email, malheureusement nos recrutements sont gelés",
            False,
            REFUS,
        ),
        (
            "Update regarding your application at GitGuardian",
            "Thank you for applying to GitGuardian. Unfortunately, after carefully reviewing",
            False,
            REFUS,
        ),
        ("Re: " + SPONT, "Hi, Currently not looking for alternance, but good luck", False, REFUS),
        (
            "Re: " + SPONT,
            "Je ne prends pas de candidatures spontanées mais vous pouvez postuler",
            False,
            REFUS,
        ),
        (
            "Jean TEST / Chloé X (Emeria Technologies)",
            "Emeria Technologies vous a invité à un entretien.",
            False,
            POSITIF,
        ),
        (
            "Re: " + SPONT,
            "Erratum, possible de caler ça à 11h30 ? Désolé, je lance une invitation.",
            False,
            POSITIF,
        ),
        (
            "Undeliverable: " + SPONT,
            "Your message to x@acme.io couldn't be delivered.",
            False,
            NON_REMIS,
        ),
        (
            "Re: " + SPONT,
            "The email address you used is not a known address at 55.",
            False,
            NON_REMIS,
        ),
        ("Réponse automatique : " + SPONT, "Je suis en congés jusqu'au 26.", False, AUTO),
        ("I'm OOO Re: " + SPONT, "Hello and thanks for your email I'm OOO", False, AUTO),
        (
            "Re: " + SPONT,
            "Un clic pour délivrer votre email ! Je suis protégé par Protect de Mailinblack",
            False,
            AUTO,
        ),
        (
            "Merci pour ta candidature chez Hublo !",
            "Nous avons bien reçu ta candidature et sommes ravis",
            False,
            ACCUSE,
        ),
        (
            "Thanks for applying at NielsenIQ to Analytic Consultant",
            "I want to express our sincere gratitude for your application",
            False,
            ACCUSE,
        ),
        (
            "Réception de votre candidature",
            (
                "Merci pour votre candidature au poste de X. En cas de doute sur un mail, nous vous "
                "recommandons de ne pas donner suite."
            ),
            False,
            ACCUSE,
        ),
        (
            "Retour Entretien",
            "Nous avons toutefois décidé de ne pas donner suite pour cette fois",
            False,
            REFUS,
        ),
        ("Personnalisez votre application", "Faites comme chez vous avec l'appli", False, AUTRE),
        (
            "Votre candidature pour le poste Business Analyst",
            (
                "Nous accusons réception de votre candidature. Si votre candidature n'est pas retenue, "
                "nous conserverons votre CV."
            ),
            False,
            ACCUSE,
        ),
        (
            "Ta candidature chez Acme",
            "Après avoir analysé ton profil, ta candidature n'a pas été retenue.",
            False,
            REFUS,
        ),
    ],
)
def test_classer(sujet, texte, moi, attendu):
    assert classer(sujet, texte, moi)[0] == attendu


@pytest.mark.parametrize(
    ("expediteur", "sujet", "texte", "attendu"),
    [
        ("EIFFAGE - Workday <eiffage@myworkday.com>", "Votre candidature", "", "EIFFAGE"),
        (
            '"Équipe de recrutement de AccorCorpo" <notification@smartrecruiters.com>',
            "Thank You",
            "",
            "AccorCorpo",
        ),
        ('"Équipe RH de Datatorii" <datat-f7@candidates.welcomekit.co>', "x", "", "Datatorii"),
        (
            "Lucie Martin - Numberly <lucie@numberly.com>",
            "Suite à votre candidature",
            "",
            "Numberly",
        ),
        (
            "Jason Test - BearingPoint France <jason@bearingpoint.teamtailor-mail.com>",
            "x",
            "",
            "BearingPoint",
        ),
        (
            "Alexandra de SNCF CONSULTING <sc-b4@candidates.welcomekit.co>",
            "Candidature bien reçue",
            "",
            "SNCF CONSULTING",
        ),
        (
            '"Ministère de la Justice (via softgarden)" <no-reply@app.softgarden.io>',
            "x",
            "",
            "Ministère de la Justice",
        ),
        (
            "Lobellia Conseil Recruiting Team <no-reply@lobellia.recruitee.com>",
            "x",
            "",
            "Lobellia Conseil",
        ),
        ("AccorCorpo <notification@smartrecruiters.com>", "Merci", "", "AccorCorpo"),
        ("GitGuardian <someone@gitguardian.teamtailor-mail.com>", "x", "", "GitGuardian"),
        ("Sia <notification@sia-partners.com>", "Merci pour votre candidature chez Sia", "", "Sia"),
        ("Marc Test <marc@corailanalytics.com>", "Re: " + SPONT, "", "corailanalytics"),
        ('"*Rexel HQ, Recrutement" <recrutement@rexel.com>', "RE: x", "", "rexel"),
        ("<noreply@konnevia.fr>", "Votre candidature", "Merci d'avoir postulé", None),
        ("Recrutement <recrutement@affluences.com>", "Re: x", "", "affluences"),
    ],
)
def test_entreprise_du_mail(expediteur, sujet, texte, attendu):
    assert entreprise_du_mail(expediteur, sujet, texte) == attendu


def test_entreprise_destinataire():
    assert entreprise_destinataire("Hugues Test <h.test@tsg-solutions.com>") == "tsg solutions"
    assert entreprise_destinataire("contact@msentra.nestle.com") == "nestle"
    assert entreprise_destinataire("ami@gmail.com") is None


@pytest.mark.parametrize(
    ("sujet", "texte", "attendu"),
    [
        (
            "Réception de votre candidature",
            "Nous vous remercions pour votre candidature au poste de Consultant / Ingénieur BI & PPM H/F. Nous",
            "Consultant / Ingénieur BI & PPM H/F",
        ),
        ("Camille, candidature bien reçue pour Data Analyst (H/F)", "", "Data Analyst"),
        (
            "x",
            'Nous avons bien reçu votre candidature pour le poste "Data Engineer Junior" et nous',
            "Data Engineer Junior",
        ),
        (
            "Thanks for applying at NielsenIQ to Analytic Consultant - Equipe OSA",
            "",
            "Analytic Consultant - Equipe OSA",
        ),
        (
            "x",
            "Thank you for your application for the Junior Data Engineer position!",
            "Junior Data Engineer",
        ),
        (SPONT, "", "Analystics Engineer"),
        (
            'Merci pour votre Candidature au poste de Data Analytics Engineer "MATE" - F/H/X',
            "",
            'Data Analytics Engineer "MATE" - F/H/X',
        ),
        ("Bonjour", "Rien ici", None),
    ],
)
def test_poste(sujet, texte, attendu):
    assert poste_du_mail(sujet, texte) == attendu


def test_texte_utile_recolle_les_lignes():
    brut = (
        "Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
        'Nous avons bien reçu votre candidature pour le poste "Data\r\nEngineer Junior" et nous\r\n'
        "l'examinerons.\r\n"
    ).encode()
    texte = texte_utile(email.message_from_bytes(brut, policy=policy.default))
    assert poste_du_mail("x", texte) == "Data Engineer Junior"


def test_texte_utile_coupe_la_citation():
    brut = (
        "Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n"
        "Bonjour, merci mais ce n'est pas pour nous.\n\n"
        "Le lun. 10 août 2026 à 10:00, Jean Test <jean@example.com> a écrit :\n"
        "> Je serais ravi d'échanger lors d'un entretien.\n"
    ).encode()
    message = email.message_from_bytes(brut, policy=policy.default)
    texte = texte_utile(message)
    assert "entretien" not in texte
    assert "pas pour nous" in texte


def test_lire_envoi():
    lu = lire("Moi <moi@example.com>", "rh@acme-data.fr", SPONT, "Bonjour", envoye_par_moi=True)
    assert (lu.type, lu.entreprise, lu.poste) == (ENVOI, "acme data", "Analystics Engineer")


# --- Cas ajoutés après l'import d'une seconde boîte (mails réels, données fictives) ---------
@pytest.mark.parametrize(
    ("sujet", "moi"),
    [
        ("[Programme] - Candidature au Statut National Étudiant Entrepreneur", False),
        ("Renouvellement SNEE 2025-2026 - Camille EXEMPLE", False),
        ("[Appel à candidatures] Hackathon étudiants en Master - lundi 1/12", False),
        ("Candidature au Concours de Pitch 2026 - fin des candidatures dimanche", False),
        ("Re: Candidature au Prix Tremplin 2025", True),  # même envoyé par moi : pas un emploi
    ],
)
def test_vie_etudiante_hors_emploi(sujet, moi):
    from app.suivi.regles import HORS_EMPLOI

    assert classer(sujet, "Bonjour, merci pour votre candidature.", moi)[0] == HORS_EMPLOI


def test_accuse_avec_refus_au_conditionnel():
    texte = (
        "Nous vous remercions pour votre candidature au poste de Stage - Data Analyst H/F. "
        "Votre dossier va être examiné avec attention. Sans retour de notre part sous 30 jours, "
        "veuillez considérer que nous ne sommes pas en mesure de donner une suite favorable à "
        "votre candidature."
    )
    assert classer("Accusé de réception de votre candidature", texte, False)[0] == ACCUSE
    # Un vrai refus reste un refus.
    vrai = "Malheureusement, nous ne pouvons pas donner une suite favorable à votre candidature."
    assert classer("Votre candidature", vrai, False)[0] == REFUS


@pytest.mark.parametrize(
    ("expediteur", "sujet", "attendu"),
    [
        (
            "Acme France <acme+autoreply@talent.icims.eu>",
            "Enregistrement de votre candidature",
            "Acme France",
        ),
        (
            "noreply.globex@talent-soft.com",
            "Votre candidature - offre 2025-17778 – STAGE : BI",
            "globex",
        ),
        ("myhr-noreply@talent-soft.com", "Votre candidature à l'offre 2025-156365", None),
        (
            "groupeinitech@profils.org",
            "Recrutement Initech Assurances : Votre candidature pour le poste",
            "Initech Assurances",
        ),
        (
            "groupeinitech@profils.org",
            "Votre candidature Umbrella - STAGE - Data Analyst - Janvier",
            "Umbrella",
        ),
        (
            "Hooli Consulting <r-c-6ae369f3@reply.hellowork.com>",
            "Merci de votre candidature",
            "Hooli Consulting",
        ),
    ],
)
def test_entreprise_derriere_un_ats(expediteur, sujet, attendu):
    assert entreprise_du_mail(expediteur, sujet, "") == attendu


def test_administration_nommee_par_son_service():
    assert entreprise_destinataire("recrutement.contact@intradef.gouv.fr") == "Ministère des Armées"
    assert entreprise_destinataire("contact@culture.gouv.fr") == "culture"


def test_refus_formule_avec_regret():
    t = "Cependant, nous avons le regret de vous informer que nous ne donnons pas suite à votre candidature."
    assert classer("{ACME RECRUTEMENT} Candidature non retenue", t, False)[0] == REFUS


def test_invitation_a_un_entretien_automatise():
    t = "Vous êtes maintenant invité(e) à passer votre entretien de préqualification."
    assert classer("Votre candidature - Prochaine étape", t, False)[0] == POSITIF


@pytest.mark.parametrize(
    ("expediteur", "sujet", "attendu"),
    [
        (
            "nepasrepondre.recrutement@mail.acme",
            "{ACME RECRUTEMENT} Candidature non retenue",
            "ACME",
        ),
        ("contact@mail.globex", "Votre candidature", "globex"),
        ("Initech @ icims <initech+autoreply@talent.icims.eu>", "Merci d'avoir postulé", "Initech"),
    ],
)
def test_entreprise_cas_limites(expediteur, sujet, attendu):
    assert entreprise_du_mail(expediteur, sujet, "") == attendu
