"""Appel à Gemini (API Google AI Studio, offre gratuite) avec sortie JSON contrainte.

- La clé vient de .env (GEMINI_API_KEY) et part dans l'en-tête, jamais dans l'URL.
- La réponse est forcée au format JSON par un schéma : le modèle ne peut pas répondre
  autre chose qu'un objet conforme, ce qui limite l'effet d'un texte d'offre piégé.
- Quota gratuit limité (quelques requêtes par minute) : reprise automatique sur 429/503,
  puis essai d'un modèle de secours.
"""

import json
import os
import re
import time

import httpx

import app.config  # noqa: F401 — charge .env (GEMINI_API_KEY)

URL = "https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent"
# Flash-Lite : rapide (quelques secondes) et disponible sur l'offre gratuite ; les modèles
# Flash, plus fins, sont souvent saturés (503) ou très lents : ils servent de secours.
MODELE_DEFAUT = "gemini-3.1-flash-lite"
MODELES_SECOURS = ("gemini-flash-lite-latest", "gemini-3.8-flash", "gemini-flash-latest")
DELAI_MAX = 90  # secondes par requête
ESSAIS_PAR_MODELE = (
    5  # surcharges 503 fréquentes sur l'offre gratuite : on patiente avant de changer de modèle
)


class LLMIndisponible(RuntimeError):
    """Pas de clé, quota épuisé ou réponse inexploitable : le message est montré à l'utilisateur."""


def disponible() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _modeles() -> list[str]:
    choisi = os.getenv("GEMINI_MODELE", "").strip() or MODELE_DEFAUT
    return [choisi, *(m for m in MODELES_SECOURS if m != choisi)]


def _attente(reponse: httpx.Response, essai: int) -> float:
    """Délai conseillé par Google (« retryDelay »: "17s"), sinon 5, 10, 20, 30, 40 s."""
    m = re.search(r'"retryDelay":\s*"(\d+)', reponse.text)
    return min(float(m.group(1)) + 1, 60) if m else (5, 10, 20, 30, 40)[min(essai, 4)]


def generer_json(
    consigne: str, contenu: str, schema: dict, temperature: float = 0.0, graine: int | None = 42
) -> tuple[dict, str]:
    """Renvoie (objet JSON, modèle utilisé). `consigne` = instruction système, `contenu` = données."""
    cle = os.getenv("GEMINI_API_KEY", "").strip()
    if not cle:
        raise LLMIndisponible("Clé Gemini absente : ajoute GEMINI_API_KEY dans .env.")
    corps = {
        "systemInstruction": {"parts": [{"text": consigne}]},
        "contents": [{"role": "user", "parts": [{"text": contenu}]}],
        "generationConfig": {
            "temperature": temperature,
            **({"seed": graine} if graine is not None else {}),
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "thinkingConfig": {"thinkingLevel": "low"},  # réflexion courte : réponse en secondes
        },
    }
    derniere = ""
    with httpx.Client(timeout=DELAI_MAX) as client:
        a_essayer = _modeles()
        while a_essayer:
            modele = a_essayer.pop(0)
            for essai in range(ESSAIS_PAR_MODELE):
                try:
                    r = client.post(
                        URL.format(modele=modele), json=corps, headers={"x-goog-api-key": cle}
                    )
                except httpx.HTTPError as e:
                    derniere = f"réseau : {e}"
                    time.sleep(3)
                    continue
                if r.status_code == 200:
                    try:
                        texte = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                        return json.loads(texte), modele
                    except (KeyError, IndexError, ValueError):
                        derniere = f"réponse illisible de {modele}"
                        break
                if r.status_code in (429, 500, 503):
                    derniere = f"{modele} : quota ou surcharge ({r.status_code})"
                    time.sleep(_attente(r, essai))
                    continue
                if r.status_code in (401, 403):
                    raise LLMIndisponible("Clé Gemini refusée : vérifie GEMINI_API_KEY dans .env.")
                derniere = f"{modele} : erreur {r.status_code}"
                # Modèle retiré : Google indique son remplaçant (« use models/gemini-x »).
                suggere = re.search(r"use models/([\w.-]+)", r.text)
                if r.status_code == 404 and suggere and suggere.group(1) not in a_essayer:
                    a_essayer.insert(0, suggere.group(1))
                break  # 404, 400… : on passe au modèle suivant
    raise LLMIndisponible(
        f"Gemini indisponible pour le moment ({derniere}). Réessaie dans une minute."
    )
