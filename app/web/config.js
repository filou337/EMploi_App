// Configuration du mode en ligne (Supabase). Vide = mode local (API Python du PC).
// Ces deux valeurs sont PUBLIQUES par conception : la protection vient de la connexion à ton
// compte et des règles RLS de la base (sql/supabase/securite.sql). Ne jamais mettre ici la clé
// « service_role » ni la chaîne de connexion Postgres.
window.EMPLOI_CONFIG = {
  supabaseUrl: "",
  supabaseAnonKey: "",
  urlPublique: "",
};
