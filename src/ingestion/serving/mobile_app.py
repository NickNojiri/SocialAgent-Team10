"""The SpotBot phone app prototype (ADR-0008): one installable page served at /app.

Screens: My Spots (with a restaurants-only filter), add a reel by link or from Android's
share sheet, and an event page per spot with Going / Maybe / Can't, a "find a time" poll
and an invite link for friends who aren't in Discord. It talks to the same admin API as
the bot, with the same tenant tokens:

  member link  /app#g=<guild>&t=<guest token>&v=<token signed for the user>&u=<user id>&n=<name>&r=member
               made by the bot's /app command (ephemeral, only that user sees it)
  guest link   /app#g=<guild>&t=<guest token>&e=<spot id>&r=guest
               made by "Invite friends" (POST /api/invite); RSVP and poll only

The fragment never reaches the server; the page keeps it in localStorage and clears it
from the address bar. Every piece of user text is inserted with textContent, never as
HTML. With no link it offers a demo with example spots that lives only in the browser.

Instagram import: "Connect Instagram" walks the user through Instagram's own "Download
your information" export, then reads the file on the phone (zip parsed in the page with
DecompressionStream) and sends only reel links to POST /api/import. Message text and
friends' names never leave the phone; "Maya sent this" is looked up locally.
"""

MANIFEST = """{
  "name": "SpotBot",
  "short_name": "SpotBot",
  "description": "Turn the reels your friends share into places you actually go.",
  "start_url": "/app",
  "scope": "/app",
  "display": "standalone",
  "background_color": "#0b0a14",
  "theme_color": "#0b0a14",
  "icons": [
    {"src": "/app/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/app/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
  ],
  "share_target": {
    "action": "/app/share",
    "method": "GET",
    "params": {"title": "title", "text": "text", "url": "url"}
  }
}
"""

# App shell only: the page and its icons work offline; API calls always go to the network.
SERVICE_WORKER = """
const SHELL = "spotbot-app-v1";
self.addEventListener("install", e => e.waitUntil(
  caches.open(SHELL).then(c => c.addAll(["/app", "/app/manifest.webmanifest", "/app/icon-192.png"]))
));
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k))))
));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then(r => {
    const copy = r.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); return r;
  }).catch(() => caches.match(e.request)));
});
"""

APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0a14">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/app/manifest.webmanifest">
<link rel="apple-touch-icon" href="/app/icon-192.png">
<title>SpotBot</title>
<style>
  :root { color-scheme: dark; --bg: #0b0a14; --card: #16142a; --card2: #1f1c3a; --line: rgba(255,255,255,.09);
          --ink: #f1f0fb; --ink2: #b4b1cf; --ink3: #807ca1; --pink: #ff3d8b; --violet: #7c5cff; --orange: #ff8a4c;
          --green: #34d399; --amber: #fbbf24; --red: #f87171;
          --grad: linear-gradient(110deg, var(--violet), var(--pink) 55%, var(--orange)); }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; background: var(--bg); color: var(--ink);
               font: 16px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  button, input { font: inherit; color: inherit; }
  button { cursor: pointer; }
  button:focus-visible, input:focus-visible, a:focus-visible { outline: 2px solid var(--pink); outline-offset: 2px; }
  a { color: #ff8fbd; }
  .app { max-width: 520px; margin: 0 auto; min-height: 100vh; padding: env(safe-area-inset-top) 0 calc(84px + env(safe-area-inset-bottom)); }
  header { position: sticky; top: 0; z-index: 3; background: rgba(11,10,20,.92); backdrop-filter: blur(10px);
           padding: 14px 18px 10px; border-bottom: 1px solid var(--line); }
  .brand { display: flex; align-items: center; justify-content: space-between; }
  .brand h1 { margin: 0; font-size: 22px; letter-spacing: -.01em; }
  .brand small { color: var(--ink3); font-size: 13px; }
  .pill { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px;
          border: 1px solid var(--line); background: var(--card); font-size: 14px; white-space: nowrap; }
  .pill[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .chips { display: flex; gap: 8px; overflow-x: auto; padding: 10px 18px 2px; scrollbar-width: none; }
  .chips::-webkit-scrollbar { display: none; }
  main { padding: 12px 18px; }
  .add { display: flex; gap: 8px; margin: 6px 0 14px; }
  .add input { flex: 1; min-width: 0; padding: 12px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--card); }
  .btn { border: 0; border-radius: 14px; padding: 12px 16px; font-weight: 700; background: var(--card2); }
  .btn.primary { background: var(--grad); color: #fff; }
  .btn.small { padding: 8px 12px; border-radius: 12px; font-size: 14px; }
  .status { font-size: 14px; color: var(--ink2); margin: -6px 0 12px; min-height: 1em; }
  .spot { display: flex; gap: 12px; padding: 12px; margin-bottom: 10px; border-radius: 18px; background: var(--card);
          border: 1px solid var(--line); text-align: left; width: 100%; align-items: center; }
  .thumb { width: 64px; height: 64px; border-radius: 14px; flex: none; display: grid; place-items: center; font-size: 30px;
           background-size: cover; background-position: center; }
  .spot b { display: block; font-size: 17px; }
  .spot .meta { color: var(--ink3); font-size: 13px; }
  .spot .who { color: var(--ink2); font-size: 13px; margin-top: 2px; }
  .empty { text-align: center; color: var(--ink2); padding: 40px 10px; }
  .empty b { display: block; color: var(--ink); font-size: 19px; margin-bottom: 6px; }
  .poster { height: 220px; border-radius: 0 0 26px 26px; margin: -12px -18px 16px; display: flex; align-items: flex-end;
            padding: 18px; background-size: cover; background-position: center; position: relative; }
  .poster::after { content: ""; position: absolute; inset: 0; border-radius: inherit;
                   background: linear-gradient(transparent 30%, rgba(11,10,20,.92)); }
  .poster > * { position: relative; z-index: 1; }
  .poster h2 { margin: 0; font-size: 30px; line-height: 1.05; letter-spacing: -.02em; }
  .poster .cat { font-size: 14px; color: var(--ink2); }
  .back { position: absolute; top: 14px; left: 14px; z-index: 2; }
  section.block { background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 14px; margin-bottom: 12px; }
  section.block h3 { margin: 0 0 10px; font-size: 15px; color: var(--ink2); font-weight: 600; }
  .rsvp { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .rsvp button { border: 1px solid var(--line); background: var(--card2); border-radius: 14px; padding: 12px 6px; font-weight: 700; }
  .rsvp button[aria-pressed="true"].going { background: var(--green); color: #052e1c; border-color: var(--green); }
  .rsvp button[aria-pressed="true"].maybe { background: var(--amber); color: #3a2a02; border-color: var(--amber); }
  .rsvp button[aria-pressed="true"].cant { background: var(--red); color: #3b0909; border-color: var(--red); }
  .people { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .person { font-size: 13px; padding: 4px 10px; border-radius: 999px; background: var(--card2); }
  .proof { font-size: 15px; margin-top: 10px; }
  .slot { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-top: 1px solid var(--line); }
  .slot:first-of-type { border-top: 0; }
  .slot input { width: 22px; height: 22px; accent-color: var(--pink); }
  .slot .when { flex: 1; } .slot .n { color: var(--ink3); font-size: 13px; }
  .bar { height: 6px; border-radius: 3px; background: var(--card2); margin-top: 4px; overflow: hidden; }
  .bar i { display: block; height: 100%; background: var(--grad); }
  .lead { color: var(--green); font-size: 12px; font-weight: 700; margin-left: 6px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; }
  .times input { width: 100%; padding: 10px; border-radius: 12px; border: 1px solid var(--line); background: var(--card2); margin-bottom: 8px; }
  nav.tabs { position: fixed; left: 0; right: 0; bottom: 0; z-index: 4; background: rgba(11,10,20,.95);
             border-top: 1px solid var(--line); padding: 8px 0 calc(8px + env(safe-area-inset-bottom)); }
  nav.tabs .in { max-width: 520px; margin: 0 auto; display: grid; grid-template-columns: repeat(4, 1fr); }
  nav.tabs button { background: none; border: 0; color: var(--ink3); font-size: 12px; padding: 4px; }
  nav.tabs button b { display: block; font-size: 22px; font-weight: 400; }
  nav.tabs button[aria-current="page"] { color: var(--ink); }
  .toast { position: fixed; left: 50%; bottom: calc(96px + env(safe-area-inset-bottom)); transform: translateX(-50%);
           background: var(--ink); color: var(--bg); padding: 10px 16px; border-radius: 999px; font-size: 14px; z-index: 9;
           opacity: 0; transition: opacity .2s; pointer-events: none; max-width: 90vw; }
  .toast.on { opacity: 1; }
  .demo { font-size: 12px; color: var(--amber); }
  .field { width: 100%; padding: 12px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--card); margin: 6px 0 12px; }
  .hint { color: var(--ink3); font-size: 13px; }
  .steps { margin: 8px 0 12px; padding-left: 22px; color: var(--ink2); }
  .steps li { margin-bottom: 6px; } .steps b { color: var(--ink); }
  .big { font-size: 34px; font-weight: 800; letter-spacing: -.02em; line-height: 1.1; }
  .check { border: 1px solid var(--amber); }
  .quick { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 6px 0 12px; }
  .sent { color: var(--orange); font-size: 13px; margin-top: 2px; }
  @media (prefers-reduced-motion: reduce) { .toast { transition: none; } }
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="brand"><h1>📍 SpotBot</h1><small id="who"></small></div>
  </header>
  <div id="view"></div>
</div>
<nav class="tabs" id="tabs" aria-label="Sections"><div class="in">
  <button data-tab="spots" aria-current="page"><b>🗺️</b>My Spots</button>
  <button data-tab="plans"><b>📅</b>Plans</button>
  <button data-tab="friends"><b>🤝</b>Friends</button>
  <button data-tab="me"><b>🙂</b>You</button>
</div></nav>
<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
"use strict";
const EMOJI = { food_drink: "🍽️", cafe_dessert: "🍰", nightlife: "🍸", live_music: "🎶",
                market_popup: "🛍️", outdoors: "🏞️", community: "🤝", other: "📍" };
const LABEL = { food_drink: "Food & drink", cafe_dessert: "Cafés & dessert", nightlife: "Nightlife",
                live_music: "Live music", market_popup: "Markets & pop-ups", outdoors: "Outdoors",
                community: "Community", other: "Other" };
const TINT = { food_drink: "#ff8a4c", cafe_dessert: "#f472b6", nightlife: "#7c5cff", live_music: "#22d3ee",
               market_popup: "#a3e635", outdoors: "#34d399", community: "#fbbf24", other: "#94a3b8" };
const FOOD = new Set(["food_drink", "cafe_dessert"]);
const KEY = "spotbot.session.v1";
const SENT_KEY = "spotbot.import.v1";       // reel code -> {f: who sent it, a: when}; stays on this phone
const SEEN_KEY = "spotbot.checked.v1";      // spots already answered in "is this the right place?"
function local(key, value) {
  try { if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null"); localStorage.setItem(key, JSON.stringify(value)); }
  catch (e) { return null; }
}

// ── session: from the link fragment, then localStorage ───────────────────────
function store(get, value) {
  try { if (get) return JSON.parse(localStorage.getItem(KEY) || "null"); localStorage.setItem(KEY, JSON.stringify(value)); }
  catch (e) { return null; }
}
const frag = new URLSearchParams(location.hash.slice(1));
let session = store(true) || {};
const matchRoom = frag.get("m");
// A match link from a friend on the same server keeps your own session.
if (frag.get("t") && !(matchRoom && session.t && session.g === frag.get("g"))) {
  session = { g: frag.get("g") || "", t: frag.get("t"), v: frag.get("v") || "", u: frag.get("u") || "", n: frag.get("n") || session.n || "",
              role: frag.get("r") === "guest" ? "guest" : "member", rid: session.rid };
}
if (!session.rid) session.rid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/[^A-Za-z0-9_-]/g, "");
const sharedLink = frag.get("shared");
const startSpot = frag.get("e");
store(false, session);
if (location.hash) history.replaceState(null, "", location.pathname);
// A link opened while the app is already open (match, invite) only changes the #: reload to use it.
window.addEventListener("hashchange", () => location.reload());
const demo = !session.t;

// ── tiny DOM helpers: data goes in as text nodes only ────────────────────────
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "style") el.setAttribute("style", v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
function toast(msg) { const t = document.getElementById("toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("on"), 2600); }
function when(iso) { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
function mapLink(s) { return "https://www.openstreetmap.org/search?query=" + encodeURIComponent([s.venue, s.area].filter(Boolean).join(", ")); }

// ── data: the real API, or a demo kept in this browser ───────────────────────
const DEMO = [
  { id: "d1", venue: "Chef Tian", category: "food_drink", area: "Irvine, CA", blurb: "Hunan dry pots, a lamb scorpion pot and the viral giant youtiao.", votes: 3, voters: ["maya", "jordan", "sam"] },
  { id: "d2", venue: "Tim Ho Wan", category: "food_drink", area: "Irvine, CA", blurb: "Michelin-starred dim sum. Get the baked BBQ pork buns.", votes: 2, voters: ["maya", "alex"] },
  { id: "d3", venue: "Le Cafe Du Parc", category: "cafe_dessert", area: "Irvine, CA", blurb: "A little French café in the park; mini pancakes.", votes: 1, voters: ["sam"] },
  { id: "d4", venue: "Sunset Jazz Night", category: "live_music", area: "Long Beach, CA", blurb: "A free outdoor show by the harbor.", votes: 4, voters: ["jordan", "alex", "maya", "sam"] },
  { id: "d5", venue: "Katsu Bar", category: "food_drink", area: "Irvine, CA", blurb: "Casual katsu lunch; weekday deals.", votes: 0, voters: [], geo_confidence: 0.3, source_url: "https://www.instagram.com/reel/DEMOkatsu1/" },
].map(s => Object.assign({ theme: "", image: "", source_url: "", rsvps: { going: [], maybe: [], cant: [] }, poll: null }, s));
DEMO[1].source_url = "https://www.instagram.com/reel/DEMOthw001/";
DEMO[0].rsvps = { going: ["maya", "jordan"], maybe: ["sam"], cant: [] };
DEMO[0].poll = { slots: [{ at: nextAt(5, 19), count: 2, names: ["maya", "jordan"] }, { at: nextAt(6, 12), count: 1, names: ["sam"] }, { at: nextAt(6, 19), count: 0, names: [] }], leading: 0 };
function nextAt(dow, hour) { const d = new Date(); d.setDate(d.getDate() + ((dow - d.getDay() + 7) % 7 || 7)); d.setHours(hour, 0, 0, 0);
  const p = n => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(hour)}:00`; }
let demoSpots = DEMO;

// Reads, RSVPs and poll votes use the guest token (t); acting as you uses your own (v).
async function api(path, opts = {}, asMe = false) {
  const token = asMe ? session.v : session.t;
  const res = await fetch(path, Object.assign({}, opts, { headers: Object.assign({ "Content-Type": "application/json", "X-Tenant-Token": token }, opts.headers || {}) }));
  if (!res.ok) { let msg = res.status === 403 ? "This link isn't valid any more. Ask for a new one." : "Something went wrong (" + res.status + ")";
    try { const j = await res.json(); if (j.detail && res.status === 400) msg = j.detail; } catch (e) {} throw new Error(msg); }
  return res.json();
}
async function loadSpots() {
  if (demo) return demoSpots;
  const data = await api("/api/events?guild_id=" + encodeURIComponent(session.g));
  return data.events || [];
}
function myName() { return session.n || ""; }
function askName() {
  if (myName()) return myName();
  const n = (prompt("Your name, so friends know who's coming:") || "").trim().slice(0, 40);
  if (n) { session.n = n; store(false, session); render(); }
  return n;
}
async function setRsvp(spot, state) {
  const name = askName(); if (!name) return;
  if (demo) {
    for (const k of ["going", "maybe", "cant"]) spot.rsvps[k] = spot.rsvps[k].filter(x => x !== name);
    spot.rsvps[state].push(name); return spot;
  }
  const r = await api(`/api/events/${encodeURIComponent(spot.id)}/rsvp`, { method: "POST",
    body: JSON.stringify({ guild_id: session.g, rsvp_id: session.rid, name, state }) });
  spot.rsvps = r.rsvps;
  // Members' answers also move the Discord card's vote, so the two surfaces agree.
  if (session.role === "member" && session.u && state !== "maybe") {
    try { await api(`/api/events/${encodeURIComponent(spot.id)}/vote?guild_id=${encodeURIComponent(session.g)}`, { method: "POST",
      body: JSON.stringify({ delta: state === "going" ? 1 : -1, user_id: session.u, user_name: name }) }, true); } catch (e) {}
  }
  return spot;
}
async function votePoll(spot, picks) {
  const name = askName(); if (!name) return;
  if (demo) {
    spot.poll.slots.forEach((s, i) => { s.names = s.names.filter(x => x !== name); if (picks.includes(i)) s.names.push(name); s.count = s.names.length; });
    const c = spot.poll.slots.map(s => s.count); spot.poll.leading = c.some(Boolean) ? c.indexOf(Math.max(...c)) : null; return spot;
  }
  const r = await api(`/api/events/${encodeURIComponent(spot.id)}/poll/vote`, { method: "POST",
    body: JSON.stringify({ guild_id: session.g, rsvp_id: session.rid, name, picks }) });
  spot.poll = r.poll; return spot;
}
async function startPoll(spot, slots) {
  if (demo) { spot.poll = { slots: slots.map(at => ({ at, count: 0, names: [] })), leading: null }; return spot; }
  const r = await api(`/api/events/${encodeURIComponent(spot.id)}/poll`, { method: "POST", body: JSON.stringify({ guild_id: session.g, user_id: session.u, slots }) }, true);
  spot.poll = r.poll; return spot;
}
async function inviteLink(spot) {
  if (demo) return location.origin + "/app#e=" + spot.id + "&r=guest (demo)";
  const r = await api("/api/invite", { method: "POST", body: JSON.stringify({ guild_id: session.g, user_id: session.u }) }, true);
  const p = new URLSearchParams({ g: session.g, t: r.token, e: spot.id, r: "guest" });
  return location.origin + "/app#" + p.toString();
}
async function capture(link) {
  if (demo) { toast("In the demo, links aren't captured. Open /app from Discord to add real spots."); return; }
  if (!session.u || !session.v) throw new Error("Open your /app link from Discord to add spots.");
  const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ urls: [link], guild_id: session.g, user_id: session.u }) }, true);
  for (let i = 0; i < 90; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const j = await api("/api/jobs/" + encodeURIComponent(job.job_id));   // guest token reads job status
    setStatus(j.stage ? "Reading the reel: " + j.stage + "…" : "Reading the reel…");
    if (j.state === "done") return j;
    if (j.state === "failed") throw new Error("Couldn't read that reel. Try again, or add it in Discord.");
  }
  throw new Error("Still working. It'll show up in My Spots when it's done.");
}
function setStatus(msg) { const s = document.getElementById("status"); if (s) s.textContent = msg; }

// ── Instagram import: read the user's own data download, on this phone ──────
// Only reel links leave the phone. Who sent each one stays in localStorage.
const DYI_URL = "https://accountscenter.instagram.com/info_and_permissions/dyi/";
const REEL_RE = /instagram\.com\/(?:reel|reels|p|tv)\/([A-Za-z0-9_-]{5,40})/g;
const FOOD_RE = /\b(food|foodie|eats?|eating|restaurant|menu|brunch|breakfast|lunch|dinner|ramen|pho|sushi|taco|tacos|birria|pizza|burger|boba|coffee|caf[eé]|bakery|dessert|noodles?|dumplings?|bbq|kbbq|steak|wings|brisket|omakase|dim sum|matcha|croissant|ice cream|gelato|hot ?pot)\b|[🍜🍣🍕🍔🌮🍩🍰☕🧋🍱🥟🍝🍗🥩🍤🥐🍦🍲🌯🥘🍛🍙]/iu;
const HANDLE_RE = /(eats|food|eater|bites|kitchen|munch)/i;      // food accounts' handles: "someeats", "ocfoodie"
// Instagram writes UTF-8 bytes as \u00XX escapes ("Ã©" for "é"); turn them back into text.
function fixText(t) {
  if (typeof t !== "string" || !/[\u0080-ÿ]/.test(t) || /[^\u0000-ÿ]/.test(t)) return t;
  try { return new TextDecoder().decode(Uint8Array.from(t, c => c.charCodeAt(0))); } catch (e) { return t; }
}
function codeOf(url) { const m = /instagram\.com\/(?:reel|reels|p|tv)\/([A-Za-z0-9_-]{5,40})/.exec(url || ""); return m ? m[1] : ""; }

// A zip reader on the platform's own inflater: reads the directory, then only the
// .json/.html entries, one slice at a time (exports can be hundreds of MB).
async function zipEntries(file) {
  const u16 = (b, o) => b[o] | (b[o + 1] << 8), u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
  const bytes = async (a, z) => new Uint8Array(await file.slice(a, z).arrayBuffer());
  const tailStart = Math.max(0, file.size - 65558), tail = await bytes(tailStart, file.size);
  let e = -1; for (let i = tail.length - 22; i >= 0; i--) if (u32(tail, i) === 0x06054b50) { e = i; break; }
  if (e < 0) throw new Error("That file isn't a zip. Pick the file Instagram sent you.");
  const count = u16(tail, e + 10), dirSize = u32(tail, e + 12), dirAt = u32(tail, e + 16);
  if (dirAt === 0xffffffff) throw new Error("This export is too big to read here. Unzip it and pick the JSON files instead.");
  const dir = await bytes(dirAt, dirAt + dirSize), out = [];
  for (let i = 0, p = 0; i < count && u32(dir, p) === 0x02014b50; i++) {
    const method = u16(dir, p + 10), csize = u32(dir, p + 20), nlen = u16(dir, p + 28), xlen = u16(dir, p + 30), clen = u16(dir, p + 32), at = u32(dir, p + 42);
    const name = new TextDecoder().decode(dir.subarray(p + 46, p + 46 + nlen));
    p += 46 + nlen + xlen + clen;
    if (/\.(json|html?)$/i.test(name) && /(messages|saved|inbox)/i.test(name)) out.push({ name, method, csize, at });
  }
  return { async *[Symbol.asyncIterator]() {
    for (const f of out) {
      const head = await bytes(f.at, f.at + 30), start = f.at + 30 + u16(head, 26) + u16(head, 28);
      const blob = file.slice(start, start + f.csize);
      let text;
      if (f.method === 0) text = await blob.text();
      else if (f.method === 8 && typeof DecompressionStream === "function") text = await new Response(blob.stream().pipeThrough(new DecompressionStream("deflate-raw"))).text();
      else throw new Error("This phone's browser can't open zip files. Unzip the export and pick the JSON files instead.");
      yield { name: f.name, text };
    }
  } };
}
// Every reel link in one export file, with who sent it and when if the file says.
function linksIn(name, text, found, people) {
  const saved = /saved/i.test(name);
  if (/\.json$/i.test(name)) {
    let data; try { data = JSON.parse(text); } catch (e) { return; }
    // You are in every conversation; the name in the most threads is yours.
    for (const p of (data && Array.isArray(data.participants) ? data.participants : [])) if (p && p.name) { const n = fixText(p.name); people[n] = (people[n] || 0) + 1; }
    (function walk(v, ctx) {
      if (typeof v === "string") { for (const m of v.matchAll(REEL_RE)) add(m[1], ctx); return; }
      if (!v || typeof v !== "object") return;
      if (!Array.isArray(v)) {
        const share = v.share && typeof v.share === "object" ? v.share : null;
        ctx = { f: v.sender_name ? fixText(v.sender_name) : ctx.f, a: v.timestamp_ms || v.timestamp || ctx.a,
                t: share ? fixText([share.share_text, share.original_content_owner].filter(Boolean).join(" ")) : (v.title ? fixText(String(v.title)) : ctx.t) };
      }
      for (const k in v) walk(v[k], ctx);
    })(data, { f: "", a: 0, t: "" });
  } else for (const m of text.matchAll(REEL_RE)) add(m[1], { f: "", a: 0, t: "" });
  function add(code, ctx) {
    const had = found.get(code);
    if (!had) found.set(code, { f: ctx.f, a: ctx.a, t: ctx.t || "", saved });
    else { if (!had.t && ctx.t) had.t = ctx.t; if (!had.f && ctx.f) had.f = ctx.f; }
  }
}
async function readExport(files, progress) {
  const found = new Map(), people = {};
  for (const file of files) {
    if (/\.zip$/i.test(file.name) || file.type === "application/zip") {
      let n = 0; for await (const entry of await zipEntries(file)) { linksIn(entry.name, entry.text, found, people); if (++n % 20 === 0) progress(`Reading your messages… ${found.size} reels so far`); }
    } else linksIn(file.name, await file.text(), found, people);
  }
  // Your own shares aren't "from a friend". With one conversation there's no telling, so keep names.
  const threads = Object.values(people), me = threads.length && Math.max(...threads) > 1 ? Object.keys(people).sort((x, y) => people[y] - people[x])[0] : "";
  const rows = [...found.entries()].map(([code, r]) => ({ code, from: r.saved || r.f === me ? "" : r.f, at: r.a, saved: r.saved,
    food: r.t ? FOOD_RE.test(r.t) || HANDLE_RE.test(r.t) : null }));
  return { rows, friends: new Set(rows.map(r => r.from).filter(Boolean)).size };
}
function rememberSenders(rows) {
  const map = local(SENT_KEY) || {};
  for (const r of rows) if (!map[r.code]) map[r.code] = { f: r.from || (r.saved ? "You saved" : ""), a: r.at || 0 };
  const keys = Object.keys(map); if (keys.length > 8000) for (const k of keys.slice(0, keys.length - 8000)) delete map[k];
  local(SENT_KEY, map);
}
async function sendImport(rows) {
  rememberSenders(rows);
  if (demo) return { added: rows.length, demo: true };
  if (session.role === "guest") return { added: rows.length, local: true };   // kept on this phone, for matching
  if (!session.u || !session.v) throw new Error("Open your /app link from Discord first, so SpotBot knows whose reels these are.");
  let added = 0, dup = 0;
  for (let i = 0; i < rows.length; i += 2000) {
    const r = await api("/api/import", { method: "POST", body: JSON.stringify({ guild_id: session.g, user_id: session.u,
      urls: rows.slice(i, i + 2000).map(x => "https://www.instagram.com/reel/" + x.code + "/") }) }, true);
    added += r.added; dup += r.duplicates;
  }
  return { added, duplicates: dup };
}
function sentLine(s) {
  const r = (local(SENT_KEY) || {})[codeOf(s.source_url)];
  if (!r || !r.f) return null;
  return r.f === "You saved" ? "🔖 You saved this" : "💬 " + r.f + " sent this";
}

// ── views ────────────────────────────────────────────────────────────────────
let tab = matchRoom ? "friends" : startSpot ? "spot" : session.role === "guest" || sharedLink || local("spotbot.onboarded") ? "spots" : "connect", openId = startSpot, filter = "food", spots = [];
let pasteOpen = !!sharedLink;
document.getElementById("tabs").addEventListener("click", e => {
  const b = e.target.closest("button[data-tab]"); if (!b) return; tab = b.dataset.tab; openId = null; render();
});
function thumb(s, big) {
  const img = /^https:\/\/[^"'()\\\s]+$/.test(s.image || "") ? s.image : "";   // only plain https URLs reach CSS
  const style = img ? `background-image:url("${img}")` : `background:linear-gradient(140deg, ${TINT[s.category] || TINT.other}, #1f1c3a)`;
  return h("div", { class: big ? "poster" : "thumb", style }, big ? null : (img ? "" : EMOJI[s.category] || "📍"));
}
function whoLine(s) {
  const going = (s.rsvps && s.rsvps.going) || [], voters = s.voters || [];
  const names = Array.from(new Set(going.concat(voters)));
  if (!names.length) return "Be the first in";
  return names.length === 1 ? names[0] + " is in" : `${names[0]} + ${names.length - 1} other${names.length > 2 ? "s" : ""} are in`;
}
function spotsView() {
  const wrap = h("main", {});
  if (demo) wrap.append(h("p", { class: "demo" }, "Demo: example spots that live only on this phone. Open /app from Discord to see yours."));
  if (session.role !== "guest") {
    wrap.append(h("div", { class: "quick" },
      h("button", { class: "btn primary", on: { click: () => { tab = "connect"; render(); } } }, "📥 Import from Instagram"),
      h("button", { class: "btn", "aria-expanded": String(pasteOpen), on: { click: () => { pasteOpen = !pasteOpen; render(); } } }, "🔗 Paste a link")));
    const check = checkCard(); if (check) wrap.append(check);
  }
  if (session.role !== "guest" && pasteOpen) {
    const input = h("input", { type: "url", placeholder: "Paste an Instagram or TikTok link", "aria-label": "Reel link", value: sharedLink || "" });
    const go = h("button", { class: "btn primary", on: { click: async () => {
      const link = input.value.trim(); if (!/^https?:\/\//.test(link)) { toast("Paste a reel link first"); return; }
      go.disabled = true; setStatus("Sending to SpotBot…");
      try { await capture(link); input.value = ""; setStatus("Saved. 🎉"); spots = await loadSpots(); render(); }
      catch (err) { setStatus(err.message); } finally { go.disabled = false; }
    } } }, "Add");
    wrap.append(h("div", { class: "add" }, input, go), h("div", { class: "status", id: "status" }, sharedLink ? "Shared from another app. Tap Add to save it." : ""));
  }
  const chips = h("div", { class: "chips", style: "padding:0 0 10px" });
  for (const [key, label] of [["food", "🍽️ Restaurants"], ["all", "📍 Everything"], ["music", "🎶 Music"], ["night", "🍸 Nightlife"]]) {
    chips.append(h("button", { class: "pill", "aria-pressed": String(filter === key), on: { click: () => { filter = key; render(); } } }, label));
  }
  wrap.append(chips);
  const shown = spots.filter(s => filter === "all" || (filter === "food" ? FOOD.has(s.category) : filter === "music" ? s.category === "live_music" : s.category === "nightlife"));
  if (!shown.length) wrap.append(h("div", { class: "empty" }, h("b", {}, "Nothing here yet"), "Share a reel to SpotBot, or import the ones friends already sent you."));
  for (const s of shown) {
    wrap.append(h("button", { class: "spot", on: { click: () => { tab = "spot"; openId = s.id; render(); } } },
      thumb(s), h("div", {}, h("b", {}, s.venue || "Unknown spot"),
        h("div", { class: "meta" }, [LABEL[s.category] || "Spot", s.area].filter(Boolean).join(" · ")),
        sentLine(s) ? h("div", { class: "sent" }, sentLine(s)) : null,
        h("div", { class: "who" }, whoLine(s)))));
  }
  return wrap;
}
// Connect Instagram: share from now on, and bring in the past from your data download.
let importState = null;      // null | {stage: "reading"|"found"|"sending"|"sent"|"error", ...}
function connectView() {
  const wrap = h("main", {}, h("h2", { style: "margin:4px 0 4px" }, "Get your reels in"),
    h("p", { class: "hint", style: "margin:0 0 14px" }, "No copy and paste. Nothing is posted, and SpotBot never logs in to your Instagram."));
  wrap.append(h("section", { class: "block" }, h("h3", {}, "1 · From now on: share to SpotBot"),
    h("p", { style: "margin:0" }, "On any reel tap ", h("b", {}, "Share"), ", then ", h("b", {}, "SpotBot"), ". That's it."),
    h("p", { class: "hint" }, "Android: install this app first (browser menu → Install app). iPhone: coming with the iPhone app; until then use Paste a link."),
    h("p", { class: "hint", style: "margin-bottom:0" }, "Soon: send reels to the SpotBot Instagram account like you would to a friend.")));
  const file = h("input", { type: "file", accept: ".zip,.json,.html,application/zip", multiple: true, style: "display:none", on: { change: () => runImport([...file.files]) } });
  const box = h("section", { class: "block" }, h("h3", {}, "2 · Bring in every reel you've been sent"), file);
  if (!importState || importState.stage === "error") {
    box.append(
      h("ol", { class: "steps" },
        h("li", {}, h("a", { href: DYI_URL, target: "_blank", rel: "noopener" }, "Open Instagram's download page"), " and tap ", h("b", {}, "Create export"), " → ", h("b", {}, "Export to device"), "."),
        h("li", {}, "Customize information: pick only ", h("b", {}, "Messages"), " and ", h("b", {}, "Saved"), "."),
        h("li", {}, "Date range ", h("b", {}, "All time"), ", format ", h("b", {}, "JSON"), ", media quality ", h("b", {}, "Low"), "."),
        h("li", {}, "Instagram emails you when it's ready (minutes to a day or two). Download it, come back here and tap the button below.")),
      h("div", { class: "row" },
        h("a", { class: "btn", href: DYI_URL, target: "_blank", rel: "noopener", style: "text-decoration:none" }, "Open Instagram"),
        h("button", { class: "btn primary", on: { click: () => file.click() } }, "I have the file")),
      importState ? h("p", { class: "status", style: "margin:10px 0 0;color:var(--red)" }, importState.message) : "",
      h("p", { class: "hint", style: "margin-bottom:0" }, "SpotBot reads the file on this phone and keeps only reel links. Your messages and your friends' names never leave it."));
  } else if (importState.stage === "reading") {
    box.append(h("p", { class: "status", id: "status" }, importState.message || "Reading your file…"));
  } else if (importState.stage === "found") {
    const { rows, friends } = importState, food = rows.filter(r => r.food), unknown = rows.filter(r => r.food === null);
    box.append(h("div", { class: "big" }, rows.length.toLocaleString() + " reels"),
      h("p", { style: "margin:4px 0 12px;color:var(--ink2)" }, `from ${friends} friend${friends === 1 ? "" : "s"}` +
        (food.length ? ` · ${food.length} look like food` : "") + (unknown.length && food.length ? ` · ${unknown.length} we can't tell yet` : "")));
    const pick = food.length ? food.concat(unknown) : rows;
    box.append(h("div", { class: "row" },
      h("button", { class: "btn primary", on: { click: () => sendRows(pick) } }, food.length ? `Add the ${pick.length} food reels` : `Add all ${rows.length}`),
      food.length && pick.length < rows.length ? h("button", { class: "btn", on: { click: () => sendRows(rows) } }, `Add all ${rows.length}`) : null),
      h("p", { class: "hint", style: "margin-bottom:0" }, "SpotBot reads them a few at a time in the background, so live shares always go first."));
  } else if (importState.stage === "sending") {
    box.append(h("p", { class: "status" }, "Sending links to SpotBot…"));
  } else if (importState.stage === "sent") {
    box.append(h("div", { class: "big" }, "🎉 " + importState.added.toLocaleString() + " on the way"),
      h("p", { style: "margin:4px 0 8px;color:var(--ink2)" }, importState.demo ? "Demo: nothing was sent. In your real app they'd start showing up in My Spots." :
        importState.local ? "Kept on this phone, ready to compare with your friend." :
        (importState.duplicates ? importState.duplicates + " were already in. " : "") + "New spots show up in My Spots as SpotBot reads them."),
      h("p", { class: "status", id: "importprog", style: "margin:0 0 10px" }, ""),
      h("button", { class: "btn primary", on: { click: () => { tab = match ? "friends" : "spots"; render(); } } }, match ? "Back to the match" : "See My Spots"));
    if (!importState.demo && !importState.local) setTimeout(pollImport, 0);
  }
  wrap.append(box);
  wrap.append(h("button", { class: "btn", style: "width:100%", on: { click: () => { local("spotbot.onboarded", 1); tab = "spots"; render(); } } }, "Skip for now"));
  return wrap;
}
async function runImport(files) {
  if (!files.length) return;
  importState = { stage: "reading", message: "Reading your file…" }; render();
  try {
    const r = await readExport(files, msg => { importState.message = msg; setStatus(msg); });
    if (!r.rows.length) throw new Error("No reels in that file. Make sure you picked Messages and Saved when you made the export.");
    importState = Object.assign({ stage: "found" }, r);
  } catch (err) { importState = { stage: "error", message: err.message }; }
  render();
}
async function sendRows(rows) {
  importState = Object.assign({}, importState, { stage: "sending" }); render();
  try { const r = await sendImport(rows); local("spotbot.onboarded", 1); importState = Object.assign({ stage: "sent" }, r); }
  catch (err) { importState = { stage: "error", message: err.message }; }
  render();
}
async function pollImport() {
  const el = document.getElementById("importprog"); if (!el) return;
  try { const st = await api("/api/import?guild_id=" + encodeURIComponent(session.g));
    el.textContent = st.pending ? `${st.pending.toLocaleString()} waiting · ${st.sent.toLocaleString()} sent to be read` : "All sent. The last few are being read now.";
    if (st.pending) setTimeout(pollImport, 15000);
  } catch (e) {}
}
// "Is this the right place?": spots SpotBot wasn't sure about, one at a time.
function unsure() {
  const seen = local(SEEN_KEY) || {};
  return spots.filter(s => !seen[s.id] && (s.venue === "Unknown" || (typeof s.geo_confidence === "number" && s.geo_confidence < 0.5)));
}
function checkCard() {
  const list = unsure(); if (!list.length || session.role === "guest") return null;
  const s = list[0], done = () => { const seen = local(SEEN_KEY) || {}; seen[s.id] = 1; local(SEEN_KEY, seen); render(); };
  return h("section", { class: "block check" }, h("h3", {}, `Is this the right place?${list.length > 1 ? "  (1 of " + list.length + ")" : ""}`),
    h("div", { style: "display:flex;gap:12px;align-items:center" }, thumb(s), h("div", {}, h("b", {}, s.venue || "Unknown spot"),
      h("div", { class: "meta" }, s.area || "No area yet"), s.source_url ? h("a", { href: s.source_url, target: "_blank", rel: "noopener", class: "hint" }, "▶ Watch the reel") : null)),
    h("div", { class: "rsvp", style: "margin-top:10px" },
      h("button", { on: { click: done } }, "✓ Yes"),
      h("button", { on: { click: async () => { try { await notAPlace(s); spots = spots.filter(x => x.id !== s.id); toast("Moved to Not a place"); done(); } catch (err) { toast(err.message); } } } }, "✗ Not a place"),
      h("button", { on: { click: () => { tab = "spot"; openId = s.id; render(); } } }, "Fix it")));
}
async function notAPlace(s) {
  if (demo) return;
  if (!session.u || !session.v) throw new Error("Open your /app link from Discord to change spots.");
  await api(`/api/events/${encodeURIComponent(s.id)}/not-a-place?guild_id=${encodeURIComponent(session.g)}`, { method: "POST", body: JSON.stringify({ user_id: session.u }) }, true);
}

// ── Match: private set intersection on P-256 (same math as serving/psi.py) ──
// Each phone multiplies hashed items by its own secret; the server only relays points.
const PSI = (() => {
  const P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffffn;
  const B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604bn;
  const N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551n;
  const mod = a => { const r = a % P; return r < 0n ? r + P : r; };
  function pow(b, e) { let r = 1n; b = mod(b); while (e > 0n) { if (e & 1n) r = r * b % P; b = b * b % P; e >>= 1n; } return r; }
  const rhs = x => mod(x * x % P * x - 3n * x + B);
  function sqrt(v) { const y = pow(v, (P + 1n) / 4n); return y * y % P === v ? y : null; }
  function dbl(p) {                                  // Jacobian doubling, a = -3
    if (!p || p[1] === 0n) return null;
    const [X, Y, Z] = p, ZZ = Z * Z % P, YY = Y * Y % P;
    const M = mod(3n * mod(X - ZZ) * (X + ZZ)), S = 4n * X * YY % P;
    const X3 = mod(M * M - 2n * S);
    return [X3, mod(M * mod(S - X3) - 8n * YY * YY), 2n * Y * Z % P];
  }
  function add(p, q) {
    if (!p) return q; if (!q) return p;
    const [X1, Y1, Z1] = p, [X2, Y2, Z2] = q, Z1Z1 = Z1 * Z1 % P, Z2Z2 = Z2 * Z2 % P;
    const U1 = X1 * Z2Z2 % P, U2 = X2 * Z1Z1 % P, S1 = Y1 * Z2 % P * Z2Z2 % P, S2 = Y2 * Z1 % P * Z1Z1 % P;
    const H = mod(U2 - U1), R = mod(S2 - S1);
    if (H === 0n) return R === 0n ? dbl(p) : null;
    const HH = H * H % P, HHH = H * HH % P, V = U1 * HH % P, X3 = mod(R * R - HHH - 2n * V);
    return [X3, mod(R * mod(V - X3) - S1 * HHH), Z1 * Z2 % P * H % P];
  }
  function mul(k, [x, y]) {                          // 4-bit fixed window
    const base = [x, y, 1n], table = [null, base];
    for (let i = 2; i < 16; i++) table.push(add(table[i - 1], base));
    let r = null;
    for (let i = 63; i >= 0; i--) {
      r = dbl(dbl(dbl(dbl(r))));
      const w = Number((k >> BigInt(4 * i)) & 15n);
      if (w) r = add(r, table[w]);
    }
    const zi = pow(r[2], P - 2n), zi2 = zi * zi % P;
    return [r[0] * zi2 % P, r[1] * zi2 % P * zi % P];
  }
  const hex = b => Array.from(new Uint8Array(b), x => x.toString(16).padStart(2, "0")).join("");
  // SHA-256 in plain JS, for pages served over http (crypto.subtle needs https or localhost).
  const KK = Uint32Array.from([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
  function sha256js(bytes) {
    const l = bytes.length, n = ((l + 9 + 63) >> 6) << 6, m = new Uint8Array(n); m.set(bytes); m[l] = 0x80;
    const dv = new DataView(m.buffer); dv.setUint32(n - 4, l * 8); dv.setUint32(n - 8, Math.floor(l / 0x20000000));
    const H = Uint32Array.from([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]), W = new Uint32Array(64);
    const r = (x, k) => (x >>> k) | (x << (32 - k));
    for (let o = 0; o < n; o += 64) {
      for (let i = 0; i < 16; i++) W[i] = dv.getUint32(o + 4 * i);
      for (let i = 16; i < 64; i++) W[i] = (W[i - 16] + (r(W[i - 15], 7) ^ r(W[i - 15], 18) ^ (W[i - 15] >>> 3)) + W[i - 7] + (r(W[i - 2], 17) ^ r(W[i - 2], 19) ^ (W[i - 2] >>> 10))) >>> 0;
      let [a, b, c, d, e, f, g, h] = H;
      for (let i = 0; i < 64; i++) {
        const t1 = (h + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + KK[i] + W[i]) >>> 0, t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      [a, b, c, d, e, f, g, h].forEach((v, i) => { H[i] = (H[i] + v) >>> 0; });
    }
    const out = new Uint8Array(32), ov = new DataView(out.buffer); H.forEach((v, i) => ov.setUint32(4 * i, v)); return out.buffer;
  }
  const sha256 = async bytes => (crypto.subtle ? crypto.subtle.digest("SHA-256", bytes) : sha256js(bytes));
  async function hashToPoint(item) {
    for (let ctr = 0; ; ctr++) {
      const d = await sha256(new TextEncoder().encode(`spotbot-psi-v1|${ctr}|${item}`));
      const x = BigInt("0x" + hex(d)) % P, y = sqrt(rhs(x));
      if (y !== null) return [x, y % 2n === 0n ? y : P - y];
    }
  }
  const encode = ([x, y]) => (y % 2n === 0n ? "02" : "03") + x.toString(16).padStart(64, "0");
  function decode(s) {
    if (!/^0[23][0-9a-f]{64}$/.test(s || "")) throw new Error("bad point");
    const x = BigInt("0x" + s.slice(2)); if (x >= P) throw new Error("bad point");
    let y = sqrt(rhs(x)); if (y === null) throw new Error("bad point");
    if (Number(y % 2n) !== parseInt(s.slice(0, 2), 16) % 2) y = P - y;
    return [x, y];
  }
  function secret() { const b = crypto.getRandomValues(new Uint8Array(32)); return BigInt("0x" + hex(b)) % (N - 1n) + 1n; }
  const tick = () => new Promise(r => setTimeout(r, 0));
  async function blind(items, k, progress) {
    const out = []; for (let i = 0; i < items.length; i++) { out.push(encode(mul(k, await hashToPoint(items[i])))); if (i % 8 === 7) { progress && progress(i + 1); await tick(); } } return out;
  }
  async function reblind(points, k, progress) {
    const out = []; for (let i = 0; i < points.length; i++) { out.push(encode(mul(k, decode(points[i])))); if (i % 8 === 7) { progress && progress(i + 1); await tick(); } } return out;
  }
  const sha256hex = async str => hex(await sha256(new TextEncoder().encode(str)));
  return { hashToPoint, mul, encode, decode, secret, blind, reblind, sha256hex };
})();

function norm(t) { return String(t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(); }
const DEMO_MINE = ["d1", "d2", "d4"];
// What this phone brings to a match: reels in your Instagram import, and places you're in on.
function matchItems() {
  const map = local(SENT_KEY) || {}, me = myName(), seen = new Set(), items = [];
  const push = it => { if (!seen.has(it.key) && items.length < 2000) { seen.add(it.key); items.push(it); } };
  for (const s of spots) {
    const mine = demo ? DEMO_MINE.includes(s.id) : (me && s.rsvps && (s.rsvps.going.includes(me) || s.rsvps.maybe.includes(me))) ||
      (me && (s.voters || []).includes(me)) || !!map[codeOf(s.source_url)];
    if (mine && s.venue && s.venue !== "Unknown") push({ key: "place:" + norm(s.venue) + "|" + norm(s.area), kind: "place", spot: s });
  }
  for (const [code, r] of Object.entries(map)) push({ key: "reel:" + code, kind: "reel", code, from: r.f });
  for (let i = items.length - 1; i > 0; i--) { const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1); [items[i], items[j]] = [items[j], items[i]]; }
  return items;
}
let match = null;   // {role: "a"|"b", stage, room, key, k, items, link, friend, shared, theirs, ms, error}
function matchLink(room) { return location.origin + "/app#" + new URLSearchParams({ g: session.g, t: session.t, m: room, r: "guest" }).toString(); }
async function startMatch() {
  const items = matchItems();
  if (!items.length) { match = { stage: "empty" }; return render(); }
  const t0 = performance.now();
  match = { role: "a", stage: "working", items, msg: "Encrypting your list on this phone…" }; render();
  try {
    if (demo) return await demoMatch(items, t0);
    const k = PSI.secret(), r = await api("/api/match", { method: "POST", body: JSON.stringify({ guild_id: session.g, name: myName() }) });
    const a1 = await PSI.blind(items.map(i => i.key), k, n => setStatus(`Encrypting ${n} of ${items.length}…`));
    await api(`/api/match/${encodeURIComponent(r.room)}/a`, { method: "POST", body: JSON.stringify({ guild_id: session.g, key: r.key, a1 }) });
    Object.assign(match, { stage: "waiting", room: r.room, key: r.key, k, link: matchLink(r.room), ms: performance.now() - t0 }); render();
    pollMatch();
  } catch (err) { match = { stage: "error", msg: err.message }; render(); }
}
async function pollMatch() {
  if (!match || match.role !== "a" || match.stage !== "waiting") return;
  try {
    const v = await api(`/api/match/${encodeURIComponent(match.room)}?guild_id=${encodeURIComponent(session.g)}`);
    if (v.stage === "joined") {
      const t0 = performance.now(); match.stage = "working"; match.msg = (v.name_b || "Your friend") + " joined. Comparing…"; render();
      const b2 = await PSI.reblind(v.b1, match.k);
      await api(`/api/match/${encodeURIComponent(match.room)}/finish`, { method: "POST", body: JSON.stringify({ guild_id: session.g, key: match.key, b2 }) });
      const theirs = new Set(b2);
      Object.assign(match, { stage: "done", friend: v.name_b || "Your friend", theirs: v.b1.length, ms: match.ms + performance.now() - t0,
        shared: match.items.filter((it, i) => theirs.has(v.a2[i])) });
      return render();
    }
  } catch (err) { match = { stage: "error", msg: err.message }; return render(); }
  setTimeout(pollMatch, 2000);
}
async function joinMatch(room) {
  const items = matchItems();
  if (!items.length) { match = { stage: "empty", room }; return render(); }
  const t0 = performance.now();
  match = { role: "b", stage: "working", room, items, msg: "Encrypting your list on this phone…" }; render();
  try {
    const v = await api(`/api/match/${encodeURIComponent(room)}?guild_id=${encodeURIComponent(session.g)}`);
    if (v.stage !== "waiting") throw new Error("This match is already finished. Ask for a new link.");
    const k = PSI.secret();
    const a2 = await PSI.reblind(v.a1, k, n => setStatus(`Working… ${n} of ${v.a1.length + items.length}`));
    const b1 = await PSI.blind(items.map(i => i.key), k, n => setStatus(`Working… ${v.a1.length + n} of ${v.a1.length + items.length}`));
    await api(`/api/match/${encodeURIComponent(room)}/b`, { method: "POST", body: JSON.stringify({ guild_id: session.g, name: myName(), a2, b1 }) });
    Object.assign(match, { stage: "waiting-b", friend: v.name_a || "Your friend", a2, ms: performance.now() - t0, theirs: v.a1.length }); render();
    pollJoined();
  } catch (err) { match = { stage: "error", msg: err.message }; render(); }
}
async function pollJoined() {
  if (!match || match.stage !== "waiting-b") return;
  try {
    const v = await api(`/api/match/${encodeURIComponent(match.room)}?guild_id=${encodeURIComponent(session.g)}`);
    if (v.stage === "done") {
      const theirs = new Set(match.a2);
      match.shared = match.items.filter((it, i) => theirs.has(v.b2[i])); match.stage = "done"; return render();
    }
  } catch (err) { match = { stage: "error", msg: err.message }; return render(); }
  setTimeout(pollJoined, 2000);
}
// Demo: a pretend friend on this phone, running the same math for both sides.
async function demoMatch(items, t0) {
  const friend = ["place:" + norm("Tim Ho Wan") + "|" + norm("Irvine, CA"), "place:" + norm("Sunset Jazz Night") + "|" + norm("Long Beach, CA"),
    "place:" + norm("Le Cafe Du Parc") + "|" + norm("Irvine, CA"), "reel:DEMOthw001", "reel:FOODAAAA02", "reel:FRIENDONLY1", "reel:FRIENDONLY2", "reel:FRIENDONLY3"];
  const a = PSI.secret(), b = PSI.secret();
  const a1 = await PSI.blind(items.map(i => i.key), a), a2 = await PSI.reblind(a1, b);
  const b1 = await PSI.blind(friend, b), b2 = new Set(await PSI.reblind(b1, a));
  Object.assign(match, { stage: "done", friend: "Maya (demo)", theirs: friend.length, ms: performance.now() - t0,
    shared: items.filter((it, i) => b2.has(a2[i])) });
  render();
}
function friendsView() {
  const wrap = h("main", {}, h("h2", { style: "margin:4px 0 4px" }, "What you have in common"),
    h("p", { class: "hint", style: "margin:0 0 14px" }, "Compare with a friend and see only the places and reels you both have. Neither of you sees the rest of the other's list, and SpotBot can't either."));
  const box = h("section", { class: "block" });
  const m = match;
  if (!m || m.stage === "error" || m.stage === "empty") {
    if (m && m.stage === "error") box.append(h("p", { class: "status", style: "color:var(--red)" }, m.msg));
    if (m && m.stage === "empty") box.append(h("p", { class: "status" }, "Nothing to compare yet. Import your Instagram reels, or tap Going on a few spots."),
      h("button", { class: "btn", style: "margin-bottom:10px", on: { click: () => { tab = "connect"; render(); } } }, "📥 Import from Instagram"));
    box.append(h("ol", { class: "steps" }, h("li", {}, "Tap ", h("b", {}, "Start a match"), " and send the link to one friend."),
      h("li", {}, "They open it and tap ", h("b", {}, "Compare"), "."),
      h("li", {}, "You both see what you have in common: places you're both into, and reels in both your Instagram.")));
    box.append(m && m.room && m.role !== "a" ? h("button", { class: "btn primary", on: { click: () => joinMatch(m.room) } }, "Compare")
      : h("button", { class: "btn primary", on: { click: startMatch } }, demo ? "Try it with a demo friend" : "Start a match"));
  } else if (m.stage === "invited") {
    box.append(h("div", { class: "big" }, "🤝 " + m.friend), h("p", { style: "color:var(--ink2)" }, "wants to see what you have in common. Only what you both have is shown to either of you."),
      h("button", { class: "btn primary", on: { click: () => joinMatch(m.room) } }, "Compare"));
  } else if (m.stage === "working") {
    box.append(h("p", { class: "status", id: "status" }, m.msg || "Working…"));
  } else if (m.stage === "waiting") {
    box.append(h("p", { style: "margin-top:0" }, "Send this link to one friend. It works for 30 minutes."),
      h("input", { class: "field", value: m.link, readonly: true, "aria-label": "Match link" }),
      h("button", { class: "btn primary", on: { click: async () => {
        try { if (navigator.share) await navigator.share({ title: "Compare spots on SpotBot", url: m.link }); else { await navigator.clipboard.writeText(m.link); toast("Link copied"); } }
        catch (e) { if (e.name !== "AbortError") toast(e.message); } } } }, "📨 Send the link"),
      h("p", { class: "hint" }, `Your ${m.items.length} items were encrypted on this phone. Waiting for your friend…`));
  } else if (m.stage === "waiting-b") {
    box.append(h("p", { class: "status" }, `Sent. Waiting for ${m.friend} to finish…`));
  } else if (m.stage === "done") {
    const places = m.shared.filter(i => i.kind === "place"), reels = m.shared.filter(i => i.kind === "reel");
    box.append(h("div", { class: "big" }, m.shared.length ? `${m.shared.length} in common` : "Nothing in common yet"),
      h("p", { style: "margin:4px 0 12px;color:var(--ink2)" }, `You and ${m.friend}` + (m.ms ? ` · compared privately in ${(m.ms / 1000).toFixed(1)} s` : "")));
    if (places.length) {
      box.append(h("h3", {}, "Places you're both into"));
      for (const it of places) box.append(h("button", { class: "spot", on: { click: () => { tab = "spot"; openId = it.spot.id; render(); } } }, thumb(it.spot),
        h("div", {}, h("b", {}, it.spot.venue), h("div", { class: "meta" }, [LABEL[it.spot.category] || "Spot", it.spot.area].filter(Boolean).join(" · ")), h("div", { class: "sent" }, "Tap to plan it"))));
    }
    if (reels.length) {
      box.append(h("h3", { style: "margin-top:12px" }, "Reels in both your Instagram"));
      for (const it of reels) box.append(h("div", { class: "slot" }, h("div", { class: "when" }, it.from ? (it.from === "You saved" ? "🔖 You saved this" : "💬 " + it.from + " sent you this") : "🎞️ In both your messages"),
        h("a", { class: "pill", href: "https://www.instagram.com/reel/" + encodeURIComponent(it.code) + "/", target: "_blank", rel: "noopener" }, "▶ Watch")));
    }
    box.append(h("p", { class: "hint" }, `Only these were revealed. You brought ${m.items.length} items and ${m.friend} brought ${m.theirs}; nobody saw the rest.`),
      h("button", { class: "btn", on: { click: () => { match = null; render(); } } }, "New match"));
  }
  wrap.append(box);
  return wrap;
}
function plansView() {
  const wrap = h("main", {}, h("h2", { style: "margin:4px 0 12px" }, "Plans"));
  const mine = spots.filter(s => (s.rsvps && (s.rsvps.going.includes(myName()) || s.rsvps.maybe.includes(myName()))) || (s.poll && s.poll.slots.length));
  if (!mine.length) wrap.append(h("div", { class: "empty" }, h("b", {}, "No plans yet"), "Open a spot and tap Going, or start a “find a time” poll."));
  for (const s of mine) {
    const p = s.poll && s.poll.leading != null ? s.poll.slots[s.poll.leading] : null;
    wrap.append(h("button", { class: "spot", on: { click: () => { tab = "spot"; openId = s.id; render(); } } }, thumb(s),
      h("div", {}, h("b", {}, s.venue), h("div", { class: "meta" }, p ? "Leading: " + when(p.at) : (s.poll ? "Poll open" : "Time TBD")), h("div", { class: "who" }, whoLine(s)))));
  }
  return wrap;
}
function meView() {
  const name = h("input", { class: "field", value: myName(), placeholder: "Your name", "aria-label": "Your name", maxlength: "40" });
  return h("main", {},
    h("h2", { style: "margin:4px 0 6px" }, "You"),
    h("label", { class: "hint" }, "Name friends see on your RSVPs"), name,
    h("button", { class: "btn primary", on: { click: () => { session.n = name.value.trim().slice(0, 40); store(false, session); toast("Saved"); render(); } } }, "Save"),
    h("section", { class: "block", style: "margin-top:16px" }, h("h3", {}, "Connected to"),
      h("p", { style: "margin:0" }, demo ? "Nothing yet: this is the demo." : session.role === "guest" ? "An invite link (RSVP only)." : "Your SpotBot catalog in Discord."),
      h("p", { class: "hint" }, "Get your own link with /app in Discord. On Android, install this app (browser menu → Install) and SpotBot appears in the share sheet when you share a reel from Instagram.")),
    session.role === "guest" ? null : h("button", { class: "btn primary", style: "width:100%;margin-bottom:12px", on: { click: () => { tab = "connect"; render(); } } }, "📥 Connect Instagram"),
    h("section", { class: "block" }, h("h3", {}, "Privacy"),
      h("p", { style: "margin:0" }, "Your link stays on this phone. An Instagram import is read on this phone and only reel links are sent. Reels are read by SpotBot on your group's own computer; nothing goes to outside AI services. /privacy in Discord deletes everything.")),
    demo ? null : h("button", { class: "btn", on: { click: () => { try { localStorage.removeItem(KEY); } catch (e) {} location.reload(); } } }, "Forget this link"));
}
function spotView(s) {
  if (!s) return h("main", {}, h("div", { class: "empty" }, h("b", {}, "That spot isn't here"), "It may have been removed."));
  const wrap = h("main", { style: "position:relative" });
  const poster = thumb(s, true);
  poster.append(h("div", {}, h("div", { class: "cat" }, (EMOJI[s.category] || "📍") + " " + (LABEL[s.category] || "Spot") + (s.area ? " · " + s.area : "")), h("h2", {}, s.venue)));
  // A guest's invite opens one spot; there is no list to go back to.
  if (session.role !== "guest") wrap.append(h("button", { class: "pill back", on: { click: () => { tab = "spots"; openId = null; render(); } } }, "‹ Back"));
  wrap.append(poster);
  if (sentLine(s)) wrap.append(h("p", { class: "sent", style: "font-size:15px;margin:0 0 8px" }, sentLine(s)));
  if (s.blurb || s.theme) wrap.append(h("p", { style: "color:var(--ink2);margin:0 0 12px" }, s.blurb || s.theme));
  wrap.append(h("div", { class: "row", style: "margin-bottom:12px" },
    h("a", { class: "pill", href: mapLink(s), target: "_blank", rel: "noopener" }, "🗺️ Map"),
    s.source_url ? h("a", { class: "pill", href: s.source_url, target: "_blank", rel: "noopener" }, "▶ Reel") : null));
  // RSVP
  const mine = ["going", "maybe", "cant"].find(k => s.rsvps && s.rsvps[k].includes(myName()));
  const rsvp = h("div", { class: "rsvp" });
  for (const [k, label] of [["going", "✅ Going"], ["maybe", "🤔 Maybe"], ["cant", "❌ Can't"]]) {
    rsvp.append(h("button", { class: k, "aria-pressed": String(mine === k), on: { click: async () => {
      try { await setRsvp(s, k); render(); } catch (err) { toast(err.message); } } } }, label));
  }
  const people = h("div", { class: "people" });
  for (const n of (s.rsvps ? s.rsvps.going : [])) people.append(h("span", { class: "person" }, "✅ " + n));
  for (const n of (s.rsvps ? s.rsvps.maybe : [])) people.append(h("span", { class: "person" }, "🤔 " + n));
  wrap.append(h("section", { class: "block" }, h("h3", {}, "Are you in?"), rsvp, h("div", { class: "proof" }, whoLine(s)), people));
  // Poll
  const pb = h("section", { class: "block" }, h("h3", {}, "Find a time"));
  if (s.poll && s.poll.slots.length) {
    const max = Math.max(1, ...s.poll.slots.map(x => x.count));
    const boxes = [];
    s.poll.slots.forEach((slot, i) => {
      const box = h("input", { type: "checkbox", "aria-label": when(slot.at), checked: slot.names.includes(myName()) });
      boxes.push(box);
      pb.append(h("label", { class: "slot" }, box, h("div", { class: "when" }, when(slot.at), s.poll.leading === i ? h("span", { class: "lead" }, "LEADING") : null,
        h("div", { class: "bar" }, h("i", { style: `width:${Math.round(100 * slot.count / max)}%` }))), h("span", { class: "n" }, String(slot.count))));
    });
    pb.append(h("button", { class: "btn primary small", style: "margin-top:8px", on: { click: async () => {
      try { await votePoll(s, boxes.map((b, i) => b.checked ? i : -1).filter(i => i >= 0)); toast("Thanks! Your times are in."); render(); } catch (err) { toast(err.message); } } } }, "Save my times"));
  } else if (session.role !== "guest") {
    const t = [nextAt(5, 19), nextAt(6, 12), nextAt(6, 19)].map(v => h("input", { type: "datetime-local", value: v, "aria-label": "Option" }));
    pb.append(h("p", { class: "hint", style: "margin-top:0" }, "Pick a few options; friends tap the ones that work."), h("div", { class: "times" }, t),
      h("button", { class: "btn primary small", on: { click: async () => {
        try { await startPoll(s, t.map(x => x.value).filter(Boolean)); toast("Poll started"); render(); } catch (err) { toast(err.message); } } } }, "Start the poll"));
  } else pb.append(h("p", { class: "hint", style: "margin:0" }, "No time picked yet."));
  wrap.append(pb);
  if (session.role !== "guest") {
    wrap.append(h("button", { class: "btn primary", style: "width:100%", on: { click: async () => {
      try { const link = await inviteLink(s);
        if (navigator.share && !demo) await navigator.share({ title: s.venue + " on SpotBot", text: "Are you in? " + s.venue, url: link });
        else { await navigator.clipboard.writeText(link); toast("Invite link copied"); }
      } catch (err) { if (err.name !== "AbortError") toast(err.message); } } } }, "📨 Invite friends (no Discord needed)"));
  }
  return wrap;
}
function render() {
  document.getElementById("who").textContent = demo ? "demo" : (myName() || (session.role === "guest" ? "guest" : ""));
  document.getElementById("tabs").style.display = session.role === "guest" ? "none" : "";
  for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-current", b.dataset.tab === tab ? "page" : "false");
  const v = document.getElementById("view"); v.replaceChildren();
  v.append(tab === "spot" ? spotView(spots.find(s => s.id === openId)) : tab === "plans" ? plansView() : tab === "me" ? meView() : tab === "connect" ? connectView() : tab === "friends" ? friendsView() : spotsView());
  window.scrollTo(0, 0);
}
(async () => {
  try { spots = await loadSpots(); } catch (err) { toast(err.message); }
  if (session.role === "guest" && !openId && !matchRoom && spots[0]) { tab = "spot"; openId = spots[0].id; }
  if (matchRoom) {
    match = { stage: "invited", room: matchRoom, friend: "A friend" };
    try { const v = await api(`/api/match/${encodeURIComponent(matchRoom)}?guild_id=${encodeURIComponent(session.g)}`); match.friend = v.name_a || "A friend";
      if (v.stage !== "waiting") match = { stage: "error", msg: "This match is already finished. Ask for a new link." }; }
    catch (err) { match = { stage: "error", msg: err.message }; }
  }
  render();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/app/sw.js", { scope: "/app" }).catch(() => {});
})();
</script>
</body>
</html>
"""
