-- 025 : preuves d'envoi. envois.pieces : noms des pièces jointes ; envois.copie_a : ta propre
-- adresse quand une copie cachée (Cci) t'a été envoyée. v_envois : journal lisible dans l'appli.
ALTER TABLE envois ADD COLUMN pieces TEXT[], ADD COLUMN copie_a TEXT;

CREATE OR REPLACE VIEW v_envois WITH (security_invoker = true) AS
SELECT n.id, n.type, n.automatique, n.statut, n.numero, n.destinataire, n.objet, n.pieces,
       n.copie_a, n.gmail_message_id, n.gmail_thread_id, n.erreur, n.cree_le, n.envoye_le,
       coalesce(eo.nom_affiche, es.nom_affiche, ec.nom_affiche) AS entreprise,
       coalesce(o.intitule, c.poste) AS poste
FROM envois n
LEFT JOIN offres o ON n.type = 'candidature' AND o.id = n.ref_id
LEFT JOIN entreprises eo ON eo.id = o.entreprise_id
LEFT JOIN spontanees s ON n.type = 'spontanee' AND s.id = n.ref_id
LEFT JOIN entreprises es ON es.id = s.entreprise_id
LEFT JOIN candidatures c ON n.type = 'relance' AND c.id = n.ref_id
LEFT JOIN entreprises ec ON ec.id = c.entreprise_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        GRANT SELECT ON v_envois TO authenticated;
    END IF;
END $$;
