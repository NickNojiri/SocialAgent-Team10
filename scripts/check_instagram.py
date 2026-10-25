"""Check SpotBot's Instagram connection end to end and say what's missing (ADR-0007).

    python scripts/check_instagram.py                      # local checks
    python scripts/check_instagram.py --public https://<x>.trycloudflare.com

Reads .env itself. Never prints a secret: only whether each one is set.
  1. settings present (IG_APP_SECRET, IG_WEBHOOK_VERIFY_TOKEN, IG_ACCESS_TOKEN)
  2. the access token works and the account is professional (Graph API /me)
  3. the webhook app answers on :8011, refuses an unsigned delivery, accepts a signed one
  4. (with --public) Meta's handshake works through the tunnel
  5. the admin app is up on :8010 to capture what arrives
"""

import argparse
import hashlib
import hmac
import json
import ssl
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
LOCAL_HOOK = "http://127.0.0.1:8011"


def read_env() -> dict:
    env = {}
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            line = line.strip().lstrip("﻿")
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"')
    return env


def client() -> httpx.Client:
    try:
        import truststore

        return httpx.Client(timeout=20, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    except ImportError:
        return httpx.Client(timeout=20)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", help="the tunnel's https base URL, to test Meta's handshake through it")
    args = ap.parse_args()
    env = read_env()
    problems = 0

    def report(ok: bool, what: str, fix: str = "", optional: bool = False) -> None:
        nonlocal problems
        problems += not ok and not optional
        mark = "  ok    " if ok else ("  maybe " if optional else "  FIX   ")
        print(mark + what + ("" if ok or not fix else f"\n        → {fix}"))

    print("1. Settings in .env")
    secret, verify, token = env.get("IG_APP_SECRET"), env.get("IG_WEBHOOK_VERIFY_TOKEN"), env.get("IG_ACCESS_TOKEN")
    report(bool(secret), "IG_APP_SECRET is set",
           "Meta dashboard → your app → Instagram → API setup with Instagram login → copy the Instagram app secret")
    report(bool(verify), "IG_WEBHOOK_VERIFY_TOKEN is set", "run .\\scripts\\run_instagram.ps1 once (it creates one)")
    report(bool(token), "IG_ACCESS_TOKEN is set (optional: SpotBot's 'got it' replies)",
           "same page → 'Generate access tokens' for the SpotBot account", optional=True)

    with client() as http:
        print("2. The Instagram account")
        if token:
            version = env.get("IG_GRAPH_VERSION") or "v25.0"
            try:
                r = http.get(f"https://graph.instagram.com/{version}/me",
                             params={"fields": "user_id,username,account_type"},
                             headers={"Authorization": f"Bearer {token}"})
                me = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                if r.status_code == 200:
                    kind = me.get("account_type", "?")
                    report(True, f"token works: @{me.get('username', '?')} ({kind})")
                    report(kind in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"), "the account is professional",
                           "Instagram app → Settings → Account type and tools → Switch to professional account")
                else:
                    msg = (me.get("error") or {}).get("message", f"HTTP {r.status_code}")
                    report(False, f"token refused: {msg}", "generate a new access token in the Meta dashboard")
            except httpx.HTTPError as exc:
                report(False, f"couldn't reach graph.instagram.com ({type(exc).__name__})", "check the network")
        else:
            print("  skip  no access token")

        print("3. The webhook app on :8011")
        try:
            health = http.get(f"{LOCAL_HOOK}/health").json()
            report(True, "webhook app is running")
            report(health.get("app_secret") and health.get("verify_token"), "it has the secret and verify token",
                   "restart .\\scripts\\run_instagram.ps1 so it reads .env again")
            ping = json.dumps({"object": "instagram", "entry": []}).encode()
            unsigned = http.post(f"{LOCAL_HOOK}/webhooks/instagram", content=ping,
                                 headers={"Content-Type": "application/json"})
            report(unsigned.status_code == 403, "an unsigned delivery is refused")
            if secret:
                sig = "sha256=" + hmac.new(secret.encode(), ping, hashlib.sha256).hexdigest()
                signed = http.post(f"{LOCAL_HOOK}/webhooks/instagram", content=ping,
                                   headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
                report(signed.status_code == 200, "a correctly signed delivery is accepted",
                       "the running app has a different IG_APP_SECRET; restart it")
        except httpx.HTTPError:
            report(False, "webhook app is not running", "run .\\scripts\\run_instagram.ps1")

        if args.public:
            print("4. Through the tunnel (what Meta sees)")
            try:
                r = http.get(f"{args.public.rstrip('/')}/webhooks/instagram",
                             params={"hub.mode": "subscribe", "hub.verify_token": verify or "", "hub.challenge": "spotbot-check"})
                report(r.status_code == 200 and r.text == "spotbot-check", "the handshake works from the internet",
                       f"got HTTP {r.status_code}; is the tunnel still running? (data/local_run/tunnel.err)")
            except httpx.HTTPError as exc:
                report(False, f"couldn't reach {args.public} ({type(exc).__name__})", "rerun run_instagram.ps1")

        print("5. The admin app on :8010 (does the capturing)")
        try:
            report(http.get("http://127.0.0.1:8010/health").status_code == 200, "admin app is running")
        except httpx.HTTPError:
            report(False, "admin app is not running", "run .\\scripts\\run_local.ps1")

    print("\nAll set. DM a reel to the SpotBot account from a tester account." if not problems
          else f"\n{problems} thing(s) to fix above.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
