-- 026 : listes d'entreprises partenaires (fichier Excel importé dans l'onglet Veille).
-- veille_entreprises.liste : nom de la liste d'origine (NULL = entreprise ajoutée à la main).
-- v_offres_partenaires : offres en cours chez ces entreprises, les plus récentes d'abord.
ALTER TABLE veille_entreprises ADD COLUMN liste TEXT;

CREATE OR REPLACE VIEW v_veille_entreprises WITH (security_invoker = true) AS
SELECT v.id, v.origine, v.active, v.ajoutee_le, v.derniere_veille, v.note,
       e.id AS entreprise_id, e.nom_affiche, e.ats, e.ats_slug,
       (SELECT count(*) FROM offres o WHERE o.entreprise_id = e.id AND o.etape IS NOT NULL
        AND o.statut_validite IN ('a_verifier', 'valide')) AS offres_pipeline,
       v.liste, e.secteur
FROM veille_entreprises v JOIN entreprises e ON e.id = v.entreprise_id;

CREATE OR REPLACE VIEW v_offres_partenaires WITH (security_invoker = true) AS
SELECT o.id, o.intitule, o.ville, o.contrat, o.source, o.recu_le, o.date_publication,
       o.etape, o.lieu_statut, o.entreprise_id, e.nom_affiche AS entreprise, e.secteur, v.liste,
       coalesce(o.url_fiche, CASE WHEN o.url_cle LIKE 'http%' THEN o.url_cle END) AS lien,
       (SELECT a.score_matching FROM analyses a WHERE a.offre_id = o.id
        ORDER BY a.cree_le DESC LIMIT 1) AS score_matching,
       (SELECT c.statut FROM candidatures c WHERE c.offre_id = o.id) AS candidature
FROM offres o
JOIN veille_entreprises v ON v.entreprise_id = o.entreprise_id AND v.liste IS NOT NULL
JOIN entreprises e ON e.id = o.entreprise_id
WHERE o.statut_validite IN ('a_verifier', 'valide') AND o.pertinente IS NOT FALSE
  AND o.lieu_statut IS DISTINCT FROM 'hors_zone';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        GRANT SELECT ON v_offres_partenaires TO authenticated;
    END IF;
END $$;

-- Import du fichier depuis l'appli en ligne : exécuté par le travailleur du PC.
ALTER TABLE demandes DROP CONSTRAINT demandes_type_check;
ALTER TABLE demandes ADD CONSTRAINT demandes_type_check CHECK (type IN (
    'analyser_cv', 'matching', 'creer_dossier', 'deposer_document', 'importer_candidatures',
    'exporter_excel', 'lancer_veille', 'fabriquer', 'preparer_spontanee', 'envoyer_mail',
    'importer_partenaires'));
