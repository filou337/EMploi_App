"use strict";
// Interface de l'appli locale. Toute donnée venue d'un mail ou d'une offre est échappée
// (esc) avant d'être insérée : un texte d'alerte ne doit jamais devenir du HTML.

const LIBELLES = {
  a_postuler: "À postuler", postule: "Postulé", relance: "Relancé", reponse_positive: "Réponse positive",
  refus: "Refus", sans_suite: "Sans suite", ignore: "Non remise",
  a_verifier: "À vérifier", valide: "Valide", expiree: "Expirée", pourvue: "Pourvue",
  ancienne: "Ancienne", doublon: "Doublon",
};
const COULEURS = {
  valide: "vert", a_verifier: "ambre", expiree: "rouge", pourvue: "rouge", ancienne: "", doublon: "",
  postule: "sarcelle", relance: "sarcelle", a_postuler: "ambre", reponse_positive: "vert",
  refus: "rouge", sans_suite: "", ignore: "",
};
const AGENTS = {
  collecte: { nom: "Agent Recherche", role: "lit les alertes Gmail et extrait les offres", c: "#087f8c" },
  validite: { nom: "Agent Validité", role: "repère les doublons et les offres trop anciennes", c: "#173448" },
  fiches: { nom: "Agent Fiches", role: "cherche la fiche complète chez l'employeur", c: "#9a641d" },
  suivi: { nom: "Agent Suivi", role: "relit les envois, accusés et réponses", c: "#2d7a4f" },
  veille: { nom: "Agent Veille", role: "surveille le site carrières de tes entreprises cibles", c: "#5b4b8a" },
  candidatures_auto: { nom: "Agent Candidatures", role: "prépare CV + LM + mail, envoie sous le seuil, met de côté au-dessus", c: "#2f5d8a" },
  spontanees: { nom: "Agent Spontanées", role: "prépare des mails de candidature spontanée (envoyés par toi)", c: "#8a4b6b" },
  rapport: { nom: "Agent Rapport", role: "fait le point tous les 3 jours", c: "#4a6b2f" },
  matching: { nom: "Agent Matching", role: "compare ton CV aux offres et qualifie les meilleures", c: "#a3352b" },
};
const ETAPES = [
  ["reperee", "Repérée", "Dans tes critères. Fiche complète = matching automatique."],
  ["qualifiee", "Qualifiée", "Matching ≥ seuil : dossier à préparer."],
  ["fabrication", "En fabrication", "CV et LM adaptés en cours (ou à revoir) : voir le détail."],
  ["ats_friendly", "ATS friendly", "CV adapté validé par le checker."],
  ["a_envoyer", "À envoyer", "Tout est prêt : à toi de cliquer sur « Postuler »."],
  ["envoyee", "Envoyée", "Candidature au suivi, en attente de réponse."],
];

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const lienSur = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : null);
const jour = (d) => (d ? new Date(d).toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const depuis = (d) => {
  if (!d) return "—";
  const h = (Date.now() - new Date(d)) / 36e5;
  if (h < 1) return "à l'instant";
  if (h < 24) return `il y a ${Math.round(h)} h`;
  return `il y a ${Math.round(h / 24)} j`;
};
const etiquette = (s) => `<span class="etiquette ${COULEURS[s] ?? ""}">${esc(LIBELLES[s] ?? s)}</span>`;

// Données : Supabase en ligne, API Python du PC en local (voir donnees.js).
const api = (chemin, options = {}) => (Donnees.EN_LIGNE ? Donnees.apiSupabase(chemin, options) : Donnees.apiLocal(chemin, options));
const envoyer = (chemin, methode, corps) => api(chemin, {
  method: methode, headers: { "Content-Type": "application/json" }, body: JSON.stringify(corps ?? {}),
});

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.minuterie);
  toast.minuterie = setTimeout(() => { t.hidden = true; }, 3200);
}

function ouvrir(titre, html) {
  $("#fenetre-titre").textContent = titre;
  $("#fenetre-corps").innerHTML = html;
  $("#fenetre").showModal();
  return $("#fenetre-corps");
}
const fermer = () => $("#fenetre").close();

// --- Tableau de bord -------------------------------------------------------------------------
async function vueTableau(v) {
  const [r, journal, relances] = await Promise.all([api("resume"), api("journal"), api("relances").catch(() => [])]);
  const c = r.candidatures, o = r.offres;
  const cv = r.documents.find((d) => d.type === "cv");
  const semaines = r.semaines;
  const max = Math.max(1, ...semaines.map((s) => s.n));
  v.innerHTML = `
    ${etatPC(r.pc)}
    <div class="entete"><div><h1>Ta recherche, en un coup d'œil.</h1>
      <p>Données réelles de ta boîte Gmail et des sites carrières, mises à jour à chaque passage des agents.</p></div>
      <div class="actions"><button class="bouton" data-telecharger="candidatures/export.xlsx">Exporter en Excel</button>
      <a class="bouton principal" href="#offres">Voir les opportunités →</a></div></div>
    <div class="grille">
      ${kpi(c.total, "candidatures", `${c.par_statut.ignore ?? 0} non remises exclues`)}
      ${kpi(c.en_cours, "en cours", "postulées, sans réponse")}
      ${kpi(c.entretiens, "entretiens / réponses positives", "")}
      ${kpi(`${c.taux_reponse.toLocaleString("fr-FR")} %`, "taux de réponse", `${c.refus} refus`)}
      ${kpi(o.data_ouvertes, "offres data ouvertes", `${o.par_statut.valide ?? 0} vérifiées chez l'employeur`)}
      ${kpi(relances.length, "relances à faire", relances.length ? '<a href="#candidatures">Voir les textes →</a>' : "aucune due")}
      ${kpi(r.reponses_a_confirmer, "réponses à confirmer", r.reponses_a_confirmer ? '<a href="#reponses">Les confirmer →</a>' : "rien en attente")}
    </div>
    <div class="grille-2 section">
      <div class="carte"><h3>Candidatures par semaine (12 dernières)</h3>
        ${semaines.length ? `<div class="histo">${semaines.map((s) => {
          const offre = s.n - s.spontanees;
          return `<div class="barre-h" title="${s.n} candidatures">
            <span class="nb">${s.n}</span>
            <div class="pile" style="height:${(s.n / max) * 120}px">
              <span class="offre" style="height:${(offre / s.n) * 100}%"></span>
              <span class="spont" style="height:${(s.spontanees / s.n) * 100}%"></span></div>
            <span class="lib">${new Date(s.semaine).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })}</span></div>`;
        }).join("")}</div>
        <div class="legende"><span><i style="background:var(--teal)"></i>sur offre</span>
          <span><i style="background:#8fc9cd"></i>spontanées</span></div>` : '<p class="vide">Aucune candidature sur la période.</p>'}
      </div>
      <div class="carte"><h3>Indicateurs CV</h3>${r.ats.score_ats != null ? `<div class="jauges">
          ${jauge("ATS", r.ats.score_ats)}${jauge("Qualité", r.ats.score_qualite)}${jauge("Matching moyen", r.ats.matching_moyen, `${r.ats.matchings} offre(s) analysée(s) sur 30 j`)}</div>
          ${r.ats.historique.length > 1 ? `<p class="note">Évolution : ${r.ats.historique.slice().reverse().map((h) => `${Math.round(h.score_ats)}/${Math.round(h.score_qualite)}`).join(" → ")} (ATS/qualité)</p>` : ""}
          <a href="#documents">Voir le rapport complet →</a>` : cv ? `${blocDiagnostic(cv)}<a class="bouton principal" href="#documents">Lancer l'ATS checker</a>` : `
        <p class="note">Aucun CV déposé pour l'instant. Dépose ton CV et ta lettre par défaut : l'outil les adaptera à chaque offre.</p>
        <a class="bouton principal" href="#documents">Déposer mon CV</a>`}</div>
    </div>
    <div class="carte section"><h3>Activité récente des agents</h3>${filJournal(journal.slice(0, 5))}
      <a href="#salon">Tout le salon →</a></div>`;
}
function etatPC(pc) {
  if (!Donnees.EN_LIGNE) return "";
  const vu = pc && pc.vu_le ? new Date(pc.vu_le) : null;
  const allume = vu && Date.now() - vu < 3 * 60000;
  return `<p class="etat-pc ${allume ? "ok" : ""}">${allume ? "● PC allumé : les analyses sont faites tout de suite."
    : `○ PC éteint${vu ? ` (vu ${depuis(vu)})` : ""} : les analyses attendront qu'il se rallume.`}</p>`;
}
const kpi = (valeur, libelle, detail) => `<div class="carte kpi"><div class="valeur">${esc(valeur)}</div>
  <div class="libelle">${esc(libelle)}</div>${detail ? `<div class="detail">${detail}</div>` : ""}</div>`;

function blocDiagnostic(doc) {
  const d = doc.diagnostic || {};
  const alertes = d.alertes || [];
  return `<p class="note">${esc(doc.nom_fichier)} · ${d.pages ? `${d.pages} page(s) · ` : ""}${d.mots ?? 0} mots lisibles</p>
    <p><strong>Sections reconnues :</strong> ${(d.sections || []).map((s) => `<span class="etiquette sarcelle">${esc(s)}</span>`).join(" ") || "aucune"}</p>
    ${alertes.length ? `<ul class="liste alertes">${alertes.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`
      : '<p class="ok">Aucun problème de lisibilité détecté.</p>'}
    `;
}

// --- Moteur ATS : rendu des rapports -----------------------------------------------------------
const teinte = (n) => (n == null ? "" : n >= 80 ? "vert" : n >= 60 ? "ambre" : "rouge");
const jauge = (titre, n, sous = "") => `<div class="jauge ${teinte(n)}"><div class="jauge-valeur">${n == null ? "—" : Math.round(n)}<small>/100</small></div>
  <div class="jauge-titre">${esc(titre)}</div>${sous ? `<div class="note">${sous}</div>` : ""}</div>`;
const NOMS_QUALITE = { clarte: "Clarté", pertinence_experiences: "Pertinence des expériences",
  competences_contextualisees: "Compétences en contexte", resultats_quantifies: "Résultats chiffrés",
  absence_repetitions: "Pas de répétitions", coherence: "Cohérence du parcours", completude: "Complétude" };

function rapportCV(a) {
  const r = a.resultat, cv = r.cv || {};
  return `<div class="jauges">${jauge("Compatibilité ATS", a.score_ats, "calculée sur ton PC")}
      ${jauge("Qualité du CV", a.score_qualite, `par ${esc(a.modele || "Gemini")}`)}</div>
    <p class="note">Analyse du ${jour(a.cree_le)} · ${esc(a.document || "")} · coordonnées masquées avant envoi.
      ${a.depuis_cache ? "<br>✔ Même fichier, même barème : résultat identique à la dernière analyse (pas de nouvel appel)." : ""}
      ${r.fiabilite && r.fiabilite.essais.length > 1 ? `<br>Stabilité : ${r.fiabilite.essais.join(" / ")} sur ${r.fiabilite.essais.length} essais (écart ${r.fiabilite.ecart}).` : ""}
      Le score ATS est calculé sur ton PC : il ne change que si le fichier change.</p>
    <div class="grille-2 section"><div><h3>Points forts</h3><ul class="liste">${(r.points_forts || []).map((x) => `<li class="ok">${esc(x)}</li>`).join("")}</ul></div>
      <div><h3>À améliorer</h3><ul class="liste alertes">${(r.points_faibles || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>
    <h3 class="section">Compatibilité ATS, critère par critère</h3>
    <table><tbody>${r.compatibilite.criteres.map((c) => `<tr><td><strong>${esc(c.nom)}</strong><div class="sous">${esc(c.detail)}</div></td>
      <td class="num"><span class="etiquette ${teinte((100 * c.note) / c.max)}">${c.note} / ${c.max}</span></td></tr>`).join("")}</tbody></table>
    <h3 class="section">Qualité, critère par critère</h3>
    <table><tbody>${Object.entries(r.qualite || {}).map(([k, v]) => `<tr><td><strong>${esc(NOMS_QUALITE[k] || k)}</strong>
      <div class="sous">${esc(v.justification)}</div></td><td class="num"><span class="etiquette ${teinte(v.note * 10)}">${v.note} / 10</span></td></tr>`).join("")}</tbody></table>
    <details class="section"><summary><strong>Ce que le moteur a compris de ton CV</strong> (CV structuré)</summary>
      <p><strong>${esc(cv.titre)}</strong> — ${esc(cv.resume)}</p>
      <ul class="liste">${(cv.experiences || []).map((e) => `<li><span><strong>${esc(e.poste)}</strong> · ${esc(e.entreprise)}
        <div class="sous">${esc(e.debut)} – ${esc(e.fin)} · ${(e.competences || []).map(esc).join(", ")}</div></span></li>`).join("")}</ul>
      <p><strong>Compétences :</strong> ${(cv.competences_techniques || []).map((x) => `<span class="etiquette sarcelle">${esc(x)}</span>`).join(" ")}</p>
      <p><strong>Langues :</strong> ${(cv.langues || []).map((l) => `${esc(l.langue)} (${esc(l.niveau)})`).join(", ") || "—"}</p></details>`;
}

const STATUTS_MATCH = { exacte: ["vert", "retrouvée"], synonyme: ["vert", "synonyme"],
  semantique: ["sarcelle", "démontrée"], absente: ["rouge", "absente"] };

function rapportMatching(a) {
  const r = a.resultat, d = r.detail_score || {};
  const lignes = (r.competences || []).map((c) => {
    const [coul, lib] = STATUTS_MATCH[c.statut] || ["", c.statut];
    const retro = c.statut_modele ? `<div class="erreur">preuve introuvable dans ton CV : « ${esc(c.statut_modele)} » refusé</div>` : "";
    return `<tr><td><strong>${esc(c.nom)}</strong><div class="sous">${c.type === "soft" ? "soft skill" : "technique"}${c.obligatoire ? " · obligatoire" : ""}</div></td>
      <td><span class="etiquette ${coul}">${lib}</span></td>
      <td>${c.statut !== "absente" ? `« ${esc(c.preuve)} »` : '<span class="note">—</span>'}${retro}</td></tr>`;
  }).join("");
  return `${r.avertissement ? `<p class="etiquette ambre">${esc(r.avertissement)}</p>` : ""}
    <div class="jauges">${jauge("Matching avec ton CV", a.score_matching, `par ${esc(a.modele || "Gemini")} · ${jour(a.cree_le)}`)}
      <div class="carte detail-score">${[["Compétences techniques", "competences_techniques", 50], ["Expérience", "experience", 20],
        ["Formation", "formation", 10], ["Soft skills", "soft_skills", 10], ["Critères obligatoires", "criteres_obligatoires", 10]]
        .map(([l, k, m]) => `<div><span>${l}</span><b>${d[k] ?? 0} / ${m}</b></div>`).join("")}</div></div>
    <p>${esc(r.synthese)}</p>
    ${r.mots_cles_manquants?.length ? `<p><strong>Absents de ton CV :</strong> ${r.mots_cles_manquants.map((x) => `<span class="etiquette rouge">${esc(x)}</span>`).join(" ")}</p>` : ""}
    <table><thead><tr><th>Compétence demandée</th><th>Dans ton CV</th><th>Preuve (citation vérifiée)</th></tr></thead><tbody>${lignes}</tbody></table>
    <p class="note">Le score est calculé par l'appli à partir de ces statuts. Toute preuve qui n'existe pas mot pour mot dans ton CV est refusée.</p>`;
}

// --- Opportunités ----------------------------------------------------------------------------
const filtreOffres = { statut: "a_verifier,valide", q: "", source: "", data: true };

async function vueOffres(v) {
  v.innerHTML = `<div class="entete"><div><h1>Tes opportunités.</h1>
    <p>Offres reçues par alerte, dédoublonnées. « Valide » = retrouvée chez l'employeur, avec la fiche complète.
    Les autres restent à vérifier : colle la fiche pour lever le plafond de score à 84.</p></div></div>
    <div class="filtres" id="f-offres">
      ${[["a_verifier,valide", "Ouvertes"], ["valide", "Vérifiées"], ["a_verifier", "À vérifier"],
         ["expiree,ancienne", "Expirées / anciennes"], ["doublon", "Doublons"], ["", "Toutes"]]
        .map(([s, l]) => `<button class="puce ${filtreOffres.statut === s ? "actif" : ""}" data-statut="${s}">${l}</button>`).join("")}
      <select class="champ" id="f-source"><option value="">Toutes sources</option>
        <option value="linkedin">LinkedIn</option><option value="indeed">Indeed</option></select>
      <input class="champ" type="search" id="f-q" placeholder="Entreprise ou intitulé…" value="${esc(filtreOffres.q)}">
      <label class="case"><input type="checkbox" id="f-data" ${filtreOffres.data ? "checked" : ""}> postes data seulement</label>
      <span class="compte" id="compte-offres"></span>
    </div><div id="liste-offres" class="chargement">Chargement…</div>`;
  $("#f-source").value = filtreOffres.source;
  $("#f-offres").addEventListener("click", (e) => {
    const b = e.target.closest("[data-statut]");
    if (!b) return;
    filtreOffres.statut = b.dataset.statut;
    v.querySelectorAll("[data-statut]").forEach((x) => x.classList.toggle("actif", x === b));
    chargerOffres();
  });
  $("#f-source").addEventListener("change", (e) => { filtreOffres.source = e.target.value; chargerOffres(); });
  $("#f-data").addEventListener("change", (e) => { filtreOffres.data = e.target.checked; chargerOffres(); });
  let attente;
  $("#f-q").addEventListener("input", (e) => {
    clearTimeout(attente);
    attente = setTimeout(() => { filtreOffres.q = e.target.value.trim(); chargerOffres(); }, 250);
  });
  await chargerOffres();
}

async function chargerOffres() {
  const zone = $("#liste-offres");
  const p = new URLSearchParams({ statut: filtreOffres.statut, q: filtreOffres.q, source: filtreOffres.source,
    data: filtreOffres.data, limite: 300 });
  const rows = await api(`offres?${p}`);
  $("#compte-offres").textContent = `${rows.length} offre(s)${rows.length === 300 ? " (300 premières)" : ""}`;
  zone.className = "";
  if (!rows.length) { zone.innerHTML = '<div class="carte vide">Aucune offre pour ces filtres.</div>'; return; }
  zone.innerHTML = `<div class="table-defile"><table><thead><tr><th>Offre</th><th>Lieu</th><th>Source</th>
    <th>Statut</th><th>Reçue</th><th></th></tr></thead><tbody>${rows.map((o) => {
      const lien = lienSur(o.lien);
      const recente = o.candidature_recente ? `<span class="etiquette ambre" title="Règle des 3 mois">déjà postulé le ${jour(o.candidature_recente)}</span>` : "";
      return `<tr data-id="${o.id}">
        <td><strong>${esc(o.intitule)}</strong><div class="sous">${esc(o.entreprise)}${o.contrat ? ` · ${esc(o.contrat)}` : ""}</div>
          <div>${o.fiche_complete ? '<span class="etiquette vert">fiche complète</span> ' : ""}${recente}
          ${o.candidature ? etiquette(o.candidature) : ""}
          ${o.score_matching != null ? `<span class="etiquette ${teinte(o.score_matching)}">matching ${Math.round(o.score_matching)}/100</span>` : ""}</div></td>
        <td>${esc(o.ville || "—")}</td><td><span class="etiquette">${esc(o.source)}</span></td>
        <td>${etiquette(o.statut_validite)}</td>
        <td class="num">${depuis(o.recu_le)}${o.date_publication ? `<div class="sous">publiée ${jour(o.date_publication)}</div>` : ""}</td>
        <td class="num"><div class="actions">
          <button class="bouton petit" data-action="detail">Détail</button>
          ${lien ? `<a class="bouton petit" href="${lien}" target="_blank" rel="noopener noreferrer">Ouvrir ↗</a>` : ""}
          ${o.fiche_complete ? "" : '<button class="bouton petit" data-action="fiche">Coller la fiche</button>'}
          ${o.candidature ? "" : '<button class="bouton petit principal" data-action="postule">J\'ai postulé</button>'}
        </div></td></tr>`;
    }).join("")}</tbody></table></div>`;
  zone.onclick = (e) => {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    const id = b.closest("tr").dataset.id;
    ({ detail: detailOffre, fiche: collerFiche, postule: postuler })[b.dataset.action](id, b);
  };
}

async function detailOffre(id) {
  const o = await api(`offres/${id}`);
  const lien = lienSur(o.url_fiche) || lienSur(o.url_cle);
  ouvrir(o.intitule || "Offre", `
    <p><strong>${esc(o.entreprise)}</strong> · ${esc(o.ville || "lieu inconnu")} · ${etiquette(o.statut_validite)}
      ${o.fiche_complete ? '<span class="etiquette vert">fiche complète</span>' : '<span class="etiquette ambre">extrait seulement (score plafonné à 84)</span>'}</p>
    <p class="note">Source : ${esc(o.source)} · reçue ${jour(o.recu_le)}${o.date_publication ? ` · publiée ${jour(o.date_publication)}` : ""}
      ${o.ats ? ` · site carrières : ${esc(o.ats)}${o.ats_slug ? `:${esc(o.ats_slug)}` : ""}` : ""}</p>
    ${o.note_validite ? `<p class="note">Contrôle : ${esc(o.note_validite)}</p>` : ""}
    ${o.original ? `<p class="note">Doublon de l'offre n°${o.original.id} (${esc(o.original.source)}) : ${esc(o.original.intitule)}</p>` : ""}
    ${lien ? `<p><a class="bouton" href="${lien}" target="_blank" rel="noopener noreferrer">Ouvrir l'offre ↗</a></p>` : ""}
    <div class="carte section" id="zone-matching"><p class="chargement">Matching…</p></div>
    <div class="carte section" id="zone-dossier"><p class="chargement">Dossier…</p></div>
    <h3 class="section">${o.texte ? "Fiche complète" : "Extrait de l'alerte"}</h3>
    <div class="texte-ats">${esc(o.texte || o.extrait || "—")}</div>`);
  afficherMatching(id, await api(`ats/matching/${id}`));
  afficherDossier(id, await api(`fabrications?offre_id=${id}`));
}

const STATUTS_FAB = { en_cours: ["ambre", "en cours de fabrication sur le PC…"], ats_friendly: ["vert", "ATS friendly"],
  a_revoir: ["ambre", "à revoir"], erreur: ["rouge", "erreur"], validee: ["vert", "validé : à envoyer"] };

function rapportDossier(f) {
  const [coul, lib] = STATUTS_FAB[f.statut] || ["", f.statut];
  const c = f.contenu || {}, cv = c.cv || {}, ctl = f.controle || {}, ver = f.verification || {};
  const retires = [...((ver.cv || {}).retires || []), ...((ver.lm || {}).retires || [])];
  const fichiers = (f.fichiers || []).map((x) => `<button class="bouton petit" data-telecharger="fichiers/${x.id}">
    ${x.type === "cv" ? "CV" : "Lettre"} ${x.format.toUpperCase()}</button>`).join(" ");
  const delta = f.score_matching != null && f.score_matching_origine != null ? f.score_matching - f.score_matching_origine : null;
  return `<p><span class="etiquette ${coul}">${esc(lib)}</span> <span class="etiquette">${f.langue === "en" ? "anglais" : "français"}</span>
      <span class="note">${jour(f.cree_le)}${f.modele ? ` · ${esc(f.modele)}` : ""}</span></p>
    ${f.statut === "erreur" ? `<p class="erreur">${esc(f.erreur || "")}</p>` : ""}
    ${f.score_ats != null ? `<div class="jauges">${jauge("ATS du CV adapté", f.score_ats, "relu comme un ATS")}
      ${jauge("Matching", f.score_matching, delta != null ? `${delta >= 0 ? "+" : ""}${delta.toFixed(1)} vs ton CV d'origine (${f.score_matching_origine})` : "")}</div>` : ""}
    ${(ctl.motifs || []).length ? `<ul class="liste alertes">${ctl.motifs.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
    ${retires.length ? `<details class="section"><summary><strong>${retires.length} élément(s) retiré(s) par le garde-fou</strong></summary>
      <ul class="liste">${retires.map((r) => `<li><span>${esc(r.texte || "")}<div class="sous">${esc(r.ou)} : ${esc(r.motif ||
        [...(r.termes_inventes || []), ...(r.nombres_inventes || [])].join(", ") + " absent(s) de tes documents")}</div></span></li>`).join("")}</ul></details>` : ""}
    ${fichiers ? `<p class="actions section">${fichiers}</p>` : ""}
    ${cv.experiences ? `<details class="section"><summary><strong>Ce qui a changé par rapport à ton CV</strong></summary>
      <p><strong>Titre :</strong> ${esc(cv.titre)}</p><p><strong>Accroche :</strong> ${esc(cv.accroche)}</p>
      <p><strong>Compétences :</strong> ${(cv.competences || []).map((k) => `${esc(k.categorie)} : ${k.elements.map(esc).join(", ")}`).join(" · ")}</p>
      ${cv.experiences.map((e) => `<h3 class="section">${esc(e.poste)} — ${esc(e.entreprise)}</h3>
        <div class="grille-2"><div><p class="note">Ton CV</p><ul>${(e.puces_origine || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>
        <div><p class="note">CV adapté</p><ul>${(e.puces || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div></div>`).join("")}
      </details>` : ""}`;
}

function afficherDossier(id, fabs) {
  const zone = $("#zone-dossier");
  if (!zone) return;
  const derniere = fabs[0];
  const autre = derniere ? (derniere.langue === "fr" ? "en" : "fr") : null;
  zone.innerHTML = `<div class="entete" style="margin-bottom:12px"><h3>Dossier de candidature (CV + lettre)</h3>
    <div class="actions">${derniere ? `<button class="bouton petit" data-fab="${autre}">Version ${autre === "en" ? "anglaise" : "française"}</button>
      <button class="bouton petit" data-fab="${derniere.langue}">Refaire</button>` : ""}
      ${derniere && ["ats_friendly", "a_revoir"].includes(derniere.statut) ? `<button class="bouton petit principal" data-valider="${derniere.id}">Valider → À envoyer</button>` : ""}
      ${derniere ? "" : '<button class="bouton principal" data-fab="">Préparer le dossier</button>'}</div></div>
    ${derniere ? rapportDossier(derniere) : '<p class="note">CV et lettre adaptés à cette offre, dans sa langue, en PDF et DOCX (2 à 4 min). Aucune compétence ni aucun chiffre ne peut être inventé : le code le vérifie. Tes coordonnées ne sont jamais envoyées au LLM.</p>'}
    ${fabs.length > 1 ? `<p class="note">${fabs.length - 1} version(s) précédente(s) conservée(s).</p>` : ""}`;
  zone.onclick = async (e) => {
    const f = e.target.closest("[data-fab]"), v = e.target.closest("[data-valider]");
    if (v) {
      try { await envoyer(`fabrications/${v.dataset.valider}/valider`, "POST"); toast("Dossier validé : il passe « À envoyer »."); afficherDossier(id, await api(`fabrications?offre_id=${id}`)); }
      catch (err) { toast(err.message); }
      return;
    }
    if (!f) return;
    zone.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    zone.insertAdjacentHTML("afterbegin", '<p class="etiquette ambre">⏳ Fabrication en cours (2 à 4 min) : tu peux fermer cette fenêtre, tu seras notifié.</p>');
    try {
      await api(`offres/${id}/fabriquer${f.dataset.fab ? `?langue=${f.dataset.fab}` : ""}`, { method: "POST" });
      toast("Dossier fabriqué.");
    } catch (err) { toast(err.message); }
    afficherDossier(id, await api(`fabrications?offre_id=${id}`));
    if ($("#liste-offres")) chargerOffres();
  };
}

function afficherMatching(id, analyse) {
  const zone = $("#zone-matching");
  if (!zone) return;
  zone.innerHTML = `<div class="entete" style="margin-bottom:12px"><h3>ATS checker : ton CV face à cette offre</h3>
    <button class="bouton ${analyse ? "" : "principal"}" id="b-matching">${analyse ? "Relancer" : "Lancer le matching"}</button></div>
    ${analyse ? rapportMatching(analyse) : '<p class="note">Compare ton CV par défaut aux compétences demandées (10 à 30 s). Les coordonnées du CV sont masquées avant envoi.</p>'}`;
  $("#b-matching").onclick = async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Analyse en cours…";
    try { afficherMatching(id, await api(`ats/matching/${id}`, { method: "POST" })); if ($("#liste-offres")) chargerOffres(); }
    catch (err) { toast(err.message); e.target.disabled = false; e.target.textContent = "Réessayer"; }
  };
}

function collerFiche(id) {
  const corps = ouvrir("Coller la fiche de poste", `<form class="formulaire" id="form-fiche">
    <p class="note">Copie le texte complet de l'offre depuis le site de l'employeur ou de la plateforme, et colle-le ici.
    L'offre sera marquée « fiche complète » et pourra atteindre 85 au scoring.</p>
    <label>Texte de l'offre<textarea class="champ" name="texte" required minlength="200"></textarea></label>
    <label>Lien de la fiche (facultatif)<input class="champ" name="url" type="url" placeholder="https://…"></label>
    <p class="erreur" id="err-fiche"></p>
    <div class="actions"><button class="bouton principal">Enregistrer la fiche</button></div></form>`);
  $("#form-fiche", corps).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await envoyer(`offres/${id}/fiche`, "POST", { texte: f.get("texte"), url: f.get("url") || null });
      fermer(); toast("Fiche enregistrée."); chargerOffres();
    } catch (err) { $("#err-fiche").textContent = err.message; }
  });
}

async function postuler(id, bouton) {
  bouton.disabled = true;
  try { await envoyer(`offres/${id}/postule`, "POST"); toast("Candidature ajoutée au suivi."); chargerOffres(); }
  catch (err) { toast(err.message); bouton.disabled = false; }
}

// --- Candidatures ----------------------------------------------------------------------------
const filtreCand = { statut: "", type: "", q: "" };

async function vueCandidatures(v) {
  v.innerHTML = `<div class="entete"><div><h1>Tes candidatures.</h1>
    <p>Le suivi est la source de vérité : il empêche de postuler deux fois et de relancer trop tôt.
    Ajoute ici celles faites ailleurs (formulaire ou fichier Excel / CSV).</p></div>
    <div class="actions"><button class="bouton" id="b-ajout">+ Ajouter</button>
      <button class="bouton" id="b-import">Importer Excel / CSV</button>
      <button class="bouton principal" data-telecharger="candidatures/export.xlsx">Exporter en Excel</button></div></div>
    <div class="filtres" id="f-cand">
      ${[["", "Toutes"], ["postule", "En cours"], ["reponse_positive", "Positives"], ["refus", "Refus"],
         ["relance", "Relancées"], ["sans_suite", "Sans suite"], ["ignore", "Non remises"]]
        .map(([s, l]) => `<button class="puce ${filtreCand.statut === s ? "actif" : ""}" data-statut="${s}">${l}</button>`).join("")}
      <select class="champ" id="f-type"><option value="">Tous types</option><option value="offre">Sur offre</option>
        <option value="spontanee">Spontanées</option></select>
      <input class="champ" type="search" id="f-qc" placeholder="Entreprise ou poste…" value="${esc(filtreCand.q)}">
      <span class="compte" id="compte-cand"></span></div>
    <div id="zone-relances"></div>
    <div id="liste-cand" class="chargement">Chargement…</div>`;
  $("#f-type").value = filtreCand.type;
  $("#f-cand").addEventListener("click", (e) => {
    const b = e.target.closest("[data-statut]");
    if (!b) return;
    filtreCand.statut = b.dataset.statut;
    v.querySelectorAll("#f-cand [data-statut]").forEach((x) => x.classList.toggle("actif", x === b));
    chargerCand();
  });
  $("#f-type").addEventListener("change", (e) => { filtreCand.type = e.target.value; chargerCand(); });
  let attente;
  $("#f-qc").addEventListener("input", (e) => {
    clearTimeout(attente);
    attente = setTimeout(() => { filtreCand.q = e.target.value.trim(); chargerCand(); }, 250);
  });
  $("#b-ajout").onclick = formulaireAjout;
  $("#b-import").onclick = formulaireImport;
  afficherRelances();
  await chargerCand();
}

// --- Relances (J+7, J+14) : texte prêt, envoyé par toi depuis ta messagerie ---------------------
const ANGLAIS = /\b(engineer|analyst|developer|manager|consultant|data scientist)\b/i;

function texteRelance(r, ident, langue) {
  const nom = ident.nom || "", tel = ident.telephone ? `\n${ident.telephone}` : "";
  const quand = r.postule_le ? new Date(r.postule_le).toLocaleDateString(langue === "en" ? "en-GB" : "fr-FR") : "";
  const poste = r.poste || r.offre_intitule || "";
  if (langue === "en") {
    const objet = `Follow-up – application${poste ? ` for the ${poste} position` : ""}`;
    const corps = r.numero === 1
      ? `Hello,\n\nI am following up on my application${poste ? ` for the ${poste} position` : ""}${quand ? ` sent on ${quand}` : ""}. `
        + `I remain very interested in joining ${r.entreprise || "your team"} and would welcome the opportunity to discuss it with you.\n\n`
        + `Please let me know if you need any further information.\n\nBest regards,\n${nom}${tel}`
      : `Hello,\n\nI am reaching out one last time regarding my application${poste ? ` for the ${poste} position` : ""}. `
        + `If the position is still open, I would be glad to have a short call.\n\nBest regards,\n${nom}${tel}`;
    return { objet, corps };
  }
  const objet = `Relance – candidature${poste ? ` au poste de ${poste}` : " spontanée"}`;
  const corps = r.numero === 1
    ? `Bonjour,\n\nJe me permets de revenir vers vous au sujet de ma candidature${poste ? ` au poste de ${poste}` : ""}`
      + `${quand ? `, envoyée le ${quand}` : ""}. Je reste très motivé à l'idée de rejoindre ${r.entreprise || "votre équipe"} `
      + `et serais ravi d'échanger avec vous.\n\nJe reste à votre disposition pour tout complément d'information.\n\nBien cordialement,\n${nom}${tel}`
    : `Bonjour,\n\nJe me permets de vous relancer une dernière fois au sujet de ma candidature${poste ? ` au poste de ${poste}` : ""}. `
      + `Si le poste est toujours ouvert, je serais heureux d'en discuter lors d'un court échange.\n\nBien cordialement,\n${nom}${tel}`;
  return { objet, corps };
}

async function afficherRelances() {
  const zone = $("#zone-relances");
  if (!zone) return;
  const [rows, params, env] = await Promise.all([api("relances"), api("parametres"), api("envoi").catch(() => ({}))]);
  const ident = params.identite || {};
  const textes = {};
  if (!rows.length) { zone.innerHTML = ""; return; }
  zone.innerHTML = `<div class="carte section"><h3>Relances à faire (${rows.length})</h3>
    <p class="note">J+7 après la candidature, puis J+14. Relis le texte, envoie-le depuis ta messagerie, puis clique « J'ai relancé ».</p>
    <ul class="liste">${rows.map((r) => {
      const langue = ANGLAIS.test(r.poste || r.offre_intitule || "") && !/[éèà]/.test(r.poste || "") ? "en" : "fr";
      const t = texteRelance(r, ident, langue);
      textes[r.id] = t;
      const mailto = `mailto:${encodeURIComponent(r.contact_email || "")}?subject=${encodeURIComponent(t.objet)}&body=${encodeURIComponent(t.corps)}`;
      return `<li data-id="${r.id}"><span style="flex:1"><strong>${esc(r.entreprise || "—")}</strong> · ${esc(r.poste || r.offre_intitule || "candidature")}
        <span class="etiquette ambre">relance ${r.numero}</span>
        <div class="sous">candidature du ${jour(r.postule_le)} · due le ${jour(r.due_le)} · ${r.contact_email ? esc(r.contact_email) : "destinataire à saisir (aucun contact connu)"}</div>
        <details><summary class="note">Voir le texte</summary><pre class="texte-relance">${esc(t.objet)}\n\n${esc(t.corps)}</pre></details></span>
        <span class="actions">${env.actif && r.contact_email ? '<button class="bouton petit principal" data-envoyer>Envoyer depuis Gmail</button>' : ""}
          <a class="bouton petit ${env.actif && r.contact_email ? "" : "principal"}" href="${mailto}">Ouvrir dans ma messagerie</a>
          <button class="bouton petit" data-copier>Copier</button>
          <button class="bouton petit" data-relance>J'ai relancé</button>
          <button class="bouton petit" data-ignorer>Ne pas relancer</button></span></li>`;
    }).join("")}</ul></div>`;
  zone.onclick = async (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    if (e.target.matches("[data-envoyer]")) {
      const t = textes[li.dataset.id];
      if (!confirm(`Envoyer maintenant cette relance depuis ${env.adresse} ?\n\n${t.objet}`)) return;
      e.target.disabled = true;
      try { await envoyer(`candidatures/${li.dataset.id}/envoyer-relance`, "POST", t); toast("Relance envoyée et enregistrée."); afficherRelances(); chargerCand(); }
      catch (err) { toast(err.message); e.target.disabled = false; }
    } else if (e.target.matches("[data-copier]")) {
      try { await navigator.clipboard.writeText(li.querySelector(".texte-relance").textContent); toast("Texte copié."); }
      catch { toast("Copie impossible : sélectionne le texte à la main."); }
    } else if (e.target.matches("[data-relance]")) {
      await envoyer(`candidatures/${li.dataset.id}/relance`, "POST"); toast("Relance enregistrée."); afficherRelances(); chargerCand();
    } else if (e.target.matches("[data-ignorer]")) {
      await envoyer(`candidatures/${li.dataset.id}/ignorer-relance`, "POST"); toast("Plus de relance pour celle-ci."); afficherRelances();
    }
  };
}

async function chargerCand() {
  const zone = $("#liste-cand");
  const rows = await api(`candidatures?${new URLSearchParams(filtreCand)}`);
  $("#compte-cand").textContent = `${rows.length} candidature(s)`;
  zone.className = "";
  if (!rows.length) { zone.innerHTML = '<div class="carte vide">Aucune candidature pour ces filtres.</div>'; return; }
  const options = (actuel) => Object.keys(LIBELLES).slice(0, 7)
    .map((s) => `<option value="${s}" ${s === actuel ? "selected" : ""}>${LIBELLES[s]}</option>`).join("");
  zone.innerHTML = `<div class="table-defile"><table><thead><tr><th>Date</th><th>Entreprise</th><th>Poste</th>
    <th>Type</th><th>Statut</th><th>Réponses</th><th>Notes</th></tr></thead><tbody>${rows.map((c) => {
      const lien = lienSur(c.url);
      return `<tr data-id="${c.id}"><td class="num">${jour(c.postule_le)}</td>
      <td><strong>${esc(c.entreprise || "—")}</strong><div class="sous">${esc(c.canal || "")}</div></td>
      <td>${lien ? `<a href="${lien}" target="_blank" rel="noopener noreferrer">${esc(c.poste || "lien ↗")}</a>` : esc(c.poste || "—")}</td>
      <td><span class="etiquette">${c.type === "spontanee" ? "spontanée" : "offre"}</span></td>
      <td><select class="champ" data-champ="statut" aria-label="Statut">${options(c.statut)}</select></td>
      <td class="num">${c.nb_reponses || ""}${c.derniere_reponse ? `<div class="sous">${depuis(c.derniere_reponse)}</div>` : ""}</td>
      <td><button class="bouton petit" data-champ="notes" title="${esc(c.notes || "")}">${c.notes ? "✎ Notes" : "+ Note"}</button></td></tr>`;
    }).join("")}</tbody></table></div>`;
  zone.onchange = async (e) => {
    if (e.target.dataset.champ !== "statut") return;
    const id = e.target.closest("tr").dataset.id;
    try { await envoyer(`candidatures/${id}`, "PATCH", { statut: e.target.value }); toast("Statut mis à jour."); }
    catch (err) { toast(err.message); }
  };
  zone.onclick = (e) => {
    const b = e.target.closest('[data-champ="notes"]');
    if (!b) return;
    const id = b.closest("tr").dataset.id;
    const c = rows.find((x) => String(x.id) === id);
    const corps = ouvrir(`Notes — ${c.entreprise || ""}`, `<form class="formulaire" id="form-notes">
      <textarea class="champ" name="notes" maxlength="5000">${esc(c.notes || "")}</textarea>
      <div class="actions"><button class="bouton principal">Enregistrer</button></div></form>`);
    $("#form-notes", corps).addEventListener("submit", async (ev) => {
      ev.preventDefault();
      await envoyer(`candidatures/${id}`, "PATCH", { notes: new FormData(ev.target).get("notes") });
      fermer(); toast("Notes enregistrées."); chargerCand();
    });
  };
}

function formulaireAjout() {
  const corps = ouvrir("Ajouter une candidature déjà faite", `<form class="formulaire" id="form-ajout">
    <div class="ligne"><label>Entreprise *<input class="champ" name="entreprise" required maxlength="120"></label>
      <label>Poste<input class="champ" name="poste" maxlength="200"></label></div>
    <div class="ligne"><label>Date de candidature<input class="champ" name="postule_le" type="date" value="${new Date().toISOString().slice(0, 10)}"></label>
      <label>Statut<select class="champ" name="statut">${["postule", "relance", "reponse_positive", "refus", "sans_suite"]
        .map((s) => `<option value="${s}">${LIBELLES[s]}</option>`).join("")}</select></label></div>
    <div class="ligne"><label>Type<select class="champ" name="type"><option value="offre">Sur offre</option>
      <option value="spontanee">Spontanée</option></select></label>
      <label>Lien de l'offre<input class="champ" name="url" type="url" placeholder="https://…"></label></div>
    <label>Notes<textarea class="champ" name="notes" style="min-height:90px"></textarea></label>
    <p class="erreur" id="err-ajout"></p>
    <div class="actions"><button class="bouton principal">Ajouter au suivi</button></div></form>`);
  $("#form-ajout", corps).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    for (const k of ["url", "notes", "postule_le"]) if (!f[k]) f[k] = null;
    try { await envoyer("candidatures", "POST", f); fermer(); toast("Candidature ajoutée."); chargerCand(); }
    catch (err) { $("#err-ajout").textContent = err.message; }
  });
}

function formulaireImport() {
  const corps = ouvrir("Importer des candidatures", `<form class="formulaire" id="form-import">
    <p class="note">Fichier .xlsx ou .csv dont la première ligne donne les titres de colonnes. Seule « Entreprise » est obligatoire ;
    sont aussi reconnues : Poste / Intitulé, Date, Statut (refus, entretien, en attente…), Lien, Notes, Type (spontanée).
    Une ligne déjà présente (même entreprise, poste et date) est ignorée.</p>
    <input class="champ" type="file" name="fichier" accept=".xlsx,.csv" required>
    <p class="erreur" id="err-import"></p>
    <div class="actions"><button class="bouton principal">Importer</button></div></form>`);
  $("#form-import", corps).addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("candidatures/import", { method: "POST", body: new FormData(e.target) });
      fermer(); toast(`${r.ajoutees} candidature(s) importée(s), ${r.deja_presentes} déjà présente(s).`); chargerCand();
    } catch (err) { $("#err-import").textContent = err.message; }
  });
}

// --- Réponses ---------------------------------------------------------------------------------
async function vueReponses(v) {
  const rows = await api("reponses?a_confirmer=true");
  v.innerHTML = `<div class="entete"><div><h1>Réponses à confirmer.</h1>
    <p>Mails dont le classement automatique n'est pas assez sûr (confiance &lt; 90 %). Rien n'est supprimé tant que tu n'as pas confirmé.</p></div></div>
    ${rows.length ? `<div class="table-defile"><table><thead><tr><th>Reçue</th><th>Entreprise</th><th>Objet</th>
      <th>Classement proposé</th><th>Ton avis</th></tr></thead><tbody>${rows.map((r) => `<tr data-id="${r.id}">
        <td class="num">${jour(r.recu_le)}</td><td><strong>${esc(r.entreprise || "—")}</strong><div class="sous">${esc(r.poste || "")}</div></td>
        <td>${esc(r.extrait)}<div class="sous">${esc(r.expediteur)}</div></td>
        <td><span class="etiquette ${r.classement === "negative" ? "rouge" : r.classement === "positive" ? "vert" : ""}">${esc(r.classement)}</span>
          <div class="sous">confiance ${Math.round(r.confiance * 100)} %</div></td>
        <td><div class="actions"><button class="bouton petit" data-c="negative">C'est un refus</button>
          <button class="bouton petit" data-c="positive">Positive</button>
          <button class="bouton petit" data-c="neutre">Neutre</button></div></td></tr>`).join("")}</tbody></table></div>`
      : '<div class="carte vide">Tout est confirmé. 👌</div>'}`;
  v.onclick = async (e) => {
    const b = e.target.closest("[data-c]");
    if (!b) return;
    const tr = b.closest("tr");
    await envoyer(`reponses/${tr.dataset.id}/confirmer`, "POST", { classement: b.dataset.c });
    tr.remove(); toast("Réponse confirmée."); majBadge();
  };
}

// --- Documents --------------------------------------------------------------------------------
async function vueDocuments(v) {
  const docs = await api("documents");
  const bloc = (type, titre, aide) => {
    const versions = docs.filter((d) => d.type === type);
    const actuel = versions.find((d) => d.par_defaut);
    return `<div class="carte"><h3>${titre}</h3>
      <label class="depot" data-type="${type}"><input type="file" accept=".pdf,.docx" hidden>
        <strong>${actuel ? "Remplacer" : "Déposer"} ${type === "cv" ? "mon CV" : "ma lettre"}</strong>
        PDF ou DOCX, 10 Mo maximum — glisse le fichier ici ou clique.</label>
      <p class="note">${aide}</p>
      ${actuel ? `<div class="section">${blocDiagnostic(actuel)}
        <div class="actions"><button class="bouton petit" data-texte="${actuel.id}">Ce que lit un ATS</button>
        <button class="bouton petit" data-telecharger="documents/${actuel.id}/fichier">Télécharger</button></div></div>` : ""}
      ${versions.length > 1 ? `<h3 class="section">Versions</h3><ul class="liste">${versions.map((d) => `<li>
        <span>${esc(d.nom_fichier)}<div class="sous note">${jour(d.depose_le)} · ${Math.round(d.taille / 1024)} Ko</div></span>
        ${d.par_defaut ? '<span class="etiquette vert">par défaut</span>' : `<button class="bouton petit" data-defaut="${d.id}">Utiliser</button>`}</li>`).join("")}</ul>` : ""}
    </div>`;
  };
  v.innerHTML = `<div class="entete"><div><h1>Ton CV et ta lettre de base.</h1>
    <p>Un seul CV et une seule lettre, en français. L'outil les adaptera à chaque offre (Data Analyst, Analytics Engineer ou
    Data Engineer) et produira la version anglaise. Il n'ajoutera jamais une compétence qui n'est pas dans ton profil.</p></div></div>
    <div class="grille-2">${bloc("cv", "CV par défaut", "Le texte extrait est ce que verra un logiciel de recrutement (ATS) : vérifie qu'il est complet et dans le bon ordre.")}
    ${bloc("lm", "Lettre de motivation par défaut", "Sert de trame : ton, structure et arguments seront repris et adaptés à chaque entreprise.")}</div>
    ${docs.some((d) => d.type === "cv") ? '<div class="carte section" id="zone-ats"><p class="chargement">Chargement de l\'analyse…</p></div>' : ""}`;
  if (docs.some((d) => d.type === "cv")) afficherRapportCV(await api("ats/cv"));
  const params = await api("parametres");
  const id_ = params.identite || {};
  const champ = (nom, lib, type = "text") => `<label>${lib}<input class="champ" type="${type}" name="${nom}" value="${esc(id_[nom] || "")}"></label>`;
  v.insertAdjacentHTML("beforeend", `<div class="carte section"><h3>Tes coordonnées sur les CV et lettres générés</h3>
    <p class="note">Pré-remplies depuis ton CV : vérifie-les. Elles sont imprimées sur les documents par ton PC et
    retirées de tout texte envoyé à Gemini (un contrôle bloque l'envoi s'il en reste une).</p>
    <form class="formulaire" id="form-identite"><div class="ligne">${champ("nom", "Prénom et nom")}${champ("email", "Mail", "email")}</div>
      <div class="ligne">${champ("telephone", "Téléphone", "tel")}${champ("ville", "Ville")}</div>
      <div class="ligne">${champ("linkedin", "LinkedIn")}${champ("portfolio", "Portfolio")}</div>
      <div class="ligne">${champ("permis", "Permis")}${champ("mobilite", "Mobilité / télétravail")}</div>
      <div class="actions"><button class="bouton principal">Enregistrer mes coordonnées</button></div></form></div>`);
  $("#form-identite").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await envoyer("parametres", "PUT", { identite: Object.fromEntries(new FormData(e.target)) }); toast("Coordonnées enregistrées."); }
    catch (err) { toast(err.message); }
  });
  v.querySelectorAll(".depot").forEach((zone) => {
    const input = $("input", zone);
    const envoi = async (fichier) => {
      if (!fichier) return;
      const f = new FormData();
      f.append("type", zone.dataset.type);
      f.append("fichier", fichier);
      zone.querySelector("strong").textContent = "Analyse en cours…";
      try { await api("documents", { method: "POST", body: f }); toast("Document enregistré et analysé."); }
      catch (err) { toast(err.message); }
      vueDocuments(v);
    };
    input.addEventListener("change", () => envoi(input.files[0]));
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("survol"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("survol"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("survol"); envoi(e.dataTransfer.files[0]); });
  });
  v.onclick = async (e) => {
    const t = e.target.closest("[data-texte]");
    if (t) {
      const { texte } = await api(`documents/${t.dataset.texte}/texte`);
      ouvrir("Ce que lit un ATS", `<p class="note">Texte extrait, dans l'ordre de lecture. Une information absente ici est invisible pour un ATS.</p>
        <div class="texte-ats">${esc(texte || "(aucun texte lisible)")}</div>`);
    }
    const d = e.target.closest("[data-defaut]");
    if (d) { await envoyer(`documents/${d.dataset.defaut}/defaut`, "POST"); vueDocuments(v); }
  };
}

function afficherRapportCV(analyse) {
  const zone = $("#zone-ats");
  if (!zone) return;
  zone.innerHTML = `<div class="entete" style="margin-bottom:12px"><div><h2>ATS checker</h2>
    <p>Compatibilité avec les logiciels de recrutement et qualité du CV par défaut.</p></div>
    <button class="bouton ${analyse ? "" : "principal"}" id="b-ats">${analyse ? "Relancer l'analyse" : "Analyser mon CV"}</button></div>
    ${analyse ? rapportCV(analyse) : '<p class="note">Lecture du CV, score ATS /100 et score qualité /100 (10 à 60 s). Tes coordonnées sont masquées avant l\'envoi à Gemini.</p>'}`;
  $("#b-ats").onclick = async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Analyse en cours…";
    try { afficherRapportCV(await api("ats/cv", { method: "POST" })); toast("Analyse terminée."); }
    catch (err) { toast(err.message); e.target.disabled = false; e.target.textContent = "Réessayer"; }
  };
}

// --- Salon (journal des agents) ----------------------------------------------------------------
function filJournal(rows) {
  if (!rows.length) return '<p class="vide">Aucune activité sur les 30 derniers jours.</p>';
  return `<ul class="fil">${rows.map((x) => {
    const a = AGENTS[x.tache] || { nom: x.tache || "Pipeline", role: "", c: "#627583" };
    const d = x.details || {};
    let resume;
    if (x.tache === "collecte") resume = `${x.mails_lus} mails lus, ${x.offres_vues} offres vues, ${x.offres_vues - x.doublons} nouvelles`;
    else if (x.tache === "validite") resume = `${x.doublons} doublons repérés, ${x.rejetees} offres passées en « ancienne »`;
    else if (x.tache === "fiches") resume = Object.entries(d).map(([k, n]) => `${n} ${k}`).join(", ") || `${x.offres_vues} offres contrôlées`;
    else if (["veille", "matching", "rapport", "spontanees", "candidatures_auto"].includes(x.tache)) resume = Object.entries(d).map(([k, n]) => `${k} : ${n}`).join(" · ") || "rien à signaler";
    else if (x.tache === "suivi") resume = `${x.mails_lus} mails relus` + (Object.keys(d).length ? ` — ${Object.entries(d).map(([k, n]) => `${n} ${k}`).join(", ")}` : "");
    else resume = `${x.mails_lus} mails, ${x.offres_vues} offres`;
    return `<li><span class="avatar" style="background:${a.c}">${esc(a.nom.split(" ").pop()[0])}</span>
      <div><strong>${esc(a.nom)}</strong> <span class="note">${esc(a.role)}</span><div>${esc(resume)}</div>
      ${x.nb_erreurs ? `<div class="erreur">${x.nb_erreurs} erreur(s)</div>` : ""}</div>
      <span class="quand">${depuis(x.fin || x.debut)}</span></li>`;
  }).join("")}</ul>`;
}

const heure = (d) => (d ? new Date(d).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : "");
const STATUT_ACT = { en_cours: ["ambre", "en cours"], fait: ["vert", "fait"], erreur: ["rouge", "erreur"] };
let minuterieSalon = null;

function agentDe(cle) { return AGENTS[cle] || { nom: cle, role: "", c: "#627583" }; }
function avatar(a) { return `<span class="avatar" style="background:${a.c}">${esc(a.nom.split(" ").pop()[0])}</span>`; }
function poste(x) {
  const morceaux = [x.entreprise, x.intitule].filter(Boolean).map(esc);
  return morceaux.length ? `<strong>${morceaux.join(" — ")}</strong>` : "";
}

function htmlSalon(acts, journal) {
  const recents = acts.filter((x) => Date.now() - new Date(x.cree_le) < 2 * 36e5);
  const enCours = recents.filter((x) => x.statut === "en_cours");
  const surPoste = enCours.filter((x) => x.action !== "Au travail");
  const agentsActifs = new Set(enCours.map((x) => x.agent));
  const derniere = {};
  for (const x of acts) if (!derniere[x.agent]) derniere[x.agent] = x;
  const bandeau = enCours.length
    ? `<div class="carte" style="border-left:4px solid var(--teal)"><h3>En ce moment</h3><ul class="fil">${(surPoste.length ? surPoste : enCours).map((x) => {
        const a = agentDe(x.agent);
        return `<li>${avatar(a)}<div><strong>${esc(a.nom)}</strong> · ${esc(x.action)} ${poste(x)}
          <div class="sous">${esc(x.detail || "")}</div></div><span class="quand">depuis ${depuis(x.cree_le)}</span></li>`;
      }).join("")}</ul></div>`
    : '<div class="carte"><h3>En ce moment</h3><p class="note">Aucun agent au travail. Prochain passage automatique dans l\'heure (ou bouton « Lancer la veille maintenant », onglet Veille).</p></div>';
  const cartes = Object.entries(AGENTS).map(([cle, a]) => {
    const d = derniere[cle], actif = agentsActifs.has(cle);
    return `<div class="carte">${avatar(a)}<h3 style="margin-top:10px">${esc(a.nom)}
        ${actif ? '<span class="etiquette ambre">au travail</span>' : ""}</h3><p class="note">${esc(a.role)}</p>
      <p class="sous">${d ? `${actif ? "En cours" : "Dernière action"} : ${esc(d.action)} · ${depuis(d.cree_le)}` : "Pas encore d'activité."}</p></div>`;
  }).join("");
  const fil = acts.filter((x) => x.action !== "Au travail" || x.statut !== "fait").slice(0, 60);
  return `<div class="entete"><div><h1>Le salon des agents.</h1>
      <p>Qui travaille, sur quel poste, et ce qui a été fait : offres récupérées, fiches trouvées, postes analysés,
      dossiers CV + LM + mail préparés, candidatures envoyées ou mises de côté. Mise à jour toutes les 10 secondes.</p></div></div>
    ${bandeau}
    <div class="carte section"><h3>Fil en direct</h3>${fil.length ? `<ul class="fil">${fil.map((x) => {
        const a = agentDe(x.agent), [coul, lib] = STATUT_ACT[x.statut] || ["", x.statut];
        return `<li>${avatar(a)}<div><strong>${esc(a.nom)}</strong> · ${esc(x.action)} ${poste(x)}
          ${x.statut !== "fait" ? `<span class="etiquette ${coul}">${esc(lib)}</span>` : ""}
          <div class="sous">${esc(x.detail || "")}</div></div><span class="quand">${heure(x.cree_le)}</span></li>`;
      }).join("")}</ul>` : '<p class="vide">Rien pour l\'instant : le fil se remplit au prochain passage des agents.</p>'}</div>
    <div class="grille section">${cartes}</div>
    <div class="carte section"><h3>Journal des passages (30 derniers jours)</h3>${filJournal(journal)}</div>`;
}

async function vueSalon(v) {
  const charger = async () => {
    const [acts, journal] = await Promise.all([api("activites").catch(() => []), api("journal")]);
    if (location.hash.split("?")[0] !== "#salon") return;
    v.innerHTML = htmlSalon(acts, journal);
  };
  await charger();
  clearInterval(minuterieSalon);
  minuterieSalon = setInterval(() => {
    if (location.hash.split("?")[0] !== "#salon" || document.hidden) return;
    charger().catch(() => {});
  }, 10000);
}

// --- Candidatures spontanées ----------------------------------------------------------------------
const STATUT_SPONT = { brouillon: ["", "à envoyer"], envoyee: ["vert", "envoyée"], ignoree: ["", "ignorée"], erreur: ["rouge", "erreur"] };

async function vueSpontanees(v) {
  const [rows, docs, env] = await Promise.all([api("spontanees"), api("documents").catch(() => []), api("envoi").catch(() => ({}))]);
  const cv = docs.find((d) => d.type === "cv" && d.par_defaut), lm = docs.find((d) => d.type === "lm" && d.par_defaut);
  const pj = [cv && `<button class="bouton petit" data-telecharger="documents/${cv.id}/fichier">CV</button>`,
    lm && `<button class="bouton petit" data-telecharger="documents/${lm.id}/fichier">Lettre</button>`].filter(Boolean).join(" ");
  const brouillons = rows.filter((r) => r.statut === "brouillon"), autres = rows.filter((r) => r.statut !== "brouillon");
  v.innerHTML = `<div class="entete"><div><h1>Candidatures spontanées.</h1>
      <p>L'outil trouve l'adresse de recrutement publique de l'entreprise et rédige le mail ; c'est toi qui l'envoies
      depuis ta messagerie, en joignant ton CV et ta lettre. Au plus 5 brouillons par jour, jamais deux fois la même entreprise en 90 jours.</p></div></div>
    <div class="carte"><h3>Envoi depuis Gmail</h3>${env.autorise
      ? `<p class="note">Compte autorisé : <strong>${esc(env.adresse)}</strong> · ${env.envoyes_aujourdhui}/${env.plafond} envoi(s) aujourd'hui.
          Activé, chaque brouillon gagne un bouton « Envoyer depuis Gmail » : rien ne part sans ton clic.</p>
        <label class="actions"><input type="checkbox" id="envoi-actif" ${env.actif ? "checked" : ""}>
          <span>${env.actif ? "Activé : l'agent peut envoyer à ton nom quand tu cliques" : "Désactivé : tu envoies depuis ta messagerie"}</span></label>`
      : `<p class="note">Pas encore autorisé. Sur le PC, lance <code>python -m app.envoi --autoriser</code> et choisis
          le compte de ton CV : seul le droit d'<em>envoyer</em> est demandé, la lecture reste en lecture seule.</p>`}</div>
    <div class="carte section"><h3>Préparer une spontanée</h3>
      <form class="formulaire" id="form-spont"><div class="ligne">
        <label>Entreprise<input class="champ" name="entreprise" required maxlength="200"></label>
        <label>Site web<input class="champ" name="site_web" placeholder="acme.fr"></label></div>
        <label>Adresse de recrutement (si tu la connais)<input class="champ" type="email" name="email" placeholder="recrutement@acme.fr"></label>
        <p class="erreur" id="err-spont"></p>
        <div class="actions"><button class="bouton principal">Préparer le mail</button></div></form></div>
    <div class="section">${brouillons.length ? brouillons.map((r) => `<div class="carte" data-id="${r.id}">
        <h3>${esc(r.entreprise)} <span class="note">→ ${esc(r.destinataire)}</span></h3>
        <p class="note">Adresse trouvée sur ${lienSur(r.source_url) ? `<a href="${lienSur(r.source_url)}" target="_blank" rel="noopener">${esc(r.source_url)}</a>` : esc(r.source_url || "—")}
          · préparé ${depuis(r.cree_le)}${r.verification && !r.verification.ok ? " · un paragraphe retiré par le garde-fou" : ""}
          ${r.verification?.adresse_generique ? '<br><span class="etiquette ambre">adresse générale</span> aucune adresse de recrutement publiée : le mail demande de transmettre au service RH.' : ""}</p>
        <div class="formulaire"><label>Objet<input class="champ" data-champ="objet" value="${esc(r.objet)}"></label>
          <label>Message<textarea class="champ" data-champ="corps" rows="14">${esc(r.corps)}</textarea></label></div>
        <div class="actions section">
          ${env.actif ? `<button class="bouton principal" data-a="envoyer">Envoyer depuis Gmail${pj ? " (CV + LM joints)" : ""}</button>` : ""}
          <a class="bouton ${env.actif ? "" : "principal"}" data-mailto>Ouvrir dans ma messagerie</a>
          <button class="bouton" data-a="copier">Copier le texte</button>
          ${pj ? `<span class="note">Pièces jointes :</span> ${pj}` : '<span class="note">Dépose ton CV et ta lettre (onglet « CV &amp; lettre ») pour les joindre.</span>'}
        </div>
        <div class="actions">
          <button class="bouton" data-a="envoyee">J'ai envoyé</button>
          <button class="bouton petit" data-a="enregistrer">Enregistrer mes modifications</button>
          <button class="bouton petit" data-a="ignorer">Ignorer</button>
          <button class="bouton petit" data-a="opposition" title="Ne plus jamais contacter cette adresse">Opposition</button>
        </div></div>`).join("")
      : '<div class="carte vide">Aucun brouillon en attente. L\'agent en prépare chaque jour pour les entreprises de ta veille dont le site est connu.</div>'}</div>
    ${autres.length ? `<div class="carte section"><h3>Historique</h3><ul class="fil">${autres.map((r) => `<li><div>
      <span class="etiquette ${STATUT_SPONT[r.statut]?.[0] ?? ""}">${esc(STATUT_SPONT[r.statut]?.[1] ?? r.statut)}</span>
      <strong>${esc(r.entreprise)}</strong> <span class="note">${esc(r.destinataire || "")}</span>
      <div class="sous">${jour(r.envoyee_le || r.cree_le)}${r.opposition ? " · opposition enregistrée" : ""}</div></div></li>`).join("")}</ul></div>` : ""}`;

  const valeurs = (carte) => ({ objet: $("[data-champ=objet]", carte).value, corps: $("[data-champ=corps]", carte).value });
  v.querySelectorAll("[data-mailto]").forEach((a) => {
    const carte = a.closest("[data-id]"), r = rows.find((x) => x.id === +carte.dataset.id);
    const maj = () => { const m = valeurs(carte);
      a.href = `mailto:${encodeURIComponent(r.destinataire)}?subject=${encodeURIComponent(m.objet)}&body=${encodeURIComponent(m.corps)}`; };
    maj(); carte.addEventListener("input", maj);
  });
  $("#envoi-actif")?.addEventListener("change", async (e) => {
    try { await envoyer("envoi", "PUT", { actif: e.target.checked }); toast(e.target.checked ? "Envoi depuis Gmail activé." : "Envoi désactivé."); router(); }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  });
  $("#form-spont").addEventListener("submit", async (e) => {
    e.preventDefault();
    const b = e.target.querySelector("button"), f = Object.fromEntries(new FormData(e.target));
    Object.keys(f).forEach((k) => { if (!f[k]) delete f[k]; });
    b.disabled = true; $("#err-spont").textContent = ""; toast("Recherche du contact et rédaction… (jusqu'à une minute)");
    try { await envoyer("spontanees", "POST", f); toast("Brouillon prêt."); router(); }
    catch (err) { $("#err-spont").textContent = err.message; b.disabled = false; }
  });
  v.onclick = async (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const carte = b.closest("[data-id]"), id = carte.dataset.id, a = b.dataset.a;
    try {
      if (a === "copier") { const m = valeurs(carte); await navigator.clipboard.writeText(`${m.objet}\n\n${m.corps}`); toast("Texte copié."); return; }
      if (a === "opposition" && !confirm("Ne plus jamais contacter cette adresse ?")) return;
      if (a === "envoyer") {
        const r = rows.find((x) => x.id === +id);
        if (!confirm(`Envoyer maintenant ce mail à ${r.destinataire} depuis ${env.adresse}, avec ton CV et ta lettre ?`)) return;
        b.disabled = true; toast("Envoi en cours…");
        try { await envoyer(`spontanees/${id}/envoyer`, "POST", valeurs(carte)); toast("Envoyé : candidature ajoutée au suivi."); router(); }
        catch (err) { toast(err.message); b.disabled = false; }
        return;
      }
      if (a === "envoyee" || a === "enregistrer") await envoyer(`spontanees/${id}`, "PUT", valeurs(carte));
      if (a !== "enregistrer") await envoyer(`spontanees/${id}/${a}`, "POST");
      toast({ envoyee: "Candidature ajoutée au suivi (relance prévue).", enregistrer: "Modifications enregistrées.",
        ignorer: "Brouillon ignoré.", opposition: "Opposition enregistrée." }[a]);
      if (a !== "enregistrer") router();
    } catch (err) { toast(err.message); }
  };
}

// --- Candidatures automatiques ------------------------------------------------------------------
const STATUT_AUTO = { pret: ["ambre", "prête (envoi au prochain passage)"], envoyee: ["vert", "envoyée"],
  mise_de_cote: ["sarcelle", "mise de côté pour toi"], ignoree: ["", "ignorée"], erreur: ["rouge", "erreur"] };

async function vueEnvoisAuto(v) {
  const [rows, env, params] = await Promise.all([api("candidatures-auto"), api("envoi").catch(() => ({})), api("parametres")]);
  const seuil = (params.seuils || {}).fabrication ?? 85, mini = (params.seuils || {}).envoi_auto_min ?? 50;
  const cote = rows.filter((r) => r.statut === "mise_de_cote"), autres = rows.filter((r) => r.statut !== "mise_de_cote");
  const pieces = (r) => (r.fichiers || []).filter((f) => f.format === "pdf")
    .map((f) => `<button class="bouton petit" data-telecharger="fichiers/${f.id}">${f.type === "cv" ? "CV" : "LM"} (PDF)</button>`).join(" ");
  v.innerHTML = `<div class="entete"><div><h1>Candidatures automatiques.</h1>
      <p>Pour chaque offre notée entre ${mini} et ${seuil - 1} sans contrainte violée : CV et lettre adaptés, mail jovial et pro avec ton portfolio,
      envoyé automatiquement au contact publié par l'employeur (${env.plafond_auto ?? 10} par jour au plus, jamais deux fois le même employeur en 3 mois).
      À partir de ${seuil}, ou sans contact fiable, tout est préparé et <strong>mis de côté pour toi</strong>, sans envoi.</p></div></div>
    <div class="carte"><h3>Envoi automatique</h3>${env.autorise
      ? `<label class="actions"><input type="checkbox" id="auto-actif" ${env.auto ? "checked" : ""}>
          <span>${env.auto ? `Activé : envoi depuis <strong>${esc(env.adresse)}</strong> — ${env.auto_aujourdhui}/${env.plafond_auto} aujourd'hui`
            : "Désactivé : rien ne part tout seul (les dossiers ne sont pas préparés)"}</span></label>`
      : '<p class="note">Autorise d\'abord l\'envoi sur le PC : <code>python -m app.envoi --autoriser</code>.</p>'}</div>
    <div class="section">${cote.length ? `<h3>Mises de côté pour toi (${cote.length})</h3>` + cote.map((r) => `<div class="carte" data-id="${r.id}">
        <h3>${esc(r.entreprise || "?")} — ${esc(r.intitule)} <span class="note">${r.score != null ? Math.round(r.score) + "/100" : ""}</span></h3>
        <p class="note">${esc(r.motif || "")}${r.destinataire ? ` · contact : <strong>${esc(r.destinataire)}</strong>` : ""}
          ${lienSur(r.url_fiche || r.url_cle) ? ` · <a href="${lienSur(r.url_fiche || r.url_cle)}" target="_blank" rel="noopener">voir l'offre</a>` : ""}</p>
        <div class="formulaire"><label>Objet<input class="champ" data-champ="objet" value="${esc(r.objet || "")}"></label>
          <label>Message<textarea class="champ" data-champ="corps" rows="12">${esc(r.corps || "")}</textarea></label></div>
        <div class="actions section">
          ${r.destinataire && env.actif ? '<button class="bouton principal" data-a="envoyer">Envoyer depuis Gmail</button>' : ""}
          ${r.destinataire ? `<a class="bouton" data-mailto="${esc(r.destinataire)}">Ouvrir dans ma messagerie</a>` : ""}
          <button class="bouton petit" data-a="copier">Copier le texte</button> ${pieces(r)}
          <button class="bouton petit" data-a="ignorer">Ignorer</button></div></div>`).join("")
      : '<div class="carte vide">Rien de mis de côté pour l\'instant.</div>'}</div>
    ${autres.length ? `<div class="carte section"><h3>Historique</h3><ul class="fil">${autres.map((r) => `<li><div>
      <span class="etiquette ${STATUT_AUTO[r.statut]?.[0] ?? ""}">${esc(STATUT_AUTO[r.statut]?.[1] ?? r.statut)}</span>
      <strong>${esc(r.entreprise || "?")}</strong> ${esc(r.intitule)} <span class="note">${r.score != null ? Math.round(r.score) + "/100" : ""}</span>
      <div class="sous">${jour(r.envoyee_le || r.cree_le)}${r.destinataire ? ` · ${esc(r.destinataire)}` : ""}${r.erreur ? ` · ${esc(r.erreur.slice(0, 120))}` : ""}</div></div></li>`).join("")}</ul></div>` : ""}`;
  const valeurs = (carte) => ({ objet: $("[data-champ=objet]", carte).value, corps: $("[data-champ=corps]", carte).value });
  v.querySelectorAll("[data-mailto]").forEach((a) => {
    const carte = a.closest("[data-id]");
    const maj = () => { const m = valeurs(carte);
      a.href = `mailto:${encodeURIComponent(a.dataset.mailto)}?subject=${encodeURIComponent(m.objet)}&body=${encodeURIComponent(m.corps)}`; };
    maj(); carte.addEventListener("input", maj);
  });
  $("#auto-actif")?.addEventListener("change", async (e) => {
    if (e.target.checked && !confirm("Activer l'envoi AUTOMATIQUE de candidatures à ton nom, sans relecture ?")) { e.target.checked = false; return; }
    try { await envoyer("envoi", "PUT", { actif: e.target.checked, cle: "auto" }); toast(e.target.checked ? "Candidatures automatiques activées." : "Désactivées."); router(); }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  });
  v.onclick = async (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const carte = b.closest("[data-id]"), id = carte.dataset.id, a = b.dataset.a;
    try {
      if (a === "copier") { const m = valeurs(carte); await navigator.clipboard.writeText(`${m.objet}\n\n${m.corps}`); toast("Texte copié."); return; }
      if (a === "envoyer") {
        if (!confirm("Envoyer cette candidature maintenant, avec le CV et la lettre adaptés ?")) return;
        b.disabled = true; toast("Envoi en cours…");
        await envoyer(`candidatures-auto/${id}/envoyer`, "POST", valeurs(carte)); toast("Candidature envoyée et ajoutée au suivi.");
      } else { await envoyer(`candidatures-auto/${id}/ignorer`, "POST"); toast("Ignorée."); }
      router();
    } catch (err) { toast(err.message); b.disabled = false; }
  };
}

// --- Rapport (tous les 3 jours) -----------------------------------------------------------------
const CLASSEMENT = { positive: ["vert", "positive"], negative: ["rouge", "refus"], neutre: ["", "neutre"] };

function listeRapport(titre, rows, ligne, vide) {
  return `<div class="carte"><h3>${titre}</h3>${rows.length
    ? `<ul class="fil">${rows.map((r) => `<li><div>${ligne(r)}</div></li>`).join("")}</ul>`
    : `<p class="vide">${vide}</p>`}</div>`;
}

async function vueRapport(v) {
  const rapports = await api("rapports");
  if (!rapports.length) {
    v.innerHTML = `<div class="entete"><div><h1>Rapport.</h1><p>Le premier rapport sera produit au prochain passage
      des agents (ou maintenant : <code>python -m app.rapport --forcer</code>).</p></div></div>`;
    return;
  }
  const choisi = Number(new URLSearchParams(location.hash.split("?")[1] || "").get("id")) || rapports[0].id;
  const r = rapports.find((x) => x.id === choisi) || rapports[0];
  const c = r.contenu, n = c.chiffres, t = c.taux_reponse;
  v.innerHTML = `<div class="entete"><div><h1>Rapport du ${jour(r.periode_fin)}.</h1>
      <p>Période du ${jour(r.periode_debut)} au ${jour(r.periode_fin)}. Un nouveau rapport arrive tous les 3 jours.</p></div>
      ${rapports.length > 1 ? `<div class="actions"><select class="champ" id="choix-rapport">${rapports.map((x) =>
        `<option value="${x.id}" ${x.id === r.id ? "selected" : ""}>${jour(x.periode_fin)}</option>`).join("")}</select></div>` : ""}</div>
    <div class="grille">
      ${kpi(n.reponses_positives, "réponses positives", n.refus ? `${n.refus} refus` : "aucun refus")}
      ${kpi(n.relances_dues, "relances à faire", n.relances_dues ? '<a href="#candidatures">Voir les textes →</a>' : "aucune due")}
      ${kpi(n.dossiers_prets, "dossiers prêts", n.dossiers_prets ? '<a href="#pipeline">Pipeline →</a>' : "")}
      ${kpi(n.candidatures_envoyees, "candidatures envoyées", n.passees_sans_suite ? `${n.passees_sans_suite} passée(s) sans suite` : "")}
      ${kpi(n.offres_data, "offres data repérées", `${n.offres_collectees} offres collectées`)}
      ${kpi(`${n.classe_a} / ${n.classe_b} / ${n.classe_c}`, "classées A / B / C", "sur la période")}
      ${kpi(t.taux != null ? `${t.taux.toLocaleString("fr-FR")} %` : "—", `taux de réponse (${t.jours} j)`, `${t.repondues}/${t.envoyees} réponses, ${t.positives} positive(s)`)}
    </div>
    <div class="grille-2 section">
      ${listeRapport("Réponses reçues", c.reponses, (x) => `<span class="etiquette ${CLASSEMENT[x.classement]?.[0] ?? ""}">${esc(CLASSEMENT[x.classement]?.[1] ?? x.classement)}</span>
        <strong>${esc(x.entreprise)}</strong> ${esc(x.poste)}${x.a_confirmer ? ' <span class="note">(à confirmer)</span>' : ""}
        <div class="sous">${jour(x.recu_le)} · ${esc(x.extrait || "")}</div>`, "Aucune réponse sur la période.")}
      ${listeRapport("Relances à faire", c.relances, (x) => `<strong>${esc(x.entreprise)}</strong> ${esc(x.poste)}
        <div class="sous">relance ${x.numero} due le ${jour(x.due_le)}${x.contact_email ? ` · ${esc(x.contact_email)}` : ""}</div>`, "Aucune relance due.")}
      ${listeRapport("Dossiers prêts ou à préparer", c.a_envoyer, (x) => `<strong>${esc(x.entreprise)}</strong> ${esc(x.intitule)}
        <div class="sous">${x.score != null ? `${Math.round(x.score)}/100 · ` : ""}${esc(ETAPES.find((e) => e[0] === x.etape)?.[1] ?? x.etape)}</div>`, "Aucun dossier en attente.")}
      ${listeRapport("À étudier (classe B)", c.a_etudier, (x) => `<strong>${esc(x.entreprise)}</strong> ${esc(x.intitule)}
        <div class="sous">${Math.round(x.score)}/100${x.motif ? ` · ${esc(x.motif)}` : ""}</div>`, "Rien à étudier.")}
      ${listeRapport("Candidatures envoyées", c.envoyees, (x) => `<strong>${esc(x.entreprise)}</strong> ${esc(x.poste)}
        <div class="sous">${jour(x.postule_le)}${x.type === "spontanee" ? " · spontanée" : ""}</div>`, "Aucune candidature sur la période.")}
    </div>`;
  $("#choix-rapport")?.addEventListener("change", (e) => { location.hash = `rapport?id=${e.target.value}`; });
}

// --- Pipeline (kanban) -------------------------------------------------------------------------
const SUIVANTE = { reperee: ["qualifiee", "Qualifier"], qualifiee: ["fabrication", "Préparer le dossier"],
  fabrication: ["ats_friendly", "Marquer ATS friendly"], ats_friendly: ["a_envoyer", "Prêt à envoyer"],
  a_envoyer: ["envoyee", "J'ai postulé"] };

async function vuePipeline(v) {
  const p = await api("pipeline");
  v.innerHTML = `<div class="entete"><div><h1>Le pipeline des dossiers.</h1>
    <p>Chaque offre intéressante avance d'une colonne à l'autre. Les agents font avancer les deux premières ;
    rien n'est jamais envoyé sans ton clic.</p></div>
    <div class="actions"><a class="bouton" href="#veille">Réglages de la veille</a></div></div>
    <div class="kanban">${ETAPES.map(([cle, titre, aide]) => `<section class="colonne">
      <header><h3>${titre} <span class="etiquette">${p[cle].total}</span></h3><p class="note">${aide}</p></header>
      ${p[cle].offres.map((o) => carteOffre(o, cle)).join("") || '<p class="note vide">—</p>'}
      ${p[cle].total > p[cle].offres.length ? `<p class="note">… et ${p[cle].total - p[cle].offres.length} autres (voir Opportunités)</p>` : ""}
    </section>`).join("")}</div>`;
  v.onclick = async (e) => {
    const b = e.target.closest("[data-etape], [data-detail]");
    if (!b) return;
    const id = b.closest(".carte-offre").dataset.id;
    if (b.dataset.detail !== undefined) { detailOffre(id); return; }
    b.disabled = true;
    try {
      const r = await envoyer(`offres/${id}/etape`, "POST", { etape: b.dataset.etape || null });
      toast(r.dossier ? `Dossier créé : ${r.dossier}` : "Étape mise à jour.");
      vuePipeline(v);
    } catch (err) { toast(err.message); b.disabled = false; }
  };
}

function carteOffre(o, etape) {
  const [suivante, libelle] = SUIVANTE[etape] || [];
  return `<article class="carte-offre" data-id="${o.id}">
    <strong>${esc(o.intitule)}</strong><div class="sous">${esc(o.entreprise || "")} · ${esc(o.ville || "—")}</div>
    <div class="etiquettes">${o.score_matching != null ? `<span class="etiquette ${teinte(o.score_matching)}">${Math.round(o.score_matching)}/100</span>` : ""}
      ${o.fiche_complete ? '<span class="etiquette vert">fiche</span>' : '<span class="etiquette ambre">extrait</span>'}
      <span class="etiquette">${esc(o.source)}</span>${o.modifiee_le ? '<span class="etiquette ambre">modifiée</span>' : ""}
      ${o.statut_validite === "expiree" ? '<span class="etiquette rouge">expirée</span>' : ""}</div>
    ${o.dossier ? `<div class="note">📁 ${esc(o.dossier)}</div>` : ""}
    <div class="actions"><button class="bouton petit" data-detail>Détail</button>
      ${suivante ? `<button class="bouton petit principal" data-etape="${suivante}">${libelle}</button>` : ""}
      ${etape !== "envoyee" ? '<button class="bouton petit" data-etape="" title="Retirer du pipeline">Écarter</button>' : ""}</div>
  </article>`;
}

// --- Veille : entreprises, critères, lancement -------------------------------------------------
async function vueVeille(v) {
  const [ents, params, etat] = await Promise.all([api("veille/entreprises"), api("parametres"), api("veille/etat")]);
  const c = params.criteres, s = params.seuils;
  v.innerHTML = `<div class="entete"><div><h1>La veille des offres.</h1>
    <p>L'agent Veille interroge le site carrières de ces entreprises toutes les ${params.veille.frequence_minutes} min
    (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee). Sans ATS public, abonne-toi à leurs alertes officielles avec ta boîte Gmail :
    l'agent Recherche les lira.</p></div>
    <div class="actions"><button class="bouton" id="b-sans-gmail">Lancer sans Gmail</button>
      <button class="bouton principal" id="b-lancer">Lancer la veille maintenant</button></div></div>
    <p class="note" id="etat-veille">${etat.en_cours ? "⏳ Passage en cours…" : etat.bilan ? "Dernier passage lancé depuis l'appli terminé." : ""}</p>
    <div class="grille-2">
      <div class="carte"><h3>Entreprises surveillées</h3>
        <form class="filtres" id="form-veille"><input class="champ" name="nom" list="sugg" placeholder="SNCF, Air France, Qonto…" required minlength="2">
          <datalist id="sugg"></datalist><button class="bouton principal">Surveiller</button></form>
        <ul class="liste">${ents.map((x) => `<li data-id="${x.id}"><span><strong>${esc(x.nom_affiche)}</strong>
          ${x.origine === "auto" ? '<span class="etiquette">par défaut</span>' : ""}
          ${x.ats && x.ats !== "aucun" ? `<span class="etiquette vert">${esc(x.ats)}</span>` : x.ats === "aucun" ? '<span class="etiquette ambre">pas d\'ATS public</span>' : ""}
          <div class="sous">${x.derniere_veille ? `vue ${depuis(x.derniere_veille)} · ` : "jamais vue · "}${esc(x.note || "")}
          ${x.offres_pipeline ? ` · ${x.offres_pipeline} dans le pipeline` : ""}</div></span>
          <span class="actions"><label class="case"><input type="checkbox" data-active ${x.active ? "checked" : ""}> active</label>
          <button class="bouton petit" data-retirer>Retirer</button></span></li>`).join("") || '<li class="note">Aucune entreprise : ajoute-en une.</li>'}</ul></div>
      <div class="carte"><h3>Tes critères</h3><form class="formulaire" id="form-criteres">
        <label>Postes recherchés (séparés par des virgules)<textarea class="champ" name="postes" style="min-height:70px">${esc(c.postes.join(", "))}</textarea></label>
        <div class="ligne"><label>Contrats à EXCLURE (jamais proposés ni envoyés)<input class="champ" name="contrats_exclus" placeholder="stage, alternance, freelance, interim" value="${esc(c.contrats_exclus.join(", "))}"></label>
          <label>Niveaux exclus<input class="champ" name="niveaux_exclus" value="${esc(c.niveaux_exclus.join(", "))}"></label></div>
        <label>Lieux acceptés (lieu inconnu = accepté)<textarea class="champ" name="lieux" style="min-height:70px">${esc(c.lieux.join(", "))}</textarea></label>
        <div class="ligne"><label>Seuil de qualification (/100)<input class="champ" type="number" min="50" max="100" name="fabrication" value="${s.fabrication}"></label>
          <label>Dossiers par jour (max)<input class="champ" type="number" min="1" max="50" name="dossiers_par_jour" value="${s.dossiers_par_jour}"></label></div>
        <label>Matchings Gemini par passage (quota)<input class="champ" type="number" min="0" max="100" name="matchings_par_passage" value="${s.matchings_par_passage}"></label>
        <div class="actions"><button class="bouton principal">Enregistrer les critères</button></div></form></div></div>`;
  const liste = (t) => t.split(",").map((x) => x.trim()).filter(Boolean);
  $("#form-criteres").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    await envoyer("parametres", "PUT", {
      criteres: { postes: liste(f.postes), contrats_exclus: liste(f.contrats_exclus), niveaux_exclus: liste(f.niveaux_exclus), lieux: liste(f.lieux) },
      seuils: { fabrication: +f.fabrication, dossiers_par_jour: +f.dossiers_par_jour, matchings_par_passage: +f.matchings_par_passage },
    });
    toast("Critères enregistrés : ils s'appliquent au prochain passage.");
  });
  let attente;
  $("#form-veille [name=nom]").addEventListener("input", (e) => {
    clearTimeout(attente);
    attente = setTimeout(async () => {
      $("#sugg").innerHTML = (await api(`veille/suggestions?q=${encodeURIComponent(e.target.value)}`)).map((n) => `<option value="${esc(n)}">`).join("");
    }, 200);
  });
  $("#form-veille").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await envoyer("veille/entreprises", "POST", { nom: new FormData(e.target).get("nom") }); toast("Entreprise ajoutée à la veille."); vueVeille(v); }
    catch (err) { toast(err.message); }
  });
  v.querySelector(".liste").addEventListener("change", async (e) => {
    if (!e.target.matches("[data-active]")) return;
    await envoyer(`veille/entreprises/${e.target.closest("li").dataset.id}`, "PATCH", { active: e.target.checked });
  });
  v.querySelector(".liste").addEventListener("click", async (e) => {
    if (!e.target.matches("[data-retirer]")) return;
    await api(`veille/entreprises/${e.target.closest("li").dataset.id}`, { method: "DELETE" });
    vueVeille(v);
  });
  const lancer = async (gmail) => {
    const r = await api(`veille/lancer?gmail=${gmail}`, { method: "POST" });
    toast(r.deja_en_cours ? "Un passage est déjà en cours." : "Veille lancée : suis-la dans le salon.");
    suivrePassage();
  };
  $("#b-lancer").onclick = () => lancer(true);
  $("#b-sans-gmail").onclick = () => lancer(false);
}

async function suivrePassage() {
  const etat = await api("veille/etat");
  const zone = $("#etat-veille");
  if (zone) zone.textContent = etat.en_cours ? "⏳ Passage en cours…" : "✅ Passage terminé.";
  if (etat.en_cours) setTimeout(suivrePassage, 4000);
  else { majNotifications(); if (location.hash === "#salon" || location.hash === "#pipeline") router(); }
}

// --- Notifications ---------------------------------------------------------------------------
let dernieresVues = new Set();
async function majNotifications(premiere = false) {
  let rows;
  try { rows = await api("notifications"); } catch { return; }
  const nonLues = rows.filter((n) => !n.lue);
  const b = $("#badge-notif");
  b.hidden = !nonLues.length; b.textContent = nonLues.length;
  if (!premiere && "Notification" in window && Notification.permission === "granted") {
    for (const n of nonLues) if (!dernieresVues.has(n.id) && n.type === "qualifiee") new Notification(n.titre, { body: n.message || "" });
  }
  dernieresVues = new Set(rows.map((n) => n.id));
  majNotifications.rows = rows;
}

$("#cloche").addEventListener("click", async () => {
  const rows = majNotifications.rows || [];
  const corps = ouvrir("Notifications", `${"Notification" in window && Notification.permission === "default"
      ? '<p><button class="bouton" id="b-perm">Activer les notifications du navigateur</button></p>' : ""}
    ${rows.length ? `<ul class="liste">${rows.map((n) => `<li><span>${n.lue ? "" : "● "}<strong>${esc(n.titre)}</strong>
      <div class="sous">${esc(n.message || "")}</div></span><span class="quand note">${depuis(n.cree_le)}</span></li>`).join("")}</ul>`
      : '<p class="vide">Aucune notification.</p>'}
    <div class="actions"><button class="bouton" id="b-lu">Tout marquer comme lu</button></div>`);
  $("#b-lu", corps).onclick = async () => { await api("notifications/lues", { method: "POST" }); fermer(); majNotifications(true); };
  const perm = $("#b-perm", corps);
  if (perm) perm.onclick = async () => { await Notification.requestPermission(); fermer(); };
});

// --- Sur mon téléphone (Tailscale + installation) ------------------------------------------------
async function vueTelephone(v) {
  if (Donnees.EN_LIGNE) {
    const url = (window.EMPLOI_CONFIG.urlPublique || location.href.split("#")[0]);
    v.innerHTML = `<div class="entete"><div><h1>Sur ton téléphone.</h1>
      <p>L'appli est en ligne : ouvre-la sur n'importe quel appareil, connecte-toi avec ton compte, et installe-la.</p></div></div>
      <div class="grille-2"><div class="carte"><h3>Scanne ce QR code</h3>
        <div class="qr"><img src="qr.svg" alt="QR code vers l'appli" width="260" height="260"></div>
        <p><a href="${esc(url)}">${esc(url)}</a></p></div>
      <div class="carte"><h3>Installer l'appli</h3><ol class="etapes">
        <li><strong>Android</strong> : ouvre le lien dans <strong>Chrome</strong>, puis ⋮ → <em>Installer l'application</em>.</li>
        <li><strong>PC</strong> : dans Chrome, icône ⊕ de la barre d'adresse → <em>Installer</em>.</li>
        <li>Les analyses (ATS, matching, dépôt de CV, Excel) sont faites par ton PC : s'il est éteint,
          elles attendent qu'il se rallume. Tout le reste marche partout.</li></ol>
        <p><button class="bouton" id="b-deconnexion">Se déconnecter</button></p></div></div>`;
    $("#b-deconnexion").onclick = async () => { await Donnees.seDeconnecter(); location.reload(); };
    return;
  }
  const a = await api("acces");
  const ts = a.tailscale;
  const fait = (ok) => (ok ? '<span class="etiquette vert">fait</span>' : '<span class="etiquette ambre">à faire</span>');
  v.innerHTML = `<div class="entete"><div><h1>Sur ton téléphone.</h1>
    <p>L'appli reste sur ton PC ; Tailscale crée un réseau privé entre ton PC et ton téléphone (gratuit).
    Rien n'est exposé sur Internet : seuls tes appareils connectés à ton compte Tailscale y ont accès.
    Le PC doit être allumé.</p></div></div>
    <div class="grille-2"><div class="carte">
      ${a.url ? `<h3>Scanne ce QR code avec ton téléphone</h3><div class="qr">${a.qr}</div>
        <p><a href="${esc(a.url)}">${esc(a.url)}</a></p>
        <p class="note">Puis dans Chrome : menu ⋮ → <strong>Installer l'application</strong>. L'icône EMploi apparaît sur ton écran d'accueil.</p>`
      : `<h3>QR code</h3><p class="note">Il apparaîtra ici dès que les étapes ci-contre seront faites.</p>`}
    </div><div class="carte"><h3>Mise en place (une seule fois)</h3><ol class="etapes">
      <li>${fait(ts.installe)} <strong>Installer Tailscale sur le PC</strong> — dans un terminal :
        <pre>curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale set --operator=$USER</pre></li>
      <li>${fait(ts.connecte)} <strong>Se connecter</strong> : <code>tailscale up</code> ouvre une page ; choisis ton compte (Google par ex.).</li>
      <li>${fait(ts.connecte && ts.nom)} <strong>Activer HTTPS</strong> : sur login.tailscale.com → <em>DNS</em>, active <em>MagicDNS</em>
        puis <em>HTTPS Certificates</em> (nécessaire pour installer l'appli et recevoir les notifications).</li>
      <li>${fait(ts.servi)} <strong>Publier l'appli sur ton réseau privé</strong> :
        <pre>tailscale serve --bg 8765</pre>
        <span class="note">« serve » = visible de tes appareils seulement (jamais « funnel », qui l'ouvrirait à Internet).</span></li>
      <li><strong>Sur le téléphone Android</strong> : installe l'appli <em>Tailscale</em> (Play Store), connecte-toi au même compte,
        puis scanne le QR code.</li>
    </ol>${ts.installe ? "" : '<p class="note">Rafraîchis cette page après chaque étape : les badges se mettent à jour.</p>'}</div></div>
    <div class="carte section"><h3>Sur ce PC aussi</h3><p class="note">Dans Chrome, l'icône ⊕ de la barre d'adresse (ou menu ⋮ →
      <em>Installer EMploi_App</em>) crée une appli de bureau qui s'ouvre dans sa propre fenêtre.</p></div>`;
}

// --- Navigation ---------------------------------------------------------------------------------
const VUES = { telephone: vueTelephone, tableau: vueTableau, pipeline: vuePipeline, veille: vueVeille, offres: vueOffres, candidatures: vueCandidatures, reponses: vueReponses,
  documents: vueDocuments, salon: vueSalon, rapport: vueRapport, spontanees: vueSpontanees, auto: vueEnvoisAuto };

async function majBadge() {
  try {
    const n = (await api("reponses?a_confirmer=true")).length;
    const b = $("#badge-reponses");
    b.hidden = !n; b.textContent = n;
  } catch {}
}

async function router() {
  const nom = location.hash.slice(1).split("?")[0] || "tableau";
  if (nom !== "salon") clearInterval(minuterieSalon);
  const vue = VUES[nom] || vueTableau;
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("actif", a.getAttribute("href") === `#${nom}`));
  const v = $("#vue");
  v.onclick = null; v.innerHTML = '<p class="chargement">Chargement…</p>';
  try { await vue(v); }
  catch (err) { v.innerHTML = `<div class="carte"><h2>Impossible de charger cette page</h2><p class="erreur">${esc(err.message)}</p>
    <p class="note">${navigator.onLine ? "Le PC est-il allumé et l'appli lancée ? (python -m app)" : "Pas de réseau : l'appli a besoin de joindre ton PC."}</p></div>`; }
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-telecharger]");
  if (!b) return;
  b.disabled = true;
  if (Donnees.EN_LIGNE) toast("Préparation du fichier…");
  try { await Donnees.telecharger(b.dataset.telecharger); } catch (err) { toast(err.message); }
  b.disabled = false;
});

function vueConnexion() {
  document.body.classList.add("deconnecte");
  $("#vue").innerHTML = `<div class="connexion carte"><h1>EMploi<span>_App</span></h1>
    <p class="note">Connecte-toi avec ton compte pour accéder à ta recherche.</p>
    <form class="formulaire" id="form-connexion">
      <label>Adresse mail<input class="champ" type="email" name="email" autocomplete="username" required></label>
      <label>Mot de passe<input class="champ" type="password" name="mdp" autocomplete="current-password" required></label>
      <p class="erreur" id="err-connexion"></p>
      <button class="bouton principal">Se connecter</button></form></div>`;
  $("#form-connexion").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { await Donnees.seConnecter(f.get("email"), f.get("mdp")); location.reload(); }
    catch { $("#err-connexion").textContent = "Mail ou mot de passe incorrect."; }
  });
}

async function demarrer() {
  if (Donnees.EN_LIGNE && !(await Donnees.session())) { vueConnexion(); return; }
  window.addEventListener("hashchange", router);
  router();
majBadge();
majNotifications(true);
setInterval(majNotifications, 60000);
}
demarrer();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
