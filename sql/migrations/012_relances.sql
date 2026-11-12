-- 012 : relances (J+7, J+14) et passage automatique « sans suite ».

ALTER TABLE candidatures ADD COLUMN contact_email TEXT;        -- destinataire d'une relance, si connu
ALTER TABLE candidatures ADD COLUMN relance_ignoree BOOLEAN NOT NULL DEFAULT FALSE;

-- Relances à faire aujourd'hui, selon les seuils (paramètres « seuils »).
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
       CASE WHEN c.statut = 'postule' THEN 1 ELSE 2 END AS numero,
       CASE WHEN c.statut = 'postule' THEN c.postule_le + s.j1 ELSE c.relance_le + s.j2 END AS due_le
FROM candidatures c CROSS JOIN s
LEFT JOIN entreprises e ON e.id = c.entreprise_id
LEFT JOIN offres o ON o.id = c.offre_id
WHERE NOT c.relance_ignoree
  AND (c.type = 'offre' OR s.spont)
  AND NOT EXISTS (SELECT 1 FROM reponses r WHERE r.candidature_id = c.id
                  AND r.classement IN ('positive', 'negative'))
  AND ((c.statut = 'postule' AND c.postule_le IS NOT NULL AND c.postule_le + s.j1 <= current_date)
    OR (c.statut = 'relance' AND c.relance2_le IS NULL AND c.relance_le + s.j2 <= current_date));

-- « J'ai relancé » : relance 1 -> statut « relancé » ; relance 2 -> date enregistrée.
CREATE OR REPLACE FUNCTION marquer_relance(p_id INT) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    st TEXT;
BEGIN
    SELECT statut INTO st FROM candidatures WHERE id = p_id;
    IF st IS NULL THEN RAISE EXCEPTION 'Candidature introuvable'; END IF;
    IF st = 'postule' THEN
        UPDATE candidatures SET statut = 'relance', relance_le = current_date, maj_le = now() WHERE id = p_id;
        RETURN 1;
    ELSIF st = 'relance' THEN
        UPDATE candidatures SET relance2_le = current_date, maj_le = now() WHERE id = p_id;
        RETURN 2;
    END IF;
    RAISE EXCEPTION 'Cette candidature n''est plus en attente de relance.';
END $$;

CREATE OR REPLACE FUNCTION ignorer_relance(p_id INT) RETURNS VOID LANGUAGE sql AS $$
    UPDATE candidatures SET relance_ignoree = TRUE, maj_le = now() WHERE id = p_id;
$$;

-- Aucune réponse 30 jours (par défaut) après la dernière relance : « sans suite ».
CREATE OR REPLACE FUNCTION classer_sans_suite() RETURNS INT LANGUAGE sql AS $$
    WITH s AS (SELECT coalesce((SELECT (valeur->>'sans_suite_jours')::int FROM parametres
                                WHERE cle = 'seuils'), 30) AS j),
    maj AS (
        UPDATE candidatures c SET statut = 'sans_suite', maj_le = now() FROM s
        WHERE c.statut = 'relance' AND coalesce(c.relance2_le, c.relance_le) + s.j <= current_date
          AND NOT EXISTS (SELECT 1 FROM reponses r WHERE r.candidature_id = c.id
                          AND r.classement IN ('positive', 'negative'))
        RETURNING 1)
    SELECT count(*)::int FROM maj;
$$;
