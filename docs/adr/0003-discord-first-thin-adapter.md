# ADR-0003: Discord is the only client until the loop is proven; the backend stays chat-agnostic

- **Status:** accepted
- **Decided:** 2026-07-01 · **Recorded:** 2026-09-15 · **Owner:** Nick
- **Related:** `docs/PRODUCT_ROADMAP.md` ("Direction decided with Nick"), `docs/ARCHITECTURE.md` §5–6

## Context

The behaviour SpotBot converts already happens in group chats: someone pastes a reel,
everyone says "we should go", nobody does. Capture therefore has to live where the
paste happens. The question was which chat surface to build first, and how much of the
system should know about it. The team had one full-time builder and beginners joining
later, so every additional client would be a second UI to keep working.

## Options considered

1. **iMessage.** Where many of the target friend groups actually chat, but there is no
   bot API — it would need a Mac relay and screen-level automation. Not buildable
   reliably by a student team; fails "capture requires no behaviour change" for anyone
   not on iOS.
2. **Telegram / WhatsApp.** Telegram's bot API is the closest analogue to Discord's
   (cards and buttons map cleanly); WhatsApp's Business API has approval and cost
   overhead. Both are viable *second* clients, not a reason to split the first build.
3. **Web-first.** A website nobody is in when the reel gets shared; capture would need
   copy-paste out of the chat, which is the behaviour change the product exists to
   avoid.
4. **Several clients from day one behind thin adapters.** Correct shape, wrong time:
   each adapter is a real surface with its own failure modes, and the core loop
   (capture → card → votes → event) was still unproven.
5. **Discord gateway bot only, backend as plain HTTP** — chosen.

## Decision

`app/` is the only Discord-specific code, and it is a thin client: a `discord.py`
gateway bot that turns messages and button presses into HTTP calls against the admin
(`:8010`) and recommend (`:8003`) services. Nothing under `src/ingestion/` imports
Discord. New chat surfaces are added as sibling adapters, not by changing the backend.
The bot uses the gateway (persistent WebSocket) rather than HTTP interactions because
**message events only arrive on the gateway** — reel capture is `on_message` — so the
gateway is load-bearing and an inbound interactions endpoint would add nothing.

## Consequences

**Good:** one UI to keep correct while the loop is proven; the backend is testable
without Discord (`test_serving*.py`); a `/share` web page gives non-Discord users a
read-only view without a second client; Telegram is a contained future task.

**Bad:** platform dependence — Discord's Message Content intent, verification rules
and rate limits shape what the product can do; a Discord outage is a SpotBot outage.
`app/` ships as its own Docker image (`COPY . .`, cannot see `src/`), so anything
shared between bot and services is duplicated by hand (`cards.py` mirrors
`discord_format.py`; the tenant-auth work mirrors `tenant_auth.py` the same way).

**Follow-ups:** a Telegram adapter is the first test of the boundary (roadmap Phase 3);
if that lands, promote the shared helpers into a tiny package both images install.

## Evidence

- `bee3173c` (2026-07-01) "docs: add product roadmap (hosted+OSS, UX phases, growth
  playbook)" — `docs/PRODUCT_ROADMAP.md`: "deepen Discord first, then web companion +
  more capture sources, other chat apps later"; "the ingestion/recommend backend is
  already chat-agnostic HTTP — only the `app/` layer is Discord-specific."
- `app/bot.py:69` `discord.Client(intents=…)`, `bot.run(token)`; no port published for
  the `discord-bot` service in `docker-compose.yml`.
- `HANDOFF-tenant-auth.md` §1 (main checkout, uncommitted): why an Ed25519 HTTP
  interactions endpoint was rejected — there is nothing for it to receive.
- `docs/USER_GUIDE.md`, `README.md` command table: the whole product surface today.
