-- 001 : plusieurs boîtes Gmail, source Indeed, candidatures importées depuis l'historique.

-- Boîte Gmail d'origine ('principale', 'ancienne'...) : un identifiant de message n'est
-- unique qu'à l'intérieur d'une boîte.
ALTER TABLE offres  ADD COLUMN boite TEXT;
UPDATE offres SET boite = 'principale' WHERE gmail_message_id IS NOT NULL;
ALTER TABLE reponses ADD COLUMN boite TEXT;
ALTER TABLE reponses DROP CONSTRAINT reponses_gmail_message_id_key;
ALTER TABLE reponses ADD CONSTRAINT reponses_boite_message_key UNIQUE (boite, gmail_message_id);

-- Indeed rejoint les plateformes d'alerte.
ALTER TABLE offres DROP CONSTRAINT offres_source_check;
ALTER TABLE offres ADD CONSTRAINT offres_source_check
    CHECK (source IN ('wttj', 'france_travail', 'jobrapido', 'linkedin', 'apec', 'indeed',
                      'adzuna', 'site_carrieres', 'manuelle'));

-- Candidatures faites hors outil : pas d'offre en base, mais un poste et le mail qui les prouve.
ALTER TABLE candidatures ADD COLUMN poste TEXT;
ALTER TABLE candidatures ADD COLUMN canal TEXT;             -- ats (smartrecruiters...), mail, linkedin
ALTER TABLE candidatures ADD COLUMN boite TEXT;
ALTER TABLE candidatures ADD COLUMN gmail_message_id TEXT;  -- mail d'envoi ou accusé de réception
ALTER TABLE candidatures ADD COLUMN gmail_thread_id TEXT;   -- relie les réponses à la candidature
ALTER TABLE candidatures ADD CONSTRAINT candidatures_boite_message_key
    UNIQUE (boite, gmail_message_id);
ALTER TABLE reponses ADD COLUMN extrait TEXT;               -- objet du mail, pour relecture
