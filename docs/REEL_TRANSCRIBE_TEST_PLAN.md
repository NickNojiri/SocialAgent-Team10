# Test Plan: Reel Audio Transcribe + Summarize

A follow-at-home runbook to validate that the ingestion pipeline can transcribe a
reel's spoken audio and summarize it alongside the caption — and that it degrades
cleanly when a reel has no useful speech (music-only / map-only).

> Why not in the dev container: its network policy blocks `instagram.com` (403 on
> CONNECT), and it has no Ollama / `faster-whisper` / `ffmpeg`. The transcribe +
> summarize **logic** is already covered by 24 offline tests
> (`test_transcriber.py`, `test_ig_embed.py`); this plan measures the **live** path.

---

## Prerequisites (at home, on a clean network)

- 📶 Home network / hotspot — **not** a TLS-intercepting campus/work network. If
  you must use one, the demo script already calls `truststore.inject_into_ssl()`
  so the HuggingFace model download + IG CDN download route through the OS trust
  store. (Heads-up only.)
- 🐍 Python 3.12 + the repo checked out on branch `claude/recent-branch-review-0t0r9y`
  (or `feature/reel-capture`).
- 🦙 **Ollama** installed, running, with the model `config.py` expects pulled.
- 🎬 **ffmpeg** on PATH (faster-whisper needs it to decode audio).

---

## One-time setup

```bash
# from the repo root
python3 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-ingestion.txt
pip install faster-whisper truststore        # optional audio deps (not in base reqs)

# ffmpeg
sudo apt install -y ffmpeg                    # Linux
# brew install ffmpeg                         # macOS

# Ollama (separate terminal)
ollama serve
ollama pull llama3.1:8b                        # matches IngestionSettings.ollama_model
```

Sanity-check the toolchain before spending time on reels:

```bash
. .venv/bin/activate
python -c "import faster_whisper, truststore; print('audio deps OK')"
which ffmpeg
curl -s http://localhost:11434/api/tags | head -c 200   # Ollama should answer
```

---

## The reels under test

| # | URL | Expectation |
|---|-----|-------------|
| 1 | https://www.instagram.com/reel/DU3evm2Ewhn/ | spoken audio → transcript + summary |
| 2 | https://www.instagram.com/reel/DZd_edNJKJI/ | spoken audio → transcript + summary |
| 3 | https://www.instagram.com/reel/DWCIfM4jYwa/ | shows a map, **no spoken audio** → caption-only summary |
| 4 | https://www.instagram.com/reel/DX0z0HRyRsg/ | music + caption only → caption-only summary |
| 5 | https://www.instagram.com/reel/DZtagrnR1vA/ | music + caption only → caption-only summary |
| 6 | https://www.instagram.com/reel/DE4ECPeRPbv/ | unknown — classify on first run (spoken vs music/caption-only) |

(`?igsh=...` tracking params stripped — the extractor strips them anyway.)

---

## Run it

```bash
. .venv/bin/activate
python scripts/test_transcribe.py \
  "https://www.instagram.com/reel/DU3evm2Ewhn/" \
  "https://www.instagram.com/reel/DZd_edNJKJI/" \
  "https://www.instagram.com/reel/DWCIfM4jYwa/" \
  "https://www.instagram.com/reel/DX0z0HRyRsg/" \
  "https://www.instagram.com/reel/DZtagrnR1vA/" \
  "https://www.instagram.com/reel/DE4ECPeRPbv/"
```

Tip: start with **one** known-good reel (#1) to confirm the whole chain works
before running all five.

---

## What to verify per reel

For each reel, confirm:

1. **Fetch** — status is `OK` (not `LOGIN_WALL` / `ERROR`). If `LOGIN_WALL`, the
   `og:`/`video_versions` data often still comes through; note it.
2. **Video URL found** — an mp4 was located (`og:video` or the `video_versions`
   HTML fallback). No video → transcription is correctly skipped.
3. **Transcript** — reels 1–2 produce non-empty spoken text; reels 3–5 produce
   empty/whitespace → treated as "no transcript" (this is correct, not a failure).
4. **Summary** — the Ollama summary is coherent and, for 1–2, reflects something
   *said* (e.g. a venue/neighborhood not in the caption). For 3–5 it should read
   as a caption-only summary.

---

## Results table (fill in at home)

| # | Fetch status | Video found | Transcript (Y/N) | Summary sane (Y/N) | Notes |
|---|--------------|-------------|------------------|--------------------|-------|
| 1 |              |             |                  |                    |       |
| 2 |              |             |                  |                    |       |
| 3 |              |             |                  |                    |       |
| 4 |              |             |                  |                    |       |
| 5 |              |             |                  |                    |       |
| 6 |              |             |                  |                    |       |

---

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `net::ERR_TUNNEL...` / connection refused | Network blocks IG — switch off campus/work VPN/proxy. |
| HF model download or CDN download hangs/TLS error | TLS interception — `truststore` should handle it; confirm it's installed and imported. |
| `ffmpeg not found` / decode error | Install ffmpeg and re-check `which ffmpeg`. |
| Summary step errors / empty | Ollama not running or model not pulled — `ollama serve` + `ollama pull llama3.1:8b`. |
| Fetch returns `LOGIN_WALL` and no video | IG walled this specific reel logged-out; expected ceiling for the unauth path (~40–70%, see `docs/IG_AUTH_INGESTION_PLAN.md`). |
| Transcript empty on reels 1–2 | The reel may be music-only after all, or whisper model too small — try `whisper_model="small"`. |

---

## If results are good → next steps

- Add `faster-whisper` + `truststore` to `requirements-ingestion.txt` (currently
  optional/commented) so setup is a single `pip install`.
- Promote these 5 URLs into `test_ig_live.py` as `live`-marked cases so a
  networked machine runs them automatically (`pytest -m live`).
