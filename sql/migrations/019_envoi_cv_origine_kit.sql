-- 019 : candidatures automatiques plus souvent envoyées.
-- cv_origine : le CV adapté est moins bon que ton CV d'origine (matching, compatibilité ATS ou
--              phrase retirée du CV) -> la candidature part avec ton CV d'origine + la LM adaptée.
-- kit        : employeur sans adresse publiée -> tout ce qu'il faut pour remplir son formulaire
--              (lien, champs pré-remplis, message de motivation), soumis par toi.

ALTER TABLE candidatures_auto ADD COLUMN cv_origine BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE candidatures_auto ADD COLUMN kit JSONB;

CREATE OR REPLACE VIEW v_candidatures_auto WITH (security_invoker = true) AS
SELECT a.id, a.offre_id, o.intitule, e.nom_affiche AS entreprise, o.url_fiche, o.url_cle, a.score,
       a.destinataire, a.source_contact, a.objet, a.corps, a.statut, a.motif, a.erreur,
       a.cree_le, a.envoyee_le, a.candidature_id, a.fabrication_id,
       (SELECT jsonb_agg(jsonb_build_object('id', d.id, 'type', d.type, 'format', d.format, 'nom', d.nom)
                         ORDER BY d.type, d.format)
        FROM dossier_fichiers d WHERE d.fabrication_id = a.fabrication_id) AS fichiers,
       a.cv_origine, a.kit
FROM candidatures_auto a JOIN offres o ON o.id = a.offre_id
LEFT JOIN entreprises e ON e.id = o.entreprise_id;

-- « J'ai postulé » (formulaire soumis par toi) : la candidature préparée sort des « mises de côté ».
CREATE OR REPLACE FUNCTION j_ai_postule(p_offre INT) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    o RECORD;
    cid INT;
BEGIN
    SELECT id, entreprise_id, intitule, url_cle, url_fiche INTO o FROM offres WHERE id = p_offre;
    IF NOT FOUND THEN RAISE EXCEPTION 'Offre introuvable'; END IF;
    INSERT INTO candidatures (offre_id, entreprise_id, statut, postule_le, origine, poste, url, canal)
    VALUES (o.id, o.entreprise_id, 'postule', current_date, 'agent', o.intitule,
            coalesce(o.url_fiche, CASE WHEN o.url_cle LIKE 'http%' THEN o.url_cle END), 'outil')
    ON CONFLICT (offre_id) DO UPDATE SET statut = 'postule', postule_le = current_date, maj_le = now()
    RETURNING id INTO cid;
    UPDATE entreprises SET derniere_candidature = current_date WHERE id = o.entreprise_id;
    UPDATE offres SET etape = 'envoyee', etape_maj_le = now() WHERE id = o.id;
    UPDATE candidatures_auto SET statut = 'envoyee', envoyee_le = now(), candidature_id = cid, motif = NULL
    WHERE offre_id = o.id AND statut IN ('pret', 'mise_de_cote');
    RETURN cid;
END $$;
