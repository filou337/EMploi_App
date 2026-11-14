# Les agents d'EMploi_App

Un **passage** enchaîne les agents ci-dessous, dans cet ordre, toutes les 30 minutes
(Supabase `pg_cron` déclenche le workflow GitHub `agents.yml`). Un verrou Postgres empêche
deux passages simultanés ; l'erreur d'un agent n'arrête pas les suivants. Chaque agent écrit
ce qu'il fait dans le **Salon** de l'appli (fil en direct et journal des passages).

| # | Agent | Code | Envoie quelque chose ? |
|---|-------|------|------------------------|
| 1 | Recherche | `app/collecte/importer.py` | Non (Gmail en lecture seule) |
| 2 | Suivi | `app/agents/suivi.py` | Non (relances préparées, envoyées par toi) |
| 3 | Validité | `app/validite.py` | Non |
| 4 | Fiches | `app/fiches/recherche.py` | Non |
| 5 | Veille | `app/agents/veille.py` | Non |
| 6 | Matching | `app/agents/matching.py` | Non |
| 7 | **Candidatures** | `app/agents/candidature_auto.py` | **Oui : mails de candidature** |
| 8 | Spontanées | `app/spontanees/agent.py` | Non (brouillons, envoyés par toi) |
| 9 | Rapport | `app/rapport.py` | Non (lu dans l'appli) |

## 1. Recherche
Lit les alertes emploi reçues dans Gmail (2 derniers jours), extrait chaque offre (intitulé,
entreprise, lien) et l'ajoute à la base sans doublon (même URL = doublon).

## 2. Suivi
Relit les mails des 3 derniers jours : candidatures envoyées, accusés de réception, refus,
invitations. Un accusé de réception confirme un envoi (l'offre passe « Envoyée »). Notifie
chaque réponse, prépare les relances à J+7 puis J+14 (onglet Candidatures) et classe « sans
suite » une candidature sans réponse 30 jours après la dernière relance.

## 3. Validité
Écarte les doublons et marque les offres trop anciennes.

## 4. Fiches
Retrouve l'annonce complète sur le site carrières de l'employeur (Greenhouse, Lever, Ashby,
SmartRecruiters, Recruitee, pages « JobPosting »). Offre absente d'un site vérifié : « expirée ».
Sans fiche complète, une offre est plafonnée à 84/100. Ne consulte jamais LinkedIn ni Indeed.

## 5. Veille
Surveille le site carrières des entreprises de ta liste de veille : nouveaux postes qui passent
tes critères, annonces modifiées, postes retirés.

## 6. Matching
Fait entrer dans le pipeline les offres qui passent tes critères (poste, contrat, lieu), compare
ton CV à chaque fiche complète (Gemini) et attribue une note sur 100, une classe A / B / C et un
motif. Les classes A passent « Qualifiée ».

## 7. Candidatures (envoi automatique)
Pour chaque offre **CDI / CDD** notée **≥ 50** (`seuils.envoi_auto_min`), sans contrainte violée,
de la meilleure à la moins bonne :

1. **Garde-fous** : jamais deux fois le même employeur en 3 mois ; jamais de stage, alternance,
   freelance ou intérim ; contact en opposition refusé.
2. **Dossier** : CV et lettre adaptés à l'offre (véracité contrôlée), relus comme un ATS.
3. **Contact** : adresse publiée par l'employeur (fiche de l'offre, contact connu, page
   recrutement de son site). **Jamais d'adresse devinée.**
4. **Mail** d'accompagnement court, jovial et pro, avec le lien du portfolio ; CV et LM joints.
5. **Envoi automatique** depuis ta boîte Gmail (interrupteur « Envoi automatique ») :
   - au plus `envois_auto_par_jour` (10) par jour ;
   - dont au plus `envois_auto_haut_par_jour` (5) notées ≥ `fabrication` (85) ;
   - si le CV adapté est **moins bon** que ton CV d'origine (matching, compatibilité ATS, phrase
     retirée du CV), c'est **ton CV d'origine** qui part, avec la lettre adaptée.
6. **Mis de côté pour toi** (tout est prêt, rien ne part) :
   - **lettre à relire** : le garde-fou de véracité a retiré une phrase de la LM ;
   - notes ≥ 85 au-delà de 5 par jour ;
   - **aucune adresse publiée** : un **kit formulaire** est prêt (lien du formulaire, champs
     pré-remplis à copier, message de motivation, CV et LM). Tu soumets, puis « J'ai postulé ».
     Les réponses salaire / disponibilité / autorisation de travail se règlent dans l'onglet
     « Envois auto » > « Pour les formulaires » (jamais imprimées sur les CV).

Prévu ensuite : remplissage automatique des formulaires des sites carrières.

## 8. Spontanées
Cible les entreprises de ta veille dont le site est connu, trouve une adresse RH publique et
rédige un mail de candidature spontanée (5 brouillons par jour au plus). Rien n'est envoyé :
tu envoies depuis l'onglet Spontanées.

## 9. Rapport
Tous les 3 jours : offres repérées, candidatures envoyées, réponses, relances dues, dossiers
prêts, taux de réponse. Lisible dans l'appli et sur le téléphone.
