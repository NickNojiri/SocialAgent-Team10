# Track D — Cybersecurity

**Specialist:** [Member 5 full name] · **Directed by:** Nick (Architecture & Platform)
**Points:** 420 across 5 features · **Demo-day claim:** *"I attacked our own system on a
schedule, closed what got through, and kept every release reviewed."*

You own security as **evidence, not assertion**. Everyone else owns one security feature
inside their area; you own the thing that checks whether those features actually hold —
a repeatable attack run against our own local copy, and the reviews and monitoring that
keep it honest as the system changes.

**Scope rule, and it is not negotiable:** you attack **our** system, on a local copy or
our own staging instance, with Nick's sign-off. Never a third party, never Instagram or
TikTok's infrastructure, never another student's project. Findings go in the repo, not
anywhere public, until Nick says otherwise.

---

## 1. Handoff — what already exists

| File | What it is |
|---|---|
| `docs/THREAT_MODEL.md` | The current model: T1 mention injection (**fixed**), T2 tenant authorization (**built**), T3 SSRF (**open** — Track A, Phase 1), T4 prompt injection (Track B, Phase 4), T6 secrets/privacy (Nick + Track C) |
| `src/ingestion/serving/tenant_auth.py`, `app/tenant_auth.py` | HMAC-SHA256 tenant tokens over `v1\n{scope}\n{guild_id}\n{user_id}`; scopes `rw` / `r`; fail-closed 503 if no key |
| `scripts/ensure_signing_key.py` | Creates/loads `SPOTBOT_SIGNING_KEY` |
| `scripts/bench_admin_authz.py` | The existing forged-request benchmark — **32/32 forged rejected, 15/15 authentic accepted, 0/32 with the gate off**. This is the seed of your #12. |
| `test_admin_authz.py`, `test_signing_key_setup.py` | Existing security coverage |
| `docs/ARCHITECTURE.md`, `docs/adr/0001-chromadb-per-guild-collections.md` | Trust boundaries: one Chroma collection per guild |

**What's already true:** every guild-scoped call on the admin (:8010) and serving (:8003)
services requires an `X-Tenant-Token`. Share links mint read-only tokens. The bot sends
no mentions (`AllowedMentions.none()`).

**What's openly weak, and yours to close:**
- **Tokens never expire** and cannot be revoked individually — whoever copies one keeps
  access until the whole signing key is rotated. That's your #27.
- No dependency scanning, no secret scanning in CI. That's your #28.
- Nothing logs or alerts on refused authorizations, rate-limit hits, or blocked hosts —
  we can't see an attack even in hindsight. That's your #29.

**Traps:**
- The signing key must never be committed, printed, or logged — not even a prefix. If you
  ever suspect it leaked, tell Nick immediately; rotation invalidates outstanding share
  links, which is a user-visible event.
- Attack scripts must be obviously scoped to localhost/our staging host, with the target
  host as an explicit argument and a refusal to run against anything else.
- Test fixtures need fake keys, never real ones.

---

## 2. Your features

| # | Feature | Need | Pts | Phase |
|---|---|---|---|---|
| 12 | Security test suite + simulated attack ★ | Must | 100 | 1 |
| 27 | Authentication & session hardening ★ | Should | 100 | 2 |
| 13 | Threat model & security review | Must | 60 | 3 |
| 28 | Dependency & supply-chain scanning | Should | 60 | 3 |
| 29 | Abuse & intrusion monitoring ★ | Should | 100 | 4 |

★ unique to SpotBot

---

## 3. To-do list

### Phase 1 — Sep 15 – Oct 3 (100 pts)

**#12 Security test suite + simulated attack ★ (100)**
- [ ] Take `scripts/bench_admin_authz.py` as the model and build a full attack runner
      against a **local** instance.
- [ ] Attack classes, one module each:
      forged/tampered tenant token · a token for server X used against server Y ·
      faked votes on someone else's card · an injected caption ("ignore previous
      instructions…") · a link pointing at an internal address (coordinate with Track A's
      #3) · a request with no token at all.
- [ ] Enumerate **every** protected endpoint and assert each one is covered — a new
      endpoint with no attack case should fail the suite.
- [ ] Report: attacks refused vs let through, per class.
- [ ] Wire it into CI so reopening a hole fails the build.
- [ ] **Done when:** one command prints "N attacks, M refused, K got through," the list of
      K is empty or written down with owners, and CI runs it.

### Phase 2 — Oct 6 – Oct 24 (100 pts)

**#27 Authentication & session hardening ★ (100)**
- [ ] Add an expiry to the signed tenant tokens, and a token id so one can be revoked
      without rotating the master key.
- [ ] Revocation list (or key-version) check on every verify; keep it fail-closed.
- [ ] Defend against replay of a captured token — expiry plus a revocation path at
      minimum; document what remains possible.
- [ ] Coordinate with Nick's #10 (secrets & key rotation) — same key, two features.
- [ ] Tests: an expired token is refused; a revoked token is refused immediately; a valid
      one still works; share links still work across a rotation or are re-issued.
- [ ] **Done when:** those tests pass and the attack suite gains a replay case that fails
      to get in.

### Phase 3 — Oct 27 – Nov 14 (120 pts)

**#13 Threat model & security review (60)**
- [ ] Bring `docs/THREAT_MODEL.md` up to date with everything that landed in Phases 1–2;
      mark T3/T4 closed only when the tests prove it.
- [ ] Define the release review: a short checklist, run before the staging release, with
      findings tracked to closure (issue per finding, owner, date).
- [ ] **Done when:** the staging release (Nick's #11) is signed off by a written review
      and every finding is closed or explicitly accepted with a reason.

**#28 Dependency & supply-chain scanning (60)**
- [ ] Automated dependency audit in CI that fails the build on a known vulnerability.
- [ ] Pin versions; add a secret scanner so a key can never be committed.
- [ ] **Done when:** CI is green, and a deliberate test commit containing a fake secret is
      caught by the scanner before merge.

### Phase 4 — Nov 17 – Dec 11 (100 pts)

**#29 Abuse & intrusion monitoring ★ (100)**
- [ ] Log security events: refused authorizations, rate-limit hits, blocked hosts, unusual
      capture volume. Event text only — never token material, never raw credentials.
- [ ] Alert on spikes; add a panel to the operations dashboard (Nick's #26).
- [ ] Write the incident runbook: what to do when a key leaks, when one server is abused,
      who is told, in what order, and how a rotation is announced.
- [ ] **Done when:** a simulated abuse run raises an alert you can point at, and the team
      walks the runbook end to end once.

---

## 4. Commands you live in

```bash
python scripts/bench_admin_authz.py
```
```bash
pytest -k "not live" -q
```

---

## 5. Handoffs you owe, and receive

**You owe Track A** — **Phase 1**: the SSRF attack cases, so their hardening (#3) is
written against real probes rather than a guess.

**You owe Track B** — **Phase 4**: the injected-caption corpus you attack with, so their
grounding guard (#7) is tested on the same inputs.

**You owe Track C** — **Phase 3**: the deletion-path test results (data actually gone;
one server cannot trigger another's deletion).

**You owe Nick** — **end of every phase**: the attack report (refused vs got through).
This is the single number security is graded on; Nick puts it in the status update, and
it goes on the demo-day slide.

**You receive from everyone** — every new endpoint, every new outbound fetch, every new
stored field. Ask for them in the PR: "does this add an endpoint, a fetch, or a stored
field?" If yes, it needs an attack case before merge.

**You receive from Nick** — sign-off and scope for each attack run, and the staging host
when it exists (Phase 3). Never run against staging without asking first.

---

## 6. Where to ask

Report findings in the team channel as: what you attacked, the exact command, what got
through, and which threat-model item it maps to. If you find something serious, tell Nick
directly before posting it anywhere.
