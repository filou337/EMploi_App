-- 021 : zone « ville + rayon ».
-- lieux_geo : communes déjà géocodées (lat/lon NULL = introuvable), lues par les agents seulement.
-- offres.lieu_statut : dans_zone / hors_zone / teletravail / a_verifier ; distance_km depuis ta
-- ville ; lieu_zone : réglage de zone utilisé pour ce calcul (recalcul quand il change).
CREATE TABLE lieux_geo (
    cle     TEXT PRIMARY KEY,
    lat     DOUBLE PRECISION,
    lon     DOUBLE PRECISION,
    maj_le  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE offres
    ADD COLUMN lieu_statut TEXT,
    ADD COLUMN distance_km NUMERIC(6, 1),
    ADD COLUMN lieu_zone TEXT;

CREATE OR REPLACE VIEW v_offres WITH (security_invoker = true) AS
SELECT o.id, o.intitule, o.ville, o.contrat, o.source, o.statut_validite, o.fiche_complete,
       o.url_cle, o.url_fiche, o.recu_le, o.date_publication, o.note_validite, o.score, o.classe,
       o.entreprise_id, o.pertinente, o.etape, o.etape_maj_le, o.dossier, o.modifiee_le,
       e.nom_affiche AS entreprise, e.ats,
       (SELECT c.statut FROM candidatures c WHERE c.offre_id = o.id) AS candidature,
       (SELECT a.score_matching FROM analyses a WHERE a.offre_id = o.id
        ORDER BY a.cree_le DESC LIMIT 1) AS score_matching,
       (SELECT max(c.postule_le) FROM candidatures c WHERE c.entreprise_id = o.entreprise_id
        AND c.statut <> 'ignore' AND c.postule_le > current_date - 90) AS candidature_recente,
       coalesce(o.url_fiche, CASE WHEN o.url_cle LIKE 'http%' THEN o.url_cle END) AS lien,
       o.lieu_statut, o.distance_km
FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        ALTER TABLE lieux_geo ENABLE ROW LEVEL SECURITY;   -- aucune politique : agents seulement
        REVOKE ALL ON lieux_geo FROM anon, authenticated;
    END IF;
END $$;
