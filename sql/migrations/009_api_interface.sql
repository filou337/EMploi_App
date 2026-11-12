-- 009 : API de l'interface (lue directement par le téléphone via Supabase).
-- Vues en security_invoker : la sécurité ligne à ligne (RLS) de l'utilisateur connecté s'applique.
-- Fonctions en SECURITY INVOKER pour la même raison. Le PC (rôle propriétaire) n'est pas concerné.

-- Normalisation d'un nom d'entreprise, comme app/collecte/normalisation.norm_entreprise (sans espaces).
CREATE OR REPLACE FUNCTION nom_compact(nom TEXT) RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(nullif(regexp_replace(
        regexp_replace(
            ' ' || regexp_replace(translate(lower(coalesce(nom, '')),
                'àâäáãéèêëíìîïóòôöõúùûüçñÿ', 'aaaaaeeeeiiiiooooouuuucny'), '[^a-z0-9]+', ' ', 'g') || ' ',
            ' (sa|sas|sasu|sarl|eurl|se|inc|ltd|llc|gmbh|group|groupe|france|en) ', ' ', 'g'),
        ' ', '', 'g'), ''),
        regexp_replace(lower(coalesce(nom, '')), '[^a-z0-9]', '', 'g'))
$$;

CREATE OR REPLACE FUNCTION entreprise_pour(nom TEXT) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    eid INT;
BEGIN
    SELECT id INTO eid FROM entreprises WHERE replace(nom_norm, ' ', '') = nom_compact(nom)
    ORDER BY id LIMIT 1;
    IF eid IS NULL THEN
        INSERT INTO entreprises (nom_norm, nom_affiche) VALUES (nom_compact(nom), trim(nom))
        ON CONFLICT (nom_norm) DO UPDATE SET nom_norm = EXCLUDED.nom_norm
        RETURNING id INTO eid;
    END IF;
    RETURN eid;
END $$;

-- --- Vues ----------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_offres WITH (security_invoker = true) AS
SELECT o.id, o.intitule, o.ville, o.contrat, o.source, o.statut_validite, o.fiche_complete,
       o.url_cle, o.url_fiche, o.recu_le, o.date_publication, o.note_validite, o.score, o.classe,
       o.entreprise_id, o.pertinente, o.etape, o.etape_maj_le, o.dossier, o.modifiee_le,
       e.nom_affiche AS entreprise, e.ats,
       (SELECT c.statut FROM candidatures c WHERE c.offre_id = o.id) AS candidature,
       (SELECT a.score_matching FROM analyses a WHERE a.offre_id = o.id
        ORDER BY a.cree_le DESC LIMIT 1) AS score_matching,
       (SELECT max(c.postule_le) FROM candidatures c WHERE c.entreprise_id = o.entreprise_id
        AND c.statut <> 'ignore' AND c.postule_le > current_date - 90) AS candidature_recente,
       coalesce(o.url_fiche, CASE WHEN o.url_cle LIKE 'http%' THEN o.url_cle END) AS lien
FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id;

CREATE OR REPLACE VIEW v_offre_detail WITH (security_invoker = true) AS
SELECT o.id, o.intitule, o.ville, o.contrat, o.source, o.statut_validite, o.fiche_complete,
       o.url_cle, o.url_fiche, o.recu_le, o.date_publication, o.note_validite, o.texte, o.extrait,
       o.doublon_de, o.etape, o.dossier, e.nom_affiche AS entreprise, e.ats, e.ats_slug,
       d.source AS original_source, d.intitule AS original_intitule
FROM offres o LEFT JOIN entreprises e ON e.id = o.entreprise_id
LEFT JOIN offres d ON d.id = o.doublon_de;

CREATE OR REPLACE VIEW v_candidatures WITH (security_invoker = true) AS
SELECT c.id, c.type, c.statut, c.postule_le, c.relance_le, c.canal, c.poste, c.notes, c.url,
       c.origine, c.offre_id, e.nom_affiche AS entreprise,
       (SELECT count(*) FROM reponses r WHERE r.candidature_id = c.id) AS nb_reponses,
       (SELECT max(r.recu_le) FROM reponses r WHERE r.candidature_id = c.id) AS derniere_reponse
FROM candidatures c LEFT JOIN entreprises e ON e.id = c.entreprise_id;

CREATE OR REPLACE VIEW v_reponses WITH (security_invoker = true) AS
SELECT r.id, r.recu_le, r.expediteur, r.classement, r.confiance, r.a_confirmer, r.confirme_le,
       r.extrait, c.id AS candidature_id, c.poste, e.nom_affiche AS entreprise
FROM reponses r JOIN candidatures c ON c.id = r.candidature_id
LEFT JOIN entreprises e ON e.id = c.entreprise_id;

CREATE OR REPLACE VIEW v_documents WITH (security_invoker = true) AS
SELECT id, type, langue, nom_fichier, format, taille, diagnostic, par_defaut, depose_le
FROM documents;

CREATE OR REPLACE VIEW v_analyses WITH (security_invoker = true) AS
SELECT a.id, a.type, a.offre_id, a.cree_le, a.modele, a.score_ats, a.score_qualite,
       a.score_matching, a.resultat, d.nom_fichier AS document, d.par_defaut
FROM analyses a JOIN documents d ON d.id = a.document_id;

CREATE OR REPLACE VIEW v_veille_entreprises WITH (security_invoker = true) AS
SELECT v.id, v.origine, v.active, v.ajoutee_le, v.derniere_veille, v.note,
       e.id AS entreprise_id, e.nom_affiche, e.ats, e.ats_slug,
       (SELECT count(*) FROM offres o WHERE o.entreprise_id = e.id AND o.etape IS NOT NULL
        AND o.statut_validite IN ('a_verifier', 'valide')) AS offres_pipeline
FROM veille_entreprises v JOIN entreprises e ON e.id = v.entreprise_id;

CREATE OR REPLACE VIEW v_journal WITH (security_invoker = true) AS
SELECT id, tache, debut, fin, mails_lus, offres_vues, doublons, rejetees, details,
       jsonb_array_length(coalesce(erreurs, '[]'::jsonb)) AS nb_erreurs
FROM executions WHERE debut > now() - interval '30 days';

-- --- Fonctions ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION resume() RETURNS JSONB LANGUAGE sql STABLE AS $$
WITH o AS (SELECT statut_validite, fiche_complete, pertinente FROM offres),
     c AS (SELECT statut, count(*) AS n FROM candidatures GROUP BY statut),
     tot AS (SELECT coalesce(sum(n) FILTER (WHERE statut <> 'ignore'), 0) AS total FROM c),
     cv AS (SELECT score_ats, score_qualite, cree_le FROM v_analyses
            WHERE type = 'cv' AND par_defaut ORDER BY cree_le DESC LIMIT 1),
     m AS (SELECT count(*) AS n, round(avg(score_matching), 1) AS moyenne FROM analyses
           WHERE type = 'matching' AND cree_le > now() - interval '30 days')
SELECT jsonb_build_object(
  'offres', jsonb_build_object(
      'total', (SELECT count(*) FROM o),
      'par_statut', (SELECT coalesce(jsonb_object_agg(statut_validite, n), '{}') FROM
                     (SELECT statut_validite, count(*) AS n FROM o GROUP BY 1) x),
      'data_ouvertes', (SELECT count(*) FROM o WHERE pertinente
                        AND statut_validite IN ('a_verifier', 'valide')),
      'fiches_completes', (SELECT count(*) FROM o WHERE fiche_complete)),
  'candidatures', jsonb_build_object(
      'total', (SELECT total FROM tot),
      'en_cours', (SELECT coalesce(sum(n), 0) FROM c WHERE statut IN ('postule', 'relance', 'a_postuler')),
      'entretiens', (SELECT coalesce(sum(n), 0) FROM c WHERE statut = 'reponse_positive'),
      'refus', (SELECT coalesce(sum(n), 0) FROM c WHERE statut = 'refus'),
      'par_statut', (SELECT coalesce(jsonb_object_agg(statut, n), '{}') FROM c),
      'taux_reponse', (SELECT CASE WHEN total > 0 THEN round(100.0 * (SELECT coalesce(sum(n), 0) FROM c
                        WHERE statut IN ('refus', 'reponse_positive')) / total, 1) ELSE 0 END FROM tot)),
  'semaines', (SELECT coalesce(jsonb_agg(s ORDER BY s.semaine), '[]') FROM (
      SELECT date_trunc('week', postule_le)::date AS semaine, count(*) AS n,
             count(*) FILTER (WHERE type = 'spontanee') AS spontanees
      FROM candidatures WHERE postule_le >= current_date - 84 AND statut <> 'ignore' GROUP BY 1) s),
  'reponses_a_confirmer', (SELECT count(*) FROM reponses WHERE a_confirmer AND confirme_le IS NULL),
  'documents', (SELECT coalesce(jsonb_agg(jsonb_build_object('type', type, 'nom_fichier', nom_fichier,
                'diagnostic', diagnostic, 'depose_le', depose_le)), '[]') FROM documents WHERE par_defaut),
  'ats', jsonb_build_object(
      'disponible', true,
      'score_ats', (SELECT score_ats FROM cv), 'score_qualite', (SELECT score_qualite FROM cv),
      'analyse_le', (SELECT cree_le FROM cv),
      'matchings', (SELECT n FROM m), 'matching_moyen', (SELECT moyenne FROM m),
      'historique', (SELECT coalesce(jsonb_agg(h ORDER BY h.cree_le DESC), '[]') FROM (
          SELECT score_ats, score_qualite, cree_le FROM v_analyses WHERE type = 'cv'
          ORDER BY cree_le DESC LIMIT 8) h)),
  'pc', (SELECT valeur FROM parametres WHERE cle = 'pc'))
$$;

CREATE OR REPLACE FUNCTION j_ai_postule(p_offre INT) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    o RECORD;
    cid INT;
BEGIN
    SELECT id, entreprise_id, intitule, url_cle, url_fiche INTO o FROM offres WHERE id = p_offre;
    IF NOT FOUND THEN RAISE EXCEPTION 'Offre introuvable'; END IF;
    INSERT INTO candidatures (offre_id, entreprise_id, statut, postule_le, origine, poste, url, canal)
    VALUES (o.id, o.entreprise_id, 'postule', current_date, 'agent', o.intitule,
            coalesce(o.url_fiche, CASE WHEN o.url_cle LIKE 'http%' THEN o.url_cle END), 'outil')
    ON CONFLICT (offre_id) DO UPDATE SET statut = 'postule', postule_le = current_date, maj_le = now()
    RETURNING id INTO cid;
    UPDATE entreprises SET derniere_candidature = current_date WHERE id = o.entreprise_id;
    UPDATE offres SET etape = 'envoyee', etape_maj_le = now() WHERE id = o.id;
    RETURN cid;
END $$;

CREATE OR REPLACE FUNCTION changer_etape(p_offre INT, p_etape TEXT) RETURNS JSONB LANGUAGE plpgsql AS $$
BEGIN
    IF p_etape = 'envoyee' THEN
        RETURN jsonb_build_object('candidature_id', j_ai_postule(p_offre));
    END IF;
    UPDATE offres SET etape = p_etape, etape_maj_le = now() WHERE id = p_offre;
    IF NOT FOUND THEN RAISE EXCEPTION 'Offre introuvable'; END IF;
    IF p_etape = 'fabrication' THEN  -- le dossier (offre.txt…) est créé sur le PC
        INSERT INTO demandes (type, parametres) VALUES ('creer_dossier', jsonb_build_object('offre_id', p_offre));
    END IF;
    RETURN jsonb_build_object('ok', true);
END $$;

CREATE OR REPLACE FUNCTION coller_fiche(p_offre INT, p_texte TEXT, p_url TEXT DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF length(coalesce(p_texte, '')) < 200 THEN
        RAISE EXCEPTION 'La fiche doit faire au moins 200 caractères.';
    END IF;
    UPDATE offres SET texte = trim(p_texte), fiche_complete = TRUE, url_fiche = coalesce(p_url, url_fiche),
           statut_validite = CASE WHEN statut_validite = 'a_verifier' THEN 'valide' ELSE statut_validite END,
           verifiee_le = now(), note_validite = 'fiche collée à la main'
    WHERE id = p_offre;
    IF NOT FOUND THEN RAISE EXCEPTION 'Offre introuvable'; END IF;
END $$;

CREATE OR REPLACE FUNCTION ajouter_candidature(p JSONB) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    eid INT;
    jour DATE := coalesce(nullif(p->>'postule_le', '')::date, current_date);
    cid INT;
BEGIN
    IF length(trim(coalesce(p->>'entreprise', ''))) = 0 THEN RAISE EXCEPTION 'Entreprise obligatoire'; END IF;
    eid := entreprise_pour(p->>'entreprise');
    IF EXISTS (SELECT 1 FROM candidatures WHERE entreprise_id = eid AND postule_le = jour
               AND coalesce(lower(poste), '') = lower(trim(coalesce(p->>'poste', '')))) THEN
        RAISE EXCEPTION 'Cette candidature existe déjà (même entreprise, poste et date).';
    END IF;
    INSERT INTO candidatures (entreprise_id, type, statut, postule_le, origine, poste, url, notes, canal)
    VALUES (eid, coalesce(nullif(p->>'type', ''), 'offre'), coalesce(nullif(p->>'statut', ''), 'postule'),
            jour, 'manuelle', nullif(trim(coalesce(p->>'poste', '')), ''), nullif(p->>'url', ''),
            nullif(p->>'notes', ''), 'saisie')
    RETURNING id INTO cid;
    UPDATE entreprises SET derniere_candidature = GREATEST(derniere_candidature, jour) WHERE id = eid;
    RETURN cid;
END $$;

CREATE OR REPLACE FUNCTION confirmer_reponse(p_id INT, p_classement TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    cid INT;
BEGIN
    UPDATE reponses SET classement = p_classement, a_confirmer = FALSE, confirme_le = now()
    WHERE id = p_id RETURNING candidature_id INTO cid;
    IF cid IS NULL THEN RAISE EXCEPTION 'Réponse introuvable'; END IF;
    IF p_classement IN ('positive', 'negative') THEN
        UPDATE candidatures SET statut = CASE p_classement WHEN 'positive' THEN 'reponse_positive'
                                              ELSE 'refus' END, maj_le = now() WHERE id = cid;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION surveiller(p_nom TEXT) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    eid INT;
BEGIN
    IF length(trim(coalesce(p_nom, ''))) < 2 THEN RAISE EXCEPTION 'Nom trop court'; END IF;
    eid := entreprise_pour(p_nom);
    INSERT INTO veille_entreprises (entreprise_id, origine) VALUES (eid, 'manuelle')
    ON CONFLICT (entreprise_id) DO UPDATE SET active = TRUE;
    RETURN eid;
END $$;

CREATE OR REPLACE FUNCTION mettre_par_defaut(p_id INT) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    d RECORD;
BEGIN
    SELECT type, langue INTO d FROM documents WHERE id = p_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Document introuvable'; END IF;
    UPDATE documents SET par_defaut = FALSE WHERE type = d.type AND langue = d.langue;
    UPDATE documents SET par_defaut = TRUE WHERE id = p_id;
END $$;
