-- 023 : une candidature automatique prête attend avant de partir (2 h par défaut,
-- seuils.delai_envoi_auto_heures) : pendant ce temps tu peux l'envoyer tout de suite ou la bloquer.
ALTER TABLE candidatures_auto ADD COLUMN depart_le TIMESTAMPTZ;
UPDATE candidatures_auto SET depart_le = now() + interval '2 hours' WHERE statut = 'pret';

CREATE OR REPLACE VIEW v_candidatures_auto WITH (security_invoker = true) AS
SELECT a.id, a.offre_id, o.intitule, e.nom_affiche AS entreprise, o.url_fiche, o.url_cle, a.score,
       a.destinataire, a.source_contact, a.objet, a.corps, a.statut, a.motif, a.erreur,
       a.cree_le, a.envoyee_le, a.candidature_id, a.fabrication_id,
       (SELECT jsonb_agg(jsonb_build_object('id', d.id, 'type', d.type, 'format', d.format, 'nom', d.nom)
                         ORDER BY d.type, d.format)
        FROM dossier_fichiers d WHERE d.fabrication_id = a.fabrication_id) AS fichiers,
       a.cv_origine, a.kit, a.depart_le
FROM candidatures_auto a JOIN offres o ON o.id = a.offre_id
LEFT JOIN entreprises e ON e.id = o.entreprise_id;
