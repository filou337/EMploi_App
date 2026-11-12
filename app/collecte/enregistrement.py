"""Écriture des offres d'alerte dans Postgres (idempotent : une offre connue est ignorée)."""

from datetime import datetime

import psycopg

from app.collecte.normalisation import cle_floue, contrat_depuis_intitule
from app.collecte.parseurs import OffreAlerte
from app.entreprises import entreprise_id


def enregistrer(
    conn: psycopg.Connection,
    offre: OffreAlerte,
    boite: str,
    gmail_id: str,
    recu_le: datetime | None,
) -> bool:
    """Insère l'offre ; renvoie False si son URL est déjà en base."""
    row = conn.execute(
        """
        INSERT INTO offres (url_cle, cle_floue, entreprise_id, source, boite, gmail_message_id,
                            intitule, ville, contrat, extrait, date_publication, recu_le)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
        ON CONFLICT (url_cle) DO NOTHING
        RETURNING id
        """,
        (
            offre.url_cle,
            cle_floue(offre.entreprise, offre.intitule, offre.lieu),
            entreprise_id(conn, offre.entreprise),
            offre.source,
            boite,
            gmail_id,
            offre.intitule,
            offre.lieu or None,
            contrat_depuis_intitule(offre.intitule),
            offre.extrait,
            offre.publiee_le,
            recu_le,
        ),
    ).fetchone()
    return row is not None
