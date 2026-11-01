"""Reddit video posts (free; your own Reddit app, personal/non-commercial use).

Flow:
- OAuth: /api/v1/authorize (duration=permanent, scopes identity submit read flair) →
  /api/v1/access_token with Basic client auth; refresh with grant_type=refresh_token.
- Media: POST oauth.reddit.com/api/media/asset {filepath, mimetype} → S3 form
  (args.action + args.fields) → upload → <Location> from the XML reply.
- Video submit: POST /api/submit kind=video, url=<video location>,
  video_poster_url=<poster image location>, title, text, sr, api_type=json.
  Errors come back as 200 + json.errors; RATELIMIT means nothing was posted.
- Media submits answer only with a websocket URL, so the post is
  recovered read-only from /user/{name}/submitted (same subreddit + title, created after
  the submit). It is NEVER re-submitted, which would duplicate the post.
"""
from __future__ import annotations

import base64
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("reddit",)
NATIVE_SCHEDULE = False
PORT = 8769
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
OAUTH = "https://oauth.reddit.com"
SCOPES = "identity submit read flair"
SETUP = f"""Reddit (free for personal use):
 1. reddit.com/prefs/apps → "create another app" → type "web app", redirect uri {REDIRECT}.
    Copy the client id (under the app name) and the secret.
 2. Run:  publish.py setup reddit --client-id ... --client-secret ...   then   publish.py auth reddit
 Each post needs options.subreddit (and the subreddit must allow video posts). Read each
 subreddit's rules: self-promotion limits apply."""


def _ua() -> dict:
    name = C.tokens("reddit").get("username") or "unknown"
    return {"User-Agent": f"python:shorts-clipper-social-publish:1.0 (by /u/{name})"}


def _basic() -> dict:
    app = C.app_config("reddit")
    if not (app.get("client_id") and app.get("client_secret")):
        raise C.ApiError("Reddit app not configured — run `publish.py setup reddit`\n" + SETUP)
    raw = f"{app['client_id']}:{app['client_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode(), **_ua()}


def auth(args) -> dict:
    app = C.app_config("reddit")
    h = _basic()
    url = "https://www.reddit.com/api/v1/authorize?" + urllib.parse.urlencode({
        "client_id": app["client_id"], "response_type": "code", "state": str(int(time.time())),
        "redirect_uri": REDIRECT, "duration": "permanent", "scope": SCOPES})
    got = C.loopback_authorize(url, PORT, "/callback")
    tok = C.request("POST", "https://www.reddit.com/api/v1/access_token", headers=h, form={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT})
    _store(tok)
    me = C.request("GET", f"{OAUTH}/api/v1/me", headers={"Authorization": f"Bearer {tok['access_token']}", **_ua()})
    C.save_tokens("reddit", {"username": me.get("name")})
    return whoami()


def _store(tok: dict) -> None:
    if "access_token" not in tok:
        raise C.ApiError(f"Reddit token error: {tok}")
    vals = {"access_token": tok["access_token"], "expires_at": int(time.time()) + int(tok.get("expires_in", 3600)) - 60}
    if tok.get("refresh_token"):
        vals["refresh_token"] = tok["refresh_token"]
    C.save_tokens("reddit", vals)


def _h() -> dict:
    t = C.tokens("reddit")
    if not t.get("refresh_token"):
        raise C.ApiError("Reddit not connected — run `publish.py auth reddit`")
    if t.get("expires_at", 0) < time.time():
        _store(C.request("POST", "https://www.reddit.com/api/v1/access_token", headers=_basic(), form={
            "grant_type": "refresh_token", "refresh_token": t["refresh_token"]}))
    return {"Authorization": f"Bearer {C.tokens('reddit')['access_token']}", **_ua()}


def whoami() -> dict:
    t = C.tokens("reddit")
    return {"platform": "reddit", "connected": bool(t.get("refresh_token")), "username": t.get("username")}


def validate(entry: dict) -> list[str]:
    o = entry.get("options", {})
    errs = [] if C.tokens("reddit").get("refresh_token") else ["Reddit not connected — run `publish.py auth reddit`"]
    if not o.get("subreddit"):
        errs.append("reddit needs options.subreddit")
    if not (o.get("title") or entry.get("title")):
        errs.append("reddit needs a title")
    elif len(o.get("title") or entry.get("title")) > 300:
        errs.append("reddit title > 300 characters")
    return errs


def _upload(path: Path, mime: str) -> str:
    body, ctype = C.multipart({"filepath": path.name, "mimetype": mime}, {})
    lease = C.request("POST", f"{OAUTH}/api/media/asset", headers={**_h(), "Content-Type": ctype}, data=body)
    args = lease["args"]
    fields = {f["name"]: f["value"] for f in args["fields"]}
    body, ctype = C.multipart(fields, {"file": (path.name, path.read_bytes(), mime)})
    resp = C.request("POST", "https:" + args["action"], headers={"Content-Type": ctype}, data=body, timeout=1800)
    text = resp.get("_text", "") if isinstance(resp, dict) else str(resp)
    m = re.search(r"<Location>(.*?)</Location>", text)
    if not m:
        raise C.ApiError("Reddit media upload did not return a location")
    return urllib.parse.unquote(m.group(1))


def _poster(entry: dict, media: Path) -> Path:
    o = entry.get("options", {})
    for cand in (o.get("poster"), str(media.with_suffix(".jpg"))):
        if cand and Path(cand).exists():
            return Path(cand)
    out = media.with_suffix(".poster.jpg")                  # grab a frame if no thumbnail exists
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(media), "-frames:v", "1",
                    str(out)], check=False)
    if not out.exists():
        raise C.ApiError("reddit video needs a poster image (options.poster)")
    return out


def publish(entry: dict, media: Path) -> dict:
    o = entry.get("options", {})
    sr = re.sub(r"^/?r/", "", o["subreddit"]).strip("/").lower()
    title = (o.get("title") or entry.get("title", ""))[:300]
    video = _upload(media, "video/mp4")
    poster = _upload(_poster(entry, media), "image/jpeg")
    started = time.time()
    form = {"api_type": "json", "kind": "video", "title": title, "url": video, "video_poster_url": poster,
            "text": entry.get("text", ""), "sr": sr, "resubmit": "true"}
    if o.get("flair_id"):
        form["flair_id"] = o["flair_id"]
    r = C.request("POST", f"{OAUTH}/api/submit", headers=_h(), form=form)
    errors = r.get("json", {}).get("errors") or []
    if errors:
        kind = "rate limited (nothing was posted; retry later)" if all(e and e[0] == "RATELIMIT" for e in errors) else "rejected"
        raise C.ApiError(f"Reddit {kind} r/{sr}: " + ", ".join(str(e[1] if len(e) > 1 else e[0]) for e in errors))
    data = r.get("json", {}).get("data", {})
    if data.get("id"):
        return {"id": data["id"], "url": data.get("url"), "status": "published"}
    # media posts answer with a websocket url only → recover the post, never resubmit
    user = C.tokens("reddit").get("username")
    for _ in range(36):
        time.sleep(10)
        lst = C.request("GET", f"{OAUTH}/user/{user}/submitted?limit=25&sort=new", headers=_h())
        for c in lst.get("data", {}).get("children", []):
            d = c.get("data", {})
            if (d.get("subreddit", "").lower() == sr and d.get("title") == title
                    and d.get("created_utc", 0) > started - 60):
                return {"id": d["id"], "url": f"https://www.reddit.com{d.get('permalink', '')}", "status": "published"}
    return {"id": None, "url": f"https://www.reddit.com/r/{sr}", "status": "submitted (processing; check r/" + sr + ")"}


def status(entry: dict) -> dict:
    return {"note": "Reddit video posts appear after processing; see the URL."}


def stats(entry: dict) -> dict:
    pid = entry["result"].get("id")
    if not pid:
        return {"note": "post id unknown (still processing?)"}
    r = C.request("GET", f"{OAUTH}/api/info?id=t3_{pid}", headers=_h())
    d = (r.get("data", {}).get("children") or [{}])[0].get("data", {})
    return {"score": d.get("score"), "upvote_ratio": d.get("upvote_ratio"), "comments": d.get("num_comments"),
            "views": d.get("view_count")}
