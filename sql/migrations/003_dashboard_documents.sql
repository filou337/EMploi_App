-- 003 : dashboard (notes, lien, saisie manuelle) et documents de base (CV, LM).

ALTER TABLE candidatures ADD COLUMN notes TEXT;
ALTER TABLE candidatures ADD COLUMN url TEXT;            -- lien de l'offre, saisi ou importé

-- CV et lettres déposés dans l'appli. Chaque dépôt crée une version ; une seule par type
-- est « par défaut » (celle que l'outil adaptera aux offres).
CREATE TABLE documents (
    id           SERIAL PRIMARY KEY,
    type         TEXT NOT NULL CHECK (type IN ('cv', 'lm')),
    langue       TEXT NOT NULL DEFAULT 'fr' CHECK (langue IN ('fr', 'en')),
    nom_fichier  TEXT NOT NULL,                  -- nom d'origine, pour l'affichage
    chemin       TEXT NOT NULL,                  -- fichier stocké sous profil/documents/
    format       TEXT NOT NULL CHECK (format IN ('pdf', 'docx')),
    taille       INT NOT NULL,
    empreinte    TEXT NOT NULL,                  -- sha256 : un même fichier n'est pas stocké deux fois
    texte        TEXT,                           -- texte extrait (lecture ATS)
    diagnostic   JSONB,                          -- pages, mots, sections reconnues, alertes
    par_defaut   BOOLEAN NOT NULL DEFAULT FALSE,
    depose_le    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX documents_un_par_defaut ON documents (type, langue) WHERE par_defaut;
CREATE UNIQUE INDEX documents_empreinte ON documents (type, empreinte);
