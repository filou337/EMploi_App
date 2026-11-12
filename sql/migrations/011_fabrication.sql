-- 011 : agent Dossier — fabrication du CV et de la LM adaptés à une offre.

CREATE TABLE fabrications (
    id                     SERIAL PRIMARY KEY,
    offre_id               INT NOT NULL REFERENCES offres(id) ON DELETE CASCADE,
    langue                 TEXT NOT NULL CHECK (langue IN ('fr', 'en')),
    statut                 TEXT NOT NULL DEFAULT 'en_cours'
                           CHECK (statut IN ('en_cours', 'ats_friendly', 'a_revoir', 'erreur', 'validee')),
    modele                 TEXT,
    contenu                JSONB,     -- CV et LM structurés (ce qui a été rendu)
    verification           JSONB,     -- garde-fou de véracité : termes et chiffres contrôlés
    controle               JSONB,     -- relecture ATS du PDF produit, scores et motifs
    score_ats              NUMERIC(5,1),
    score_matching         NUMERIC(5,1),
    score_matching_origine NUMERIC(5,1),
    erreur                 TEXT,
    cree_le                TIMESTAMPTZ NOT NULL DEFAULT now(),
    validee_le             TIMESTAMPTZ
);
CREATE INDEX fabrications_offre_idx ON fabrications (offre_id, cree_le DESC);

CREATE TABLE dossier_fichiers (
    id              SERIAL PRIMARY KEY,
    fabrication_id  INT NOT NULL REFERENCES fabrications(id) ON DELETE CASCADE,
    type            TEXT NOT NULL CHECK (type IN ('cv', 'lm')),
    format          TEXT NOT NULL CHECK (format IN ('pdf', 'docx')),
    nom             TEXT NOT NULL,
    contenu         BYTEA NOT NULL,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE demandes DROP CONSTRAINT demandes_type_check;
ALTER TABLE demandes ADD CONSTRAINT demandes_type_check CHECK (type IN (
    'analyser_cv', 'matching', 'creer_dossier', 'deposer_document', 'importer_candidatures',
    'exporter_excel', 'lancer_veille', 'fabriquer'));

CREATE OR REPLACE VIEW v_fabrications WITH (security_invoker = true) AS
SELECT f.id, f.offre_id, f.langue, f.statut, f.modele, f.contenu, f.verification, f.controle,
       f.score_ats, f.score_matching, f.score_matching_origine, f.erreur, f.cree_le, f.validee_le,
       (SELECT jsonb_agg(jsonb_build_object('id', d.id, 'type', d.type, 'format', d.format, 'nom', d.nom)
                         ORDER BY d.type, d.format)
        FROM dossier_fichiers d WHERE d.fabrication_id = f.id) AS fichiers
FROM fabrications f;

-- « Préparer le dossier » : la fabrication (Gemini + rendu) est faite par le PC.
CREATE OR REPLACE FUNCTION changer_etape(p_offre INT, p_etape TEXT) RETURNS JSONB LANGUAGE plpgsql AS $$
BEGIN
    IF p_etape = 'envoyee' THEN
        RETURN jsonb_build_object('candidature_id', j_ai_postule(p_offre));
    END IF;
    UPDATE offres SET etape = p_etape, etape_maj_le = now() WHERE id = p_offre;
    IF NOT FOUND THEN RAISE EXCEPTION 'Offre introuvable'; END IF;
    IF p_etape = 'fabrication' THEN
        INSERT INTO demandes (type, parametres) VALUES ('fabriquer', jsonb_build_object('offre_id', p_offre));
    END IF;
    RETURN jsonb_build_object('ok', true);
END $$;

-- Validation par l'utilisateur : le dossier passe « À envoyer ».
CREATE OR REPLACE FUNCTION valider_fabrication(p_id INT) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    oid INT;
BEGIN
    UPDATE fabrications SET statut = 'validee', validee_le = now()
    WHERE id = p_id AND statut IN ('ats_friendly', 'a_revoir') RETURNING offre_id INTO oid;
    IF oid IS NULL THEN RAISE EXCEPTION 'Dossier introuvable ou pas encore prêt'; END IF;
    UPDATE offres SET etape = 'a_envoyer', etape_maj_le = now() WHERE id = oid;
END $$;
