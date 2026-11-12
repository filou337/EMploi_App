"use strict";
// Couche de données de l'interface.
// - Mode en ligne (config.js renseigné) : lit et écrit directement dans Supabase, protégé par la
//   connexion à ton compte et les règles RLS. Ce qui demande du Python (analyse ATS, matching,
//   dépôt de CV, Excel, passage des agents) est déposé dans `demandes` et exécuté par le PC.
// - Mode local (config.js vide) : appelle l'API Python du PC, comme avant.
// Les vues appellent toujours api("chemin", options) : seule cette couche change.

const CONFIG = window.EMPLOI_CONFIG || {};
const EN_LIGNE = Boolean(CONFIG.supabaseUrl && CONFIG.supabaseAnonKey && window.supabase);
const sb = EN_LIGNE ? window.supabase.createClient(CONFIG.supabaseUrl, CONFIG.supabaseAnonKey, {
  auth: { persistSession: true, autoRefreshToken: true },
}) : null;

class ErreurDonnees extends Error {}

const verifier = ({ data, error }) => {
  if (error) throw new ErreurDonnees(error.message || "Erreur de la base");
  return data;
};

// --- Fichiers binaires (bytea) ------------------------------------------------------------------
const versHex = (tampon) => "\\x" + Array.from(new Uint8Array(tampon), (o) => o.toString(16).padStart(2, "0")).join("");
function depuisHex(texte) {
  const hex = (texte || "").replace(/^\\x/, "");
  const octets = new Uint8Array(hex.length / 2);
  for (let i = 0; i < octets.length; i++) octets[i] = parseInt(hex.substr(i * 2, 2), 16);
  return octets;
}
function enregistrerFichier(octets, nom, type = "application/octet-stream") {
  const url = URL.createObjectURL(new Blob([octets], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: nom });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// --- File des demandes (exécutées par le PC) -------------------------------------------------------
async function deposerDemande(type, parametres = {}, fichier = null) {
  const ligne = { type, parametres };
  if (fichier) { ligne.fichier = versHex(await fichier.arrayBuffer()); ligne.nom_fichier = fichier.name; }
  const [d] = verifier(await sb.from("demandes").insert(ligne).select("id"));
  return d.id;
}

async function attendreDemande(id, { delai = 240000 } = {}) {
  const fin = Date.now() + delai;
  let enAttenteDepuis = Date.now();
  for (;;) {
    const d = verifier(await sb.from("demandes").select("statut, resultat, erreur").eq("id", id).single());
    if (d.statut === "faite") return d.resultat;
    if (d.statut === "erreur") throw new ErreurDonnees(d.erreur || "Erreur sur le PC");
    if (d.statut === "en_cours") enAttenteDepuis = Date.now();
    if (d.statut === "en_attente" && Date.now() - enAttenteDepuis > 25000) {
      throw new ErreurDonnees("PC éteint ou hors ligne : ta demande est enregistrée, il la traitera dès qu'il sera allumé.");
    }
    if (Date.now() > fin) throw new ErreurDonnees("Toujours en cours sur le PC : reviens dans quelques minutes.");
    await new Promise((r) => setTimeout(r, 2500));
  }
}

async function demandeVersFichier(type, parametres = {}) {
  const id = await deposerDemande(type, parametres);
  const r = await attendreDemande(id);
  const d = verifier(await sb.from("demandes").select("fichier_resultat").eq("id", id).single());
  enregistrerFichier(depuisHex(d.fichier_resultat), r.nom || "export.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
  return r;
}

// --- Routes : même chemins que l'API Python -------------------------------------------------------
const corps = (o) => (o.body && typeof o.body === "string" ? JSON.parse(o.body) : {});
const listeStatuts = (s) => (s || "").split(",").filter(Boolean);

const ROUTES = [
  ["GET", /^resume$/, async () => verifier(await sb.rpc("resume"))],
  ["GET", /^spontanees$/, async () => verifier(await sb.from("v_spontanees").select("*")
    .order("cree_le", { ascending: false }).limit(50))],
  ["POST", /^spontanees$/, async (_m, _q, o) => attendreDemande(await deposerDemande("preparer_spontanee", corps(o)))],
  ["PUT", /^spontanees\/(\d+)$/, async (m, _q, o) => {
    const b = corps(o);
    verifier(await sb.from("spontanees").update({ objet: b.objet, corps: b.corps }).eq("id", m[1]).eq("statut", "brouillon"));
    return { ok: true };
  }],
  ["POST", /^spontanees\/(\d+)\/envoyee$/, async (m) => ({ candidature_id: verifier(await sb.rpc("spontanee_envoyee", { p_id: +m[1] })) })],
  ["POST", /^spontanees\/(\d+)\/ignorer$/, async (m) => {
    verifier(await sb.from("spontanees").update({ statut: "ignoree" }).eq("id", m[1]).eq("statut", "brouillon"));
    return { ok: true };
  }],
  ["POST", /^spontanees\/(\d+)\/opposition$/, async (m) => { verifier(await sb.rpc("spontanee_opposition", { p_id: +m[1] })); return { ok: true }; }],
  ["GET", /^rapports$/, async () => verifier(await sb.from("rapports").select("*").order("cree_le", { ascending: false }).limit(10))],
  ["GET", /^journal$/, async () => verifier(await sb.from("v_journal").select("*").order("debut", { ascending: false }).limit(50))],

  ["GET", /^offres$/, async (_m, q) => {
    let r = sb.from("v_offres").select("*").order("recu_le", { ascending: false }).limit(+q.get("limite") || 150);
    const st = listeStatuts(q.get("statut"));
    if (st.length) r = r.in("statut_validite", st);
    if (q.get("source")) r = r.eq("source", q.get("source"));
    if (q.get("data") === "true") r = r.eq("pertinente", true);
    const texte = (q.get("q") || "").replace(/[,()]/g, " ").trim();
    if (texte) r = r.or(`intitule.ilike.*${texte}*,entreprise.ilike.*${texte}*`);
    return verifier(await r);
  }],
  ["GET", /^offres\/(\d+)$/, async (m) => {
    const o = verifier(await sb.from("v_offre_detail").select("*").eq("id", m[1]).single());
    if (o.doublon_de) o.original = { id: o.doublon_de, source: o.original_source, intitule: o.original_intitule };
    return o;
  }],
  ["POST", /^offres\/(\d+)\/fiche$/, async (m, _q, o) => {
    const b = corps(o);
    verifier(await sb.rpc("coller_fiche", { p_offre: +m[1], p_texte: b.texte, p_url: b.url || null }));
    return { ok: true };
  }],
  ["POST", /^offres\/(\d+)\/postule$/, async (m) => ({ candidature_id: verifier(await sb.rpc("j_ai_postule", { p_offre: +m[1] })) })],
  ["POST", /^offres\/(\d+)\/etape$/, async (m, _q, o) => {
    const r = verifier(await sb.rpc("changer_etape", { p_offre: +m[1], p_etape: corps(o).etape || null }));
    return corps(o).etape === "fabrication" ? { ...r, dossier: "en cours de création sur le PC" } : r;
  }],
  ["GET", /^pipeline$/, async () => {
    const rows = verifier(await sb.from("v_offres").select("*").not("etape", "is", null).limit(2000));
    const colonnes = Object.fromEntries(["reperee", "qualifiee", "fabrication", "ats_friendly", "a_envoyer", "envoyee"].map((e) => [e, []]));
    for (const r of rows) {
      if (["reperee", "qualifiee"].includes(r.etape) && !["a_verifier", "valide"].includes(r.statut_validite)) continue;
      colonnes[r.etape].push(r);
    }
    const t = (r) => new Date(r.etape_maj_le || r.recu_le).getTime();
    return Object.fromEntries(Object.entries(colonnes).map(([e, l]) => {
      l.sort((a, b) => (b.score_matching ?? -1) - (a.score_matching ?? -1) || (b.fiche_complete - a.fiche_complete) || t(b) - t(a));
      return [e, { total: l.length, offres: l.slice(0, 60) }];
    }));
  }],

  ["GET", /^candidatures$/, async (_m, q) => {
    let r = sb.from("v_candidatures").select("*").order("postule_le", { ascending: false, nullsFirst: false }).order("id", { ascending: false });
    if (q.get("statut")) r = r.eq("statut", q.get("statut"));
    if (q.get("type")) r = r.eq("type", q.get("type"));
    const texte = (q.get("q") || "").replace(/[,()]/g, " ").trim();
    if (texte) r = r.or(`entreprise.ilike.*${texte}*,poste.ilike.*${texte}*`);
    return verifier(await r);
  }],
  ["POST", /^candidatures$/, async (_m, _q, o) => ({ id: verifier(await sb.rpc("ajouter_candidature", { p: corps(o) })) })],
  ["PATCH", /^candidatures\/(\d+)$/, async (m, _q, o) => {
    const b = corps(o), maj = { maj_le: new Date().toISOString() };
    if (b.statut) maj.statut = b.statut;
    if (b.notes !== undefined && b.notes !== null) maj.notes = b.notes;
    verifier(await sb.from("candidatures").update(maj).eq("id", m[1]));
    return { ok: true };
  }],
  ["POST", /^candidatures\/import$/, async (_m, _q, o) => {
    const fichier = o.body.get("fichier");
    return attendreDemande(await deposerDemande("importer_candidatures", {}, fichier));
  }],
  ["GET", /^candidatures\/export\.xlsx$/, async () => demandeVersFichier("exporter_excel")],

  ["GET", /^reponses$/, async (_m, q) => {
    let r = sb.from("v_reponses").select("*").order("recu_le", { ascending: false }).limit(200);
    if (q.get("a_confirmer") === "true") r = r.eq("a_confirmer", true).is("confirme_le", null);
    return verifier(await r);
  }],
  ["POST", /^reponses\/(\d+)\/confirmer$/, async (m, _q, o) => {
    verifier(await sb.rpc("confirmer_reponse", { p_id: +m[1], p_classement: corps(o).classement }));
    return { ok: true };
  }],

  ["GET", /^documents$/, async () => verifier(await sb.from("v_documents").select("*").order("type").order("depose_le", { ascending: false }))],
  ["POST", /^documents$/, async (_m, _q, o) => {
    const id = await deposerDemande("deposer_document", { type: o.body.get("type") }, o.body.get("fichier"));
    return attendreDemande(id, { delai: 120000 });
  }],
  ["GET", /^documents\/(\d+)\/texte$/, async (m) => verifier(await sb.from("documents").select("texte").eq("id", m[1]).single())],
  ["GET", /^documents\/(\d+)\/fichier$/, async (m) => {
    const d = verifier(await sb.from("documents").select("contenu, nom_fichier, format").eq("id", m[1]).single());
    enregistrerFichier(depuisHex(d.contenu), d.nom_fichier,
      d.format === "pdf" ? "application/pdf" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    return { ok: true };
  }],
  ["POST", /^documents\/(\d+)\/defaut$/, async (m) => { verifier(await sb.rpc("mettre_par_defaut", { p_id: +m[1] })); return { ok: true }; }],

  ["GET", /^ats\/cv$/, async () => verifier(await sb.from("v_analyses").select("*").eq("type", "cv").eq("par_defaut", true)
    .order("cree_le", { ascending: false }).limit(1).maybeSingle())],
  ["POST", /^ats\/cv$/, async (_m, q) => attendreDemande(await deposerDemande("analyser_cv", { forcer: q.get("forcer") === "true" }))],
  ["GET", /^ats\/matching\/(\d+)$/, async (m) => verifier(await sb.from("v_analyses").select("*").eq("type", "matching")
    .eq("offre_id", m[1]).eq("par_defaut", true).order("cree_le", { ascending: false }).limit(1).maybeSingle())],
  ["POST", /^ats\/matching\/(\d+)$/, async (m, q) => attendreDemande(await deposerDemande("matching", { offre_id: +m[1], forcer: q.get("forcer") === "true" }))],

  ["GET", /^relances$/, async () => verifier(await sb.from("v_relances").select("*").order("due_le"))],
  ["POST", /^candidatures\/(\d+)\/relance$/, async (m) => ({ numero: verifier(await sb.rpc("marquer_relance", { p_id: +m[1] })) })],
  ["POST", /^candidatures\/(\d+)\/ignorer-relance$/, async (m) => { verifier(await sb.rpc("ignorer_relance", { p_id: +m[1] })); return { ok: true }; }],
  ["GET", /^fabrications$/, async (_m, q) => verifier(await sb.from("v_fabrications").select("*")
    .eq("offre_id", q.get("offre_id")).order("cree_le", { ascending: false }).limit(10))],
  ["POST", /^offres\/(\d+)\/fabriquer$/, async (m, q) => {
    const o = verifier(await sb.from("offres").select("etape").eq("id", m[1]).single());
    if (!o.etape || ["reperee", "qualifiee"].includes(o.etape)) {
      verifier(await sb.from("offres").update({ etape: "fabrication", etape_maj_le: new Date().toISOString() }).eq("id", m[1]));
    }
    const id = await deposerDemande("fabriquer", { offre_id: +m[1], langue: q.get("langue") || null });
    return attendreDemande(id, { delai: 420000 });
  }],
  ["POST", /^fabrications\/(\d+)\/valider$/, async (m) => { verifier(await sb.rpc("valider_fabrication", { p_id: +m[1] })); return { ok: true }; }],
  ["GET", /^fichiers\/(\d+)$/, async (m) => {
    const f = verifier(await sb.from("dossier_fichiers").select("contenu, nom, format").eq("id", m[1]).single());
    enregistrerFichier(depuisHex(f.contenu), f.nom, f.format === "pdf" ? "application/pdf"
      : "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    return { ok: true };
  }],
  ["GET", /^veille\/entreprises$/, async () => verifier(await sb.from("v_veille_entreprises").select("*").order("active", { ascending: false }).order("nom_affiche"))],
  ["POST", /^veille\/entreprises$/, async (_m, _q, o) => ({ entreprise_id: verifier(await sb.rpc("surveiller", { p_nom: corps(o).nom })) })],
  ["PATCH", /^veille\/entreprises\/(\d+)$/, async (m, _q, o) => {
    verifier(await sb.from("veille_entreprises").update({ active: corps(o).active }).eq("id", m[1]));
    return { ok: true };
  }],
  ["DELETE", /^veille\/entreprises\/(\d+)$/, async (m) => { verifier(await sb.from("veille_entreprises").delete().eq("id", m[1])); return { ok: true }; }],
  ["GET", /^veille\/suggestions$/, async (_m, q) => {
    const t = (q.get("q") || "").trim();
    if (t.length < 2) return [];
    return verifier(await sb.from("entreprises").select("nom_affiche").ilike("nom_affiche", `%${t}%`).limit(10)).map((r) => r.nom_affiche);
  }],
  ["GET", /^parametres$/, async () => Object.fromEntries(verifier(await sb.from("parametres").select("cle, valeur")).map((r) => [r.cle, r.valeur]))],
  ["PUT", /^parametres$/, async (_m, _q, o) => {
    const b = corps(o);
    const actuels = Object.fromEntries(verifier(await sb.from("parametres").select("cle, valeur")).map((r) => [r.cle, r.valeur]));
    const lignes = Object.entries(b).filter(([, v]) => v).map(([cle, v]) => ({ cle, valeur: { ...(actuels[cle] || {}), ...v }, maj_le: new Date().toISOString() }));
    verifier(await sb.from("parametres").upsert(lignes));
    return { ...actuels, ...Object.fromEntries(lignes.map((l) => [l.cle, l.valeur])) };
  }],
  ["POST", /^veille\/lancer$/, async (_m, q) => {
    const deja = verifier(await sb.from("demandes").select("id").eq("type", "lancer_veille").in("statut", ["en_attente", "en_cours"]).limit(1));
    if (deja.length) return { deja_en_cours: true };
    await deposerDemande("lancer_veille", { gmail: q.get("gmail") !== "false" });
    return { lance: true };
  }],
  ["GET", /^veille\/etat$/, async () => {
    const d = verifier(await sb.from("demandes").select("statut, resultat, cree_le").eq("type", "lancer_veille")
      .order("cree_le", { ascending: false }).limit(1).maybeSingle());
    return { en_cours: Boolean(d && ["en_attente", "en_cours"].includes(d.statut)), bilan: d && d.resultat, debut: d && d.cree_le };
  }],

  ["GET", /^notifications$/, async () => verifier(await sb.from("notifications").select("*").order("cree_le", { ascending: false }).limit(40))],
  ["POST", /^notifications\/lues$/, async () => { verifier(await sb.from("notifications").update({ lue: true }).eq("lue", false)); return { ok: true }; }],
  ["GET", /^acces$/, async () => ({ en_ligne: true, url: CONFIG.urlPublique || location.href.split("#")[0] })],
];

async function apiSupabase(chemin, options = {}) {
  const [route, requete = ""] = chemin.split("?");
  const methode = (options.method || "GET").toUpperCase();
  for (const [m, motif, gestion] of ROUTES) {
    const r = m === methode && route.match(motif);
    if (r) return gestion(r, new URLSearchParams(requete), options);
  }
  throw new ErreurDonnees(`Fonction indisponible en ligne : ${methode} ${route}`);
}

async function apiLocal(chemin, options = {}) {
  const r = await fetch(`/api/${chemin}`, options);
  if (!r.ok) {
    let msg = `Erreur ${r.status}`;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg ?? msg); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

// Téléchargements : lien direct en local, fichier reconstruit en ligne.
async function telecharger(chemin) {
  if (EN_LIGNE) return apiSupabase(chemin);
  location.href = `/api/${chemin}`;
  return null;
}

// --- Connexion ----------------------------------------------------------------------------------
async function session() {
  if (!EN_LIGNE) return { locale: true };
  return verifier(await sb.auth.getSession()).session;
}
async function seConnecter(email, motDePasse) {
  verifier(await sb.auth.signInWithPassword({ email, password: motDePasse }));
}
async function seDeconnecter() { if (EN_LIGNE) await sb.auth.signOut(); }

window.Donnees = { EN_LIGNE, apiSupabase, apiLocal, telecharger, session, seConnecter, seDeconnecter };
