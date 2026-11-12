-- 005 : analyses du moteur ATS (historique des scores, module H).
CREATE TABLE analyses (
    id              SERIAL PRIMARY KEY,
    document_id     INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    offre_id        INT REFERENCES offres(id) ON DELETE CASCADE,   -- NULL : analyse du CV seul
    type            TEXT NOT NULL CHECK (type IN ('cv', 'matching')),
    modele          TEXT,                                          -- LLM utilisé (NULL si local)
    score_ats       NUMERIC(5,1),
    score_qualite   NUMERIC(5,1),
    score_matching  NUMERIC(5,1),
    resultat        JSONB NOT NULL,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX analyses_document_idx ON analyses (document_id, type, cree_le DESC);
CREATE INDEX analyses_offre_idx ON analyses (offre_id, cree_le DESC);
