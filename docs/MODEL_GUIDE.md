# Model Guide — what to run, on what hardware, and when to pay

*Short version: for transcribing reels, `base` Whisper is the right default,
`small` if names keep coming out wrong, `tiny` never. Your AMD machine is fine.
Don't buy a GPU yet. The one thing genuinely worth paying for later isn't what
you'd guess.*

Measure on YOUR machine (the only numbers that matter):

```powershell
python scripts\bench_models.py path\to\some_reel.mp4
```

It times every Whisper size on real audio and every installed Ollama model on
this app's actual venue-extraction task, then shows the transcripts so you can
check the one thing that matters: **did it spell the venue right?**

---

## 1. Transcription (your stated need)

The transcript exists for exactly one reason: catching venue names that are
*spoken but never written*. Venue names are proper nouns — and proper nouns are
precisely where small speech models fail first. A transcript that says "Menya
Hanabi" is gold; one that says "many a hanabee" poisons the catalog, the map
pin, and every future recommendation. That asymmetry drives everything below.

| Model (faster-whisper, CPU int8) | Speed on a 30s reel* | Venue-name accuracy | Verdict |
|---|---|---|---|
| `tiny` | ~2–4s | weak — garbles names, struggles with music beds | Only for the max-speed profile |
| **`base`** (current default) | ~5–8s | decent — the floor for names | **Default. Keep.** |
| `small` | ~12–20s | noticeably better with background music + accents | Upgrade if names come out wrong |
| `distil-small.en` | ~base speed | ~small accuracy, **English only** | Best deal if your reels are all English |
| `large-v3-turbo` | ~30–60s CPU | best | Not worth it on CPU; this is a GPU model |

*\*Ryzen-class desktop CPU; run the bench for your real numbers.*

**Recommendation:** stay on `base`. If your Phase-3 testing shows mangled venue
names on music-heavy reels, set `WHISPER_MODEL=small` and eat the ~10 extra
seconds — accuracy compounds, speed doesn't. Try `distil-small.en` in the bench;
if its transcripts look as good as `small` at `base` speed, it wins.

## 2. The extraction LLM (where "premium" is actually tempting)

The LLM reads caption + transcript + on-screen text and isolates the venue.
`llama3.1:8b` (default) is meaningfully better than `llama3.2:3b` at messy,
emoji-soaked, half-Spanish captions — the bench prints a ✅/❌ per model so you
can see whether 3b is good enough on your data. Heuristics remain the safety
net either way.

**The premium case, honestly:** a frontier API model (e.g. Claude Haiku) would
beat both local models at extraction, and at ~$0.001–0.003 per reel it's cheap.
But it breaks the two promises the product makes — **$0 marginal cost and
"post text never leaves the machine."** Verdict: not now. Revisit only for the
*hosted* tier, where you control the privacy disclosure and captures have real
volume. Same logic, stronger, for transcription APIs: Groq/OpenAI Whisper costs
roughly **$1 per 1,500 reels** and returns in ~1s — that is the single
best money-for-value upgrade this app could ever buy *once it's hosted and
earning*. Until then: local.

## 3. Your AMD machine — reality and minimum specs

Two facts:
- **faster-whisper is CPU-only for you** (its engine has no AMD-GPU support).
  That's fine — it's designed for CPU and Ryzen AVX2 runs it well.
- **Ollama *can* use some Radeon GPUs** (roughly RX 6800 and up / RX 7000
  series on Windows). Run a capture and check `ollama ps` — if it says GPU,
  your LLM is already accelerated and you lose nothing vs. NVIDIA here.

| Tier | CPU | RAM | Settings | Capture feel |
|---|---|---|---|---|
| Squeaks by | 4c/8t | 8 GB | `llama3.2:3b` + `tiny` or `TRANSCRIBE_ENABLED=0` | ~20–30s |
| **Comfortable** | Ryzen 5+ (6c) | **16 GB** | `llama3.1:8b` + `base` | ~30–45s |
| Hosting 3–5 servers | Ryzen 7 (8c) | 32 GB | 8b + `base`, concurrent captures | ~30s each |

Disk: ~12 GB (models + browser + store). The 8b model wants ~6 GB of *free* RAM
while running — that's why 16 GB is the real minimum for the quality profile.

## 4. About buying an NVIDIA card

**Don't — not for this, not yet.** Reasons:
1. Run the bench first. If `3b + base` hits your quality bar, your current
   machine is already the right machine.
2. If a GPU ever makes sense, it's for the *hosted* tier — and hosted GPU time
   is rentable by the hour, which beats $300 up front when there's no revenue.
3. If the app makes money and you want local iron anyway: the budget classic is
   a **used RTX 3060 12 GB** (~$180–220) — the 12 GB matters more than speed,
   it fits 8b-class models whole. Skip 8 GB cards.

The path that matches the "maybe this makes money" plan: measure now → ship on
what you have → let the hosted tier's first dollars rent the first GPU.
