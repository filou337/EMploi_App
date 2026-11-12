"""Vérifie que la base répond et que schema.sql a bien créé les tables."""

import psycopg
import pytest

from app.db import connexion

TABLES_ATTENDUES = {"entreprises", "offres", "candidatures", "reponses", "contacts", "executions"}


@pytest.fixture(scope="module")
def conn():
    try:
        with connexion() as c:
            yield c
    except psycopg.OperationalError as e:
        pytest.skip(f"Base injoignable (lance `docker compose up -d`) : {e}")


def test_tables_creees(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert TABLES_ATTENDUES <= {r["table_name"] for r in rows}


def test_url_cle_unique(conn):
    """Deux offres avec la même URL nettoyée sont refusées (anti-doublon)."""
    with conn.transaction(force_rollback=True):
        conn.execute(
            "INSERT INTO offres (url_cle, cle_floue, source) VALUES ('u1', 'a|b|c', 'wttj')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(
                "INSERT INTO offres (url_cle, cle_floue, source) VALUES ('u1', 'x|y|z', 'apec')"
            )


def test_source_inconnue_refusee(conn):
    """Une source hors liste est rejetée par la contrainte CHECK."""
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO offres (url_cle, cle_floue, source) VALUES ('u2', 'a', 'monster')"
        )
