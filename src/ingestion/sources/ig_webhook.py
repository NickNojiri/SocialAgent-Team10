"""Reels people DM to SpotBot's Instagram professional account, via Meta's webhook.

The permitted capture route (ADR-0007, docs/handoffs/OFFICIAL-INSTAGRAM-API-PLAN.md): someone
shares a reel with SpotBot in Instagram, Meta POSTs a `messages` webhook, and we read the shared
post from it. No login as a person, no scraping. Everything here is pure (no network) so it is
unit-testable offline; the HTTP side lives in serving/ig_webhook_app.py.

  * verify_signature / handshake — Meta's two checks (X-Hub-Signature-256, hub.challenge).
  * shared_posts — webhook JSON → one SharedPost per shared reel/post or pasted link.
  * SharedPostStore — the caption + video the webhook carried, saved to disk so the admin
    app's capture chain can use them as its first rung (fetch_url), before the public page.

Payload shapes come from Meta's changelog: a shared reel is an attachment of type `ig_reel`
(or `reel`) whose payload has the reel's title (caption), url and video id; a shared post is
`ig_post` (media id, url, title); `share` is the legacy post type. Meta documents the payload
fields in prose only, so the parser accepts the likely key names and records what it can't read.
"""

import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

from src.ingestion.schemas.snapshot import RawPostSnapshot

log = logging.getLogger("ingestion.ig_webhook")

SHARE_TYPES = ("ig_reel", "reel", "ig_post", "share")
_EXTRACTOR_TAG = "ig_webhook"
# Where a pasted (typed) link may point. Anything else in a DM is ignored, not captured.
_LINK_HOSTS = ("instagram.com", "tiktok.com")
_LINK_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_POST_PATH = re.compile(r"^/(?:reels?|p|tv)/([A-Za-z0-9_-]{5,})", re.IGNORECASE)
_HASHTAG = re.compile(r"#(\w+)")
_MENTION = re.compile(r"@([\w.]+)")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


# ── Meta's checks ────────────────────────────────────────────────────────────


def verify_signature(app_secret: str, body: bytes, header: Optional[str]) -> bool:
    """True when `header` is "sha256=<hex HMAC of the raw body with the app secret>".

    Every POST must pass this before its body is even parsed: the webhook URL is public, so
    without it anyone could make SpotBot capture whatever they like."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):].strip().lower())


def handshake(mode: Optional[str], token: Optional[str], challenge: Optional[str], verify_token: str) -> Optional[str]:
    """The challenge to echo when Meta subscribes the webhook with our verify token, else None."""
    if mode != "subscribe" or not verify_token or not token or challenge is None:
        return None
    if not hmac.compare_digest(token.encode("utf-8"), verify_token.encode("utf-8")):
        return None
    return challenge


# ── parsing ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SharedPost:
    """One post someone sent SpotBot: a shared reel/post, or a link typed into the chat."""

    capture_url: str              # what the capture job is given (a permalink, or a synthetic key)
    sender_id: str                # Instagram-scoped id of the sender (IGSID)
    kind: str                     # ig_reel | reel | ig_post | share | link
    caption: Optional[str] = None
    media_id: Optional[str] = None
    media_url: Optional[str] = None   # the payload url when it isn't a post page (e.g. a video file)

    @property
    def has_payload(self) -> bool:
        """True when the webhook itself carried something to capture from (caption or video)."""
        return bool(self.caption or self.media_url)


def is_post_permalink(url: str) -> bool:
    """An instagram.com reel/post page (not a CDN file, profile or story)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (host == "instagram.com" or host.endswith(".instagram.com")) and bool(_POST_PATH.match(parts.path))


def _allowed_link(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _LINK_HOSTS)


def dm_key_url(media_id: str) -> str:
    """Capture URL for a shared post that arrived without a permalink.

    It names no real Instagram page on purpose: the SharedPostStore serves it from the
    webhook's own caption and video, and if that is ever missing the public fetch just
    finds nothing (a wrong-but-real shortcode could capture someone else's post)."""
    return f"https://www.instagram.com/spotbot-dm/{_SAFE_ID.sub('', media_id)[:64]}/"


def shared_posts(payload: dict) -> list[SharedPost]:
    """Every capturable post in one webhook delivery, in order, without duplicates.

    Skips SpotBot's own messages (echoes), deleted and unsupported messages, and
    attachments that aren't shares (photos, voice notes, stickers)."""
    if not isinstance(payload, dict) or payload.get("object") not in ("instagram", "page"):
        return []
    found: list[SharedPost] = []
    seen: set[str] = set()
    for entry in payload.get("entry") or []:
        for event in (entry or {}).get("messaging") or []:
            message = (event or {}).get("message")
            sender = str(((event or {}).get("sender") or {}).get("id") or "")
            if not isinstance(message, dict) or not sender:
                continue
            if message.get("is_echo") or message.get("is_deleted") or message.get("is_unsupported"):
                continue
            for post in _from_message(message, sender):
                if post.capture_url not in seen:
                    seen.add(post.capture_url)
                    found.append(post)
    return found


def _from_message(message: dict, sender: str) -> Iterable[SharedPost]:
    for attachment in message.get("attachments") or []:
        kind = str((attachment or {}).get("type") or "")
        if kind not in SHARE_TYPES:
            continue
        body = (attachment or {}).get("payload") or {}
        url = str(body.get("url") or "").strip()
        caption = (body.get("title") or body.get("caption") or "").strip() or None
        media_id = next((str(body[k]) for k in ("reel_video_id", "video_id", "media_id", "id", "ig_post_media_id")
                         if body.get(k)), None)
        if url and is_post_permalink(url):
            yield SharedPost(url, sender, kind, caption, media_id)
        elif media_id and (url or caption):
            # No post page in the payload (likely a video file link): capture from the payload.
            yield SharedPost(dm_key_url(media_id), sender, kind, caption, media_id, media_url=url or None)
        else:
            log.info(f"[ig-webhook] {kind} attachment with nothing to capture (keys: {sorted(body)})")
    for link in _LINK_RE.findall(str(message.get("text") or "")):
        link = link.rstrip(".,!?)")
        if _allowed_link(link):
            yield SharedPost(link, sender, "link")


# ── the store the capture chain reads ────────────────────────────────────────


class SharedPostStore:
    """What webhooks carried, one JSON file per capture URL, so the admin app (a separate
    process) can capture a DM'd reel without visiting Instagram. Files older than
    `max_age_s` are removed as new ones arrive; the directory sits under data/ (never committed).

    Has the `fetch_url(url) -> RawPostSnapshot | None` shape of the capture chain's first
    rung (pipeline/orchestrator.py), so it can stand in for, or in front of, the old source."""

    capture_path = "dm_share"
    counts_misses = False      # a miss is normal (most links weren't DM'd), not a flagged account

    def __init__(self, root: Path, max_age_s: float = 7 * 86400):
        self.root = Path(root)
        self.max_age_s = max_age_s

    def _path(self, url: str) -> Path:
        from src.ingestion.serving.jobs import normalize_capture_url  # lazy: keeps sources/ light

        digest = hashlib.sha256(normalize_capture_url(url).encode("utf-8")).hexdigest()[:24]
        return self.root / f"{digest}.json"

    def put(self, post: SharedPost) -> Optional[Path]:
        """Save a post that came without a post page. One with a permalink isn't saved: the
        public page carries more (location tag, author) and the chain fetches it as usual."""
        if not post.has_payload or is_post_permalink(post.capture_url):
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        self._prune()
        path = self._path(post.capture_url)
        record = {"capture_url": post.capture_url, "kind": post.kind, "caption": post.caption,
                  "media_id": post.media_id, "media_url": post.media_url, "saved_at": time.time()}
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8", newline="\n")
        return path

    def fetch_url(self, url: str) -> Optional[RawPostSnapshot]:
        path = self._path(url)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning(f"[ig-webhook] unreadable shared post {path.name}: {exc}")
            return None
        caption = record.get("caption") or None
        media_url = record.get("media_url") or None
        snapshot = RawPostSnapshot(
            source_url=record.get("capture_url") or url,
            platform="instagram",
            extractor=_EXTRACTOR_TAG,
            fetched_at=datetime.now(timezone.utc),
            caption=caption,
            hashtags=_HASHTAG.findall(caption or ""),
            mentions=_MENTION.findall(caption or ""),
            # The transcriber downloads this directly when it is a video file.
            video_url=media_url if media_url and not is_post_permalink(media_url) else None,
        )
        snapshot.capture_path = self.capture_path      # extra field: names the rung in stats
        return snapshot

    def _prune(self) -> None:
        cutoff = time.time() - self.max_age_s
        for old in self.root.glob("*.json"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass


class FirstOf:
    """Try each source in order and return the first snapshot (the shared-post store
    first, then the old logged-in source if someone still configures it)."""

    def __init__(self, *sources):
        self.sources = [s for s in sources if s is not None]

    @property
    def counts_misses(self) -> bool:
        """Misses count toward the orchestrator's circuit breaker only if an account-backed
        source is in the chain."""
        return any(getattr(s, "counts_misses", True) for s in self.sources)

    def fetch_url(self, url: str) -> Optional[RawPostSnapshot]:
        for source in self.sources:
            raw = source.fetch_url(url)
            if raw is not None:
                return raw
        return None


def scrub_event(payload: dict) -> dict:
    """The delivery with sender/recipient ids replaced by short hashes, for the local event
    log that becomes the test fixture (docs/handoffs/OFFICIAL-INSTAGRAM-API-PLAN.md)."""

    def walk(node):
        if isinstance(node, dict):
            return {k: (_hash_id(v) if k in ("sender", "recipient") else walk(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(payload)


def _hash_id(party):
    if isinstance(party, dict) and party.get("id"):
        return {"id": "h_" + hashlib.sha256(str(party["id"]).encode("utf-8")).hexdigest()[:12]}
    return party
