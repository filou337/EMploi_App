import json
from datetime import date

import httpx
import pytest

from app.fiches import jobposting as jp
from app.fiches import recherche


def _page(*offres, graph=False) -> str:
    donnee = {"@context": "https://schema.org", "@graph": list(offres)} if graph else list(offres)
    return f'<html><head><script type="application/ld+json">{json.dumps(donnee)}</script></head></html>'


OFFRE = {
    "@type": "JobPosting",
    "title": "Data Analyst H/F",
    "description": "<p>Missions : <b>SQL</b>, Power BI.</p><ul><li>KPI</li></ul>",
    "datePosted": "2026-11-02",
    "validThrough": "2026-12-31T23:59",
    "employmentType": "FULL_TIME",
    "hiringOrganization": {"@type": "Organization", "name": "Acme Industrie"},
    "jobLocation": {
        "@type": "Place",
        "address": {"addressLocality": "Nanterre", "postalCode": "92000", "addressCountry": "FR"},
    },
}


def test_lire_jobposting():
    [o] = jp.jobpostings(_page(OFFRE, graph=True))
    p, employeur = jp.poste(o, "https://acme.fr/jobs/data-analyst", date(2026, 11, 13))
    assert p.titre == "Data Analyst H/F" and employeur == "Acme Industrie"
    assert p.lieu == "Nanterre (92), FR"
    assert p.publie_le == date(2026, 11, 2)
    assert "SQL" in p.texte and "<" not in p.texte and "FULL_TIME" in p.texte
    assert p.url == "https://acme.fr/jobs/data-analyst"


def test_offre_expiree_ou_teletravail():
    expiree = {**OFFRE, "validThrough": "2026-10-01"}
    assert jp.poste(expiree, "u", date(2026, 11, 13)) is None
    distant = {**OFFRE, "jobLocation": [], "jobLocationType": "TELECOMMUTE"}
    assert jp.poste(distant, "u", date(2026, 11, 13))[0].lieu == "Télétravail"


def test_json_ld_abime_ou_absent():
    assert jp.jobpostings("<html>rien</html>") == []
    abime = '<script type="application/ld+json">{"@type": "JobPosting", "title": "Data\nEngineer"}</script>'
    assert jp.jobpostings(abime)[0]["title"].strip() == "Data Engineer"


@pytest.fixture
def site(monkeypatch):
    vues: list[str] = []
    pages = {
        "/robots.txt": "User-agent: *\nDisallow: /jobs/prive\nSitemap: https://acme.fr/sitemap-jobs.xml\n",
        "/sitemap-jobs.xml": "<urlset>"
        + "".join(
            f"<url><loc>https://acme.fr{c}</loc></url>"
            for c in (
                "/jobs/data-analyst-h-f",
                "/jobs/comptable",
                "/jobs/prive-data-engineer",
                "/actualites/data",
            )
        )
        + "</urlset>",
        "/jobs/data-analyst-h-f": _page(OFFRE),
        "/jobs/prive-data-engineer": _page({**OFFRE, "title": "Data Engineer"}),
    }

    def repondre(req: httpx.Request) -> httpx.Response:
        if req.url.host not in ("acme.fr", "www.acme.fr"):
            raise httpx.ConnectError("domaine inexistant", request=req)  # careers.acme.fr…
        vues.append(req.url.path)
        if req.url.path not in pages:
            return httpx.Response(404)
        return httpx.Response(200, text=pages[req.url.path], headers={"content-type": "text/html"})

    monkeypatch.setattr(jp, "_client", httpx.Client(transport=httpx.MockTransport(repondre)))
    monkeypatch.setattr(jp._cadence, "attendre", lambda url: __import__("threading").Lock())
    monkeypatch.setattr(jp._cadence, "fin", lambda url, v: None)
    return vues


def test_tableau_depuis_le_plan_du_site(site):
    t = jp.tableau("www.acme.fr")
    assert t.ats == "site" and t.slug == "https://www.acme.fr" and not t.complet
    assert [p.titre for p in t.postes] == ["Data Analyst H/F"]
    assert t.nom_employeur == "Acme Industrie" and "FR" in t.pays
    assert "/jobs/comptable" not in site  # pas une offre data : jamais téléchargée
    assert "/jobs/prive-data-engineer" not in site  # interdit par robots.txt
    assert "/actualites/data" not in site  # pas une page d'offre


def test_adresse_devinee_exige_le_nom_de_l_employeur(site, monkeypatch):
    monkeypatch.setattr(recherche, "CONNECTEURS", {})
    sites = [("https://acme.fr", True)]
    assert recherche._trouver_tableau("Acme Industrie", None, None, sites).ats == "site"
    assert recherche._trouver_tableau("Globex", None, None, sites) is None  # autre employeur


def test_page_de_liste_vers_les_fiches(monkeypatch):
    liste = (
        '<a href="/jobs/data-engineer-h-f-12345">Data Engineer</a>'
        '<a href="/jobs/comptable-67890">Comptable</a><a href="/a-propos">À propos</a>'
    )
    pages = {
        "/robots.txt": "",
        "/jobs": liste,
        "/jobs/data-engineer-h-f-12345": _page({**OFFRE, "title": "Data Engineer"}),
    }
    vues = []

    def repondre(req):
        vues.append(req.url.path)
        if req.url.path not in pages:
            return httpx.Response(404)
        return httpx.Response(200, text=pages[req.url.path], headers={"content-type": "text/html"})

    monkeypatch.setattr(jp, "_client", httpx.Client(transport=httpx.MockTransport(repondre)))
    monkeypatch.setattr(jp._cadence, "attendre", lambda url: None)
    monkeypatch.setattr(jp._cadence, "fin", lambda url, v: None)
    monkeypatch.setattr(jp, "SOUS_DOMAINES", ())
    t = jp.tableau("acme.fr")
    assert [p.titre for p in t.postes] == ["Data Engineer"]
    assert "/jobs/comptable-67890" not in vues  # offre hors data : jamais téléchargée


def test_sous_domaines_carrieres_d_abord():
    assert jp._bases("acme.fr")[0] == "https://careers.acme.fr"
    assert jp._bases("acme.fr")[-1] == "https://acme.fr"
    assert jp._bases("careers.acme.fr") == ["https://careers.acme.fr"]


def test_liste_en_javascript_rendue_par_chrome(monkeypatch):
    vide = "<html><body><div id='app'></div><script src='/app.js'></script></body></html>"
    rendu = '<a href="/jobs/data-analyst-h-f-99999">Data Analyst</a>'
    pages = {
        "/robots.txt": "",
        "/sitemap.xml": "<urlset><url><loc>https://acme.fr/fr/jobs/data</loc></url></urlset>",
        "/fr/jobs/data": vide,
        "/jobs/data-analyst-h-f-99999": _page(OFFRE),
    }

    def repondre(req):
        if req.url.path not in pages:
            return httpx.Response(404)
        return httpx.Response(200, text=pages[req.url.path], headers={"content-type": "text/html"})

    rendus = []
    monkeypatch.setattr(jp, "_client", httpx.Client(transport=httpx.MockTransport(repondre)))
    monkeypatch.setattr(jp._cadence, "attendre", lambda url: None)
    monkeypatch.setattr(jp._cadence, "fin", lambda url, v: None)
    monkeypatch.setattr(jp, "SOUS_DOMAINES", ())
    monkeypatch.setattr(jp, "rendre", lambda url: rendus.append(url) or rendu)
    t = jp.tableau("acme.fr")
    assert rendus == ["https://acme.fr/fr/jobs/data"]  # une seule page rendue : la liste vide
    assert [p.titre for p in t.postes] == ["Data Analyst H/F"]


def test_pages_francaises_et_data_d_abord():
    urls = [
        "https://acme.com/en/jobs/new-york/marketing-1",
        "https://acme.com/en/jobs/new-york/data-analyst-2",
        "https://acme.com/fr/jobs/paris/data-engineer-3",
        "https://acme.com/fr/jobs/paris/comptable-4",
    ]
    assert sorted(urls, key=jp._priorite) == [urls[2], urls[3], urls[1], urls[0]]
