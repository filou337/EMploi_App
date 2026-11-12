"""Notifications : enregistrées en base (affichées dans l'appli) et, si possible, sur le bureau."""

import shutil
import subprocess

import psycopg

URGENTES = {"qualifiee", "retiree"}  # celles qui méritent une notification de bureau


def notifier(
    conn: psycopg.Connection, type_: str, titre: str, message: str = "", offre_id: int | None = None
) -> None:
    conn.execute(
        "INSERT INTO notifications (offre_id, type, titre, message) VALUES (%s, %s, %s, %s)",
        (offre_id, type_, titre[:200], message[:1000]),
    )
    if type_ in URGENTES:
        bureau(titre, message)


def bureau(titre: str, message: str = "") -> None:
    """Notification du bureau Linux (notify-send), même quand l'appli est fermée."""
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "--app-name=EMploi_App", titre, message], timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            pass
