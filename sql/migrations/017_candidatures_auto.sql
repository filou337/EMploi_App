-- 017 : candidatures automatiques. Pour chaque offre notée : CV + LM adaptés, mail d'accompagnement,
-- puis envoi automatique (score < seuil) ou mise de côté pour toi (score ≥ seuil, contact
-- introuvable, dossier à revoir). Interrupteur `parametres.envoi.auto`, désactivé par défaut.

CREATE TABLE candidatures_auto (
    id              SERIAL PRIMARY KEY,
    offre_id        INT NOT NULL UNIQUE REFERENCES offres(id) ON DELETE CASCADE,
    fabrication_id  INT REFERENCES fabrications(id) ON DELETE SET NULL,
    destinataire    TEXT,
    source_contact  TEXT,           -- où l'adresse a été trouvée (fiche de l'offre, page publique…)
    objet           TEXT,
    corps           TEXT,
    statut          TEXT NOT NULL DEFAULT 'pret'
                    CHECK (statut IN ('pret', 'envoyee', 'mise_de_cote', 'ignoree', 'erreur')),
    motif           TEXT,           -- pourquoi elle est mise de côté (score ≥ seuil, pas de contact…)
    score           NUMERIC(5, 1),
    envoi_id        INT REFERENCES envois(id) ON DELETE SET NULL,
    candidature_id  INT REFERENCES candidatures(id) ON DELETE SET NULL,
    erreur          TEXT,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT now(),
    envoyee_le      TIMESTAMPTZ
);
CREATE INDEX candidatures_auto_statut_idx ON candidatures_auto (statut, cree_le DESC);

CREATE OR REPLACE VIEW v_candidatures_auto WITH (security_invoker = true) AS
SELECT a.id, a.offre_id, o.intitule, e.nom_affiche AS entreprise, o.url_fiche, o.url_cle, a.score,
       a.destinataire, a.source_contact, a.objet, a.corps, a.statut, a.motif, a.erreur,
       a.cree_le, a.envoyee_le, a.candidature_id, a.fabrication_id,
       (SELECT jsonb_agg(jsonb_build_object('id', d.id, 'type', d.type, 'format', d.format, 'nom', d.nom)
                         ORDER BY d.type, d.format)
        FROM dossier_fichiers d WHERE d.fabrication_id = a.fabrication_id) AS fichiers
FROM candidatures_auto a JOIN offres o ON o.id = a.offre_id
LEFT JOIN entreprises e ON e.id = o.entreprise_id;

ALTER TABLE envois DROP CONSTRAINT envois_type_check;
ALTER TABLE envois ADD CONSTRAINT envois_type_check CHECK (type IN ('spontanee', 'relance', 'candidature'));
ALTER TABLE envois ADD COLUMN automatique BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        ALTER TABLE candidatures_auto ENABLE ROW LEVEL SECURITY;
        CREATE POLICY proprietaire_seul ON candidatures_auto FOR ALL TO authenticated
            USING (est_proprietaire()) WITH CHECK (est_proprietaire());
        REVOKE ALL ON candidatures_auto FROM anon;
        GRANT SELECT, UPDATE ON candidatures_auto TO authenticated;
        GRANT SELECT ON v_candidatures_auto TO authenticated;
    END IF;
END $$;
