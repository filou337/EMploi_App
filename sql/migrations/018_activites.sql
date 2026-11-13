-- 018 : fil d'activité des agents, en direct (onglet Salon) : qui travaille sur quel poste.

CREATE TABLE activites (
    id         BIGSERIAL PRIMARY KEY,
    agent      TEXT NOT NULL,           -- collecte, fiches, matching, candidatures_auto, spontanees…
    action     TEXT NOT NULL,           -- « Prépare CV + LM + mail », « Candidature envoyée »…
    statut     TEXT NOT NULL DEFAULT 'fait' CHECK (statut IN ('en_cours', 'fait', 'erreur')),
    offre_id   INT REFERENCES offres(id) ON DELETE SET NULL,
    entreprise TEXT,
    intitule   TEXT,
    detail     TEXT,
    cree_le    TIMESTAMPTZ NOT NULL DEFAULT now(),
    fini_le    TIMESTAMPTZ
);
CREATE INDEX activites_recentes_idx ON activites (cree_le DESC);

CREATE OR REPLACE VIEW v_activites WITH (security_invoker = true) AS
SELECT a.id, a.agent, a.action, a.statut, a.offre_id,
       coalesce(a.entreprise, e.nom_affiche) AS entreprise, coalesce(a.intitule, o.intitule) AS intitule,
       a.detail, a.cree_le, a.fini_le
FROM activites a
LEFT JOIN offres o ON o.id = a.offre_id
LEFT JOIN entreprises e ON e.id = o.entreprise_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        ALTER TABLE activites ENABLE ROW LEVEL SECURITY;
        CREATE POLICY proprietaire_seul ON activites FOR ALL TO authenticated
            USING (est_proprietaire()) WITH CHECK (est_proprietaire());
        REVOKE ALL ON activites FROM anon;
        GRANT SELECT ON activites, v_activites TO authenticated;   -- lecture seule : seuls les agents écrivent
    END IF;
END $$;
