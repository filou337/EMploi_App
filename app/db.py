"""Accès à Postgres."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import database_url


@contextmanager
def connexion() -> Iterator[psycopg.Connection]:
    """Ouvre une connexion ; commit si tout va bien, rollback en cas d'erreur."""
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn
