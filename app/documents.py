"""CV et lettres de motivation : dépôt, validation, extraction du texte, premier diagnostic ATS.

Module A (ingestion) de l'architecture jobstep, sans LLM :
- validation : extension, signature du fichier, taille, intégrité, sécurité (PDF chiffré,
  JavaScript embarqué, DOCX avec macros refusés) ;
- prétraitement : extraction du texte (PDF texte ou DOCX, tableaux compris), détection
  d'un PDF scanné (pas de texte lisible par un ATS) ;
- diagnostic : pages, mots, sections reconnues, coordonnées, alertes de lisibilité.
Le texte extrait est ce qu'un ATS « voit » : c'est la base du futur score ATS (module C).
"""

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field

import docx
import psycopg
from psycopg.types.json import Jsonb
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.collecte.normalisation import sans_accents
from app.config import RACINE

DOSSIER = RACINE / "profil" / "documents"
TAILLE_MAX = 10 * 1024 * 1024
MOTS_MIN_PAR_PAGE = 40  # en dessous, le PDF est probablement une image scannée

SECTIONS = {
    "experience": r"exp[ée]riences?(?: professionnelles?)?|parcours professionnel|work experience|experience",
    "formation": r"(?:dipl[ôo]mes?\s+(?:et|&)\s+)?formations?(?:\s+(?:et|&)\s+dipl[ôo]mes?)?|[ée]tudes"
    r"|dipl[ôo]mes?|education|cursus|parcours acad[ée]mique",
    "competences": r"comp[ée]tences|skills|savoir-faire|outils|technologies|stack",
    "langues": r"langues?|languages?",
    "projets": r"projets?|projects?|r[ée]alisations",
    "certifications": r"certifications?|certificats?",
    "profil": r"profil|r[ée]sum[ée]|summary|[àa] propos|about me|objectif",
    "centres_interet": r"centres? d'int[ée]r[êe]ts?|loisirs|interests|hobbies",
}


class DocumentRefuse(ValueError):
    """Le fichier ne passe pas la validation ; le message est montré tel quel à l'utilisateur."""


@dataclass
class Lecture:
    format: str
    texte: str
    pages: int
    alertes: list[str] = field(default_factory=list)


def _format(nom: str, contenu: bytes) -> str:
    ext = nom.lower().rsplit(".", 1)[-1] if "." in nom else ""
    if ext == "pdf" and contenu.startswith(b"%PDF-"):
        return "pdf"
    if ext == "docx" and contenu.startswith(b"PK"):
        return "docx"
    if ext in ("doc", "docm", "odt", "pages"):
        raise DocumentRefuse(
            f"Format .{ext} non accepté : enregistre le fichier en PDF ou en DOCX."
        )
    raise DocumentRefuse(
        "Le fichier doit être un PDF ou un DOCX (signature du fichier non reconnue)."
    )


def recoller(texte: str) -> tuple[str, bool]:
    """Recolle un texte extrait « un mot par ligne » ; renvoie (texte, il_etait_eclate)."""
    lignes = [lg for lg in texte.splitlines() if lg.strip()]
    if len(lignes) < 40 or sum(len(lg.split()) <= 2 for lg in lignes) / len(lignes) < 0.6:
        return texte, False
    morceaux: list[str] = []
    for lg in texte.splitlines():
        lg = lg.strip()
        if not lg:
            morceaux.append("\n\n")
            continue
        morceaux.append(lg)
        # Fin de phrase ou ligne courte de titre : on revient à la ligne.
        morceaux.append("\n" if re.search(r"[.!?:]$", lg) else " ")
    recolle = re.sub(r" *\n *", "\n", "".join(morceaux))
    recolle = re.sub(r" ([,.;:!?)’'])", r"\1", recolle).replace("( ", "(").replace(" '", "'")
    return re.sub(r"\n{3,}", "\n\n", recolle).strip(), True


def _lire_pdf(contenu: bytes) -> Lecture:
    try:
        pdf = PdfReader(io.BytesIO(contenu))
    except (PdfReadError, ValueError, OSError) as e:
        raise DocumentRefuse(f"PDF illisible ou endommagé ({e}).") from e
    if pdf.is_encrypted:
        raise DocumentRefuse("PDF protégé par mot de passe : un ATS ne pourra pas le lire.")
    catalogue = pdf.trailer.get("/Root", {})
    noms = catalogue.get("/Names", {})
    action = catalogue.get("/OpenAction")
    action_js = hasattr(action, "get") and action.get("/S") == "/JavaScript"
    if "/JavaScript" in noms or action_js:
        raise DocumentRefuse("PDF contenant du JavaScript : refusé par sécurité.")
    pages = [page.extract_text() or "" for page in pdf.pages]
    texte = "\n".join(pages)
    alertes = []
    texte, eclate = recoller(texte)
    if eclate:
        alertes.append(
            "Texte extrait mot par mot (PDF justifié ou en blocs) : certains ATS liront"
            " des mots isolés. Exporte en PDF « texte » ou dépose plutôt le DOCX."
        )
    if len(texte.split()) < MOTS_MIN_PAR_PAGE * max(len(pages), 1) / 2:
        alertes.append(
            "Très peu de texte lisible : PDF scanné ou en image ? Un ATS n'en lira rien."
        )
    if pdf.metadata and any("canva" in str(v).lower() for v in pdf.metadata.values()):
        alertes.append("PDF exporté de Canva : vérifie l'ordre de lecture des colonnes.")
    return Lecture("pdf", texte, len(pages), alertes)


def _lire_docx(contenu: bytes) -> Lecture:
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as z:
            noms = z.namelist()
    except zipfile.BadZipFile as e:
        raise DocumentRefuse("DOCX endommagé (archive illisible).") from e
    if "word/document.xml" not in noms:
        raise DocumentRefuse("Ce n'est pas un document Word valide.")
    if any(n.lower().endswith("vbaproject.bin") for n in noms):
        raise DocumentRefuse("Document avec macros : refusé par sécurité.")
    d = docx.Document(io.BytesIO(contenu))
    lignes = [p.text for p in d.paragraphs]
    alertes = []
    if d.tables:
        alertes.append(
            f"{len(d.tables)} tableau(x) : certains ATS lisent mal la mise en page en tableau."
        )
        for t in d.tables:
            for rangee in t.rows:
                lignes.append(" | ".join(c.text.strip() for c in rangee.cells if c.text.strip()))
    entetes = " ".join(p.text for s in d.sections for p in s.header.paragraphs).strip()
    if entetes:
        alertes.append("Texte dans l'en-tête de page : de nombreux ATS l'ignorent (coordonnées ?).")
        lignes.insert(0, entetes)
    if any(n.startswith("word/media/") for n in noms) and len(" ".join(lignes).split()) < 150:
        alertes.append("Beaucoup d'images pour peu de texte : un ATS ne lit pas les images.")
    return Lecture("docx", "\n".join(lignes), 0, alertes)


def lire(nom: str, contenu: bytes) -> Lecture:
    """Valide le fichier et en extrait le texte, ou lève DocumentRefuse."""
    if not contenu:
        raise DocumentRefuse("Fichier vide.")
    if len(contenu) > TAILLE_MAX:
        raise DocumentRefuse(
            f"Fichier trop lourd ({len(contenu) // 1024 // 1024} Mo, maximum 10 Mo)."
        )
    fmt = _format(nom, contenu)
    return _lire_pdf(contenu) if fmt == "pdf" else _lire_docx(contenu)


def diagnostic(lecture: Lecture, type_: str) -> dict:
    """Premier diagnostic de lisibilité (sans LLM) : ce qu'un ATS retrouvera dans le document."""
    texte = lecture.texte
    lignes = [sans_accents(lg).lower().strip(" :•-–\t") for lg in texte.splitlines() if lg.strip()]
    trouvees = sorted(
        nom
        for nom, motif in SECTIONS.items()
        if any(len(lg) <= 40 and re.fullmatch(rf"(?:{sans_accents(motif)})s?", lg) for lg in lignes)
    )
    mots = len(texte.split())
    alertes = list(lecture.alertes)
    infos = {
        "email": bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", texte)),
        "telephone": bool(re.search(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}", texte)),
        "linkedin": "linkedin.com/" in texte.lower(),
    }
    if type_ == "cv":
        for requise in ("experience", "formation", "competences"):
            if requise not in trouvees:
                alertes.append(f"Section « {requise} » non reconnue : utilise un titre standard.")
        if not infos["email"]:
            alertes.append("Aucune adresse mail détectée dans le texte lisible.")
        if not infos["telephone"]:
            alertes.append("Aucun numéro de téléphone détecté dans le texte lisible.")
        if lecture.pages > 2:
            alertes.append(f"{lecture.pages} pages : vise 1 page (junior) ou 2 au maximum.")
        if mots and mots < 200:
            alertes.append(f"Seulement {mots} mots lisibles : contenu trop court ou mal extrait.")
    elif mots > 450:
        alertes.append(f"Lettre de {mots} mots : vise 250 à 400 mots.")
    if re.search(r"[-]", texte):
        alertes.append("Icônes ou caractères spéciaux (police d'icônes) : bruit pour un ATS.")
    return {
        "pages": lecture.pages,
        "mots": mots,
        "sections": trouvees,
        "coordonnees": infos,
        "alertes": alertes,
    }


def deposer(conn: psycopg.Connection, type_: str, nom: str, contenu: bytes) -> dict:
    """Enregistre une nouvelle version du CV ou de la lettre et la met par défaut."""
    if type_ not in ("cv", "lm"):
        raise DocumentRefuse("Type de document inconnu.")
    lecture = lire(nom, contenu)
    empreinte = hashlib.sha256(contenu).hexdigest()
    existant = conn.execute(
        "SELECT id FROM documents WHERE type = %s AND empreinte = %s", (type_, empreinte)
    ).fetchone()
    if existant:
        conn.execute(
            "UPDATE documents SET par_defaut = FALSE WHERE type = %s AND langue = 'fr'", (type_,)
        )
        conn.execute("UPDATE documents SET par_defaut = TRUE WHERE id = %s", (existant["id"],))
        return conn.execute(
            "SELECT id, type, langue, nom_fichier, format, taille, diagnostic, par_defaut, depose_le"
            " FROM documents WHERE id = %s",
            (existant["id"],),
        ).fetchone()
    DOSSIER.mkdir(parents=True, exist_ok=True)
    chemin = DOSSIER / f"{type_}_{empreinte[:12]}.{lecture.format}"
    chemin.write_bytes(contenu)
    conn.execute(
        "UPDATE documents SET par_defaut = FALSE WHERE type = %s AND langue = 'fr'", (type_,)
    )
    return conn.execute(
        """
        INSERT INTO documents (type, nom_fichier, chemin, format, taille, empreinte, texte,
                               diagnostic, par_defaut, contenu)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
        RETURNING id, type, langue, nom_fichier, format, taille, diagnostic, par_defaut, depose_le
        """,
        (
            type_,
            nom[:200],
            str(chemin.relative_to(RACINE)),
            lecture.format,
            len(contenu),
            empreinte,
            lecture.texte,
            Jsonb(diagnostic(lecture, type_)),
            contenu,
        ),
    ).fetchone()
