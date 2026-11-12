"""Serveur de l'appli locale : API JSON sur Postgres + interface web (app/web/).

Lancement :  python -m app   (ouvre http://127.0.0.1:8765 dans le navigateur)
Le serveur n'écoute que sur 127.0.0.1 : il n'est pas joignable depuis le réseau.
"""

import csv
import io
import json
import shutil
import subprocess
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import criteres, documents, llm, orchestrateur
from app.ats import service as ats
from app.collecte.normalisation import sans_accents
from app.config import RACINE
from app.db import connexion
from app.dedup import candidature_recente
from app.entreprises import entreprise_id
from app.pertinence import est_data

WEB = Path(__file__).parent / "web"
app = FastAPI(title="EMploi_App", docs_url="/api/docs", redoc_url=None)

STATUTS_CANDIDATURE = (
    "a_postuler",
    "postule",
    "relance",
    "reponse_positive",
    "refus",
    "sans_suite",
    "ignore",
)
LIBELLES = {
    "a_postuler": "À postuler",
    "postule": "Postulé",
    "relance": "Relancé",
    "reponse_positive": "Réponse positive",
    "refus": "Refus",
    "sans_suite": "Sans suite",
    "ignore": "Non remise",
    "a_verifier": "À vérifier",
    "valide": "Valide",
    "expiree": "Expirée",
    "pourvue": "Pourvue",
    "ancienne": "Ancienne",
    "doublon": "Doublon",
}


def db():
    with connexion() as conn:
        yield conn


Conn = psycopg.Connection


# --- Vue globale --------------------------------------------------------------------------------
@app.get("/api/resume")
def resume(conn: Conn = Depends(db)):
    offres = conn.execute(
        "SELECT statut_validite, fiche_complete, intitule, source FROM offres"
    ).fetchall()
    par_statut: dict[str, int] = {}
    data_ouvertes = 0
    for o in offres:
        par_statut[o["statut_validite"]] = par_statut.get(o["statut_validite"], 0) + 1
        if o["statut_validite"] in ("a_verifier", "valide") and est_data(o["intitule"] or ""):
            data_ouvertes += 1
    cand = {
        r["statut"]: r["n"]
        for r in conn.execute("SELECT statut, count(*) AS n FROM candidatures GROUP BY 1")
    }
    semaines = conn.execute(
        """
        SELECT date_trunc('week', postule_le)::date AS semaine, count(*) AS n,
               count(*) FILTER (WHERE type = 'spontanee') AS spontanees
        FROM candidatures WHERE postule_le >= current_date - 84 AND statut <> 'ignore'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    a_confirmer = conn.execute(
        "SELECT count(*) AS n FROM reponses WHERE a_confirmer AND confirme_le IS NULL"
    ).fetchone()["n"]
    docs = conn.execute(
        "SELECT type, nom_fichier, diagnostic, depose_le FROM documents WHERE par_defaut"
    ).fetchall()
    total = sum(v for k, v in cand.items() if k != "ignore")
    ats_cv = ats.derniere(conn, "cv")
    matching = conn.execute(
        "SELECT count(*) AS n, round(avg(score_matching), 1) AS moyenne FROM analyses"
        " WHERE type = 'matching' AND cree_le > now() - interval '30 days'"
    ).fetchone()
    return {
        "offres": {
            "total": len(offres),
            "par_statut": par_statut,
            "data_ouvertes": data_ouvertes,
            "fiches_completes": sum(1 for o in offres if o["fiche_complete"]),
        },
        "candidatures": {
            "total": total,
            "en_cours": cand.get("postule", 0) + cand.get("relance", 0) + cand.get("a_postuler", 0),
            "entretiens": cand.get("reponse_positive", 0),
            "refus": cand.get("refus", 0),
            "par_statut": cand,
            "taux_reponse": round(
                100 * (cand.get("refus", 0) + cand.get("reponse_positive", 0)) / total, 1
            )
            if total
            else 0,
        },
        "semaines": semaines,
        "reponses_a_confirmer": a_confirmer,
        "documents": docs,
        "ats": {
            "disponible": llm.disponible(),
            "score_ats": ats_cv and ats_cv["score_ats"],
            "score_qualite": ats_cv and ats_cv["score_qualite"],
            "analyse_le": ats_cv and ats_cv["cree_le"],
            "matchings": matching["n"],
            "matching_moyen": matching["moyenne"],
            "historique": [h for h in ats.historique(conn) if h["type"] == "cv"][:8],
        },
    }


# --- Offres -----------------------------------------------------------------------------------
@app.get("/api/offres")
def offres(
    statut: str = "a_verifier,valide",
    data: bool = True,
    q: str = "",
    source: str = "",
    limite: int = Query(150, le=500),
    conn: Conn = Depends(db),
):
    statuts = [s for s in statut.split(",") if s]
    rows = conn.execute(
        """
        SELECT o.id, o.intitule, o.ville, o.contrat, o.source, o.statut_validite, o.fiche_complete,
               o.url_cle, o.url_fiche, o.recu_le, o.date_publication, o.note_validite, o.score,
               o.classe, o.entreprise_id, e.nom_affiche AS entreprise, e.ats,
               (SELECT c.statut FROM candidatures c WHERE c.offre_id = o.id) AS candidature,
               (SELECT a.score_matching FROM analyses a WHERE a.offre_id = o.id
                ORDER BY a.cree_le DESC LIMIT 1) AS score_matching
        FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id
        WHERE (%(tous)s OR o.statut_validite = ANY(%(statuts)s))
          AND (%(source)s = '' OR o.source = %(source)s)
          AND (%(q)s = '' OR o.intitule ILIKE %(motif)s OR e.nom_affiche ILIKE %(motif)s)
        ORDER BY o.recu_le DESC
        """,
        {"tous": not statuts, "statuts": statuts, "source": source, "q": q, "motif": f"%{q}%"},
    ).fetchall()
    if data:
        rows = [r for r in rows if est_data(r["intitule"] or "")]
    rows = rows[:limite]
    recentes = {}
    for r in rows:
        eid = r["entreprise_id"]
        if eid and eid not in recentes:
            recentes[eid] = candidature_recente(conn, eid)
        r["candidature_recente"] = recentes.get(eid)
        r["lien"] = r["url_fiche"] or (r["url_cle"] if r["url_cle"].startswith("http") else None)
    return rows


@app.get("/api/offres/{offre_id}")
def offre(offre_id: int, conn: Conn = Depends(db)):
    row = conn.execute(
        """
        SELECT o.*, e.nom_affiche AS entreprise, e.ats, e.ats_slug
        FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id WHERE o.id = %s
        """,
        (offre_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Offre introuvable")
    if row["doublon_de"]:
        row["original"] = conn.execute(
            "SELECT id, source, intitule FROM offres WHERE id = %s", (row["doublon_de"],)
        ).fetchone()
    return row


class Fiche(BaseModel):
    texte: str = Field(min_length=200, max_length=50_000)
    url: str | None = None


@app.post("/api/offres/{offre_id}/fiche")
def coller_fiche(offre_id: int, fiche: Fiche, conn: Conn = Depends(db)):
    """Bouton « Coller la fiche » : la fiche complète lève le plafond de score à 84."""
    n = conn.execute(
        """
        UPDATE offres SET texte = %s, fiche_complete = TRUE, url_fiche = COALESCE(%s, url_fiche),
               statut_validite = CASE WHEN statut_validite = 'a_verifier' THEN 'valide'
                                      ELSE statut_validite END,
               verifiee_le = now(), note_validite = 'fiche collée à la main'
        WHERE id = %s
        """,
        (fiche.texte.strip(), fiche.url, offre_id),
    ).rowcount
    if not n:
        raise HTTPException(404, "Offre introuvable")
    return {"ok": True}


@app.post("/api/offres/{offre_id}/postule")
def j_ai_postule(offre_id: int, conn: Conn = Depends(db)):
    """Bouton « J'ai postulé » : crée la ligne de suivi (une seule par offre)."""
    o = conn.execute(
        "SELECT id, entreprise_id, intitule, url_cle, url_fiche FROM offres WHERE id = %s",
        (offre_id,),
    ).fetchone()
    if o is None:
        raise HTTPException(404, "Offre introuvable")
    lien = o["url_fiche"] or (o["url_cle"] if o["url_cle"].startswith("http") else None)
    row = conn.execute(
        """
        INSERT INTO candidatures (offre_id, entreprise_id, statut, postule_le, origine, poste, url, canal)
        VALUES (%s, %s, 'postule', current_date, 'agent', %s, %s, 'outil')
        ON CONFLICT (offre_id) DO UPDATE SET statut = 'postule', postule_le = current_date,
                                             maj_le = now()
        RETURNING id
        """,
        (o["id"], o["entreprise_id"], o["intitule"], lien),
    ).fetchone()
    conn.execute(
        "UPDATE entreprises SET derniere_candidature = current_date WHERE id = %s",
        (o["entreprise_id"],),
    )
    conn.execute(
        "UPDATE offres SET etape = 'envoyee', etape_maj_le = now() WHERE id = %s", (o["id"],)
    )
    return {"candidature_id": row["id"]}


# --- Candidatures -----------------------------------------------------------------------------
@app.get("/api/candidatures")
def candidatures(statut: str = "", type: str = "", q: str = "", conn: Conn = Depends(db)):
    return conn.execute(
        """
        SELECT c.id, c.type, c.statut, c.postule_le, c.relance_le, c.canal, c.poste, c.notes,
               c.url, c.origine, c.offre_id, e.nom_affiche AS entreprise,
               (SELECT count(*) FROM reponses r WHERE r.candidature_id = c.id) AS nb_reponses,
               (SELECT max(r.recu_le) FROM reponses r WHERE r.candidature_id = c.id) AS derniere_reponse
        FROM candidatures c LEFT JOIN entreprises e ON e.id = c.entreprise_id
        WHERE (%(statut)s = '' OR c.statut = %(statut)s)
          AND (%(type)s = '' OR c.type = %(type)s)
          AND (%(q)s = '' OR e.nom_affiche ILIKE %(motif)s OR c.poste ILIKE %(motif)s)
        ORDER BY c.postule_le DESC NULLS LAST, c.id DESC
        """,
        {"statut": statut, "type": type, "q": q, "motif": f"%{q}%"},
    ).fetchall()


class NouvelleCandidature(BaseModel):
    entreprise: str = Field(min_length=1, max_length=120)
    poste: str = Field("", max_length=200)
    postule_le: date | None = None
    statut: Literal[STATUTS_CANDIDATURE] = "postule"  # type: ignore[valid-type]
    type: Literal["offre", "spontanee"] = "offre"
    url: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=5000)


def _ajouter(conn: Conn, c: NouvelleCandidature) -> int | None:
    """Ajoute une candidature faite hors outil ; None si elle existe déjà (même employeur, poste, date)."""
    eid = entreprise_id(conn, c.entreprise.strip())
    jour = c.postule_le or date.today()
    deja = conn.execute(
        "SELECT id FROM candidatures WHERE entreprise_id = %s AND postule_le = %s"
        " AND coalesce(lower(poste), '') = lower(%s)",
        (eid, jour, c.poste.strip()),
    ).fetchone()
    if deja:
        return None
    row = conn.execute(
        """
        INSERT INTO candidatures (entreprise_id, type, statut, postule_le, origine, poste, url,
                                  notes, canal)
        VALUES (%s, %s, %s, %s, 'manuelle', NULLIF(%s, ''), %s, %s, 'saisie') RETURNING id
        """,
        (eid, c.type, c.statut, jour, c.poste.strip(), c.url, c.notes),
    ).fetchone()
    conn.execute(
        "UPDATE entreprises SET derniere_candidature = GREATEST(derniere_candidature, %s)"
        " WHERE id = %s",
        (jour, eid),
    )
    return row["id"]


@app.post("/api/candidatures")
def ajouter_candidature(c: NouvelleCandidature, conn: Conn = Depends(db)):
    cid = _ajouter(conn, c)
    if cid is None:
        raise HTTPException(409, "Cette candidature existe déjà (même entreprise, poste et date).")
    return {"id": cid}


class MajCandidature(BaseModel):
    statut: Literal[STATUTS_CANDIDATURE] | None = None  # type: ignore[valid-type]
    notes: str | None = Field(None, max_length=5000)


@app.patch("/api/candidatures/{cid}")
def maj_candidature(cid: int, maj: MajCandidature, conn: Conn = Depends(db)):
    n = conn.execute(
        "UPDATE candidatures SET statut = COALESCE(%s, statut), notes = COALESCE(%s, notes),"
        " maj_le = now() WHERE id = %s",
        (maj.statut, maj.notes, cid),
    ).rowcount
    if not n:
        raise HTTPException(404, "Candidature introuvable")
    return {"ok": True}


_COLONNES = {
    "entreprise": ("entreprise", "societe", "company", "employeur", "structure"),
    "poste": ("poste", "intitule", "titre", "title", "job", "offre"),
    "postule_le": ("date", "postule", "candidature", "envoi"),
    "statut": ("statut", "status", "etat", "resultat", "reponse"),
    "url": ("lien", "url", "link", "annonce"),
    "notes": ("notes", "note", "commentaire", "remarque"),
    "type": ("type",),
}
_STATUTS_LIBRES = {
    "refus": "refus",
    "refuse": "refus",
    "negatif": "refus",
    "rejected": "refus",
    "entretien": "reponse_positive",
    "positif": "reponse_positive",
    "interview": "reponse_positive",
    "relance": "relance",
    "sans suite": "sans_suite",
    "sans reponse": "sans_suite",
    "en attente": "postule",
    "envoye": "postule",
    "postule": "postule",
    "en cours": "postule",
}


def _cellule_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if v:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(str(v).strip(), fmt).date()
            except ValueError:
                continue
    return None


def lignes_import(nom: str, contenu: bytes) -> list[dict]:
    """Lit un .xlsx ou un .csv de candidatures et repère les colonnes par leur titre."""
    if nom.lower().endswith(".xlsx"):
        feuille = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True).active
        tableau = [list(r) for r in feuille.iter_rows(values_only=True)]
    elif nom.lower().endswith(".csv"):
        texte = contenu.decode("utf-8-sig", errors="replace")
        dialecte = csv.Sniffer().sniff(texte[:2000], delimiters=",;\t")
        tableau = list(csv.reader(io.StringIO(texte), dialecte))
    else:
        raise HTTPException(400, "Fichier .xlsx ou .csv attendu.")
    if not tableau:
        return []
    entetes = [sans_accents(str(h or "")).lower().strip() for h in tableau[0]]
    index = {}
    for champ, mots in _COLONNES.items():
        for i, h in enumerate(entetes):
            if i not in index.values() and any(m in h for m in mots):
                index[champ] = i
                break
    if "entreprise" not in index:
        raise HTTPException(400, "Colonne « Entreprise » introuvable dans la première ligne.")
    lignes = []
    for brut in tableau[1:]:
        val = {c: (brut[i] if i < len(brut) else None) for c, i in index.items()}
        if not val.get("entreprise"):
            continue
        statut_libre = sans_accents(str(val.get("statut") or "")).lower().strip()
        statut = next((s for k, s in _STATUTS_LIBRES.items() if k in statut_libre), "postule")
        type_libre = sans_accents(str(val.get("type") or "")).lower()
        lignes.append(
            {
                "entreprise": str(val["entreprise"]).strip(),
                "poste": str(val.get("poste") or "").strip(),
                "postule_le": _cellule_date(val.get("postule_le")),
                "statut": statut,
                "type": "spontanee" if "spont" in type_libre else "offre",
                "url": str(val["url"]).strip() if val.get("url") else None,
                "notes": str(val["notes"]).strip() if val.get("notes") else None,
            }
        )
    return lignes


@app.post("/api/candidatures/import")
async def importer_candidatures(fichier: UploadFile = File(...), conn: Conn = Depends(db)):
    """Section « candidatures déjà effectuées » : import d'un Excel ou d'un CSV."""
    contenu = await fichier.read()
    if len(contenu) > 5 * 1024 * 1024:
        raise HTTPException(400, "Fichier trop lourd (5 Mo maximum).")
    ajoutees = doublons = 0
    for ligne in lignes_import(fichier.filename or "", contenu):
        if _ajouter(conn, NouvelleCandidature(**ligne)) is None:
            doublons += 1
        else:
            ajoutees += 1
    return {"ajoutees": ajoutees, "deja_presentes": doublons}


def classeur_excel(conn: Conn) -> bytes:
    """Classeur Excel : candidatures, réponses et offres ouvertes (aussi utilisé par le travailleur)."""
    classeur = Workbook()
    entete = Font(bold=True, color="FFFFFF")
    fond = PatternFill("solid", fgColor="173448")

    def feuille(titre, colonnes, lignes, premiere=False):
        ws = classeur.active if premiere else classeur.create_sheet()
        ws.title = titre
        ws.append([c[0] for c in colonnes])
        for cell in ws[1]:
            cell.font, cell.fill = entete, fond
        for r in lignes:
            ws.append(
                [
                    LIBELLES.get(r[c[1]], r[c[1]])
                    if c[1] in ("statut", "statut_validite")
                    else r[c[1]]
                    for c in colonnes
                ]
            )
        for i, c in enumerate(colonnes, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = c[2]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    feuille(
        "Candidatures",
        [
            ("Date", "postule_le", 12),
            ("Entreprise", "entreprise", 28),
            ("Poste", "poste", 40),
            ("Type", "type", 11),
            ("Statut", "statut", 16),
            ("Canal", "canal", 16),
            ("Réponses", "nb_reponses", 10),
            ("Lien", "url", 40),
            ("Notes", "notes", 40),
        ],
        candidatures(conn=conn),
        premiere=True,
    )
    feuille(
        "Réponses",
        [
            ("Reçue le", "recu", 12),
            ("Entreprise", "entreprise", 28),
            ("Classement", "classement", 12),
            ("Confiance", "confiance", 10),
            ("Objet", "extrait", 60),
        ],
        conn.execute("""
                SELECT r.recu_le::date AS recu, e.nom_affiche AS entreprise, r.classement,
                       r.confiance, r.extrait
                FROM reponses r JOIN candidatures c ON c.id = r.candidature_id
                LEFT JOIN entreprises e ON e.id = c.entreprise_id ORDER BY r.recu_le DESC
            """).fetchall(),
    )
    feuille(
        "Offres ouvertes",
        [
            ("Reçue le", "recu", 12),
            ("Entreprise", "entreprise", 28),
            ("Intitulé", "intitule", 45),
            ("Ville", "ville", 18),
            ("Source", "source", 10),
            ("Statut", "statut_validite", 12),
            ("Lien", "lien", 45),
        ],
        [
            dict(r, recu=r["recu_le"].date())
            for r in offres(
                statut="a_verifier,valide", data=True, q="", source="", limite=500, conn=conn
            )
        ],
    )
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


@app.get("/api/candidatures/export.xlsx")
def exporter(conn: Conn = Depends(db)):
    """Export Excel de l'état de toutes les candidatures (+ réponses et offres ouvertes)."""
    nom = f"candidatures_{date.today():%Y-%m-%d}.xlsx"
    return StreamingResponse(
        io.BytesIO(classeur_excel(conn)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


# --- Réponses à confirmer ---------------------------------------------------------------------
@app.get("/api/reponses")
def reponses(a_confirmer: bool = False, conn: Conn = Depends(db)):
    return conn.execute(
        """
        SELECT r.id, r.recu_le, r.expediteur, r.classement, r.confiance, r.a_confirmer,
               r.confirme_le, r.extrait, c.id AS candidature_id, c.poste,
               e.nom_affiche AS entreprise
        FROM reponses r JOIN candidatures c ON c.id = r.candidature_id
        LEFT JOIN entreprises e ON e.id = c.entreprise_id
        WHERE NOT %s OR (r.a_confirmer AND r.confirme_le IS NULL)
        ORDER BY r.recu_le DESC LIMIT 200
        """,
        (a_confirmer,),
    ).fetchall()


class Confirmation(BaseModel):
    classement: Literal["positive", "negative", "neutre"]


@app.post("/api/reponses/{rid}/confirmer")
def confirmer(rid: int, c: Confirmation, conn: Conn = Depends(db)):
    row = conn.execute(
        "UPDATE reponses SET classement = %s, a_confirmer = FALSE, confirme_le = now()"
        " WHERE id = %s RETURNING candidature_id",
        (c.classement, rid),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Réponse introuvable")
    statut = {"positive": "reponse_positive", "negative": "refus"}.get(c.classement)
    if statut:
        conn.execute(
            "UPDATE candidatures SET statut = %s, maj_le = now() WHERE id = %s",
            (statut, row["candidature_id"]),
        )
    return {"ok": True}


# --- Documents (CV, LM) ---------------------------------------------------------------------
@app.get("/api/documents")
def liste_documents(conn: Conn = Depends(db)):
    return conn.execute(
        "SELECT id, type, langue, nom_fichier, format, taille, diagnostic, par_defaut, depose_le"
        " FROM documents ORDER BY type, depose_le DESC"
    ).fetchall()


@app.post("/api/documents")
async def deposer_document(
    type: Literal["cv", "lm"] = Form(...), fichier: UploadFile = File(...), conn: Conn = Depends(db)
):
    contenu = await fichier.read(documents.TAILLE_MAX + 1)
    try:
        doc = documents.deposer(conn, type, fichier.filename or "document", contenu)
    except documents.DocumentRefuse as e:
        raise HTTPException(400, str(e)) from e
    doc.pop("texte", None)
    return doc


@app.get("/api/documents/{did}/fichier")
def fichier_document(did: int, conn: Conn = Depends(db)):
    d = conn.execute("SELECT chemin, nom_fichier FROM documents WHERE id = %s", (did,)).fetchone()
    if d is None:
        raise HTTPException(404, "Document introuvable")
    chemin = (RACINE / d["chemin"]).resolve()
    if documents.DOSSIER.resolve() not in chemin.parents or not chemin.exists():
        raise HTTPException(404, "Fichier absent")
    return FileResponse(chemin, filename=d["nom_fichier"])


@app.get("/api/documents/{did}/texte")
def texte_document(did: int, conn: Conn = Depends(db)):
    """Ce qu'un ATS « voit » du document."""
    d = conn.execute("SELECT texte FROM documents WHERE id = %s", (did,)).fetchone()
    if d is None:
        raise HTTPException(404, "Document introuvable")
    return {"texte": d["texte"]}


@app.post("/api/documents/{did}/defaut")
def mettre_par_defaut(did: int, conn: Conn = Depends(db)):
    d = conn.execute("SELECT type, langue FROM documents WHERE id = %s", (did,)).fetchone()
    if d is None:
        raise HTTPException(404, "Document introuvable")
    conn.execute(
        "UPDATE documents SET par_defaut = FALSE WHERE type = %s AND langue = %s",
        (d["type"], d["langue"]),
    )
    conn.execute("UPDATE documents SET par_defaut = TRUE WHERE id = %s", (did,))
    return {"ok": True}


# --- Moteur ATS --------------------------------------------------------------------------------
def _ats(appel):
    try:
        return appel()
    except ats.AnalyseImpossible as e:
        raise HTTPException(400, str(e)) from e
    except llm.LLMIndisponible as e:
        raise HTTPException(503, str(e)) from e


@app.post("/api/ats/cv")
def analyser_cv(forcer: bool = False, conn: Conn = Depends(db)):
    """Score ATS (local) + lecture et qualité (Gemini) du CV par défaut. Même CV = même résultat."""
    return _ats(lambda: ats.analyser_cv(conn, forcer=forcer))


@app.get("/api/ats/cv")
def derniere_analyse_cv(conn: Conn = Depends(db)):
    return ats.derniere(conn, "cv")


@app.post("/api/ats/matching/{offre_id}")
def matcher(offre_id: int, forcer: bool = False, conn: Conn = Depends(db)):
    return _ats(lambda: ats.matcher_offre(conn, offre_id, forcer))


@app.get("/api/ats/matching/{offre_id}")
def dernier_matching(offre_id: int, conn: Conn = Depends(db)):
    return ats.derniere(conn, "matching", offre_id)


@app.get("/api/ats/historique")
def historique_ats(conn: Conn = Depends(db)):
    return ats.historique(conn)


# --- Pipeline (kanban) -----------------------------------------------------------------------
ETAPES = ("reperee", "qualifiee", "fabrication", "ats_friendly", "a_envoyer", "envoyee")


@app.get("/api/pipeline")
def pipeline(conn: Conn = Depends(db)):
    rows = conn.execute(
        """
        SELECT o.id, o.intitule, o.ville, o.source, o.etape, o.etape_maj_le, o.fiche_complete,
               o.statut_validite, o.dossier, o.modifiee_le, o.recu_le, o.date_publication,
               e.nom_affiche AS entreprise,
               (SELECT a.score_matching FROM analyses a WHERE a.offre_id = o.id
                ORDER BY a.cree_le DESC LIMIT 1) AS score_matching
        FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id
        WHERE o.etape IS NOT NULL
          AND (o.etape NOT IN ('reperee', 'qualifiee') OR o.statut_validite IN ('a_verifier', 'valide'))
        """
    ).fetchall()
    colonnes = {e: [] for e in ETAPES}
    for r in rows:
        colonnes[r["etape"]].append(r)
    for liste in colonnes.values():
        liste.sort(
            key=lambda r: (
                -(r["score_matching"] or -1),
                -(r["fiche_complete"] or 0),
                -(r["etape_maj_le"] or r["recu_le"]).timestamp(),
            )
        )
    return {e: {"total": len(liste), "offres": liste[:60]} for e, liste in colonnes.items()}


class Etape(BaseModel):
    etape: Literal[ETAPES] | None  # type: ignore[valid-type]


@app.post("/api/offres/{offre_id}/etape")
def changer_etape(offre_id: int, e: Etape, conn: Conn = Depends(db)):
    """Déplacement manuel dans le pipeline. « En fabrication » crée le dossier et offre.txt."""
    if e.etape == "envoyee":
        return j_ai_postule(offre_id, conn)
    dossier = None
    if e.etape == "fabrication":  # CV + LM fabriqués par le travailleur du PC
        conn.execute(
            "INSERT INTO demandes (type, parametres) VALUES ('fabriquer', %s)",
            (Jsonb({"offre_id": offre_id}),),
        )
        dossier = "en cours de création sur le PC"
    n = conn.execute(
        "UPDATE offres SET etape = %s, etape_maj_le = now() WHERE id = %s", (e.etape, offre_id)
    ).rowcount
    if not n:
        raise HTTPException(404, "Offre introuvable")
    return {"ok": True, "dossier": dossier}


# --- Relances --------------------------------------------------------------------------------
@app.get("/api/relances")
def relances(conn: Conn = Depends(db)):
    return conn.execute("SELECT * FROM v_relances ORDER BY due_le").fetchall()


@app.post("/api/candidatures/{cid}/relance")
def marquer_relance(cid: int, conn: Conn = Depends(db)):
    try:
        return {"numero": conn.execute("SELECT marquer_relance(%s) AS n", (cid,)).fetchone()["n"]}
    except psycopg.errors.RaiseException as e:
        raise HTTPException(400, str(e).splitlines()[0]) from e


@app.post("/api/candidatures/{cid}/ignorer-relance")
def ignorer_relance(cid: int, conn: Conn = Depends(db)):
    conn.execute("SELECT ignorer_relance(%s)", (cid,))
    return {"ok": True}


# --- Candidatures spontanées (brouillons, envoi par toi) ------------------------------------
class CibleSpontanee(BaseModel):
    entreprise: str = Field(min_length=1, max_length=200)
    site_web: str | None = None
    email: str | None = None


@app.get("/api/spontanees")
def spontanees(conn: Conn = Depends(db)):
    return conn.execute(
        "SELECT * FROM v_spontanees ORDER BY (statut = 'brouillon') DESC, cree_le DESC LIMIT 50"
    ).fetchall()


@app.post("/api/spontanees")
def preparer_spontanee(c: CibleSpontanee, conn: Conn = Depends(db)):
    from app.spontanees import agent as sp

    try:
        eid = sp.ajouter_cible(conn, c.entreprise, c.site_web, c.email)
        conn.commit()
        return sp.preparer(conn, eid)
    except (sp.SpontaneeImpossible, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    except llm.LLMIndisponible as e:
        raise HTTPException(503, str(e)) from e


class MajSpontanee(BaseModel):
    objet: str = Field(min_length=1, max_length=300)
    corps: str = Field(min_length=1, max_length=8000)


@app.put("/api/spontanees/{sid}")
def modifier_spontanee(sid: int, m: MajSpontanee, conn: Conn = Depends(db)):
    n = conn.execute(
        "UPDATE spontanees SET objet = %s, corps = %s WHERE id = %s AND statut = 'brouillon'",
        (m.objet, m.corps, sid),
    ).rowcount
    if not n:
        raise HTTPException(404, "Brouillon introuvable ou déjà traité")
    return {"ok": True}


@app.post("/api/spontanees/{sid}/envoyee")
def spontanee_envoyee(sid: int, conn: Conn = Depends(db)):
    try:
        cid = conn.execute("SELECT spontanee_envoyee(%s) AS c", (sid,)).fetchone()["c"]
    except psycopg.errors.RaiseException as e:
        raise HTTPException(400, str(e).splitlines()[0]) from e
    return {"candidature_id": cid}


@app.post("/api/spontanees/{sid}/ignorer")
def ignorer_spontanee(sid: int, conn: Conn = Depends(db)):
    conn.execute(
        "UPDATE spontanees SET statut = 'ignoree' WHERE id = %s AND statut = 'brouillon'", (sid,)
    )
    return {"ok": True}


@app.post("/api/spontanees/{sid}/opposition")
def opposition_spontanee(sid: int, conn: Conn = Depends(db)):
    conn.execute("SELECT spontanee_opposition(%s)", (sid,))
    return {"ok": True}


# --- Fabrication des dossiers (agent Dossier) ---------------------------------------------
@app.get("/api/fabrications")
def fabrications(offre_id: int, conn: Conn = Depends(db)):
    return conn.execute(
        "SELECT * FROM v_fabrications WHERE offre_id = %s ORDER BY cree_le DESC LIMIT 10",
        (offre_id,),
    ).fetchall()


@app.post("/api/offres/{offre_id}/fabriquer")
def fabriquer_dossier(offre_id: int, langue: str | None = None, conn: Conn = Depends(db)):
    """CV + LM adaptés à l'offre (2 à 4 min) : adaptation, garde-fou, rendu, relecture ATS."""
    from app.generation.fabrique import FabricationImpossible, fabriquer

    conn.execute(
        "UPDATE offres SET etape = 'fabrication', etape_maj_le = now() WHERE id = %s"
        " AND (etape IS NULL OR etape IN ('reperee', 'qualifiee'))",
        (offre_id,),
    )
    conn.commit()
    try:
        return fabriquer(conn, offre_id, langue if langue in ("fr", "en") else None)
    except (FabricationImpossible, ats.AnalyseImpossible) as e:
        raise HTTPException(400, str(e)) from e
    except llm.LLMIndisponible as e:
        raise HTTPException(503, str(e)) from e


@app.post("/api/fabrications/{fid}/valider")
def valider_fabrication(fid: int, conn: Conn = Depends(db)):
    try:
        conn.execute("SELECT valider_fabrication(%s)", (fid,))
    except psycopg.errors.RaiseException as e:
        raise HTTPException(400, str(e).splitlines()[0]) from e
    return {"ok": True}


@app.get("/api/fichiers/{fid}")
def fichier_dossier(fid: int, conn: Conn = Depends(db)):
    f = conn.execute(
        "SELECT nom, format, contenu FROM dossier_fichiers WHERE id = %s", (fid,)
    ).fetchone()
    if f is None:
        raise HTTPException(404, "Fichier introuvable")
    types = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return StreamingResponse(
        io.BytesIO(bytes(f["contenu"])),
        media_type=types[f["format"]],
        headers={"Content-Disposition": f'attachment; filename="{f["nom"]}"'},
    )


# --- Veille : entreprises surveillées, critères, lancement -----------------------------------
@app.get("/api/veille/entreprises")
def entreprises_surveillees(conn: Conn = Depends(db)):
    return conn.execute(
        """
        SELECT v.id, v.origine, v.active, v.ajoutee_le, v.derniere_veille, v.note,
               e.id AS entreprise_id, e.nom_affiche, e.ats, e.ats_slug,
               (SELECT count(*) FROM offres o WHERE o.entreprise_id = e.id AND o.etape IS NOT NULL
                AND o.statut_validite IN ('a_verifier', 'valide')) AS offres_pipeline
        FROM veille_entreprises v JOIN entreprises e ON e.id = v.entreprise_id
        ORDER BY v.active DESC, e.nom_affiche
        """
    ).fetchall()


class NouvelleVeille(BaseModel):
    nom: str = Field(min_length=2, max_length=120)


@app.post("/api/veille/entreprises")
def surveiller(v: NouvelleVeille, conn: Conn = Depends(db)):
    eid = entreprise_id(conn, v.nom.strip())
    conn.execute(
        "INSERT INTO veille_entreprises (entreprise_id, origine) VALUES (%s, 'manuelle')"
        " ON CONFLICT (entreprise_id) DO UPDATE SET active = TRUE",
        (eid,),
    )
    return {"entreprise_id": eid}


@app.get("/api/veille/suggestions")
def suggestions(q: str = "", conn: Conn = Depends(db)):
    """Entreprises connues (alertes, candidatures) pour l'autocomplétion."""
    if len(q.strip()) < 2:
        return []
    return [
        r["nom_affiche"]
        for r in conn.execute(
            "SELECT nom_affiche FROM entreprises WHERE nom_affiche ILIKE %s ORDER BY length(nom_affiche)"
            " LIMIT 10",
            (f"%{q.strip()}%",),
        )
    ]


class MajVeille(BaseModel):
    active: bool


@app.patch("/api/veille/entreprises/{vid}")
def activer(vid: int, m: MajVeille, conn: Conn = Depends(db)):
    conn.execute("UPDATE veille_entreprises SET active = %s WHERE id = %s", (m.active, vid))
    return {"ok": True}


@app.delete("/api/veille/entreprises/{vid}")
def ne_plus_surveiller(vid: int, conn: Conn = Depends(db)):
    conn.execute("DELETE FROM veille_entreprises WHERE id = %s", (vid,))
    return {"ok": True}


@app.get("/api/parametres")
def lire_parametres(conn: Conn = Depends(db)):
    return criteres.lire(conn)


class Parametres(BaseModel):
    criteres: dict | None = None
    seuils: dict | None = None
    veille: dict | None = None
    identite: dict | None = None


@app.put("/api/parametres")
def ecrire_parametres(p: Parametres, conn: Conn = Depends(db)):
    actuels = criteres.lire(conn)
    for cle in ("criteres", "seuils", "veille", "identite"):
        valeur = getattr(p, cle)
        if valeur is not None:
            criteres.ecrire(conn, cle, {**actuels[cle], **valeur})
    return criteres.lire(conn)


_passage = {"en_cours": False, "debut": None, "bilan": None}


@app.post("/api/veille/lancer")
def lancer_veille(gmail: bool = True):
    """Lance un passage complet des agents en arrière-plan (le salon montre l'avancement)."""
    if _passage["en_cours"]:
        return {"deja_en_cours": True}

    def tourner():
        _passage.update(en_cours=True, debut=datetime.now(), bilan=None)
        try:
            etapes = (
                orchestrateur.ETAPES
                if gmail
                else tuple(x for x in orchestrateur.ETAPES if x != "collecte")
            )
            _passage["bilan"] = orchestrateur.passage(etapes)
        finally:
            _passage["en_cours"] = False

    threading.Thread(target=tourner, daemon=True).start()
    return {"lance": True}


@app.get("/api/veille/etat")
def etat_veille():
    return _passage


# --- Notifications -------------------------------------------------------------------------
@app.get("/api/notifications")
def notifications(conn: Conn = Depends(db)):
    return conn.execute(
        "SELECT id, offre_id, type, titre, message, lue, cree_le FROM notifications"
        " ORDER BY cree_le DESC LIMIT 40"
    ).fetchall()


@app.post("/api/notifications/lues")
def tout_lu(conn: Conn = Depends(db)):
    conn.execute("UPDATE notifications SET lue = TRUE WHERE NOT lue")
    return {"ok": True}


# --- Accès depuis le téléphone (Tailscale + PWA) --------------------------------------------
def _tailscale() -> dict:
    """État de Tailscale sur ce PC : installé, connecté, adresse HTTPS servie par « tailscale serve »."""
    if shutil.which("tailscale") is None:
        return {"installe": False}
    try:
        statut = json.loads(
            subprocess.run(
                ["tailscale", "status", "--json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            or "{}"
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"installe": True, "connecte": False}
    moi = statut.get("Self") or {}
    nom = (moi.get("DNSName") or "").rstrip(".")
    connecte = statut.get("BackendState") == "Running"
    try:
        servi = subprocess.run(
            ["tailscale", "serve", "status"], check=False, capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        servi = ""
    return {
        "installe": True,
        "connecte": connecte,
        "nom": nom,
        "https": f"https://{nom}/" if nom else None,
        "servi": bool(nom) and nom in servi and "8765" in servi,
    }


def _qr_svg(texte: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(texte, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


@app.get("/api/acces")
def acces():
    ts = _tailscale()
    url = ts.get("https") if ts.get("servi") else None
    return {"tailscale": ts, "url": url, "qr": _qr_svg(url) if url else None}


# --- Journal (futur « salon » des agents) --------------------------------------------------
@app.get("/api/rapports")
def rapports(conn: Conn = Depends(db)):
    return conn.execute(
        "SELECT id, periode_debut, periode_fin, contenu, cree_le FROM rapports"
        " ORDER BY cree_le DESC LIMIT 10"
    ).fetchall()


@app.get("/api/journal")
def journal(conn: Conn = Depends(db)):
    return conn.execute(
        """
        SELECT id, tache, debut, fin, mails_lus, offres_vues, doublons, rejetees, details,
               jsonb_array_length(COALESCE(erreurs, '[]'::jsonb)) AS nb_erreurs
        FROM executions WHERE debut > now() - %s ORDER BY debut DESC LIMIT 50
        """,
        (timedelta(days=30),),
    ).fetchall()


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
