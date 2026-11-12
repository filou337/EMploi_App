-- 002 : recherche de la fiche complète chez l'employeur (ATS public).

-- Site carrières trouvé pour l'employeur : 'greenhouse', 'lever'... ou 'aucun'.
ALTER TABLE entreprises ADD COLUMN ats TEXT;
ALTER TABLE entreprises ADD COLUMN ats_slug TEXT;
ALTER TABLE entreprises ADD COLUMN ats_verifie_le TIMESTAMPTZ;

-- Dernier contrôle de validité d'une offre et ce qu'il a conclu (lisible dans le dashboard).
ALTER TABLE offres ADD COLUMN verifiee_le TIMESTAMPTZ;
ALTER TABLE offres ADD COLUMN note_validite TEXT;
