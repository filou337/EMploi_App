-- 015 : candidatures spontanées — brouillons préparés dans l'appli (aucun envoi automatique,
-- aucun droit d'écriture sur Gmail : c'est toi qui envoies depuis ta messagerie).

ALTER TABLE entreprises ADD COLUMN site_web TEXT;          -- page d'accueil, pour trouver le contact RH
ALTER TABLE contacts ADD COLUMN cree_le TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE contacts ADD CONSTRAINT contacts_entreprise_email_key UNIQUE (entreprise_id, email);

CREATE TABLE spontanees (
    id              SERIAL PRIMARY KEY,
    entreprise_id   INT NOT NULL REFERENCES entreprises(id) ON DELETE CASCADE,
    contact_id      INT REFERENCES contacts(id) ON DELETE SET NULL,
    destinataire    TEXT,
    objet           TEXT,
    corps           TEXT,
    statut          TEXT NOT NULL DEFAULT 'brouillon'
                    CHECK (statut IN ('brouillon', 'envoyee', 'ignoree', 'erreur')),
    verification    JSONB,      -- garde-fou de véracité (termes et chiffres retirés)
    modele          TEXT,
    erreur          TEXT,
    candidature_id  INT REFERENCES candidatures(id) ON DELETE SET NULL,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT now(),
    envoyee_le      TIMESTAMPTZ
);
CREATE INDEX spontanees_entreprise_idx ON spontanees (entreprise_id, cree_le DESC);

CREATE OR REPLACE VIEW v_spontanees WITH (security_invoker = true) AS
SELECT s.id, s.entreprise_id, e.nom_affiche AS entreprise, e.site_web, s.destinataire, s.objet,
       s.corps, s.statut, s.verification, s.erreur, s.cree_le, s.envoyee_le, s.candidature_id,
       c.source_url, c.opposition
FROM spontanees s JOIN entreprises e ON e.id = s.entreprise_id
LEFT JOIN contacts c ON c.id = s.contact_id;

-- « J'ai envoyé » : la spontanée devient une candidature suivie (relances, réponses, rapport).
CREATE OR REPLACE FUNCTION spontanee_envoyee(p_id INT) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    s spontanees%ROWTYPE;
    cid INT;
BEGIN
    SELECT * INTO s FROM spontanees WHERE id = p_id;
    IF s.id IS NULL THEN RAISE EXCEPTION 'Brouillon introuvable'; END IF;
    IF s.statut <> 'brouillon' THEN RAISE EXCEPTION 'Ce brouillon n''est plus à envoyer.'; END IF;
    INSERT INTO candidatures (entreprise_id, type, statut, postule_le, origine, poste, canal, contact_email)
    VALUES (s.entreprise_id, 'spontanee', 'postule', current_date, 'agent', 'Candidature spontanée',
            'mail', s.destinataire)
    RETURNING id INTO cid;
    UPDATE spontanees SET statut = 'envoyee', envoyee_le = now(), candidature_id = cid WHERE id = p_id;
    UPDATE contacts SET contacte_le = current_date WHERE id = s.contact_id;
    UPDATE entreprises SET derniere_candidature = GREATEST(derniere_candidature, current_date)
    WHERE id = s.entreprise_id;
    RETURN cid;
END $$;

-- Droit d'opposition : ce contact ne sera plus jamais sollicité.
CREATE OR REPLACE FUNCTION spontanee_opposition(p_id INT) RETURNS VOID LANGUAGE sql AS $$
    UPDATE contacts SET opposition = TRUE
    WHERE id = (SELECT contact_id FROM spontanees WHERE id = p_id);
    UPDATE spontanees SET statut = 'ignoree' WHERE id = p_id AND statut = 'brouillon';
$$;

ALTER TABLE demandes DROP CONSTRAINT demandes_type_check;
ALTER TABLE demandes ADD CONSTRAINT demandes_type_check CHECK (type IN (
    'analyser_cv', 'matching', 'creer_dossier', 'deposer_document', 'importer_candidatures',
    'exporter_excel', 'lancer_veille', 'fabriquer', 'preparer_spontanee'));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        ALTER TABLE spontanees ENABLE ROW LEVEL SECURITY;
        CREATE POLICY proprietaire_seul ON spontanees FOR ALL TO authenticated
            USING (est_proprietaire()) WITH CHECK (est_proprietaire());
        REVOKE ALL ON spontanees FROM anon;
        GRANT SELECT, INSERT, UPDATE, DELETE ON spontanees TO authenticated;
        GRANT USAGE, SELECT ON SEQUENCE spontanees_id_seq TO authenticated;
        REVOKE EXECUTE ON FUNCTION spontanee_envoyee(INT), spontanee_opposition(INT) FROM anon, public;
        GRANT EXECUTE ON FUNCTION spontanee_envoyee(INT), spontanee_opposition(INT) TO authenticated;
        GRANT SELECT ON v_spontanees TO authenticated;
    END IF;
END $$;
