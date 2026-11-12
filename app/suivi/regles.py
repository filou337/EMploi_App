"""Règles de lecture des mails de candidature : type de mail, entreprise, poste.

Fonctions pures (sans réseau ni base), calées sur de vrais mails : accusés d'ATS
(Workday, SmartRecruiters, Welcomekit, Teamtailor, softgarden), réponses aux spontanées,
refus, invitations à un entretien, réponses automatiques et mails non remis.
"""

import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr

from app.entreprises import est_generique_ou_ats, nom_depuis_domaine
from app.texte import html_vers_texte

# --- Type de mail -------------------------------------------------------------------------

ENVOI, ACCUSE, REFUS, POSITIF, NON_REMIS, AUTO, AUTRE, HORS_EMPLOI = (
    "envoi",
    "accuse",
    "refus",
    "positif",
    "non_remis",
    "auto",
    "autre",
    "hors_emploi",
)


def _motifs(*expressions: str) -> re.Pattern:
    return re.compile("|".join(expressions), re.IGNORECASE)


_NON_REMIS = _motifs(
    r"undeliverable",
    r"delivery has failed",
    r"couldn'?t be delivered",
    r"non remis",
    r"delivery status notification",
    r"not a known address",
    r"adresse.{0,20}introuvable",
    r"mail delivery (?:failed|subsystem)",
)
_AUTO = _motifs(
    r"r[ée]ponse automatique",
    r"automatic reply",
    r"auto-?reply",
    r"out of (?:the )?office",
    r"\bOOO\b",
    r"message d'absence",
    r"\babsente? du bureau",
    r"en cong[ée]s",
    r"un clic pour d[ée]livrer",
    r"protect de mailinblack",
)
_REFUS = _motifs(
    r"malheureusement",
    r"unfortunately",
    r"donner (?:une )?suite (?:favorable )?(?:à|a) (?:votre|ta) candidature",
    r"pas (?:de )?suite favorable",
    r"d[ée]cid[ée] de ne pas",
    r"not (?:to )?(?:move|moving) forward",
    r"malgr[ée] la qualit[ée]",
    r"ne recrutons pas",
    r"pas de poste (?:ouvert|disponible)",
    r"recrutements? (?:sont )?gel[ée]s?",
    r"not (?:currently )?looking for",
    r"currently not looking",
    r"ne prends? pas de candidatures? spontan",
    r"avons le regret",
    r"ne (?:pouvons|donnons|donnerons) pas (?:donner )?suite",
)
# Formules qu'un accusé de réception emploie au conditionnel (« si votre candidature n'est
# pas retenue… ») : elles ne font un refus que dans un mail qui n'est pas un accusé.
_REFUS_FAIBLE = _motifs(
    r"pas (?:[ée]t[ée] )?retenue?",
    r"ne pas retenir",
    r"autres? candidats?",
    r"d'autres profils",
    r"other candidates",
)
_POSITIF = _motifs(
    r"invit[ée] [àa] un entretien",
    r"vous a invit[ée]",
    r"rappel d'entretien",
    r"planifier (?:un|une) (?:[ée]change|entretien|appel|call)",
    r"caler (?:un|une|ça|ca)",
    r"vos disponibilit[ée]s",
    r"schedule (?:a|an) (?:call|interview)",
    r"invite you (?:to|for) an? interview",
    r"lance une invitation",
    r"test technique",
    r"invit[ée]\(?e?\)? [àa] passer (?:votre|un|une)",
    r"entretien de pr[ée]qualification",
)
_ACCUSE = _motifs(
    r"bien re[çc]u (?:votre|ta) candidature",
    r"accusons r[ée]ception",
    r"avons re[çc]u (?:votre|ta) candidature",
    r"candidature (?:bien )?re[çc]ue",
    r"r[ée]ception de (?:votre|ta) candidature",
    r"thanks? (?:you )?for (?:applying|your application)",
    r"received your application",
    r"(?:merci|remercions?) (?:pour|de) (?:votre|ta) candidature",
)
# Mails qui parlent de « candidature » sans rapport avec un emploi : vie étudiante
# (statut étudiant-entrepreneur, prix, concours, hackathons, appels à projets).
_HORS_EMPLOI = _motifs(
    r"statut national (?:d.)?[ée]tudiant",
    r"\bSNEE\b",
    r"p[ée]pite",
    r"appel [àa] (?:candidatures|projets)",
    r"\bAAP\b",
    r"hackathon",
    r"concours de pitch",
    r"\bprix (?:tremplin|p[ée]pite|de l.innovation|[ée]tudiant)",
    r"projet tuteur[ée]",
    r"comit[ée] d.engagement",
)
_SUJET_CANDIDATURE = _motifs(
    r"candidature", r"application", r"applying", r"applied", r"postul", r"entretien", r"interview"
)


def classer(sujet: str, texte: str, envoye_par_moi: bool) -> tuple[str, float]:
    """Type du mail et confiance de la règle (0 à 1).

    `texte` est le corps sans la partie citée (voir `texte_utile`) : sinon une réponse
    reprenant ma candidature (« ... un entretien ... ») passerait pour une invitation.
    """
    if _HORS_EMPLOI.search(sujet):
        return HORS_EMPLOI, 0.95
    if envoye_par_moi:
        if re.match(r"\s*(re|tr|fwd?|réf)\s*:", sujet, re.IGNORECASE):
            return AUTRE, 1.0
        return (ENVOI, 1.0) if re.search(r"candidature", sujet, re.IGNORECASE) else (AUTRE, 1.0)
    tout = f"{sujet}\n{texte}"
    if _NON_REMIS.search(tout):
        return NON_REMIS, 0.95
    if _AUTO.search(sujet) or _AUTO.search(texte[:400]):
        return AUTO, 0.95
    accuse = _ACCUSE.search(tout)
    if _REFUS.search(tout):
        fort = re.search(r"malheureusement|unfortunately", tout, re.IGNORECASE)
        # « Sans retour sous 30 jours, considérez que nous ne pouvons donner suite » : accusé.
        if accuse and not fort:
            return ACCUSE, 0.85
        return REFUS, 0.9 if fort else 0.8
    if _REFUS_FAIBLE.search(tout) and not accuse:
        return REFUS, 0.7
    if _POSITIF.search(tout):
        return POSITIF, 0.75
    if accuse:
        return ACCUSE, 0.95
    return AUTRE, 0.5


def parle_de_candidature(sujet: str) -> bool:
    """L'objet du mail parle d'une candidature (le corps ne suffit pas : newsletters)."""
    return bool(_SUJET_CANDIDATURE.search(sujet))


# --- Texte utile --------------------------------------------------------------------------

_CITATION = re.compile(
    r"^(?:Le .{5,120} a [ée]crit\s*:|On .{5,160} wrote\s*:|-{2,} ?(?:Original Message|Message d'origine|Forwarded message)"
    r"|De\s*:.*$|From\s*:.*$|>)",
    re.IGNORECASE | re.MULTILINE,
)


def texte_utile(message: EmailMessage, limite: int = 3000) -> str:
    """Corps du mail en texte, coupé avant la partie citée (« Le ... a écrit : »)."""
    corps = message.get_body(preferencelist=("plain", "html"))
    if corps is None:
        return ""
    try:
        texte = corps.get_content()
    except (LookupError, UnicodeDecodeError):
        return ""
    if corps.get_content_type() == "text/html":
        texte = html_vers_texte(texte)
    texte = texte.replace("\r\n", "\n").replace("\r", "\n")
    for invisible in ("\u200b", "\u034f", "\u200c", "\ufeff"):
        texte = texte.replace(invisible, "")
    texte = texte.replace("\xa0", " ")
    coupure = _CITATION.search(texte)
    if coupure:
        texte = texte[: coupure.start()]
    # Lignes coupées à 72 caractères : on recolle chaque paragraphe sur une ligne.
    texte = re.sub(r"(?<=\S)[ \t]*\n(?=[^\n])", " ", texte)
    return re.sub(r"[ \t]+", " ", texte).strip()[:limite]


# --- Entreprise ---------------------------------------------------------------------------

_FIN_NOM = r"(?=\s*(?:[!?.,;:/()\"«»]|\s-\s|\s–\s|$| et | and | pour | to | ainsi))"
_NOM = r"([A-Z0-9][\w&'’.\- ]{1,40}?)"
_EQUIPE = re.compile(
    r"^(?:[ÉE]quipe|Team)\s+(?:de\s+)?(?:recrutement|RH|recruiting|talent)?\s*(?:de|d'|of)\s+(.+)$",
    re.IGNORECASE,
)
_EQUIPE_SUFFIXE = re.compile(
    r"^(.+?)\s+(?:Recruiting|Recrutement|Talent|HR|RH)(?:\s+Team)?$", re.IGNORECASE
)
_VIA = re.compile(r"^(.+?)\s*\((?:via|par)\s+[^)]+\)$", re.IGNORECASE)
_TIRET = re.compile(r"^(.+?)\s+[-–]\s+(.+)$")
_PRENOM_DE = re.compile(r"^[A-ZÉ][\wéèëï-]+\s+de\s+(.+)$")
_PLATEFORMES = {
    "workday",
    "smartrecruiters",
    "welcome to the jungle",
    "teamtailor",
    "softgarden",
    "recrutement",
    "rh",
    "linkedin",
    "indeed",
}
_CHEZ = re.compile(rf"\b(?:chez|at)\s+{_NOM}{_FIN_NOM}")
# « Recrutement Globex Assurances : Votre candidature… », « Votre candidature Initech - STAGE… »
_SUJET_ENTREPRISE = re.compile(
    rf"^(?:Recrutement|Recruitment)\s+{_NOM}\s*:|^Votre candidature\s+(?!pour|au|à|a |chez|-|–){_NOM}\s+[-–]"
    r"|^\{([^}]{2,40}?)\s+RECRUTEMENT\}",
    re.IGNORECASE,
)
# Adresse d'ATS qui porte l'employeur : noreply.acme@talent-soft.com, groupeglobex@profils.org
_LOCAL_ROBOT = re.compile(
    r"^(?:no-?reply|ne-?pas-?repondre|notifications?|donotreply|myhr-?noreply|auto-?reply|jobs?|careers?|rh|hr|recrutement|talents?)$",
    re.IGNORECASE,
)
_INTERET = re.compile(
    rf"(?:int[ée]r[êe]t (?:que (?:vous|tu) (?:portez|portes|manifestez|avez|as) (?:à|a|pour|envers)|pour)"
    rf"|interest in)\s+(?!notre|nos|votre|ce |cette|le poste|la |l')(?:la soci[ée]t[ée] )?{_NOM}{_FIN_NOM}"
)


def _propre(nom: str) -> str | None:
    nom = re.sub(r"^\*+|\*+$", "", nom).strip(" \"'«»")
    nom = re.sub(r"\s*@\s*[\w-]+$", "", nom)  # « Acme @ icims » -> « Acme »
    nom = re.sub(r",\s*(?:recrutement|rh|hr)\b.*$", "", nom, flags=re.IGNORECASE).strip()
    if not nom or nom.lower() in _PLATEFORMES or len(nom) > 40:
        return None
    return nom


def _depuis_nom_affiche(nom: str, ats: bool) -> str | None:
    if not nom:
        return None
    for motif in (_EQUIPE, _VIA):
        m = motif.match(nom)
        if m:
            return _propre(m.group(1))
    m = _EQUIPE_SUFFIXE.match(nom)
    if m and ats:
        return _propre(m.group(1))
    m = _TIRET.match(nom)
    if m:  # « Prénom Nom - Entreprise » ou « ENTREPRISE - Workday »
        gauche, droite = m.group(1), m.group(2)
        if droite.strip().lower() in _PLATEFORMES:
            return _propre(gauche)
        return _propre(re.sub(r"\s+(?:France|Group|Groupe)$", "", droite)) or _propre(droite)
    if ats:
        m = _PRENOM_DE.match(nom)  # Welcomekit : « Alexandra de SNCF CONSULTING »
        if m:
            return _propre(m.group(1))
        return _propre(nom)  # « AccorCorpo <notification@smartrecruiters.com> »
    return None


def _depuis_adresse_ats(adresse: str) -> str | None:
    """« noreply.acme@talent-soft.com » -> « acme » ; None pour un robot anonyme."""
    local = adresse.partition("@")[0].lower()
    morceaux = [m for m in re.split(r"[._+]", local) if m and not _LOCAL_ROBOT.match(m)]
    if not morceaux or len(morceaux[0]) < 3 or re.search(r"\d{3,}", morceaux[0]):
        return None
    return _propre(morceaux[0].replace("-", " "))


def entreprise_du_mail(expediteur: str, sujet: str, texte: str) -> str | None:
    """Employeur d'un mail reçu, ou None si rien de fiable.

    Ordre : nom affiché (« Équipe RH de X », « Prénom - X »), puis « chez X » ou « Recrutement X : »
    dans l'objet, puis l'adresse d'un ATS (noreply.acme@talent-soft.com),
    puis le domaine de l'expéditeur, puis « l'intérêt que vous portez à X » dans le texte.
    """
    nom_affiche, adresse = parseaddr(expediteur)
    if not adresse and (m := re.match(r"^\s*(.*?)\s*<([^<>@\s]+@[^<>\s]+)>\s*$", expediteur or "")):
        nom_affiche, adresse = (
            m.group(1).strip('"'),
            m.group(2),
        )  # « Acme @ icims <…> » sans guillemets
    domaine = adresse.rpartition("@")[2].lower()
    ats = est_generique_ou_ats(domaine) or domaine.endswith("teamtailor-mail.com")
    sujet_ent = (m := _SUJET_ENTREPRISE.search(sujet)) and _propre(next(g for g in m.groups() if g))
    for candidat in (
        _depuis_nom_affiche(nom_affiche.strip(), ats),
        (m := _CHEZ.search(sujet)) and _propre(m.group(1)),
        sujet_ent,
        _depuis_adresse_ats(adresse) if ats else None,
        nom_depuis_domaine(domaine) if domaine else None,
        (m := _INTERET.search(texte[:800])) and _propre(m.group(1)),
    ):
        if candidat:
            return candidat
    return None


def entreprise_destinataire(destinataire: str) -> str | None:
    """Employeur d'une candidature envoyée : domaine du destinataire."""
    domaine = parseaddr(destinataire)[1].rpartition("@")[2]
    return nom_depuis_domaine(domaine) if domaine else None


# --- Poste --------------------------------------------------------------------------------

_POSTE = [
    # Poste entre guillemets : « pour le poste "Data Engineer Junior" ».
    re.compile(
        r"(?:au|pour le|du) poste (?:de |d'|:)?\s*[\"“«]\s*([^\"”»\n]{3,90}?)\s*[\"”»]",
        re.IGNORECASE,
    ),
    # Poste libre, qui peut contenir des guillemets : « au poste de Data Engineer "MATE" - F/H ».
    re.compile(
        r"(?:au|pour le|du) poste (?:de |d'|:)?\s*([^\n]{3,90}?)"
        r"\s*(?:\.\s|\.$|[,!]| au sein| chez| at |$|\n| et | nous)",
        re.IGNORECASE,
    ),
    re.compile(r"pour l'offre (?:de |d')\s*([^,\n]{3,90}?)\s*(?:,|$|\n)", re.IGNORECASE),
    re.compile(
        r"candidature (?:bien )?re[çc]ue (?:pour|[—–-])\s+([^(\n]{3,90}?)\s*(?:\(|$)", re.IGNORECASE
    ),
    re.compile(r"application for the (.{3,90}?) (?:position|role)", re.IGNORECASE),
    re.compile(r"applying at .{2,40}? to\s+(.{3,90}?)\s*$", re.IGNORECASE),
    re.compile(r"candidature spontan[ée]e\s*[-–:]\s*([^-–\n]{3,60}?)\s*(?:[-–]|$)", re.IGNORECASE),
]


def poste_du_mail(sujet: str, texte: str) -> str | None:
    for source in (sujet, texte[:1200]):
        for motif in _POSTE:
            m = motif.search(source)
            if m:
                poste = m.group(1).strip(" \"'“”«».\u200b")
                if 3 <= len(poste) <= 90 and not re.match(r"(?i)votre|ta |notre", poste):
                    return poste
    return None


@dataclass(frozen=True)
class Lecture:
    type: str
    confiance: float
    entreprise: str | None
    poste: str | None


def lire(
    expediteur: str, destinataire: str, sujet: str, texte: str, envoye_par_moi: bool
) -> Lecture:
    type_, confiance = classer(sujet, texte, envoye_par_moi)
    if envoye_par_moi:
        entreprise = entreprise_destinataire(destinataire)
    else:
        entreprise = entreprise_du_mail(expediteur, sujet, texte)
    return Lecture(type_, confiance, entreprise, poste_du_mail(sujet, texte))
