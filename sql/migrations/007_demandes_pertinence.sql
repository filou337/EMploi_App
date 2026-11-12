-- 007 : file des demandes (téléphone -> PC) et indicateur « offre data » lisible par l'interface.

-- L'interface (téléphone, GitHub Pages) ne fait pas tourner de Python : ce qui en demande
-- (analyse ATS, matching, dépôt de CV, import/export Excel, passage des agents) est déposé ici,
-- puis exécuté par le travailleur du PC dès qu'il est allumé.
CREATE TABLE demandes (
    id              SERIAL PRIMARY KEY,
    type            TEXT NOT NULL CHECK (type IN ('analyser_cv', 'matching', 'creer_dossier',
                                                  'deposer_document', 'importer_candidatures',
                                                  'exporter_excel', 'lancer_veille')),
    parametres      JSONB NOT NULL DEFAULT '{}',
    fichier         BYTEA,          -- fichier envoyé (CV, LM, Excel), 10 Mo maximum
    nom_fichier     TEXT,
    statut          TEXT NOT NULL DEFAULT 'en_attente'
                    CHECK (statut IN ('en_attente', 'en_cours', 'faite', 'erreur')),
    resultat        JSONB,
    fichier_resultat BYTEA,         -- fichier produit (export Excel)
    erreur          TEXT,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT now(),
    traitee_le      TIMESTAMPTZ,
    CHECK (fichier IS NULL OR octet_length(fichier) <= 10 * 1024 * 1024)
);
CREATE INDEX demandes_en_attente ON demandes (cree_le) WHERE statut = 'en_attente';

-- Offre data (pré-filtre Python est_data), calculée par le PC à l'insertion.
ALTER TABLE offres ADD COLUMN pertinente BOOLEAN;
