"""Ingestion des CV/LM (validation, extraction, diagnostic) et import Excel/CSV des candidatures."""

import io
import zipfile
from datetime import date

import docx
import pytest
from openpyxl import Workbook

from app.documents import DocumentRefuse, diagnostic, lire
from app.serveur import lignes_import


def _docx(paragraphes, tableau=None) -> bytes:
    d = docx.Document()
    for p in paragraphes:
        d.add_paragraph(p)
    if tableau:
        t = d.add_table(rows=1, cols=len(tableau))
        for i, v in enumerate(tableau):
            t.rows[0].cells[i].text = v
    tampon = io.BytesIO()
    d.save(tampon)
    return tampon.getvalue()


CV = [
    "Jean Test",
    "jean.test@example.com · 06 12 34 56 78 · linkedin.com/in/jeantest",
    "Profil",
    "Analytics Engineer junior. " * 10,
    "Expériences professionnelles",
    "Data Analyst — Acme (2025-2026). SQL, dbt, Power BI. " * 8,
    "Formation",
    "Master Big Data — Université Test (2026)",
    "Compétences",
    "SQL, Python, dbt, Power BI, Azure, Databricks",
    "Langues",
    "Anglais courant",
]


def test_cv_docx_lisible():
    lecture = lire("cv.docx", _docx(CV))
    d = diagnostic(lecture, "cv")
    assert {"experience", "formation", "competences", "langues", "profil"} <= set(d["sections"])
    assert d["coordonnees"] == {"email": True, "telephone": True, "linkedin": True}
    assert not [a for a in d["alertes"] if "Section" in a]


def test_cv_incomplet_signale():
    d = diagnostic(lire("cv.docx", _docx(["Jean Test", "Quelques mots"])), "cv")
    assert any("experience" in a for a in d["alertes"])
    assert any("mail" in a for a in d["alertes"])


def test_tableau_signale():
    lecture = lire("cv.docx", _docx(CV, tableau=["SQL", "Python"]))
    assert any("tableau" in a for a in lecture.alertes)
    assert "SQL | Python" in lecture.texte


@pytest.mark.parametrize(
    ("nom", "contenu", "motif"),
    [
        ("cv.pdf", b"", "vide"),
        ("cv.doc", b"\xd0\xcf\x11\xe0", "PDF ou en DOCX"),
        ("cv.pdf", b"PK\x03\x04pas un pdf", "signature"),
        ("cv.docx", b"%PDF-1.4 faux", "signature"),
        ("cv.pdf", b"%PDF-1.4\n" + b"x" * (11 * 1024 * 1024), "trop lourd"),
    ],
)
def test_refus(nom, contenu, motif):
    with pytest.raises(DocumentRefuse, match=motif):
        lire(nom, contenu)


def test_docx_avec_macros_refuse():
    brut = io.BytesIO(_docx(CV))
    with zipfile.ZipFile(brut, "a") as z:
        z.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(DocumentRefuse, match="macros"):
        lire("cv.docx", brut.getvalue())


def test_import_csv():
    csv = (
        "Société;Intitulé du poste;Date de candidature;Statut;Lien\n"
        "Orano;Data Analyst;15/09/2026;Refusé;https://orano.example/1\n"
        "Mind7;Data Engineer;2026-10-02;Entretien prévu;\n;;;;\n"
    )
    lignes = lignes_import("suivi.csv", csv.encode())
    assert [(x["entreprise"], x["poste"], x["postule_le"], x["statut"]) for x in lignes] == [
        ("Orano", "Data Analyst", date(2026, 9, 15), "refus"),
        ("Mind7", "Data Engineer", date(2026, 10, 2), "reponse_positive"),
    ]


def test_import_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.append(["Entreprise", "Poste", "Date", "Type"])
    ws.append(["Vicat", "Analytics Engineer", date(2026, 8, 1), "Spontanée"])
    tampon = io.BytesIO()
    wb.save(tampon)
    (ligne,) = lignes_import("suivi.xlsx", tampon.getvalue())
    assert (ligne["entreprise"], ligne["type"], ligne["statut"]) == (
        "Vicat",
        "spontanee",
        "postule",
    )
