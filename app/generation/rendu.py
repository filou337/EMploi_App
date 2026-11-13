"""Rendu des CV et LM : HTML « ATS friendly » -> PDF (Chrome du PC, sans installation) et DOCX.

Règles de mise en page pour les ATS : une seule colonne, ordre de lecture linéaire, titres de
section standard, polices courantes, aucune icône, aucun tableau, aucun texte en en-tête de page,
dates en clair. Le PDF produit est relu par le checker avant d'être déclaré « ATS friendly ».
"""

import io
import re
import shutil
import subprocess
import tempfile
from datetime import date
from html import escape
from pathlib import Path

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

TITRES = {
    "fr": {
        "profil": "Profil",
        "competences": "Compétences",
        "experiences": "Expériences professionnelles",
        "formations": "Formation",
        "langues": "Langues",
        "qualites": "Qualités",
    },
    "en": {
        "profil": "Summary",
        "competences": "Skills",
        "experiences": "Professional Experience",
        "formations": "Education",
        "langues": "Languages",
        "qualites": "Strengths",
    },
}
LANGUES_EN = {
    "français": "French",
    "anglais": "English",
    "espagnol": "Spanish",
    "allemand": "German",
    "italien": "Italian",
    "portugais": "Portuguese",
    "arabe": "Arabic",
    "chinois": "Chinese",
}
MOIS_EN = {
    "janv": "Jan",
    "févr": "Feb",
    "fevr": "Feb",
    "mars": "Mar",
    "avr": "Apr",
    "mai": "May",
    "juin": "Jun",
    "juil": "Jul",
    "août": "Aug",
    "aout": "Aug",
    "sept": "Sep",
    "oct": "Oct",
    "nov": "Nov",
    "déc": "Dec",
    "dec": "Dec",
    "présent": "Present",
    "present": "Present",
    "aujourd'hui": "Present",
    "depuis": "Since",
}

MOIS_ANGLAIS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

CSS = """
@page { size: A4; margin: 14mm 15mm; }
* { box-sizing: border-box; }
body { font-family: Arial, Helvetica, "Liberation Sans", sans-serif; font-size: 10.3pt; line-height: 1.38;
       color: #1b2a35; margin: 0; }
h1 { font-size: 20pt; margin: 0; color: #173448; letter-spacing: .2px; }
.titre { font-size: 12pt; font-weight: bold; color: #087f8c; margin: 2px 0 4px; }
.contacts { font-size: 9.5pt; color: #33454f; margin-bottom: 8px; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .6px; color: #173448; margin: 11px 0 4px;
     padding-bottom: 2px; border-bottom: 1.2px solid #087f8c; }
p { margin: 0 0 4px; }
.exp { margin-bottom: 7px; page-break-inside: avoid; }
.exp-titre { font-weight: bold; }
.exp-dates { color: #4a5c66; }
ul { margin: 2px 0 0 0; padding-left: 15px; }
li { margin: 0 0 2px; }
.comp { margin: 0 0 2px; }
.lm p { margin: 0 0 9px; text-align: left; }
.lm .entete { margin-bottom: 14px; }
.lm .dest { margin: 10px 0 12px; }
"""


def _date_en(texte: str) -> str:
    t = texte or ""
    for fr, en in MOIS_EN.items():
        t = t.replace(fr, en).replace(fr.capitalize(), en)
    return t.replace(".", "")


def _periode(e: dict, langue: str) -> str:
    debut, fin = e.get("debut", ""), e.get("fin", "")
    if langue == "en":
        debut, fin = _date_en(debut), _date_en(fin)
    if debut and fin and debut != fin:
        return f"{debut} – {fin}"
    return debut or fin


def _lien_court(url: str) -> str:
    """« https://www.linkedin.com/in/x/ » -> « linkedin.com/in/x »."""
    return re.sub(r"^https?://(?:www\.)?", "", (url or "").strip()).rstrip("/")


def _contacts(ident: dict, court: bool = False) -> list[str]:
    """Coordonnées de l'en-tête ; `court` : liens raccourcis, mobilité retirée (CV sur une page)."""
    liens = [ident.get("linkedin"), ident.get("portfolio")]
    if court:
        liens = [_lien_court(x) for x in liens]
    valeurs = [
        ident.get("email"),
        ident.get("telephone"),
        ident.get("ville"),
        *liens,
        ident.get("permis"),
    ]
    if not court:
        valeurs.append(ident.get("mobilite"))
    return [v for v in valeurs if v]


# Paliers de resserrage d'un CV qui dépasse une page (0 = mise en page normale).
COMPACTS = {
    1: "h1 { font-size: 18pt; } .titre { margin: 1px 0 2px; } .contacts { font-size: 9pt; margin-bottom: 5px; }",
    2: "h1 { font-size: 18pt; } .titre { margin: 1px 0 2px; } .contacts { font-size: 9pt; margin-bottom: 5px; }"
    " body { font-size: 10pt; line-height: 1.3; } h2 { margin: 8px 0 3px; } .exp { margin-bottom: 5px; }"
    " li { margin: 0 0 1px; }",
    3: "@page { margin: 11mm 13mm; } h1 { font-size: 17pt; } .titre { margin: 0 0 2px; }"
    " .contacts { font-size: 8.8pt; margin-bottom: 4px; } body { font-size: 9.6pt; line-height: 1.27; }"
    " h2 { margin: 7px 0 3px; font-size: 10.5pt; } .exp { margin-bottom: 4px; } li { margin: 0 0 1px; }",
}
COMPACTS[4] = COMPACTS[3]
PUCES_MAX = {3: 4, 4: 3}  # puces gardées par expérience aux derniers paliers


def _langue_lib(nom: str, langue: str) -> str:
    return LANGUES_EN.get((nom or "").lower(), nom) if langue == "en" else nom


def html_cv(cv: dict, ident: dict, langue: str, compact: int = 0) -> str:
    t = TITRES[langue]
    e = escape
    cv = reduire(cv, compact)
    blocs = [
        f"<h1>{e(ident.get('nom') or '')}</h1>",
        f"<div class='titre'>{e(cv.get('titre', ''))}</div>",
        f"<div class='contacts'>{' · '.join(e(c) for c in _contacts(ident, court=compact >= 1))}</div>",
    ]
    if cv.get("accroche"):
        blocs += [f"<h2>{t['profil']}</h2>", f"<p>{e(cv['accroche'])}</p>"]
    if cv.get("competences"):
        blocs.append(f"<h2>{t['competences']}</h2>")
        blocs += [
            f"<p class='comp'><strong>{e(c['categorie'])} :</strong> {e(', '.join(c['elements']))}</p>"
            for c in cv["competences"]
        ]
    if cv.get("experiences"):
        blocs.append(f"<h2>{t['experiences']}</h2>")
        for x in cv["experiences"]:
            puces = "".join(f"<li>{e(p)}</li>" for p in x.get("puces", []))
            blocs.append(
                f"<div class='exp'><div><span class='exp-titre'>{e(x['poste'])}</span> — {e(x['entreprise'])}"
                f"</div><div class='exp-dates'>{e(_periode(x, langue))}</div><ul>{puces}</ul></div>"
            )
    if cv.get("formations"):
        blocs.append(f"<h2>{t['formations']}</h2>")
        blocs += [
            f"<p><strong>{e(f['diplome'])}</strong> — {e(f['etablissement'])}"
            f"{' · ' + e(_date_en(f['annee']) if langue == 'en' else f['annee']) if f.get('annee') else ''}</p>"
            for f in cv["formations"]
        ]
    if compact >= 2 and cv.get("langues") and cv.get("qualites"):
        langues_txt = " · ".join(
            f"{_langue_lib(x.get('langue'), langue)} ({x.get('niveau')})" for x in cv["langues"]
        )
        blocs += [
            f"<h2>{t['langues']} · {t['qualites']}</h2>",
            f"<p>{e(langues_txt)} — {e(' · '.join(cv['qualites']))}</p>",
        ]
        return _page("\n".join(blocs), langue, ident.get("nom", ""), COMPACTS.get(compact, ""))
    if cv.get("langues"):
        blocs += [
            f"<h2>{t['langues']}</h2>",
            "<p>"
            + " · ".join(
                e(f"{_langue_lib(x.get('langue'), langue)} ({x.get('niveau')})")
                for x in cv["langues"]
            )
            + "</p>",
        ]
    if cv.get("qualites"):
        blocs += [f"<h2>{t['qualites']}</h2>", f"<p>{e(' · '.join(cv['qualites']))}</p>"]
    return _page("\n".join(blocs), langue, ident.get("nom", ""), COMPACTS.get(compact, ""))


def reduire(cv: dict, compact: int) -> dict:
    """Aux derniers paliers, limite le nombre de puces par expérience (les premières, les plus utiles)."""
    maxi = PUCES_MAX.get(compact)
    if not maxi:
        return cv
    return {
        **cv,
        "experiences": [
            {**x, "puces": x.get("puces", [])[:maxi]} for x in cv.get("experiences", [])
        ],
    }


def pages(pdf_octets: bytes) -> int:
    from io import BytesIO

    from pypdf import PdfReader

    return len(PdfReader(BytesIO(pdf_octets)).pages)


def pdf_cv_une_page(cv: dict, ident: dict, langue: str) -> tuple[bytes, int]:
    """PDF du CV sur une seule page : resserre par paliers (en-tête d'abord). Renvoie (pdf, palier)."""
    for compact in range(max(COMPACTS) + 1):
        octets = pdf(html_cv(cv, ident, langue, compact))
        if pages(octets) <= 1:
            return octets, compact
    return octets, compact  # dernier palier : on garde le plus compact


def html_lm(lm: dict, ident: dict, entreprise: str, langue: str) -> str:
    e = escape
    jour = date.today()
    quand = (
        f"{ident.get('ville', '').split('(')[0].strip() + ', ' if ident.get('ville') else ''}"
        + (
            f"le {jour:%d/%m/%Y}"
            if langue == "fr"
            else f"{jour.day} {MOIS_ANGLAIS[jour.month - 1]} {jour.year}"
        )
    )
    corps = "".join(f"<p>{e(p)}</p>" for p in lm.get("paragraphes", []))
    html = (
        f"<div class='lm'><div class='entete'><strong>{e(ident.get('nom') or '')}</strong><br>"
        f"{'<br>'.join(e(c) for c in _contacts(ident)[:4])}</div>"
        f"<div class='dest'><strong>{e(entreprise or '')}</strong><br>{e(quand)}</div>"
        f"<p><strong>{'Objet' if langue == 'fr' else 'Subject'} : {e(lm.get('objet', ''))}</strong></p>"
        f"<p>{e(lm.get('salutation', ''))}</p>{corps}<p>{e(lm.get('formule', ''))}</p>"
        f"<p>{e(ident.get('nom') or '')}</p></div>"
    )
    return _page(html, langue, ident.get("nom", ""))


def _page(corps: str, langue: str, auteur: str, css_en_plus: str = "") -> str:
    return (
        f"<!doctype html><html lang='{langue}'><head><meta charset='utf-8'><title>{escape(auteur)}</title>"
        f"<meta name='author' content='{escape(auteur)}'><style>{CSS}{css_en_plus}</style></head>"
        f"<body>{corps}</body></html>"
    )


def navigateur() -> str:
    for nom in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if chemin := shutil.which(nom):
            return chemin
    raise RuntimeError("Chrome ou Chromium introuvable : nécessaire pour produire les PDF.")


def pdf(html: str) -> bytes:
    """HTML -> PDF avec Chrome sans interface (police et mise en page identiques à l'aperçu)."""
    with tempfile.TemporaryDirectory() as d:
        source, sortie = Path(d) / "doc.html", Path(d) / "doc.pdf"
        source.write_text(html, "utf-8")
        subprocess.run(
            [
                navigateur(),
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-pdf-header-footer",
                f"--user-data-dir={d}/profil",
                f"--print-to-pdf={sortie}",
                source.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=90,
        )
        return sortie.read_bytes()


def _style_docx(document: docx.Document) -> None:
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "Arial", Pt(10)
    for s in document.sections:
        s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Pt(42)


def _titre_section(document: docx.Document, texte: str) -> None:
    p = document.add_paragraph()
    run = p.add_run(texte.upper())
    run.bold, run.font.size, run.font.color.rgb = True, Pt(11), RGBColor(0x17, 0x34, 0x48)
    p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(8), Pt(2)


def docx_cv(cv: dict, ident: dict, langue: str, compact: int = 0) -> bytes:
    t = TITRES[langue]
    cv = reduire(cv, compact)
    d = docx.Document()
    _style_docx(d)
    tete = d.add_paragraph()
    r = tete.add_run(ident.get("nom") or "")
    r.bold, r.font.size = True, Pt(18)
    sous = d.add_paragraph()
    rs = sous.add_run(cv.get("titre", ""))
    rs.bold, rs.font.color.rgb = True, RGBColor(0x08, 0x7F, 0x8C)
    d.add_paragraph(" · ".join(_contacts(ident, court=compact >= 1)))
    if cv.get("accroche"):
        _titre_section(d, t["profil"])
        d.add_paragraph(cv["accroche"])
    if cv.get("competences"):
        _titre_section(d, t["competences"])
        for c in cv["competences"]:
            p = d.add_paragraph()
            p.add_run(f"{c['categorie']} : ").bold = True
            p.add_run(", ".join(c["elements"]))
    if cv.get("experiences"):
        _titre_section(d, t["experiences"])
        for x in cv["experiences"]:
            p = d.add_paragraph()
            p.add_run(x["poste"]).bold = True
            p.add_run(f" — {x['entreprise']}")
            d.add_paragraph(_periode(x, langue))
            for puce in x.get("puces", []):
                d.add_paragraph(puce, style="List Bullet")
    if cv.get("formations"):
        _titre_section(d, t["formations"])
        for f in cv["formations"]:
            p = d.add_paragraph()
            p.add_run(f["diplome"]).bold = True
            p.add_run(f" — {f['etablissement']}" + (f" · {f['annee']}" if f.get("annee") else ""))
    if cv.get("langues"):
        _titre_section(d, t["langues"])
        d.add_paragraph(
            " · ".join(
                f"{_langue_lib(x.get('langue'), langue)} ({x.get('niveau')})" for x in cv["langues"]
            )
        )
    if cv.get("qualites"):
        _titre_section(d, t["qualites"])
        d.add_paragraph(" · ".join(cv["qualites"]))
    d.core_properties.author = ident.get("nom") or ""
    tampon = io.BytesIO()
    d.save(tampon)
    return tampon.getvalue()


def docx_lm(lm: dict, ident: dict, entreprise: str, langue: str) -> bytes:
    d = docx.Document()
    _style_docx(d)
    d.add_paragraph(ident.get("nom") or "").runs[0].bold = True
    for c in _contacts(ident)[:4]:
        d.add_paragraph(c)
    p = d.add_paragraph(entreprise or "")
    p.paragraph_format.space_before = Pt(12)
    d.add_paragraph(f"{date.today():%d/%m/%Y}").alignment = WD_ALIGN_PARAGRAPH.LEFT
    objet = d.add_paragraph()
    objet.add_run(f"{'Objet' if langue == 'fr' else 'Subject'} : {lm.get('objet', '')}").bold = True
    for texte in (
        lm.get("salutation", ""),
        *lm.get("paragraphes", []),
        lm.get("formule", ""),
        ident.get("nom", ""),
    ):
        d.add_paragraph(texte)
    d.core_properties.author = ident.get("nom") or ""
    tampon = io.BytesIO()
    d.save(tampon)
    return tampon.getvalue()
