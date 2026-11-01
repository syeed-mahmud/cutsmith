"""X (Twitter) API v2 — OAuth 2.0 PKCE + chunked media upload.

NOT FREE: X API v2 is pay-per-usage (docs.x.com/x-api/getting-started/pricing lists
"Post: Create" at $0.015 per request). Optional provider for users who accept that.
Docs: docs.x.com/x-api/media/quickstart/media-upload-chunked, .../oauth-2-0/authorization-code
"""
from __future__ import annotations

import base64
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("twitter",)
NATIVE_SCHEDULE = False
PORT = 8767
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
API = "https://api.x.com/2"
SETUP = f"""X / Twitter (paid per post, ~$0.015 each per X's pricing page):
 1. developer.x.com → create a Project + App, enable "OAuth 2.0", type "Web App / Automated App",
    callback URL {REDIRECT}. Copy Client ID and Client Secret.
 2. Run:  publish.py setup x --client-id ... --client-secret ...   then   publish.py auth x"""


def _basic(app: dict) -> dict:
    if not app.get("client_secret"):
        return {}
    raw = f"{app['client_id']}:{app['client_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def auth(args) -> dict:
    app = C.app_config("x")
    if not app.get("client_id"):
        raise C.ApiError("X app not configured — run `publish.py setup x`\n" + SETUP)
    verifier, challenge = C.pkce()
    url = "https://x.com/i/oauth2/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": app["client_id"], "redirect_uri": REDIRECT,
        "scope": "tweet.read tweet.write users.read media.write offline.access", "state": "x",
        "code_challenge": challenge, "code_challenge_method": "S256"})
    got = C.loopback_authorize(url, PORT, "/callback")
    tok = C.request("POST", f"{API}/oauth2/token", headers=_basic(app), form={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
        "code_verifier": verifier, "client_id": app["client_id"]})
    _store(tok)
    return whoami()


def _store(tok: dict) -> None:
    C.save_tokens("x", {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                        "expires_at": int(time.time()) + int(tok.get("expires_in", 7200)) - 60})


def _token() -> str:
    t = C.tokens("x")
    if not t.get("access_token"):
        raise C.ApiError("X not connected — run `publish.py auth x`")
    if t.get("expires_at", 0) > time.time():
        return t["access_token"]
    app = C.app_config("x")
    _store(C.request("POST", f"{API}/oauth2/token", headers=_basic(app), form={
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": app["client_id"]}))
    return C.tokens("x")["access_token"]


def whoami() -> dict:
    r = C.request("GET", f"{API}/users/me", headers={"Authorization": f"Bearer {_token()}"})
    return {"platform": "twitter", "connected": True, "username": r.get("data", {}).get("username"),
            "note": "X API is pay-per-use (~$0.015 per post)"}


def validate(entry: dict) -> list[str]:
    return ["X post text > 280 characters"] if len(entry.get("text", "")) > 280 else []


def publish(entry: dict, media: Path) -> dict:
    h = {"Authorization": f"Bearer {_token()}"}
    data = media.read_bytes()
    init = C.request("POST", f"{API}/media/upload/initialize", headers=h, json_body={
        "media_type": "video/mp4", "total_bytes": len(data), "media_category": "tweet_video"})["data"]
    mid = init["id"]
    seg = 4 * 1024 * 1024
    for i in range(0, len(data), seg):
        body, ctype = C.multipart({"segment_index": str(i // seg)}, {"media": (media.name, data[i:i + seg], "video/mp4")})
        C.request("POST", f"{API}/media/upload/{mid}/append", headers={**h, "Content-Type": ctype}, data=body, timeout=600)
    fin = C.request("POST", f"{API}/media/upload/{mid}/finalize", headers=h).get("data", {})
    info = fin.get("processing_info")
    while info and info.get("state") in ("pending", "in_progress"):
        time.sleep(int(info.get("check_after_secs", 5)))
        info = C.request("GET", f"{API}/media/upload?command=STATUS&media_id={mid}", headers=h).get("data", {}).get(
            "processing_info")
    if info and info.get("state") == "failed":
        raise C.ApiError(f"X media processing failed: {info}")
    r = C.request("POST", f"{API}/tweets", headers=h, json_body={"text": entry.get("text", ""),
                                                                "media": {"media_ids": [mid]}})
    tid = r["data"]["id"]
    return {"id": tid, "url": f"https://x.com/i/status/{tid}", "status": "published"}


def status(entry: dict) -> dict:
    return {"note": "X posts publish immediately."}


def stats(entry: dict) -> dict:
    return {"note": 'X metrics cost money per request on the pay-per-use API; skipped.'}
