# EMploi_App — Job Market Intelligence V2

Outil personnel de recherche d'emploi pour des postes **Data Analyst**, **Data Engineer** et **Analytics Engineer**.

Il repère chaque nouvelle offre pertinente dès son arrivée dans Gmail, vérifie qu'elle est encore ouverte, écarte les doublons, la note sur 100 face à mon profil, puis prépare un dossier de candidature complet (fiche de poste, CV ATS adapté, lettre de motivation). Il suit ensuite les réponses jusqu'à l'entretien.

> **Règle de conduite.** Sous le seuil de 85, et si je l'active, l'outil postule seul par mail : CV + LM adaptés et mail d'accompagnement au contact publié par l'employeur, 10 par jour au plus. À partir de 85, ou sans contact fiable, tout est préparé et **mis de côté** : c'est moi qui envoie. Aucun formulaire n'est jamais rempli à ma place.

**Statut :** étapes 1, 2, 4, 5 et 6 terminées (collecte sur deux boîtes Gmail, validité, dossiers CV + LM, suivi et relances, spontanées, rapport) ; étape 3 en calibrage. Données suivies depuis le 1er octobre 2026 : seules les offres, candidatures et réponses récentes sont gardées.

---

## Sommaire

- [Démarrage en local](#démarrage-en-local)
- [Fonctionnement](#fonctionnement)
- [Architecture](#architecture)
- [Stack technique](#stack-technique)
- [Scoring](#scoring)
- [Suivi des candidatures](#suivi-des-candidatures)
- [Garde-fous](#garde-fous)
- [Feuille de route](#feuille-de-route)
- [Documentation](#documentation)
- [Structure du dépôt](#structure-du-dépôt)

---

## Démarrage en local

Prérequis : Docker (avec Docker Compose) et Python 3.12 ou plus.

```bash
# 1. Configuration : copier le modèle puis choisir un mot de passe
cp .env.example .env          # .env est ignoré par git

# 2. Base Postgres (port 5433) ; schema.sql est chargé au premier démarrage
docker compose up -d
docker compose ps             # attendre l'état "healthy"

# 3. Environnement Python
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 4. Profil personnel (non versionné) : compétences, missions, secteurs visés
cp profil/competences.exemple.yaml profil/competences.yaml

# 5. Vérifier que tout fonctionne
pytest
```

**Ouvrir l'appli :** `python -m app` (ou `outils/lancer.sh`). La base démarre si besoin, les migrations
s'appliquent, puis le tableau de bord s'ouvre sur http://127.0.0.1:8765 (accessible depuis ce PC seulement).
Raccourci de bureau : `sed "s#/chemin/vers/EMploi_App#$PWD#" outils/EMploi_App.desktop > ~/.local/share/applications/EMploi_App.desktop`, ou dans Chrome
⋮ → *Installer EMploi_App* (appli installable, PWA).

**24 h/24 (GitHub Actions)** : `.github/workflows/agents.yml` fait un passage complet toutes les 30 minutes sur les serveurs de GitHub, PC éteint, puis traite les demandes de l'appli. Les clés, jetons et le profil sont des Secrets chiffrés (`outils/configurer_github.sh`, à relancer après chaque nouvelle autorisation Gmail). Dépôt public : les journaux GitHub n'affichent rien de personnel, le détail est dans l'onglet Salon.

**En ligne (téléphone, PC éteint)** : l'interface est publiée sur GitHub Pages et lit les données dans
Supabase (gratuit), protégées par ton compte et des règles RLS (`sql/supabase/securite.sql`). Le PC garde
l'accès Gmail, les clés et les agents ; ce qui demande du Python (ATS, matching, dépôt de CV, Excel) passe
par la file `demandes`, exécutée par le travailleur dès que le PC est allumé.

<p align="center">
  <a href="https://filou337.github.io/emploi/"><img src="docs/qr_appli.png" alt="QR code de l'appli en ligne" width="160"></a><br>
  <sub>Scanner pour ouvrir l'appli sur le téléphone — <a href="https://filou337.github.io/emploi/">filou337.github.io/emploi</a> (connexion à ton compte requise)</sub>
</p>

| Étape | Commande |
|---|---|
| Migrer la base locale vers Supabase | `python -m app.vers_supabase` (`--verifier` pour tester la connexion) |
| Travailleur permanent du PC | `outils/installer_appli.sh` (service `emploi-travailleur`) |
| Publier l'interface + QR code | `outils/publier.sh "message"` (`-n` : essai à blanc) |

Commandes utiles :

| Commande | Effet |
|---|---|
| `docker compose down` | Arrête la base (les données sont conservées) |
| `docker compose down -v` | Arrête la base **et efface les données** (pour recharger `schema.sql`) |
| `docker compose exec db psql -U emploi -d emploi` | Ouvre un terminal SQL dans la base |
| `ruff check . && ruff format .` | Vérifie et met en forme le code |
| `python -m app.collecte.inventaire --jours 30 --sujets` | Compte les mails d'alerte par source et expéditeur (en-têtes seulement) |
| `python -m app.collecte.echantillons --par-source 5` | Sauvegarde des mails bruts dans `echantillons/` (ignoré par git) pour tester les parseurs |
| `python -m app.collecte.importer --jours 30` | Importe les offres des alertes dans Postgres (relançable sans doublon) |
| `python -m app.validite` | Marque les doublons (même offre republiée, ≥ 90 %) et les offres de plus de 21 jours |
| `python -m app.suivi.historique --jours 43` | Importe les candidatures depuis Gmail (envois, accusés, refus, entretiens) sur la période (`--mois` aussi) ; `--refaire` efface puis rejoue l'import de la boîte |
| `python -m app.fiches` | Cherche la fiche complète de chaque offre data chez l'employeur (ATS publics, Adzuna si clés) ; `--limite`, `--entreprise`, `--refaire` |
| ATS checker (dans l'appli) | Onglet « CV & lettre » : score ATS /100 (local) + qualité /100 (Gemini) ; détail d'une offre : matching /100 avec preuves vérifiées |
| `python -m app.orchestrateur` | Un passage complet des agents : Recherche → Validité → Fiches → Veille → Matching → Spontanées → Rapport (`--sans-gmail`, `--etapes`) |
| `outils/installer_veille.sh` | Installe la veille automatique (timer systemd utilisateur, toutes les 60 min, appli fermée) |
| `python -m app.scoring --rescorer` | Recalcule la grille V2 (score, classe A/B/C) des offres déjà analysées, sans appel LLM |
| `python -m app.scoring --exporter-calibrage` puis `--calibrer` | Exporte 20 offres à noter dans `profil/calibrage.csv`, puis propose les poids qui collent le mieux à tes notes |
| `python -m app.spontanees.agent --entreprise "Acme" --site acme.fr` | Prépare une candidature spontanée (sans option : les brouillons du jour pour les entreprises de ta veille) ; `--email` si tu connais l'adresse |
| `python -m app.agents.candidature_auto` | Candidatures automatiques : dossier + mail pour les offres notées, envoi sous le seuil, mise de côté au-dessus (interrupteur onglet « Envois auto ») |
| `outils/configurer_github.sh` | Envoie la configuration (Supabase, Gemini, jetons Gmail, profil) dans les Secrets GitHub pour le passage 24 h/24 (`-n` : essai) |
| `python -m app.envoi --autoriser` | Autorise l'envoi en 1 clic depuis Gmail (boîte de ton CV, droit d'envoi seul) ; `--etat` pour vérifier |
| `python -m app.rapport` | Rapport des 3 derniers jours (produit par l'orchestrateur quand il est dû ; `--forcer` pour l'avoir tout de suite) |
| `python -m app.migrations` | Applique les migrations de `sql/migrations/` (à lancer après chaque mise à jour du code) |

Gmail : plusieurs boîtes sont lues (`GMAIL_BOITES=principale,ancienne` dans `.env`, option `--boite` pour en choisir une). La première commande sur une boîte ouvre le navigateur pour autoriser l'accès en lecture seule ; le jeton est gardé dans `secrets/token.json` (principale) ou `secrets/token_<nom>.json`. Les appels refusés pour quota (429, 403) ou coupés par le réseau sont repris automatiquement.

> La base de travail est Supabase (`DATABASE_URL`) ; la base Docker locale (`DATABASE_URL_LOCAL`) ne sert plus qu'aux tests (`pytest`).

---

## Fonctionnement

| Étape | Entrée | Traitement | Sortie |
|---|---|---|---|
| **Détecter** | Alertes Gmail, APIs autorisées, sites carrières | Extraction, validité, déduplication | Offres uniques et encore ouvertes |
| **Évaluer** | Offre + profil validé | Grille pondérée sur 100, justifiée | Score + CV de base choisi (DA, DE ou AE) |
| **Préparer** | Offre ≥ 85 | `offre.txt`, CV ATS, lettre | Dossier Drive + alerte |
| **Suivre** | Clic « J'ai postulé », mails reçus | Statuts, relances, classement des réponses | Dossier supprimé si refus, alerte si réponse positive |
| **Rendre compte** | Historique du suivi | Agrégats sur 3 jours | Rapport par mail + dashboard |

### Le pipeline

Chaque mail d'alerte déclenche les étapes 1 à 6 dans la minute. Les étapes 7 et 8 tournent selon un planning.

1. **Collecte** : Pub/Sub prévient Cloud Run, le service lit les nouveaux mails des expéditeurs autorisés (WTTJ, France Travail, Jobrapido, LinkedIn, APEC) et extrait entreprise, intitulé, lieu, lien et extrait. Un passage horaire rattrape les notifications perdues.
2. **Fiche complète et validité** : la fiche est cherchée chez l'employeur, sur son ATS public (Greenhouse, Lever, SmartRecruiters, Ashby, Recruitee) ou sur son site carrières (bloc schema.org JobPosting : plans du site, sous-domaines careers./recrutement./jobs., robots.txt respecté, 25 pages et 45 s au plus par site), ou via une API autorisée (Adzuna). Les pages des plateformes d'alerte ne sont pas téléchargées. L'offre est rejetée en cas de 404, si elle est pourvue ou si elle a plus de 21 jours.
3. **Déduplication** : même URL, même offre publiée ailleurs (similarité ≥ 90 % avec `rapidfuzz`), ou candidature chez la même entreprise il y a moins de 3 mois.
4. **Extraction et scoring** : un pré-filtre par mots-clés, puis le LLM extrait les critères en JSON et note la grille.
5. **Génération** (score ≥ 85) : CV et lettre adaptés, rendus en PDF puis déposés dans Drive.
6. **Alerte** : mail et bandeau dans le dashboard, avec le score, les points forts, le point faible et un bouton « J'ai postulé ».
7. **Spontanées** (tous les jours) : brouillons Gmail vers des contacts RH trouvés sur des pages publiques.
8. **Rapport** (tous les 3 jours) : récapitulatif des candidatures, des relances dues et des réponses reçues.

---

## Architecture

```
 Alertes Gmail ──┐                                       ┌──> API Claude (extraction, scoring, rédaction)
 APIs autorisées ├──> Pipeline Python (Cloud Run) ───────┼──> Postgres (offres, candidatures, réponses)
 Sites carrières ┘    1. Collecte et extraction          ├──> Google Drive (offre.txt, CV, LM)
                      2. Validité et doublons            ├──> Brouillons Gmail (relances, spontanées)
                      3. Scoring sur 100                 └──> Alertes + Dashboard
                      4. Dossier du poste
                      5. Alerte si score ≥ 85
```

Postgres est au centre. Le pipeline y écrit à chaque étape, et le bouton « J'ai postulé » comme les tâches planifiées lisent ce même suivi, ce qui empêche toute double candidature.

---

## Stack technique

| Composant | Outil |
|---|---|
| Déclencheur temps réel | Gmail API `users.watch` + Google Pub/Sub |
| Service de traitement | Python 3.12 + FastAPI sur Cloud Run |
| Tâches planifiées | Cloud Scheduler |
| Sources complémentaires | ATS publics (Greenhouse, Lever, SmartRecruiters, Ashby, Recruitee), sites carrières via schema.org JobPosting (`app/fiches/jobposting.py`), API Adzuna |
| Lecture des offres | `httpx` + `trafilatura`, Playwright en secours |
| Base de données | Postgres (Supabase) |
| Déduplication floue | `rapidfuzz` |
| Scoring et rédaction | API Claude, sortie JSON structurée |
| Rendu des documents | Jinja2 + Playwright (Chromium) → PDF |
| Stockage des fichiers | Google Drive API |
| Alertes | Mail + dashboard |
| Spontanées et relances | Gmail API `drafts.create` |
| Dashboard | Interface web sur Postgres (maquette dans `docs/`) |
| CI/CD | GitHub Actions → Docker → Cloud Run |

Tout tient dans le niveau gratuit ou quasi gratuit de Google Cloud et Supabase. Seul l'appel au LLM a un coût à l'usage.

---

## Scoring

Moyenne pondérée sur 100, avec une justification obligatoire pour chaque critère.

| Critère | Poids | Pleine note |
|---|---|---|
| Compétences techniques | 40 % | Tous les « indispensables » présents dans `competences.yaml` |
| Missions | 25 % | Missions déjà menées en expérience ou en projet |
| Séniorité | 15 % | Junior, 0 à 2 ans |
| Entreprise et secteur | 10 % | Secteurs visés |
| Contrat, lieu, télétravail | 10 % | CDI ou CDD, Île-de-France |

| Classe | Condition | Action |
|---|---|---|
| **A — Dossier** | Score ≥ 85, fiche complète | Dossier + alerte (10 par jour au maximum) |
| **B — À étudier** | Score de 70 à 84, ou fiche incomplète | Dashboard et rapport, sans génération |
| **C — Hors cible** | Score < 70, ou contrainte obligatoire violée | Archivée avec le motif |

**Fiche incomplète :** une offre connue seulement par l'extrait d'une alerte a un score plafonné à 84. Elle apparaît dans le dashboard avec un bouton « Coller la fiche » et est rescorée une fois la fiche collée.

**Calibrage :** la première semaine, l'outil note sans alerter. Je note 20 offres moi-même et les poids sont ajustés pour réduire l'écart avec mes notes.

---

## Suivi des candidatures

| Statut | Déclencheur | Effet |
|---|---|---|
| à postuler | Dossier créé (classe A) | Alerte + dashboard |
| postulé | Clic « J'ai postulé » ou accusé de réception trouvé dans Gmail | Relance prévue à J+7 |
| relancé | Brouillon de relance envoyé | Seconde relance à J+14 |
| réponse positive | Mail classé positif (entretien, appel, test) | Alerte avec le dossier |
| refus | Mail classé négatif | Fichiers mis à la corbeille Drive, ligne de suivi conservée |
| sans suite | Aucune réponse 30 jours après la dernière relance | Archivée |

---

**Lecture des mails** (`app/suivi/regles.py`) : chaque mail reçu est classé (accusé, refus, entretien, neutre, réponse automatique, non remis) et rattaché à sa candidature par son fil Gmail, ou par l'employeur quand l'objet parle de candidature.
- L'employeur est retrouvé derrière les logiciels de recrutement (Workday, iCIMS, Talent-soft, profils.org, Hellowork…) : nom affiché, objet (« Recrutement X : »), ou adresse (`noreply.x@…`).
- Un accusé qui évoque un refus au conditionnel (« sans retour sous 30 jours… ») reste un accusé ; seul un vrai refus (« malheureusement… ») ferme la candidature.
- Les mails de vie étudiante (statut étudiant-entrepreneur, prix, concours, hackathons, appels à projets) sont ignorés : ce ne sont pas des candidatures.

## Garde-fous

- **Candidatures automatiques encadrées** : interrupteur désactivé par défaut ; seulement des offres à fiche complète, valides, notées entre `envoi_auto_min` (50) et le seuil (85), sans contrainte violée (contrat, lieu, séniorité, secteur exclu) ; jamais deux fois le même employeur en 3 mois ; contact publié par l'employeur uniquement (fiche de l'offre, contact connu, adresse de recrutement du site), jamais d'adresse devinée ; dossier ATS friendly obligatoire ; 10 envois automatiques par jour ; mail relu par le garde-fou de véracité ; tout est journalisé (`envois`, `candidatures_auto`).
- **Envoi sur ton clic pour le reste** : aucun remplissage automatique de formulaire. L'envoi depuis Gmail (spontanées, relances) est désactivé par défaut ; une fois autorisé (`python -m app.envoi --autoriser`, droit `gmail.send` dans un jeton séparé, compte vérifié) et activé, chaque mail part uniquement quand tu cliques « Envoyer », 10 par jour au plus, jamais deux fois, journalisé dans `envois`.
- **Véracité du CV** : aucune compétence hors de `competences.yaml`. Un contrôle dans le code fait échouer la génération sinon.
- **Respect des sites** : lecture des mails d'alerte et des pages publiques à faible cadence. Pas d'aspiration de LinkedIn ni de connexion automatisée aux plateformes.
- **RGPD** : contacts limités aux adresses publiques liées au recrutement (jamais une adresse nominative), source enregistrée, robots.txt respecté, 5 spontanées par jour au maximum, jamais deux fois la même entreprise en 90 jours, droit d'opposition définitif (bouton « Opposition »).
- **Sécurité LLM** : le texte d'une offre est une donnée, jamais une instruction (extraction à schéma contraint). Le score de matching est calculé par le code, et toute « preuve » citée par le LLM est vérifiée mot pour mot dans le CV.
- **Vie privée** : le CV part masqué vers Gemini (nom, mail, téléphone, adresse, liens remplacés, d'après la fiche d'identité), car l'offre gratuite autorise Google à réutiliser les contenus. Un contrôle bloquant refuse l'envoi s'il reste une coordonnée.
- **Véracité des dossiers** : postes, entreprises, dates et diplômes sont recopiés du CV par le code ; tout outil ou chiffre absent de tes documents est retiré du CV ou de la LM générés (`app/generation/verite.py`).
- **Secrets** : clés API et identifiants dans Google Secret Manager, jamais dans le dépôt.

---

## Feuille de route

Sept étapes d'environ une semaine chacune. Chacune livre quelque chose d'utilisable.

- [x] **1. Collecte et base** : schéma Postgres ✅, parseurs LinkedIn, Indeed et WTTJ (sélections « jobs du mois ») ✅, import des 30 derniers jours ✅. France Travail, APEC, Jobrapido : parseurs à écrire dès réception d'alertes
- [x] **2. Validité et déduplication** : doublons flous ✅, fraîcheur 21 jours ✅, garde-fou 3 mois ✅, import des candidatures ✅ (deux boîtes), fiche complète via Greenhouse, Lever, SmartRecruiters, Ashby, Recruitee ✅ et sur le site carrières de l'employeur (offres schema.org JobPosting) ✅, Adzuna (connecteur prêt, facultatif)
- [ ] **3. Scoring et calibrage** : `profil/competences.yaml` ✅, grille V2 sur 100 + classes A/B/C ✅ (`app/scoring.py`, branchée sur l'agent Matching), outil de calibrage ✅ ; reste à noter 20 offres et ajuster les poids
- [x] **4. Génération** : moteur ATS ✅ (compatibilité, qualité, matching), agent Dossier ✅ (CV + LM adaptés dans la langue de l'offre, PDF + DOCX, garde-fou de véracité, relecture ATS, « ATS friendly » si compat ≥ 90 et matching ≥ CV d'origine), validation → « À envoyer » ✅ ; dépôt Drive remplacé par la base (téléchargement depuis le téléphone) et `Candidatures/`
- [x] **5. Alertes et suivi** : agent Veille ✅, agent Matching ✅, agent Suivi ✅ (réponses, accusés : l'offre passe « Envoyée » toute seule), relances J+7 / J+14 ✅ (texte prêt, envoi par toi depuis ta messagerie), « sans suite » à 30 jours ✅, orchestrateur + timer ✅, pipeline 6 étapes ✅, notifications appli + bureau ✅. Détails d'origine : alertes mail, bouton « J'ai postulé », relances J+7, orchestrateur qui valide la candidature à la réception de l'accusé
- [ ] **6. Spontanées et rapport** : rapport tous les 3 jours ✅ (onglet « Rapport » + notification, pas de mail : Gmail reste en lecture seule), spontanées ✅ (contact RH public trouvé sur le site, mail rédigé et vérifié, 5 par jour au plus ; brouillon dans l'onglet « Spontanées », envoyé par toi depuis ta messagerie). Déjà disponibles en avance : dashboard web ✅, salon v0 (journal des agents) ✅, export Excel ✅, import / saisie des candidatures déjà faites ✅, dépôt CV + LM ✅
- [ ] **7. Temps réel** : passage 24 h/24 toutes les 30 min sur GitHub Actions ✅ ; reste `users.watch` + Pub/Sub pour réagir à la minute (optionnel)

---

## Documentation

> En cas de divergence entre les documents, **le dossier d'architecture V2 fait foi**. Le plan technique reste la référence pour le schéma SQL, la structure du code et l'ordre de construction.

| Document | Contenu |
|---|---|
| `docs/job_market_intelligence_architecture_v2.pdf` (local, non publié) | **Référence.** Dossier d'architecture V2 : produit, sources, profil, validité, scoring, suivi, composants |
| `docs/Agent_candidature_plan_technique.pdf` (local, non publié) | Plan technique : stack, modèle de données SQL, pipeline, structure du code, ordre de construction |
| [`docs/job_market_intelligence_v2.html`](docs/job_market_intelligence_v2.html) | Maquette interactive du dashboard (à télécharger puis ouvrir dans un navigateur) |

---

## Structure du dépôt

**Actuelle**

```
EMploi_App/
├── app/
│   ├── config.py           # lecture de .env
│   ├── db.py               # connexion Postgres
│   ├── migrations.py       # applique sql/migrations/
│   ├── entreprises.py      # rattachement nom / domaine -> entreprise
│   ├── dedup.py            # doublons flous, garde-fou 3 mois
│   ├── validite.py         # fraîcheur + dédup (CLI)
│   ├── suivi/              # regles.py (lecture des mails), historique.py (import des candidatures)
│   ├── fiches/             # ats.py (API publiques des ATS), jobposting.py (sites carrières, JobPosting), adzuna.py, recherche.py (CLI)
│   ├── serveur.py          # API FastAPI de l'appli locale ; __main__.py = lanceur
│   ├── documents.py        # dépôt CV / LM : validation, extraction du texte, diagnostic ATS
│   ├── ats/                # compatibilite.py (score ATS local), moteur.py (lecture, qualité, matching),
│   │                       # anonymisation.py (coordonnées masquées), service.py
│   ├── llm.py              # client Gemini (sortie JSON contrainte, reprise, modèle de secours)
│   ├── agents/             # veille.py, matching.py, suivi.py, candidature_auto.py (dossier + mail, envoi ou mise de côté)
│   ├── generation/         # agent Dossier : identite.py (coordonnées, masquage), adaptation.py (Gemini),
│   │                       # verite.py (garde-fou), rendu.py (HTML -> PDF via Chrome, DOCX), fabrique.py
│   ├── orchestrateur.py    # enchaîne les agents, verrou anti-doublon, notifications de bureau
│   ├── criteres.py         # critères de veille et seuils (modifiables dans l'appli)
│   ├── scoring.py          # grille V2 sur 100, classes A/B/C, calibrage des poids
│   ├── rapport.py          # rapport tous les 3 jours (table rapports, onglet Rapport)
│   ├── envoi.py            # envoi depuis Gmail : 1 clic ou automatique (gmail.send, interrupteurs, plafonds, journal)
│   ├── spontanees/         # contacts.py (adresse RH publique, robots.txt), agent.py (brouillons)
│   ├── dossiers.py         # dossier local du poste (Candidatures/…/offre.txt)
│   ├── notifications.py
│   ├── travailleur.py      # exécute les demandes de l'interface en ligne (file `demandes`)
│   ├── vers_supabase.py    # migration base locale -> Supabase + bascule de .env
│   ├── web/                # interface PWA ; donnees.js = Supabase (en ligne) ou API Python (local)
│   ├── pertinence.py       # pré-filtre data par mots-clés
│   ├── texte.py            # HTML -> texte
│   └── collecte/
│       ├── gmail.py        # OAuth lecture seule, pagination, lots, reprise sur quota
│       ├── sources.py      # liste blanche des expéditeurs d'alerte
│       ├── normalisation.py# clés anti-doublon, contrat déduit de l'intitulé
│       ├── parseurs/       # un parseur par plateforme (linkedin.py, indeed.py, wttj.py)
│       ├── enregistrement.py
│       ├── importer.py     # CLI d'import
│       ├── inventaire.py   # CLI d'inventaire
│       └── echantillons.py # CLI de sauvegarde de mails bruts
├── docs/                   # architecture V2, plan technique, maquette
├── profil/                 # competences.exemple.yaml (à copier en competences.yaml, personnel et non versionné)
├── sql/schema.sql          # 6 tables (base de départ) + sql/migrations/
├── tests/                  # schéma, sources, normalisation, parseur LinkedIn (+ fixtures/)
├── docker-compose.yml      # Postgres 17 local
├── pyproject.toml
├── .env.example
└── README.md
```

**Prévue**

```
EMploi_App/
├── app/
│   ├── main.py             # FastAPI : /pubsub, /api (dashboard), /jobs/*
│   ├── collecte/           # Gmail + parseurs par source
│   ├── validite.py
│   ├── dedup.py
│   ├── scoring.py
│   ├── generation/         # cv.py, lm.py, rendu.py
│   ├── alertes.py          # mail + dashboard
│   ├── spontanee.py
│   ├── rapport.py
│   └── db.py
├── profil/                 # competences.yaml, modèles de CV, preferences.yaml
├── sql/schema.sql
├── tests/
├── docs/
├── Dockerfile
└── .github/workflows/deploy.yml
```

---

## Auteur

[@filou337](https://github.com/filou337)

## Licence

[MIT](LICENSE) — libre de réutiliser le code. Les données (offres, candidatures, CV) ne font pas partie du dépôt et restent privées.
