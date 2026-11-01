"""YouTube Data API v3 — OAuth desktop flow + resumable upload (free; your own Google project).

Docs: developers.google.com/youtube/v3/guides/using_resumable_upload_protocol,
      developers.google.com/identity/protocols/oauth2/native-app
Quota: videos.insert has its own bucket of 100 uploads/day; thumbnails.set ~50 units.
"""
from __future__ import annotations

import datetime as dt
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("youtube",)
NATIVE_SCHEDULE = True
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
SETUP = """YouTube (free):
 1. console.cloud.google.com → your project → APIs & Services → Library → enable "YouTube Data API v3".
 2. OAuth consent screen → External → add your Google account under Test users.
    (Testing mode = login expires every 7 days. Click "Publish app" to avoid that; Google shows an
     "unverified app" warning you can accept for your own account.)
 3. Credentials → Create credentials → OAuth client ID → Application type "Desktop app".
 4. Run:  publish.py setup youtube --client-id ... --client-secret ...   then   publish.py auth youtube"""


def auth(args) -> dict:
    app = C.app_config("youtube")
    if not app.get("client_id"):
        raise C.ApiError("no YouTube client_id — run `publish.py setup youtube` first\n" + SETUP)
    port = C.free_port()
    redirect = f"http://127.0.0.1:{port}"
    verifier, challenge = C.pkce()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": app["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "yt"})
    got = C.loopback_authorize(url, port, "/")
    tok = C.request("POST", "https://oauth2.googleapis.com/token", form={
        "client_id": app["client_id"], "client_secret": app.get("client_secret", ""), "code": got["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect})
    C.save_tokens("youtube", {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                              "expires_at": int(time.time()) + int(tok.get("expires_in", 3600)) - 60})
    return whoami()


def _token() -> str:
    t = C.tokens("youtube")
    if not t.get("refresh_token") and not t.get("access_token"):
        raise C.ApiError("YouTube not connected — run `publish.py auth youtube`")
    if t.get("access_token") and t.get("expires_at", 0) > time.time():
        return t["access_token"]
    app = C.app_config("youtube")
    try:
        tok = C.request("POST", "https://oauth2.googleapis.com/token", form={
            "client_id": app["client_id"], "client_secret": app.get("client_secret", ""),
            "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    except C.ApiError as e:
        raise C.ApiError(f"YouTube login expired ({e}). Run `publish.py auth youtube` again "
                         "(publish the OAuth consent screen to stop 7-day expiry).") from None
    C.save_tokens("youtube", {"access_token": tok["access_token"],
                              "expires_at": int(time.time()) + int(tok.get("expires_in", 3600)) - 60})
    return tok["access_token"]


def whoami() -> dict:
    r = C.request("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                  headers={"Authorization": f"Bearer {_token()}"})
    items = r.get("items", [])
    return {"platform": "youtube", "connected": bool(items),
            "channel": items[0]["snippet"]["title"] if items else None, "id": items[0]["id"] if items else None}


def validate(entry: dict) -> list[str]:
    o = entry.get("options", {})
    errs = []
    if not o.get("title"):
        errs.append("youtube needs options.title")
    elif len(o["title"]) > 100:
        errs.append("youtube title > 100 chars")
    if o.get("privacy") not in ("public", "unlisted", "private"):
        errs.append("youtube needs options.privacy public|unlisted|private")
    tags = o.get("tags") or []
    if sum(len(t) + 2 for t in tags) > 500:
        errs.append("youtube tags > 500 chars")
    return errs


def publish(entry: dict, media: Path) -> dict:
    o = entry.get("options", {})
    status = {"privacyStatus": o["privacy"], "selfDeclaredMadeForKids": bool(o.get("made_for_kids", False))}
    if o.get("synthetic") is not None:
        status["containsSyntheticMedia"] = bool(o["synthetic"])
    when = entry.get("schedule")
    # YouTube's own scheduler (publishAt) always ends PUBLIC, so only use it when the user
    # chose public; private/unlisted posts are uploaded at their slot by the local scheduler.
    if when and when != "now" and o["privacy"] == "public":
        t = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        if t > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
            status.update(privacyStatus="private", publishAt=t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    meta = {"snippet": {"title": o["title"], "description": entry.get("text", ""), "tags": o.get("tags") or [],
                        "categoryId": str(o.get("category", "22"))}, "status": status}
    size = media.stat().st_size
    tok = _token()
    _, hdrs, _ = C.request("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable"
                           "&part=snippet,status&notifySubscribers=" + ("true" if o.get("notify", True) else "false"),
                           headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Length": str(size),
                                    "X-Upload-Content-Type": "video/mp4"}, json_body=meta, want_headers=True)
    session = hdrs.get("Location") or hdrs.get("location")
    if not session:
        raise C.ApiError("YouTube did not return an upload session URL")
    video = None
    for attempt in range(5):
        try:
            video = C.request("PUT", session, headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                              data=media.read_bytes(), timeout=1800)
            break
        except C.ApiError as e:
            if attempt == 4 or not any(f"HTTP {c}" in str(e) for c in (500, 502, 503, 504)):
                raise
            time.sleep(2 ** attempt * 3)
    vid = video["id"]
    if o.get("thumbnail") and Path(o["thumbnail"]).exists():
        try:
            C.request("POST", f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}",
                      headers={"Authorization": f"Bearer {_token()}", "Content-Type": "image/jpeg"},
                      data=Path(o["thumbnail"]).read_bytes())
        except C.ApiError as e:
            C.log(f"[youtube] thumbnail not set (needs a verified channel for custom thumbnails?): {e}")
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}",
            "status": "scheduled" if status.get("publishAt") else "published"}


def status(entry: dict) -> dict:
    vid = entry.get("result", {}).get("id")
    r = C.request("GET", f"https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id={vid}",
                  headers={"Authorization": f"Bearer {_token()}"})
    it = (r.get("items") or [{}])[0]
    return {"upload": it.get("status", {}).get("uploadStatus"), "privacy": it.get("status", {}).get("privacyStatus"),
            "processing": it.get("processingDetails", {}).get("processingStatus")}


def stats(entry: dict) -> dict:
    vid = entry["result"]["id"]
    r = C.request("GET", f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={vid}",
                  headers={"Authorization": f"Bearer {_token()}"})
    s = (r.get("items") or [{}])[0].get("statistics", {})
    return {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0))}
