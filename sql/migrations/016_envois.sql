-- 016 : envoi en 1 clic depuis Gmail (spontanées, relances). Rien ne part sans un clic de ta part ;
-- chaque envoi est journalisé ici (preuve, plafond quotidien, pas de double envoi).

CREATE TABLE envois (
    id                SERIAL PRIMARY KEY,
    type              TEXT NOT NULL CHECK (type IN ('spontanee', 'relance')),
    ref_id            INT NOT NULL,          -- spontanees.id ou candidatures.id
    numero            INT NOT NULL DEFAULT 1, -- relance 1 ou 2 (1 pour une spontanée)
    destinataire      TEXT NOT NULL,
    objet             TEXT NOT NULL,
    statut            TEXT NOT NULL DEFAULT 'en_cours' CHECK (statut IN ('en_cours', 'envoye', 'erreur')),
    gmail_message_id  TEXT,
    gmail_thread_id   TEXT,
    erreur            TEXT,
    cree_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
    envoye_le         TIMESTAMPTZ
);
CREATE INDEX envois_jour_idx ON envois (cree_le DESC);
-- Jamais deux envois réussis (ou en cours) pour le même brouillon / la même relance.
CREATE UNIQUE INDEX envois_unique_idx ON envois (type, ref_id, numero) WHERE statut <> 'erreur';

ALTER TABLE demandes DROP CONSTRAINT demandes_type_check;
ALTER TABLE demandes ADD CONSTRAINT demandes_type_check CHECK (type IN (
    'analyser_cv', 'matching', 'creer_dossier', 'deposer_document', 'importer_candidatures',
    'exporter_excel', 'lancer_veille', 'fabriquer', 'preparer_spontanee', 'envoyer_mail'));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        ALTER TABLE envois ENABLE ROW LEVEL SECURITY;
        CREATE POLICY proprietaire_seul ON envois FOR ALL TO authenticated
            USING (est_proprietaire()) WITH CHECK (est_proprietaire());
        REVOKE ALL ON envois FROM anon;
        GRANT SELECT ON envois TO authenticated;   -- lecture seule : seul le PC écrit les envois
    END IF;
END $$;
