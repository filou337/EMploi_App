"""Configuration lue depuis les variables d'environnement (fichier .env en local)."""

import os
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent

load_dotenv(RACINE / ".env")


def _chemin(var: str, defaut: str) -> Path:
    """Chemin lu dans .env ; s'il est relatif, il part de la racine du projet."""
    chemin = Path(os.getenv(var, defaut))
    return chemin if chemin.is_absolute() else RACINE / chemin


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL manquante : copie .env.example en .env et remplis-le.")
    return url


def chemin_client_secret() -> Path:
    return _chemin("GOOGLE_CLIENT_SECRET", "secrets/client_secret.json")


def chemin_token(boite: str = "principale") -> Path:
    """Jeton OAuth d'une boîte Gmail. 'principale' garde l'ancien nom (secrets/token.json)."""
    if boite == "principale":
        return _chemin("GOOGLE_TOKEN", "secrets/token.json")
    return _chemin(f"GOOGLE_TOKEN_{boite.upper()}", f"secrets/token_{boite}.json")


def boites_gmail() -> list[str]:
    """Boîtes Gmail à lire, par leur nom court (GMAIL_BOITES=principale,ancienne)."""
    valeur = os.getenv("GMAIL_BOITES", "principale")
    return [b.strip() for b in valeur.split(",") if b.strip()]


def adzuna() -> tuple[str, str] | None:
    """Identifiants Adzuna (gratuits, developer.adzuna.com) ; None s'ils ne sont pas renseignés."""
    app_id, app_key = (
        os.getenv("ADZUNA_APP_ID", "").strip(),
        os.getenv("ADZUNA_APP_KEY", "").strip(),
    )
    return (app_id, app_key) if app_id and app_key else None
