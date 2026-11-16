-- 024 : « Envoi auto » demandé par toi sur un poste précis (onglet Envois auto).
-- envoi_auto_demande_le : ta demande, traitée au prochain passage (annonce cherchée si besoin,
--   puis dossier préparé et départ après le délai) ; remise à NULL une fois traitée.
-- envoi_auto_note : où en est ta demande (annonce introuvable, employeur déjà contacté…).
ALTER TABLE offres ADD COLUMN envoi_auto_demande_le TIMESTAMPTZ, ADD COLUMN envoi_auto_note TEXT;

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
       o.lieu_statut, o.distance_km, o.envoi_auto_demande_le, o.envoi_auto_note,
       (SELECT a.statut FROM candidatures_auto a WHERE a.offre_id = o.id) AS auto_statut
FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id;
