// SpotBot prototype presentation: 16:9, dark title/closing, light content slides.
const pptxgen = require("pptxgenjs");
const path = require("path");
const IMG = f => path.join(__dirname, "img", f);   // docs/presentation/img

const C = { ink: "15131F", ink2: "4A475C", muted: "7A7790", bg: "FAFAFA", dark: "0B0A14", card: "F0EEF6",
            pink: "E8336F", violet: "6C4CF0", orange: "F07A3C", white: "FFFFFF", darkInk: "F1F0FB", darkMuted: "B4B1CF" };
const HEAD = "Montserrat", BODY = "Arial";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";              // 10 x 5.625 in
pres.title = "SpotBot Prototype";
pres.author = "Team 10: Nick Nojiri, Blessie Gabat, Valeria Mojarro, Kimberly Reyes";
pres.theme = { headFontFace: HEAD, bodyFontFace: BODY };

pres.defineSlideMaster({ title: "DARK", background: { color: C.dark },
  objects: [{ placeholder: { options: { name: "title", type: "title", x: 0.7, y: 1.6, w: 8.6, h: 1.2, fontFace: HEAD, fontSize: 44, bold: true, color: C.white, margin: 0, align: "left" }, text: "" } },
            { placeholder: { options: { name: "body", type: "body", x: 0.7, y: 2.85, w: 8.6, h: 1.4, fontFace: BODY, fontSize: 18, color: C.darkMuted, margin: 0, valign: "top", align: "left" }, text: "" } }] });
pres.defineSlideMaster({ title: "CONTENT", background: { color: C.bg },
  objects: [{ placeholder: { options: { name: "title", type: "title", x: 0.5, y: 0.3, w: 9, h: 0.85, fontFace: HEAD, fontSize: 24, bold: true, color: C.ink, margin: 0, valign: "top", align: "left" }, text: "" } },
            { text: { text: "SpotBot · Team 10", options: { x: 0.5, y: 5.22, w: 4, h: 0.25, fontFace: BODY, fontSize: 9, color: C.muted, margin: 0 } } }],
  slideNumber: { x: 9.0, y: 5.22, w: 0.5, h: 0.25, fontFace: BODY, fontSize: 9, color: C.muted, align: "right" } });

function content(title, notes) {
  const s = pres.addSlide({ masterName: "CONTENT" });
  s.addText(title, { placeholder: "title" });
  s.addNotes(notes);
  return s;
}
function bullets(s, items, o) {
  s.addText(items.map((t, i) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: i < items.length - 1, paraSpaceAfter: 8 } })),
    Object.assign({ fontFace: BODY, fontSize: 15, color: C.ink2, valign: "top", margin: 0, isTextBox: true }, o));
}
function image(s, file, x, y, w, h, name) {
  s.addImage({ path: IMG(file), x, y, w, h, objectName: name, altText: name, rounding: false });
}
function stat(s, big, label, x, y, w, color) {
  s.addText([{ text: big, options: { fontFace: HEAD, fontSize: 30, bold: true, color: color || C.pink, breakLine: true } },
             { text: label, options: { fontFace: BODY, fontSize: 12, color: C.ink2 } }],
    { x, y, w, h: 1.0, margin: 0, valign: "top", isTextBox: true, objectName: "stat " + label });
}

// 1 · Title
{
  const s = pres.addSlide({ masterName: "DARK" });
  s.addText("SpotBot", { placeholder: "title" });
  s.addText([{ text: "Prototype: the reels your friends send, turned into plans", options: { breakLine: true, color: C.darkInk, fontSize: 20 } },
             { text: " ", options: { breakLine: true, fontSize: 8 } },
             { text: "CECS 491A · Team 10 · November 2026", options: { breakLine: true } },
             { text: "Nick Nojiri · Blessie Gabat · Valeria Mojarro · Kimberly Reyes" }], { placeholder: "body" });
  s.addNotes([
    "- hey we're team 10, this is spotbot",
    "- quick names: me (platform + the app), blessie (capture), valeria (accuracy), kim (security)",
    "- plan: what it does, horizontal prototype, vertical prototype, the new stuff, then a live demo",
  ].join("\n"));
}

// 2 · Problem
{
  const s = content("Friends send reels all day, and almost none turn into plans", [
    "- everybody does this: friend sends a food reel, \"we have to go\", never happens",
    "- the reel just gets buried in the dms",
    "- spotbot grabs it and turns it into something the group can vote on and put on the calendar",
  ].join("\n"));
  const col = (x, head, steps, color) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.4, w: 4.3, h: 3.5, fill: { color: C.card }, line: { color: C.card }, rectRadius: 0.12, objectName: head + " card" });
    s.addText(head, { x: x + 0.3, y: 1.6, w: 3.7, h: 0.4, fontFace: HEAD, fontSize: 16, bold: true, color, margin: 0, isTextBox: true });
    s.addText(steps.map((t, i) => ({ text: (i + 1) + ".  " + t, options: { breakLine: i < steps.length - 1, paraSpaceAfter: 10 } })),
      { x: x + 0.3, y: 2.15, w: 3.7, h: 2.5, fontFace: BODY, fontSize: 15, color: C.ink2, margin: 0, valign: "top", isTextBox: true });
  };
  col(0.5, "Today", ["A friend sends a reel", "Everyone says \"we should go\"", "It's buried under the next 50 messages", "Nobody goes"], C.muted);
  col(5.2, "With SpotBot", ["Paste or share the reel", "SpotBot posts a card with the place and a map", "The group votes", "At 3 votes it's a Discord event"], C.pink);
}

// 3 · How it works
{
  const s = content("One reel becomes a card the group can vote on", [
    "- you paste a reel in the channel",
    "- bot reads the caption, the audio, and text on screen, and works out the place",
    "- card shows up with a map link and buttons",
    "- 3 people hit want to go and it makes a discord event",
    "- next day it asks if you actually went",
  ].join("\n"));
  bullets(s, ["Reads the caption, the audio (Whisper) and on-screen text", "Works out the venue, city, category and time",
              "Posts a card: map link, Want to go, Not for me, Edit", "3 votes: a Discord Scheduled Event, then \"did you go?\" the next day"],
    { x: 0.5, y: 1.4, w: 3.6, h: 3.6 });
  image(s, "spot_card.jpg", 4.4, 1.35, 5.1, 2.62, "Spot card screenshot");
  image(s, "lock_in.jpg", 4.4, 4.05, 2.4, 1.12, "Lock in screenshot");
  s.addText("The card, and lock-in at 3 votes", { x: 6.95, y: 4.3, w: 2.55, h: 0.6, fontFace: BODY, fontSize: 11, color: C.muted, margin: 0, isTextBox: true });
}

// 4 · Tools
{
  const s = content("Built with free tools that run on the group's own computer", [
    "- everything runs on our machine, no paid apis",
    "- rule since day one: post text never gets sent to openai or anyone",
    "- 1,325 tests run on every push",
  ].join("\n"));
  const rows = [["Discord + discord.py", "The bot: cards, buttons, slash commands, events"],
    ["Playwright + Chromium", "Opens the reel page like a browser would"],
    ["Whisper + OCR", "Reads the audio and the text on screen"],
    ["Ollama (local AI)", "Search and summaries, no cloud"],
    ["ChromaDB", "The spot catalog, one per Discord server"],
    ["FastAPI", "The API, the web pages and the phone app"],
    ["Cloudflare tunnel", "A phone link that works from any network"],
    ["GitHub Actions", "1,325 automated tests on every push"]];
  const hdr = [{ text: "Tool", options: { bold: true, color: C.ink } }, { text: "What we use it for", options: { bold: true, color: C.ink } }];
  s.addTable([hdr, ...rows.map(([a, b]) => [{ text: a, options: { bold: true, color: C.ink } }, { text: b, options: { color: C.ink2 } }])],
    { x: 0.5, y: 1.3, w: 9, colW: [2.8, 6.2], fontFace: BODY, fontSize: 13, rowH: 0.42, border: { type: "none" },
      fill: { color: C.bg }, margin: [4, 8, 4, 0], objectName: "tools table" });
}

// 5 · Horizontal prototype
{
  const s = content("Horizontal prototype: every feature has a screen", [
    "- horizontal = every feature, but shallow",
    "- we drew a screen for each one so we'd catch anything missing",
    "- these come from the bot's real code, so they match what it actually posts",
    "- doing this found 5 problems, they're at the end",
  ].join("\n"));
  const cells = [["spot_card.jpg", "Spot card"], ["lock_in.jpg", "Lock it in"], ["plan.jpg", "/plan from the chat"], ["failure.jpg", "When a capture fails"]];
  cells.forEach(([f, label], i) => {
    const x = 0.5 + (i % 2) * 4.6, y = 1.25 + Math.floor(i / 2) * 1.95;
    image(s, f, x, y, 4.3, 4.3 * (f === "spot_card.jpg" ? 668 : f === "plan.jpg" ? 647 : 605) / 1300 > 1.6 ? 1.6 : 4.3 * (f === "spot_card.jpg" ? 668 : f === "plan.jpg" ? 647 : 605) / 1300, label);
    s.addText(label, { x, y: y + 1.62, w: 4.3, h: 0.25, fontFace: BODY, fontSize: 11, color: C.muted, margin: 0, isTextBox: true });
  });
}

// 6 · Phone app import
{
  const s = content("The phone app brings in reels without copy and paste", [
    "- people plan on their phones so we made a phone app",
    "- you download your instagram data once, the app reads it right on your phone",
    "- only the reel links get sent, your messages never leave the phone",
    "- it keeps the food ones and shows who sent it",
  ].join("\n"));
  image(s, "p1.jpg", 0.5, 1.2, 5.6, 3.91, "Phone app: connect, import, my spots");
  bullets(s, ["Instagram's own data download, read on the phone", "Only reel links go to SpotBot", "Keeps the food reels: \"4 look like food\"", "Cards say who sent it: \"Maya sent this\""],
    { x: 6.4, y: 1.4, w: 3.1, h: 3.6, fontSize: 14 });
}

// 7 · Event page
{
  const s = content("Friends outside Discord can RSVP and pick a time", [
    "- took this from partiful: going / maybe / can't",
    "- find a time poll instead of arguing in the chat",
    "- invite link works for people who don't have discord",
  ].join("\n"));
  image(s, "p2.jpg", 0.5, 1.2, 5.6, 3.91, "Phone app: event page, time poll, plans");
  bullets(s, ["Going, Maybe, Can't, and who's coming", "\"Find a time\" poll with the leading time", "Invite link: no account needed", "Going in the app also counts as your Discord vote"],
    { x: 6.4, y: 1.4, w: 3.1, h: 3.6, fontSize: 14 });
}

// 8 · Vertical prototype
{
  const s = content("Vertical prototype: one real reel through every layer", [
    "- vertical = one feature all the way down the stack",
    "- used a real reel, casa loma birria pop-up in long beach",
    "- browser grabs the page, we pull the caption, rules pick venue, city, time",
    "- saved in chroma, the api serves it with a token check, bot turns it into the card",
    "- that card on the right is what it actually made",
  ].join("\n"));
  const steps = [["Capture", "Playwright opens the reel (1.5 s)"], ["Extract", "Caption, hashtags, location"], ["Rules", "Venue, category, city, time"],
                 ["Store", "ChromaDB, one per server"], ["Serve", "FastAPI, token checked"], ["Card", "Discord embed + buttons"]];
  steps.forEach(([h, d], i) => {
    const y = 1.25 + i * 0.64;
    s.addShape(pres.shapes.OVAL, { x: 0.5, y: y + 0.05, w: 0.42, h: 0.42, fill: { color: i === 5 ? C.pink : C.violet }, line: { color: i === 5 ? C.pink : C.violet }, objectName: "step " + h });
    s.addText(String(i + 1), { x: 0.5, y: y + 0.05, w: 0.42, h: 0.42, align: "center", valign: "middle", fontFace: HEAD, fontSize: 13, bold: true, color: C.white, margin: 0, isTextBox: true });
    s.addText([{ text: h + "  ", options: { bold: true, color: C.ink } }, { text: d, options: { color: C.ink2 } }],
      { x: 1.05, y, w: 3.3, h: 0.52, fontFace: BODY, fontSize: 13, valign: "middle", margin: 0, isTextBox: true });
  });
  image(s, "real_card.jpg", 4.6, 1.5, 4.9, 2.25, "The card the run produced");
  s.addText("The real card from the run: Casa Loma, Long Beach, \"this Friday 8pm\"", { x: 4.6, y: 3.85, w: 4.9, h: 0.5, fontFace: BODY, fontSize: 11, color: C.muted, margin: 0, isTextBox: true });
}

// 9 · Match
{
  const s = content("Match shows what two friends have in common, and nothing else", [
    "- this one's my favorite",
    "- two people compare their lists and only see what they both have",
    "- it's private set intersection, kind of like how google checks leaked passwords without seeing yours",
    "- the server just passes scrambled codes, it can't read them",
    "- real run: nick and maya, 5 in common, the private stuff never showed up",
  ].join("\n"));
  image(s, "match.jpg", 0.5, 1.2, 6.2, 3.42, "Real two-phone match run");
  stat(s, "5", "in common: 1 place and 4 reels, the same on both phones", 7.0, 1.3, 2.5);
  stat(s, "0.1 s", "to compare on each phone", 7.0, 2.4, 2.5, C.violet);
  s.addText("The server only ever held scrambled codes, never a reel or a place.", { x: 7.0, y: 3.5, w: 2.5, h: 1.0, fontFace: BODY, fontSize: 12, color: C.ink2, margin: 0, valign: "top", isTextBox: true });
}

// 10 · Control + chaos
{
  const s = content("One desktop shortcut runs everything, and it heals itself", [
    "- double click, it starts everything and gives a link phones can open anywhere",
    "- shows what's broken and how to fix it",
    "- chaos monkey kills random parts on purpose to prove it comes back",
    "- 4 kills, all back in about 2 seconds",
  ].join("\n"));
  image(s, "control.jpg", 0.5, 1.2, 6.2, 3.39, "SpotBot Control window");
  stat(s, "4 of 4", "parts the chaos monkey killed came back on their own", 7.0, 1.3, 2.5);
  stat(s, "2 to 2.5 s", "to recover each time", 7.0, 2.5, 2.5, C.violet);
  s.addText("Phone link through a free Cloudflare tunnel. A gatekeeper lets only the app and token-checked calls in.", { x: 7.0, y: 3.6, w: 2.5, h: 1.2, fontFace: BODY, fontSize: 12, color: C.ink2, margin: 0, valign: "top", isTextBox: true });
}

// 11 · Findings
{
  const s = content("Building the prototype turned up five fixes", [
    "- katsu bar got tagged nightlife because it has \"bar\" in the name lol",
    "- admin page still has our old project name",
    "- manual spots show a source link with nothing behind it",
    "- some descriptions are the whole caption, hashtags and all",
    "- /events results don't have a map link yet",
    "- all of these are on the backlog",
  ].join("\n"));
  const rows = [["\"Katsu Bar\" tagged nightlife (\"bar\" in the name)", "Weigh the description over the name"],
    ["Admin page still titled with our old project name", "Rename it to SpotBot"],
    ["Hand-added spots show an empty \"source\" link", "Hide it when there's no source"],
    ["Some descriptions are the whole caption", "Use the summary, or the first sentence"],
    ["/events results have no map link", "Add the same \"Where\" link as cards"]];
  s.addTable([[{ text: "What we saw", options: { bold: true, color: C.ink } }, { text: "Fix", options: { bold: true, color: C.ink } }],
              ...rows.map(([a, b]) => [{ text: a, options: { color: C.ink } }, { text: b, options: { color: C.ink2 } }])],
    { x: 0.5, y: 1.3, w: 9, colW: [5.2, 3.8], fontFace: BODY, fontSize: 14, rowH: 0.55, border: { type: "none" }, fill: { color: C.bg }, margin: [4, 8, 4, 0], objectName: "findings table" });
}

// 12 · Team
{
  const s = content("Who built what", [
    "- quick credit where it's due",
    "- (if they're presenting, hand off here: each person takes 20 seconds on their part)",
  ].join("\n"));
  const team = [["Nick Nojiri", "Platform + Experience", "Bot, API and job queue, web pages, phone app, SpotBot Control, Match"],
    ["Blessie Gabat", "A · Capture", "Logged-out capture with the embed fallback. Reliability check: 34 of 50 random reels (68%)"],
    ["Valeria Mojarro", "B · Accuracy", "The labeled reels and the accuracy metrics, with confidence intervals"],
    ["Kimberly Reyes", "D · Security", "Signed tokens on every call. Attack suite: 88 attacks, 80 refused"]];
  team.forEach(([n, t, d], i) => {
    const x = 0.5 + i * 2.3;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.35, w: 2.1, h: 3.5, fill: { color: C.card }, line: { color: C.card }, rectRadius: 0.1, objectName: n + " card" });
    s.addText([{ text: n, options: { fontFace: HEAD, bold: true, fontSize: 15, color: C.ink, breakLine: true } },
               { text: t, options: { fontSize: 12, color: [C.pink, C.violet, C.orange, C.pink][i], bold: true, breakLine: true } },
               { text: " ", options: { fontSize: 6, breakLine: true } },
               { text: d, options: { fontSize: 12, color: C.ink2 } }],
      { x: x + 0.18, y: 1.55, w: 1.75, h: 3.1, fontFace: BODY, margin: 0, valign: "top", isTextBox: true });
  });
}

// 13 · Next steps
{
  const s = content("Next steps", [
    "- fix the 5 things from earlier",
    "- usability test with 5 people who aren't us",
    "- instagram dm account so you can just send reels to spotbot",
    "- iphone app with expo so ios gets the share button too",
    "- always-on server so it doesn't depend on my desktop",
  ].join("\n"));
  bullets(s, ["Fix the five findings and retake the screenshots", "Usability test with 5 people: save a reel, find a spot a friend sent, plan dinner",
              "Instagram DM account: send a reel to SpotBot like you would to a friend", "iPhone app (Expo) so iOS gets the share button",
              "Always-on server so SpotBot doesn't depend on one desktop"], { x: 0.5, y: 1.35, w: 9, h: 3.6, fontSize: 16 });
}

// 14 · Demo
{
  const s = pres.addSlide({ masterName: "DARK" });
  s.addText("Live demo", { placeholder: "title" });
  s.addText("Then questions", { placeholder: "body" });
  s.addNotes([
    "- demo order:",
    "- 1. paste a reel in discord, card shows up",
    "- 2. vote it, show lock in",
    "- 3. /app, open it on my phone",
    "- 4. run a match with someone's phone",
    "- 5. open spotbot control, hit kill one now, show it comes back",
    "- backup if wifi is bad: phone hotspot, or the demo mode in the app",
  ].join("\n"));
}

pres.writeFile({ fileName: path.join(__dirname, "SpotBot_Prototype.pptx") }).then(f => console.log("wrote", f));
