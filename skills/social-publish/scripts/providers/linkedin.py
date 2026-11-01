"""LinkedIn member posts with video (free; self-serve "Share on LinkedIn" + OpenID Connect).

Docs: learn.microsoft.com/linkedin/marketing/community-management/shares/videos-api and posts-api,
      .../shared/authentication/authorization-code-flow (tokens last 60 days).
"""
from __future__ import annotations

import datetime as dt
import os
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("linkedin",)
NATIVE_SCHEDULE = False
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SETUP = f"""LinkedIn (free, your own profile):
 1. linkedin.com/developers/apps → Create app (needs a LinkedIn Page to associate; any page you admin).
 2. Products tab → add "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect" (self-serve).
 3. Auth tab → Authorized redirect URLs → add  {REDIRECT}
 4. Run:  publish.py setup linkedin --client-id ... --client-secret ...   then   publish.py auth linkedin
    (Token lasts 60 days; run `auth linkedin` again when it expires.)"""


def _version() -> str:
    if os.environ.get("LINKEDIN_VERSION"):
        return os.environ["LINKEDIN_VERSION"]
    d = dt.date.today().replace(day=1) - dt.timedelta(days=1)       # previous month = released version
    return d.strftime("%Y%m")


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "LinkedIn-Version": _version(), "X-Restli-Protocol-Version": "2.0.0"}


def auth(args) -> dict:
    app = C.app_config("linkedin")
    if not (app.get("client_id") and app.get("client_secret")):
        raise C.ApiError("LinkedIn app not configured — run `publish.py setup linkedin`\n" + SETUP)
    state = str(int(time.time()))
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": app["client_id"], "redirect_uri": REDIRECT, "state": state,
        "scope": "openid profile w_member_social"})
    got = C.loopback_authorize(url, PORT, "/callback")
    tok = C.request("POST", "https://www.linkedin.com/oauth/v2/accessToken", form={
        "grant_type": "authorization_code", "code": got["code"], "client_id": app["client_id"],
        "client_secret": app["client_secret"], "redirect_uri": REDIRECT})
    me = C.request("GET", "https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})
    C.save_tokens("linkedin", {"access_token": tok["access_token"], "person": f"urn:li:person:{me['sub']}",
                               "name": me.get("name"), "expires_at": int(time.time()) + int(tok.get("expires_in", 5184000))})
    return whoami()


def _tok() -> dict:
    t = C.tokens("linkedin")
    if not t.get("access_token"):
        raise C.ApiError("LinkedIn not connected — run `publish.py auth linkedin`")
    if t.get("expires_at", 0) < time.time():
        raise C.ApiError("LinkedIn token expired (60 days) — run `publish.py auth linkedin` again")
    return t


def whoami() -> dict:
    t = C.tokens("linkedin")
    days = int((t.get("expires_at", 0) - time.time()) / 86400) if t else None
    return {"platform": "linkedin", "connected": bool(t.get("access_token")), "name": t.get("name"),
            "author": t.get("person"), "token_days_left": days, "api_version": _version()}


def validate(entry: dict) -> list[str]:
    try:
        _tok()
    except C.ApiError as e:
        return [str(e)]
    return []


def publish(entry: dict, media: Path) -> dict:
    t = _tok()
    h = _h(t["access_token"])
    size = media.stat().st_size
    init = C.request("POST", "https://api.linkedin.com/rest/videos?action=initializeUpload", headers=h, json_body={
        "initializeUploadRequest": {"owner": t["person"], "fileSizeBytes": size,
                                    "uploadCaptions": False, "uploadThumbnail": False}})["value"]
    blob = media.read_bytes()
    etags = []
    for ins in init["uploadInstructions"]:
        part = blob[ins["firstByte"]: ins["lastByte"] + 1]
        _, hdrs, _ = C.request("PUT", ins["uploadUrl"], headers={"Content-Type": "application/octet-stream"},
                               data=part, want_headers=True, timeout=900)
        etags.append(hdrs.get("ETag") or hdrs.get("etag"))
    C.request("POST", "https://api.linkedin.com/rest/videos?action=finalizeUpload", headers=h, json_body={
        "finalizeUploadRequest": {"video": init["video"], "uploadToken": init.get("uploadToken", ""),
                                  "uploadedPartIds": etags}})
    urn = init["video"]
    for _ in range(60):                                    # wait until LinkedIn has processed the video
        st = C.request("GET", "https://api.linkedin.com/rest/videos/" + urllib.parse.quote(urn, safe=""), headers=h)
        if st.get("status") == "AVAILABLE":
            break
        if st.get("status") == "PROCESSING_FAILED":
            raise C.ApiError("LinkedIn video processing failed")
        time.sleep(10)
    _, hdrs, _ = C.request("POST", "https://api.linkedin.com/rest/posts", headers=h, want_headers=True, json_body={
        "author": t["person"], "commentary": entry.get("text", ""), "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "content": {"media": {"title": entry.get("options", {}).get("title") or entry.get("title", ""), "id": urn}},
        "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False})
    post = hdrs.get("x-restli-id") or hdrs.get("X-RestLi-Id")
    return {"id": post, "url": f"https://www.linkedin.com/feed/update/{post}/" if post else None, "status": "published"}


def status(entry: dict) -> dict:
    return {"note": "LinkedIn posts publish immediately; open the URL to check."}


def stats(entry: dict) -> dict:
    return {"note": 'LinkedIn member post analytics need a restricted LinkedIn product; check the post on LinkedIn.'}
