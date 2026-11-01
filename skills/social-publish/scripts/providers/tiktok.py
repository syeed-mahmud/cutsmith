"""TikTok Content Posting API (free; your own TikTok developer app).

Two modes:
  draft  (default) /v2/post/publish/inbox/video/init/ — video lands in your TikTok inbox, you tap
         publish in the app (add sound/caption there). Max 5 pending drafts per 24 h.
  direct /v2/post/publish/video/init/ — posts straight away, BUT until TikTok audits your app all
         posts are private-only and the account must be private (error
         unaudited_client_can_only_post_to_private_accounts).
Docs: developers.tiktok.com/doc/content-posting-api-*, /doc/login-kit-desktop
"""
from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("tiktok",)
NATIVE_SCHEDULE = False
PORT = 8766
REDIRECT = f"http://127.0.0.1:{PORT}/callback/"
API = "https://open.tiktokapis.com/v2"
SETUP = f"""TikTok (free, your own developer app):
 1. developers.tiktok.com → sign up → Manage apps → Connect an app (type: Desktop).
 2. Add products "Login Kit" and "Content Posting API" (enable Direct Post if you want direct mode).
    Scopes: user.info.basic, video.upload, video.publish, video.list (analytics). Login Kit redirect URI:  http://127.0.0.1:*/callback/
 3. Use Sandbox (add your TikTok account as a target user) or submit the app for review.
 4. Run:  publish.py setup tiktok --client-key ... --client-secret ...   then   publish.py auth tiktok
 Until TikTok audits the app, "direct" posts are private-only — use the default "draft" mode."""


def auth(args) -> dict:
    app = C.app_config("tiktok")
    if not (app.get("client_key") and app.get("client_secret")):
        raise C.ApiError("TikTok app not configured — run `publish.py setup tiktok`\n" + SETUP)
    verifier, challenge = C.pkce(hex_challenge=True)          # TikTok desktop: hex SHA-256 challenge
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": app["client_key"], "response_type": "code", "scope": "user.info.basic,video.upload,video.publish,video.list",
        "redirect_uri": REDIRECT, "state": str(int(time.time())), "code_challenge": challenge,
        "code_challenge_method": "S256"})
    got = C.loopback_authorize(url, PORT, "/callback/")
    tok = C.request("POST", f"{API}/oauth/token/", form={
        "client_key": app["client_key"], "client_secret": app["client_secret"], "code": got["code"],
        "grant_type": "authorization_code", "redirect_uri": REDIRECT, "code_verifier": verifier})
    _store(tok)
    return whoami()


def _store(tok: dict) -> None:
    if "access_token" not in tok:
        raise C.ApiError(f"TikTok token error: {tok}")
    C.save_tokens("tiktok", {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                             "open_id": tok.get("open_id"),
                             "expires_at": int(time.time()) + int(tok.get("expires_in", 86400)) - 120,
                             "refresh_expires_at": int(time.time()) + int(tok.get("refresh_expires_in", 31536000))})


def _token() -> str:
    t = C.tokens("tiktok")
    if not t.get("refresh_token"):
        raise C.ApiError("TikTok not connected — run `publish.py auth tiktok`")
    if t.get("expires_at", 0) > time.time():
        return t["access_token"]
    app = C.app_config("tiktok")
    _store(C.request("POST", f"{API}/oauth/token/", form={
        "client_key": app["client_key"], "client_secret": app["client_secret"],
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"]}))   # may rotate the refresh token
    return C.tokens("tiktok")["access_token"]


def _h() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json; charset=UTF-8"}


def whoami() -> dict:
    r = C.request("GET", f"{API}/user/info/?fields=open_id,display_name", headers=_h())
    return {"platform": "tiktok", "connected": True, "name": r.get("data", {}).get("user", {}).get("display_name")}


def validate(entry: dict) -> list[str]:
    o = entry.get("options", {})
    errs = []
    if o.get("mode", "draft") not in ("draft", "direct"):
        errs.append("tiktok options.mode must be draft or direct")
    if o.get("mode") == "direct" and o.get("privacy") not in (
            "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"):
        errs.append("tiktok direct mode needs options.privacy (unaudited apps: SELF_ONLY)")
    if len(entry.get("text", "")) > 2200:
        errs.append("tiktok caption > 2200 chars")
    return errs


def _source(size: int) -> dict:
    mb = 1024 * 1024
    if size <= 64 * mb:
        return {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}
    chunk = 10 * mb
    return {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": size // chunk}


def _upload(url: str, data: bytes, src: dict) -> None:
    n, chunk, total = src["total_chunk_count"], src["chunk_size"], src["video_size"]
    for i in range(n):
        a = i * chunk
        b = total - 1 if i == n - 1 else a + chunk - 1           # last chunk takes the remainder
        C.request("PUT", url, data=data[a:b + 1], timeout=900, headers={
            "Content-Type": "video/mp4", "Content-Length": str(b - a + 1), "Content-Range": f"bytes {a}-{b}/{total}"})


def publish(entry: dict, media: Path) -> dict:
    o = entry.get("options", {})
    data = media.read_bytes()
    src = _source(len(data))
    if o.get("mode", "draft") == "draft":
        r = C.request("POST", f"{API}/post/publish/inbox/video/init/", headers=_h(), json_body={"source_info": src})
    else:
        info = C.request("POST", f"{API}/post/publish/creator_info/query/", headers=_h(), json_body={}).get("data", {})
        if o["privacy"] not in info.get("privacy_level_options", [o["privacy"]]):
            raise C.ApiError(f"privacy {o['privacy']} not allowed for this account: {info.get('privacy_level_options')}")
        r = C.request("POST", f"{API}/post/publish/video/init/", headers=_h(), json_body={
            "post_info": {"title": entry.get("text", "")[:2200], "privacy_level": o["privacy"],
                          "disable_comment": bool(o.get("disable_comments")), "disable_duet": bool(o.get("disable_duet")),
                          "disable_stitch": bool(o.get("disable_stitch")), "video_cover_timestamp_ms": 1000,
                          "brand_content_toggle": bool(o.get("branded")), "brand_organic_toggle": bool(o.get("your_brand")),
                          "is_aigc": bool(o.get("ai_generated"))},
            "source_info": src})
    if r.get("error", {}).get("code") not in (None, "ok"):
        raise C.ApiError(f"TikTok init failed: {r['error']}")
    d = r["data"]
    _upload(d["upload_url"], data, src)
    return {"id": d["publish_id"], "url": None,
            "status": "draft-in-tiktok-inbox" if o.get("mode", "draft") == "draft" else "processing"}


def status(entry: dict) -> dict:
    r = C.request("POST", f"{API}/post/publish/status/fetch/", headers=_h(),
                  json_body={"publish_id": entry["result"]["id"]})
    return r.get("data", r)


def stats(entry: dict) -> dict:
    res = entry.get("result", {})
    vid = res.get("video_id")
    if not vid:
        st = status(entry)
        ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
        if not ids:
            return {"note": f"not public yet (status {st.get('status')}); drafts need publishing in the TikTok app"}
        vid = str(ids[0])
        res["video_id"] = vid
    r = C.request("POST", f"{API}/video/query/?fields=id,view_count,like_count,comment_count,share_count,share_url",
                  headers=_h(), json_body={"filters": {"video_ids": [vid]}})
    v = (r.get("data", {}).get("videos") or [{}])[0]
    if v.get("share_url"):
        res["url"] = v["share_url"]
    return {"views": v.get("view_count"), "likes": v.get("like_count"), "comments": v.get("comment_count"),
            "shares": v.get("share_count")}
