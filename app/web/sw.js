// Service worker : l'interface (HTML, CSS, JS, polices, icônes) est gardée en cache pour
// s'ouvrir instantanément, même sans réseau. Les données (/api/) ne sont JAMAIS mises en cache :
// elles viennent toujours fraîches du serveur, sinon l'appli affiche qu'elle est hors ligne.
const VERSION = "emploi-v2";
// Chemins relatifs : l'appli marche à la racine (PC) comme sous /emploi/ (GitHub Pages).
const INTERFACE = ["./", "index.html", "style.css", "app.js", "fonts.css", "manifest.webmanifest",
  "donnees.js", "config.js", "icones/icone-192.png", "icones/icone-512.png", "icones/favicon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(INTERFACE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((cles) => Promise.all(cles.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.includes("/api/")) return;
  // Réseau d'abord (nouvelle version de l'interface dès qu'elle existe), cache en secours.
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok) { const copie = r.clone(); caches.open(VERSION).then((c) => c.put(e.request, copie)); }
    return r;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match("index.html"))));
});
