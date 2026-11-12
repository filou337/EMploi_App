-- 008 : le fichier du CV / de la LM est aussi gardé en base, pour le télécharger depuis le téléphone.
ALTER TABLE documents ADD COLUMN contenu BYTEA;
