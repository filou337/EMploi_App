"""Les tests qui touchent la base utilisent la base LOCALE (Docker), jamais Supabase."""

import os

from dotenv import dotenv_values

from app.config import RACINE

_locale = dotenv_values(RACINE / ".env").get("DATABASE_URL_LOCAL")
if _locale:
    os.environ["DATABASE_URL"] = _locale
