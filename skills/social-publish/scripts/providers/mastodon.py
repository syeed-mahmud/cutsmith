"""Mastodon (any instance) video posts (free).

Flow: multipart
upload to /api/v1/media, wait while GET /api/v1/media/{id} answers 206 (still processing),
422 = expired media, then POST /api/v1/statuses with media_ids[] and an Idempotency-Key so
retries never double-post. Auth simplified to a personal access token.
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path

import core as C

PLATFORMS = ("mastodon",)
NATIVE_SCHEDULE = False
SETUP = """Mastodon (free):
 1. On your instance: Preferences → Development → New application, scopes write:statuses,
    write:media, read:accounts → open it and copy "Your access token".
 2. Run:  publish.py setup mastodon --handle https://mastodon.social --client-secret <access token>
          publish.py auth mastodon"""


def _cfg() -> tuple[str, dict]:
    c = C.app_config("mastodon")
    if not (c.get("handle") and c.get("client_secret")):
        raise C.ApiError("Mastodon not configured — run `publish.py setup mastodon`\n" + SETUP)
    return c["handle"].rstrip("/"), {"Authorization": f"Bearer {c['client_secret']}"}


def auth(args) -> dict:
    url, h = _cfg()
    me = C.request("GET", f"{url}/api/v1/accounts/verify_credentials", headers=h)
    C.save_tokens("mastodon", {"acct": me.get("acct"), "url": url})
    return whoami()


def whoami() -> dict:
    t = C.tokens("mastodon")
    return {"platform": "mastodon", "connected": bool(t.get("acct")), "account": t.get("acct"), "instance": t.get("url")}


def validate(entry: dict) -> list[str]:
    errs = [] if C.tokens("mastodon").get("acct") else ["Mastodon not connected — run `publish.py auth mastodon`"]
    if len(entry.get("text", "")) > 500:
        errs.append("mastodon status > 500 characters (default instance limit)")
    return errs


def publish(entry: dict, media: Path) -> dict:
    url, h = _cfg()
    form = {"description": entry.get("title", "")[:1500]} if entry.get("title") else {}
    body, ctype = C.multipart(form, {"file": (media.name, media.read_bytes(), "video/mp4")})
    up = C.request("POST", f"{url}/api/v2/media", headers={**h, "Content-Type": ctype}, data=body, timeout=900)
    mid = up["id"]
    for _ in range(120):                                  # 206 / null url = still processing
        _, _, code = C.request("GET", f"{url}/api/v1/media/{mid}", headers=h, want_headers=True)
        if code == 200:
            break
        time.sleep(5)
    else:
        raise C.ApiError("Mastodon media processing timed out")
    key = f"{entry.get('id', 'post')}-{secrets.token_hex(3)}"
    body, ctype = C.multipart({"status": entry.get("text", ""), "visibility": "public", "media_ids[]": mid}, {})
    post = C.request("POST", f"{url}/api/v1/statuses", headers={**h, "Content-Type": ctype, "Idempotency-Key": key},
                     data=body)
    return {"id": post["id"], "url": post.get("url") or f"{url}/statuses/{post['id']}", "status": "published"}


def status(entry: dict) -> dict:
    return {"note": "Mastodon statuses publish immediately."}


def stats(entry: dict) -> dict:
    url, h = _cfg()
    s = C.request("GET", f"{url}/api/v1/statuses/{entry['result']['id']}", headers=h)
    return {"likes": s.get("favourites_count"), "reposts": s.get("reblogs_count"), "comments": s.get("replies_count")}
