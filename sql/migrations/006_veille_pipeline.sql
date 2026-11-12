-- 006 : agent Veille (entreprises surveillées, critères), pipeline des dossiers, notifications.

-- Entreprises que l'agent Veille surveille sur leur site carrières.
CREATE TABLE veille_entreprises (
    id              SERIAL PRIMARY KEY,
    entreprise_id   INT NOT NULL UNIQUE REFERENCES entreprises(id) ON DELETE CASCADE,
    origine         TEXT NOT NULL DEFAULT 'manuelle' CHECK (origine IN ('auto', 'manuelle')),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    ajoutee_le      TIMESTAMPTZ NOT NULL DEFAULT now(),
    derniere_veille TIMESTAMPTZ,
    note            TEXT          -- dernier résultat lisible (« 12 postes sur lever:acme »…)
);

-- Réglages modifiables dans l'appli (critères de veille, seuils).
CREATE TABLE parametres (
    cle     TEXT PRIMARY KEY,
    valeur  JSONB NOT NULL,
    maj_le  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Étape de l'offre dans le pipeline (kanban) et détection des annonces modifiées.
ALTER TABLE offres ADD COLUMN etape TEXT CHECK (etape IN
    ('reperee', 'qualifiee', 'fabrication', 'ats_friendly', 'a_envoyer', 'envoyee'));
ALTER TABLE offres ADD COLUMN etape_maj_le TIMESTAMPTZ;
ALTER TABLE offres ADD COLUMN empreinte_contenu TEXT;   -- sha1(intitulé|lieu|texte)
ALTER TABLE offres ADD COLUMN modifiee_le TIMESTAMPTZ;
ALTER TABLE offres ADD COLUMN dossier TEXT;             -- dossier local du poste (offre.txt, CV, LM)
CREATE INDEX offres_etape_idx ON offres (etape);

CREATE TABLE notifications (
    id        SERIAL PRIMARY KEY,
    offre_id  INT REFERENCES offres(id) ON DELETE CASCADE,
    type      TEXT NOT NULL,       -- nouvelle_offre, qualifiee, modifiee, retiree, erreur
    titre     TEXT NOT NULL,
    message   TEXT,
    lue       BOOLEAN NOT NULL DEFAULT FALSE,
    cree_le   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_non_lues ON notifications (cree_le DESC) WHERE NOT lue;
