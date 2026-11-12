-- Sécurité Supabase (appliquée par outils/migrer_vers_supabase.sh, rejouable sans risque).
-- Principe : la clé « anon » de l'interface est publique ; ce sont ces règles qui protègent
-- les données. Seul le compte inscrit dans `proprietaire` lit et écrit quoi que ce soit.
-- Le PC se connecte avec le rôle propriétaire de la base : la RLS ne le concerne pas.

CREATE TABLE IF NOT EXISTS proprietaire (user_id UUID PRIMARY KEY);
-- Le propriétaire est désigné par son adresse mail (python -m app.vers_supabase --proprietaire …),
-- jamais « tous les comptes » : un inconnu qui s'inscrirait n'aurait accès à rien.

CREATE OR REPLACE FUNCTION est_proprietaire() RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM proprietaire WHERE user_id = auth.uid())
$$;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS proprietaire_seul ON public.%I', t);
        IF t NOT IN ('proprietaire', 'schema_migrations') THEN
            EXECUTE format('CREATE POLICY proprietaire_seul ON public.%I FOR ALL TO authenticated '
                           'USING (est_proprietaire()) WITH CHECK (est_proprietaire())', t);
        END IF;
    END LOOP;
END $$;

-- Aucun accès anonyme ; l'utilisateur connecté passe par les règles ci-dessus.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, public;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO authenticated;
REVOKE ALL ON proprietaire, schema_migrations FROM authenticated;
-- Tables créées plus tard (nouvelles migrations) : mêmes droits par défaut, RLS à réactiver
-- en relançant ce fichier.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, public;

-- Fonctions internes de Supabase présentes dans public (ex. rls_auto_enable, déclencheur qui active
-- la RLS sur chaque nouvelle table) : l'utilisateur connecté n'a pas à les exécuter.
DO $$
DECLARE
    f TEXT;
BEGIN
    FOR f IN SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'public' AND p.prorettype = 'event_trigger'::regtype LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM authenticated, anon, public', f);
    END LOOP;
END $$;
