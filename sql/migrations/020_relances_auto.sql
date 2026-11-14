-- 020 : relances automatiques.
-- relance_motif : pourquoi une candidature ne sera pas relancée automatiquement (réponse qui
-- renvoie vers le site officiel, ou demande de ne plus être recontacté). Posé par l'agent Suivi
-- avec relance_ignoree = TRUE ; « Ne pas relancer » dans l'appli reste possible à la main.
ALTER TABLE candidatures ADD COLUMN relance_motif TEXT;
