-- 022 : candidatures spontanées envoyées automatiquement, et suite donnée à leurs réponses.
-- spontanees.a_valider : motif pour lequel ce mail attend TON clic (ex. redirection vers une
--   autre personne indiquée dans une réponse) ; NULL = peut partir automatiquement.
-- candidatures.relance_prevue_le : réponse « pas de recrutement pour l'instant » -> une seule
--   relance à cette date (J+30), même si la réponse est classée négative ; puis plus rien.
ALTER TABLE spontanees ADD COLUMN a_valider TEXT;
ALTER TABLE candidatures ADD COLUMN relance_prevue_le DATE;

CREATE OR REPLACE VIEW v_spontanees WITH (security_invoker = true) AS
SELECT s.id, s.entreprise_id, e.nom_affiche AS entreprise, e.site_web, s.destinataire, s.objet,
       s.corps, s.statut, s.verification, s.erreur, s.cree_le, s.envoyee_le, s.candidature_id,
       c.source_url, c.opposition, s.a_valider
FROM spontanees s JOIN entreprises e ON e.id = s.entreprise_id
LEFT JOIN contacts c ON c.id = s.contact_id;

-- numero 3 = relance unique après « pas pour l'instant » (motif 'plus_tard').
CREATE OR REPLACE VIEW v_relances WITH (security_invoker = true) AS
WITH s AS (
    SELECT coalesce((valeur->>'relance_jours')::int, 7) AS j1,
           coalesce((valeur->>'relance2_jours')::int, 7) AS j2,
           coalesce((valeur->>'relancer_spontanees')::boolean, false) AS spont
    FROM (SELECT valeur FROM parametres WHERE cle = 'seuils'
          UNION ALL SELECT '{}'::jsonb LIMIT 1) p
)
SELECT c.id, c.type, c.statut, c.poste, c.postule_le, c.relance_le, c.relance2_le, c.contact_email,
       c.url, c.canal, e.nom_affiche AS entreprise, o.intitule AS offre_intitule,
       CASE WHEN c.relance_prevue_le IS NOT NULL THEN 3
            WHEN c.statut = 'postule' THEN 1 ELSE 2 END AS numero,
       CASE WHEN c.relance_prevue_le IS NOT NULL THEN c.relance_prevue_le
            WHEN c.statut = 'postule' THEN c.postule_le + s.j1 ELSE c.relance_le + s.j2 END AS due_le,
       CASE WHEN c.relance_prevue_le IS NOT NULL THEN 'plus_tard' END AS motif
FROM candidatures c CROSS JOIN s
LEFT JOIN entreprises e ON e.id = c.entreprise_id
LEFT JOIN offres o ON o.id = c.offre_id
WHERE NOT c.relance_ignoree
  AND NOT EXISTS (SELECT 1 FROM reponses r WHERE r.candidature_id = c.id
                  AND r.classement = 'positive')
  AND (
    (c.relance_prevue_le IS NOT NULL AND c.relance_prevue_le <= current_date
     AND (c.relance_le IS NULL OR c.relance_le < c.relance_prevue_le))
    OR (c.relance_prevue_le IS NULL
        AND (c.type = 'offre' OR s.spont)
        AND NOT EXISTS (SELECT 1 FROM reponses r WHERE r.candidature_id = c.id
                        AND r.classement = 'negative')
        AND ((c.statut = 'postule' AND c.postule_le IS NOT NULL AND c.postule_le + s.j1 <= current_date
              AND c.postule_le >= current_date - 45)
          OR (c.statut = 'relance' AND c.relance2_le IS NULL AND c.relance_le + s.j2 <= current_date))));

CREATE OR REPLACE FUNCTION marquer_relance(p_id INT) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    c candidatures%ROWTYPE;
BEGIN
    SELECT * INTO c FROM candidatures WHERE id = p_id;
    IF c.id IS NULL THEN RAISE EXCEPTION 'Candidature introuvable'; END IF;
    IF c.relance_prevue_le IS NOT NULL
       AND (c.relance_le IS NULL OR c.relance_le < c.relance_prevue_le) THEN
        UPDATE candidatures SET statut = 'relance', relance_le = current_date, maj_le = now() WHERE id = p_id;
        RETURN 3;
    ELSIF c.statut = 'postule' THEN
        UPDATE candidatures SET statut = 'relance', relance_le = current_date, maj_le = now() WHERE id = p_id;
        RETURN 1;
    ELSIF c.statut = 'relance' THEN
        UPDATE candidatures SET relance2_le = current_date, maj_le = now() WHERE id = p_id;
        RETURN 2;
    END IF;
    RAISE EXCEPTION 'Cette candidature n''est plus en attente de relance.';
END $$;
