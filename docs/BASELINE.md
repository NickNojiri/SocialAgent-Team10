# Instagram live capture baseline

This table measures whether the logged-out Track A capture path obtains a caption
from each Instagram URL. Run it on home Wi-Fi because the test contacts real
Instagram; do not run it in CI or a sandbox.

```bash
python -m pytest test_ig_live.py -v -s --tb=short
```

Record one of `ok`, `login wall`, `no video`, `timeout`, or `no venue` for each
URL, along with the approximate elapsed time. The first local run obtained a
caption for every URL; the timings below come only from its first
`test_ig_caption_pass_rate` sweep.

| # | Link | Result | Rough time | Notes |
|---:|---|---|---|---|
| 1 | https://www.instagram.com/reel/DZIPn-ppdU4/ | ok | 3.7s | caption obtained |
| 2 | https://www.instagram.com/reel/DZXT8n7p8ME/ | ok | 4.5s | caption obtained |
| 3 | https://www.instagram.com/p/DZtDNAokmUh/ | ok | 4.3s | caption obtained |
| 4 | https://www.instagram.com/reel/DZpsu1eowNm/ | ok | 5.7s | caption obtained |
| 5 | https://www.instagram.com/reel/DYSYFuXsgbH/ | ok | 4.9s | caption obtained |
| 6 | https://www.instagram.com/reel/DC4YSNHP10U/ | ok | 5.1s | caption obtained |
| 7 | https://www.instagram.com/reel/C907WpjPUp2/ | ok | 5.0s | caption obtained |
| 8 | https://www.instagram.com/reel/DY0LbwRyhto/ | ok | 5.0s | caption obtained |
| 9 | https://www.instagram.com/reel/DUfLG_mAUc5/ | ok | 4.9s | caption obtained |
| 10 | https://www.instagram.com/reel/DRbgCEMkRSs/ | ok | 5.0s | caption obtained |
| 11 | https://www.instagram.com/reel/DKAXPHFSmEr/ | ok | 5.2s | caption obtained |
| 12 | https://www.instagram.com/reel/C51pgTAycsF/ | ok | 5.1s | caption obtained |
| 13 | https://www.instagram.com/reel/DVIRTIYjvt6/ | ok | 4.2s | caption obtained |
| 14 | https://www.instagram.com/reel/DU3evm2Ewhn/ | ok | 5.8s | caption obtained; transcription-plan reel |
| 15 | https://www.instagram.com/reel/DZd_edNJKJI/ | ok | 4.9s | caption obtained; transcription-plan reel |
| 16 | https://www.instagram.com/reel/DWCIfM4jYwa/ | ok | 5.0s | caption obtained; transcription-plan reel |
| 17 | https://www.instagram.com/reel/DX0z0HRyRsg/ | ok | 5.0s | caption obtained; transcription-plan reel |
| 18 | https://www.instagram.com/reel/DZtagrnR1vA/ | ok | 4.9s | caption obtained; transcription-plan reel |
| 19 | https://www.instagram.com/reel/DE4ECPeRPbv/ | ok | 5.0s | caption obtained; transcription-plan reel |

The embed-fallback test's second sweep is not included in the baseline timings.

- Run date: 2026-10-04
- Network: not recorded in the captured output
- Authenticated path: skipped/unavailable
- Caption pass rate: 100% (19/19)
- Login walls: 0
- Embed fallback recoveries: 0 (fallback had nothing to recover)
- Pytest result: 1 passed, 2 skipped
