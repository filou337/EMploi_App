-- 013 : pas de 1re relance au-delà de 45 jours après la candidature (trop tard pour relancer).
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
  AND ((c.statut = 'postule' AND c.postule_le IS NOT NULL AND c.postule_le + s.j1 <= current_date
        AND c.postule_le >= current_date - 45)
    OR (c.statut = 'relance' AND c.relance2_le IS NULL AND c.relance_le + s.j2 <= current_date));
