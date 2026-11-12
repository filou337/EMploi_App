-- =====================================================================
-- EMploi_App — schéma Postgres
-- Base : plan technique (option B), ajusté selon l'architecture V2 (qui fait foi).
-- Chargé automatiquement par Docker au premier démarrage d'un volume vide.
-- =====================================================================

-- ---------------------------------------------------------------------
-- entreprises : une ligne par employeur, nom normalisé
-- ---------------------------------------------------------------------
CREATE TABLE entreprises (
    id                    SERIAL PRIMARY KEY,
    nom_norm              TEXT UNIQUE NOT NULL,   -- "orano", sans SA/SAS/Group
    nom_affiche           TEXT,
    secteur               TEXT,
    taille                TEXT CHECK (taille IN ('startup', 'pme', 'eti', 'grand_groupe')),
    site_carrieres        TEXT,
    derniere_candidature  DATE                    -- garde-fou des 3 mois
);

-- ---------------------------------------------------------------------
-- offres : porte les deux clés anti-doublon (url_cle, cle_floue)
-- ---------------------------------------------------------------------
CREATE TABLE offres (
    id                SERIAL PRIMARY KEY,
    url_cle           TEXT UNIQUE NOT NULL,       -- URL nettoyée (sans ?source=..., utm_...)
    cle_floue         TEXT NOT NULL,              -- entreprise_norm|intitule_norm|ville
    entreprise_id     INT REFERENCES entreprises(id),
    source            TEXT NOT NULL
                      CHECK (source IN ('wttj', 'france_travail', 'jobrapido', 'linkedin',
                                        'apec', 'adzuna', 'site_carrieres', 'manuelle')),
    gmail_message_id  TEXT,                       -- mail d'alerte d'origine
    intitule          TEXT,
    ville             TEXT,
    contrat           TEXT,
    date_publication  DATE,
    extrait           TEXT,                       -- V2 : texte de l'alerte
    url_fiche         TEXT,                       -- V2 : fiche chez l'employeur / ATS / API
    texte             TEXT,                       -- fiche complète
    fiche_complete    BOOLEAN NOT NULL DEFAULT FALSE,  -- V2 : FALSE => score plafonné à 84
    criteres          JSONB,                      -- compétences, missions, séniorité extraites
    statut_validite   TEXT NOT NULL DEFAULT 'a_verifier'
                      CHECK (statut_validite IN ('a_verifier', 'valide', 'expiree',
                                                 'pourvue', 'ancienne', 'doublon')),
    doublon_de        INT REFERENCES offres(id),  -- même offre vue sur une autre plateforme
    score             NUMERIC(5,2) CHECK (score BETWEEN 0 AND 100),
    detail_score      JSONB,                      -- note + justification par critère
    classe            CHAR(1) CHECK (classe IN ('A', 'B', 'C')),  -- V2
    motif             TEXT,                       -- V2 : raison d'un classement C
    cv_base           TEXT CHECK (cv_base IN ('DA', 'DE', 'AE')),
    recu_le           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX offres_cle_floue_idx ON offres (cle_floue);
CREATE INDEX offres_entreprise_idx ON offres (entreprise_id);

-- ---------------------------------------------------------------------
-- candidatures : source de vérité, consultée avant toute génération
-- ---------------------------------------------------------------------
CREATE TABLE candidatures (
    id             SERIAL PRIMARY KEY,
    offre_id       INT UNIQUE REFERENCES offres(id),
    entreprise_id  INT REFERENCES entreprises(id),
    type           TEXT NOT NULL DEFAULT 'offre' CHECK (type IN ('offre', 'spontanee')),
    statut         TEXT NOT NULL DEFAULT 'a_postuler'
                   CHECK (statut IN ('a_postuler', 'postule', 'relance', 'reponse_positive',
                                     'refus', 'sans_suite', 'ignore')),
    dossier_drive  TEXT,
    postule_le     DATE,
    relance_le     DATE,                          -- J+7
    relance2_le    DATE,                          -- V2 : J+14
    origine        TEXT NOT NULL DEFAULT 'agent'
                   CHECK (origine IN ('agent', 'manuelle')),  -- manuelle = accusé trouvé dans Gmail
    maj_le         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- reponses : V2, mails reçus des entreprises suivies et leur classement
-- ---------------------------------------------------------------------
CREATE TABLE reponses (
    id                SERIAL PRIMARY KEY,
    candidature_id    INT REFERENCES candidatures(id),
    gmail_message_id  TEXT UNIQUE NOT NULL,
    expediteur        TEXT,
    recu_le           TIMESTAMPTZ,
    classement        TEXT CHECK (classement IN ('positive', 'negative', 'neutre')),
    confiance         NUMERIC(4,3) CHECK (confiance BETWEEN 0 AND 1),
    a_confirmer       BOOLEAN NOT NULL DEFAULT FALSE,  -- confiance < 0,90 : rien n'est supprimé
    confirme_le       TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- contacts : pour les spontanées (RGPD : source obligatoire)
-- ---------------------------------------------------------------------
CREATE TABLE contacts (
    id             SERIAL PRIMARY KEY,
    entreprise_id  INT REFERENCES entreprises(id),
    nom            TEXT,
    fonction       TEXT,
    email          TEXT,
    source_url     TEXT NOT NULL,                 -- page publique où le contact a été trouvé
    contacte_le    DATE,
    opposition     BOOLEAN NOT NULL DEFAULT FALSE -- V2 : ne plus contacter
);

-- ---------------------------------------------------------------------
-- executions : journal de chaque passage du pipeline
-- ---------------------------------------------------------------------
CREATE TABLE executions (
    id           SERIAL PRIMARY KEY,
    debut        TIMESTAMPTZ NOT NULL DEFAULT now(),
    fin          TIMESTAMPTZ,
    mails_lus    INT NOT NULL DEFAULT 0,
    offres_vues  INT NOT NULL DEFAULT 0,
    doublons     INT NOT NULL DEFAULT 0,
    rejetees     INT NOT NULL DEFAULT 0,
    alertes      INT NOT NULL DEFAULT 0,
    erreurs      JSONB
);
