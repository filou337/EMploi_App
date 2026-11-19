-- 027 : la section partenaires ne montre que les offres retenues par tes critères (entrées dans
-- le pipeline, ou déjà candidatées) : plus les stages ni les alternances écartés.
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
  AND o.lieu_statut IS DISTINCT FROM 'hors_zone'
  AND (o.etape IS NOT NULL OR EXISTS (SELECT 1 FROM candidatures c WHERE c.offre_id = o.id));
