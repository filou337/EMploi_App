"""Agent Relances : envoie tout seul les relances dues (J+7, puis J+14), sans ton clic.

À chaque passage, après l'agent Suivi (qui vient de lire les réponses) :
1. relances dues (vue `v_relances`) des candidatures envoyées depuis le 1er du mois dernier ;
2. jamais si l'entreprise a répondu (positif ou refus), si sa réponse renvoie vers son site
   officiel ou demande de ne plus être recontacté (`relance_ignoree`, posé par l'agent Suivi),
   si tu as cliqué « Ne pas relancer », ou si le contact est en opposition ;
3. seulement vers une adresse de personne ou de boîte RH (jamais un robot d'ATS) ;
4. texte sobre (le même que dans l'onglet Candidatures), envoyé dans le fil Gmail de la
   candidature quand c'est possible ; au plus `seuils.relances_auto_par_jour` (15) par jour,
   à part du plafond des candidatures.
Interrupteur : `parametres.envoi.relances_auto` (activé).

Usage :  python -m app.agents.relances
"""

import re

import psycopg

from app import activite, envoi
from app.db import connexion
from app.generation import identite

_ROBOT = re.compile(
    r"no-?reply|ne-?pas-?repondre|donotreply|notification|mailer|postmaster", re.IGNORECASE
)


def langue(r: dict) -> str:
    """Français par défaut. Anglais seulement si l'ANNONCE est rédigée en anglais : un intitulé
    « Data Analyst » ne dit rien (les offres françaises ont presque toutes un titre anglais)."""
    from app.generation.adaptation import detecter_langue

    texte = r.get("offre_texte") or ""
    return detecter_langue(texte) if len(texte) >= 300 else "fr"


def texte_relance(r: dict, ident: dict, lang: str = "fr") -> tuple[str, str]:
    """(objet, corps) — même texte que l'onglet Candidatures (app.js, texteRelance)."""
    nom = ident.get("nom") or ""
    tel = f"\n{ident['telephone']}" if ident.get("telephone") else ""
    poste = r.get("poste") or r.get("offre_intitule") or ""
    quand = r["postule_le"].strftime("%d/%m/%Y") if r.get("postule_le") else ""
    entreprise = r.get("entreprise")
    if entreprise and entreprise.islower():
        # Nom déduit d'un domaine (« werecruit », « sncf ») : peut être le site de recrutement
        # plutôt que l'employeur -> formule neutre.
        entreprise = None
    if lang == "en":
        objet = f"Follow-up – application{f' for the {poste} position' if poste else ''}"
        if r["numero"] == 1:
            corps = (
                f"Hello,\n\nI am following up on my application"
                f"{f' for the {poste} position' if poste else ''}{f' sent on {quand}' if quand else ''}. "
                f"I remain very interested in joining {entreprise or 'your team'} and would welcome "
                f"the opportunity to discuss it with you.\n\nPlease let me know if you need any "
                f"further information.\n\nBest regards,\n{nom}{tel}"
            )
        else:
            corps = (
                f"Hello,\n\nI am reaching out one last time regarding my application"
                f"{f' for the {poste} position' if poste else ''}. If the position is still open, "
                f"I would be glad to have a short call.\n\nBest regards,\n{nom}{tel}"
            )
        return objet, corps
    if poste:
        objet = f"Relance – candidature au poste de {poste}"
    else:
        objet = f"Relance – candidature{' spontanée' if r.get('type') == 'spontanee' else ''}"
    if r["numero"] == 1:
        corps = (
            f"Bonjour,\n\nJe me permets de revenir vers vous au sujet de ma candidature"
            f"{f' au poste de {poste}' if poste else ''}{f', envoyée le {quand}' if quand else ''}. "
            f"Je reste très motivé à l'idée de rejoindre {entreprise or 'votre équipe'} et serais "
            f"ravi d'échanger avec vous.\n\nJe reste à votre disposition pour tout complément "
            f"d'information.\n\nBien cordialement,\n{nom}{tel}"
        )
    else:
        corps = (
            f"Bonjour,\n\nJe me permets de vous relancer une dernière fois au sujet de ma candidature"
            f"{f' au poste de {poste}' if poste else ''}. Si le poste est toujours ouvert, je serais "
            f"heureux d'en discuter lors d'un court échange.\n\nBien cordialement,\n{nom}{tel}"
        )
    return objet, corps


def adresse_humaine(email: str | None) -> bool:
    """Personne, boîte RH ou relais qui transmet au recruteur (Welcomekit, HelloWork) ;
    jamais un robot (noreply, notification…)."""
    if not email or "@" not in email:
        return False
    return not _ROBOT.search(email.lower())


def interdite_dans_le_fil(boite: str | None, fil: str | None) -> str | None:
    """Relit le fil Gmail de la candidature juste avant de relancer, quelle que soit son
    ancienneté : une réponse qui renvoie vers le site officiel ou refuse d'être recontacté
    bloque la relance. Fil illisible -> on ne relance pas (prudence)."""
    if not boite or not fil:
        return None
    from email import message_from_bytes, policy
    from email.utils import parseaddr

    from app.collecte import gmail
    from app.config import chemin_token
    from app.suivi.regles import relance_interdite, texte_utile

    if not chemin_token(boite).exists():
        return None
    try:
        svc = gmail.service(boite)
        moi = gmail.adresse(svc).lower()
        messages = (
            svc.users()
            .threads()
            .get(userId="me", id=fil, format="metadata", metadataHeaders=["From"])
            .execute()
            .get("messages", [])
        )
        for m in messages:
            entetes = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            if parseaddr(entetes.get("From", ""))[1].lower() == moi:
                continue
            brut = gmail.message_brut(svc, m["id"])
            motif = relance_interdite(texte_utile(message_from_bytes(brut, policy=policy.default)))
            if motif:
                return motif
    except Exception as e:  # noqa: BLE001
        return f"fil Gmail illisible ({type(e).__name__}) : relance reportée"
    return None


def dues(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        SELECT r.*, c.boite, c.gmail_thread_id, o.texte AS offre_texte FROM v_relances r
        JOIN candidatures c ON c.id = r.id LEFT JOIN offres o ON o.id = c.offre_id
        WHERE r.postule_le >= date_trunc('month', current_date) - interval '1 month'
          AND r.contact_email IS NOT NULL
        ORDER BY r.due_le, r.id
        """
    ).fetchall()


def executer(conn: psycopg.Connection) -> dict:
    bilan = {"envoyées": 0, "sans adresse utilisable": 0}
    etat = envoi.etat(conn)
    if not etat["autorise"]:
        return {**bilan, "note": "envoi Gmail non autorisé"}
    if not etat["relances_auto"]:
        return {**bilan, "note": "relances automatiques désactivées"}
    ident = identite.lire(conn)
    if not ident.get("nom"):
        return {**bilan, "note": "coordonnées absentes (onglet CV & lettre)"}
    for r in dues(conn):
        if not adresse_humaine(r["contact_email"]):
            bilan["sans adresse utilisable"] += 1
            continue
        motif = interdite_dans_le_fil(r["boite"], r["gmail_thread_id"])
        if motif:
            if not motif.startswith("fil Gmail illisible"):
                conn.execute(
                    "UPDATE candidatures SET relance_ignoree = TRUE, relance_motif = %s,"
                    " maj_le = now() WHERE id = %s",
                    (motif, r["id"]),
                )
                conn.commit()
                bilan["bloquées (réponse dans le fil)"] = (
                    bilan.get("bloquées (réponse dans le fil)", 0) + 1
                )
            continue
        objet, corps = texte_relance(r, ident, langue(r))
        try:
            envoi.envoyer_relance(conn, r["id"], objet, corps, auto=True)
            conn.commit()
        except envoi.EnvoiImpossible as e:
            conn.rollback()
            bilan["note"] = str(e)
            if "Plafond" in str(e) or "non autorisé" in str(e) or "expirée" in str(e):
                break
            continue
        bilan["envoyées"] += 1
        activite.noter(
            "relances",
            f"Relance {r['numero']} envoyée",
            offre_id=r.get("offre_id"),
            detail=f"{r['entreprise'] or '?'} — {r['poste'] or r['offre_intitule'] or ''}"
            f" (à {r['contact_email']})",
        )
    return bilan


def main() -> None:
    with connexion() as conn:
        print(executer(conn))


if __name__ == "__main__":
    main()
