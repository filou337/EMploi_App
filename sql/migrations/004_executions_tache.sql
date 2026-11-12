-- 004 : chaque passage du pipeline dit quelle tâche (quel « agent ») l'a produit.
ALTER TABLE executions ADD COLUMN tache TEXT;
ALTER TABLE executions ADD COLUMN details JSONB;   -- bilan lisible dans le journal du dashboard
UPDATE executions SET tache = 'collecte' WHERE tache IS NULL AND offres_vues > 0;
