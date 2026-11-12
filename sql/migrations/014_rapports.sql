-- 014 : rapport périodique (tous les 3 jours) — récapitulatif lu dans l'appli et sur le téléphone.

CREATE TABLE rapports (
    id             SERIAL PRIMARY KEY,
    periode_debut  TIMESTAMPTZ NOT NULL,
    periode_fin    TIMESTAMPTZ NOT NULL,
    contenu        JSONB NOT NULL,      -- chiffres et listes (app/rapport.py)
    cree_le        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX rapports_cree_idx ON rapports (cree_le DESC);

-- Sur Supabase : même règle que les autres tables (seul le propriétaire lit) ; ignoré en local.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'est_proprietaire') THEN
        ALTER TABLE rapports ENABLE ROW LEVEL SECURITY;
        CREATE POLICY proprietaire_seul ON rapports FOR ALL TO authenticated
            USING (est_proprietaire()) WITH CHECK (est_proprietaire());
        REVOKE ALL ON rapports FROM anon;
        GRANT SELECT, INSERT, UPDATE, DELETE ON rapports TO authenticated;
    END IF;
END $$;
