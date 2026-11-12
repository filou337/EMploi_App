"""Connexion à l'API Gmail (OAuth, application de bureau) et lecture des en-têtes.

Droit demandé : lecture seule. Un jeton par boîte, enregistré dans secrets/ (ignoré par git).
Les boîtes sont désignées par un nom court ('principale', 'ancienne') : voir GMAIL_BOITES.
"""

import base64
import os
import time
from collections.abc import Iterator
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import chemin_client_secret, chemin_token

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _sauver(creds: Credentials, chemin: Path) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(creds.to_json())
    os.chmod(chemin, 0o600)


def identifiants(boite: str = "principale") -> Credentials:
    """Charge le jeton existant, le renouvelle, ou ouvre le navigateur pour autoriser l'accès.

    À la première connexion d'une boîte, choisis le bon compte Google dans le navigateur.
    """
    token = chemin_token(boite)
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _sauver(creds, token)
            return creds
        except RefreshError:
            pass  # jeton révoqué ou expiré (mode Test : 7 jours) -> nouvelle autorisation
    secret = chemin_client_secret()
    if not secret.exists():
        raise FileNotFoundError(f"Identifiant OAuth introuvable : {secret}")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    print(f"Autorisation de la boîte « {boite} » : choisis le compte Google correspondant.")
    creds = flow.run_local_server(port=0, open_browser=True, prompt="select_account")
    _sauver(creds, token)
    return creds


def service(boite: str = "principale"):
    return build("gmail", "v1", credentials=identifiants(boite), cache_discovery=False)


def adresse(svc) -> str:
    """Adresse de la boîte connectée (sert à reconnaître les mails envoyés par soi)."""
    return _executer(svc.users().getProfile(userId="me"))["emailAddress"].lower()


def ids_messages(svc, requete: str) -> Iterator[str]:
    """Identifiants de tous les messages correspondant à la requête (pagination gérée)."""
    page = None
    while True:
        rep = _executer(
            svc.users().messages().list(userId="me", q=requete, maxResults=500, pageToken=page)
        )
        for m in rep.get("messages", []):
            yield m["id"]
        page = rep.get("nextPageToken")
        if not page:
            return


TAILLE_LOT = 10  # Gmail limite les requêtes simultanées par utilisateur (erreur 429 au-delà)
ESSAIS_MAX = 6
PAUSE_ENTRE_LOTS = 0.5  # secondes, pour rester sous le quota par minute


def _reessayable(erreur: Exception) -> bool:
    """429 (trop de requêtes simultanées), 403 de quota par minute, ou erreur serveur."""
    if not isinstance(erreur, HttpError):
        return False
    statut = erreur.resp.status
    if statut in (429, 500, 503):
        return True
    return statut == 403 and "rateLimitExceeded" in str(erreur.content)


def entetes(svc, ids: list[str], noms=("From", "Subject", "Date")) -> list[dict]:
    """En-têtes de chaque message, par petits lots (une requête HTTP par lot).

    Les messages refusés pour cause de débit (429, quota 403) ou d'erreur serveur sont
    repris après une pause qui double à chaque essai.
    """
    resultats: list[dict] = []
    for i in range(0, len(ids), TAILLE_LOT):
        restants = ids[i : i + TAILLE_LOT]
        for essai in range(ESSAIS_MAX):
            a_reprendre: list[str] = []

            def _recu(request_id, reponse, erreur, a_reprendre=a_reprendre):
                if erreur is None:
                    h = {x["name"]: x["value"] for x in reponse["payload"].get("headers", [])}
                    resultats.append(
                        {
                            "id": reponse["id"],
                            "thread": reponse.get("threadId"),
                            "snippet": reponse.get("snippet", ""),
                            **{n: h.get(n, "") for n in noms},
                        }
                    )
                elif _reessayable(erreur):
                    a_reprendre.append(request_id)
                else:
                    raise erreur

            lot = svc.new_batch_http_request(callback=_recu)
            for mid in restants:
                requete = (
                    svc.users()
                    .messages()
                    .get(userId="me", id=mid, format="metadata", metadataHeaders=list(noms))
                )
                lot.add(requete, request_id=mid)
            lot.execute()
            time.sleep(PAUSE_ENTRE_LOTS)
            if not a_reprendre:
                break
            restants = a_reprendre
            time.sleep(2 ** (essai + 1))
        else:
            raise RuntimeError(
                f"Gmail refuse encore {len(restants)} messages après {ESSAIS_MAX} essais"
            )
    return resultats


def _executer(requete):
    """Exécute une requête Gmail en reprenant après une pause si le quota est dépassé."""
    for essai in range(ESSAIS_MAX):
        try:
            return requete.execute()
        except HttpError as e:
            if not _reessayable(e) or essai == ESSAIS_MAX - 1:
                raise
        except (TimeoutError, ConnectionError):  # coupure réseau passagère
            if essai == ESSAIS_MAX - 1:
                raise
        time.sleep(2 ** (essai + 1))


def message_brut(svc, mid: str) -> bytes:
    """Mail complet au format RFC 822 (en-têtes + corps MIME), tel que reçu."""
    rep = _executer(svc.users().messages().get(userId="me", id=mid, format="raw"))
    return base64.urlsafe_b64decode(rep["raw"])
