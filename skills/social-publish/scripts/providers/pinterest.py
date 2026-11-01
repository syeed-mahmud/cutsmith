"""Pinterest video pins (free; your own Pinterest developer app).

Flow: OAuth with
Basic client auth at /v5/oauth/token, POST /v5/media {media_type: video} → upload the file to
upload_url with the returned upload_parameters (multipart), poll /v5/media/{id} until
"succeeded", then POST /v5/pins with media_source {source_type: video_id, media_id,
cover_image_url}; release URL https://www.pinterest.com/pin/{id}.
"""
from __future__ import annotations

import base64
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("pinterest",)
NATIVE_SCHEDULE = False
API = "https://api.pinterest.com/v5"
PORT = 8768
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"
SETUP = f"""Pinterest (free):
 1. developers.pinterest.com → My apps → Connect app (needs a Pinterest business account).
 2. Add redirect URI  {REDIRECT}   and copy App ID + App secret.
 3. Run:  publish.py setup pinterest --client-id ... --client-secret ...
          publish.py auth pinterest [--board <board id>]   (lists your boards)
 New apps start with "Trial access"; Pinterest may require upgrading to Standard to publish widely."""


def _basic() -> dict:
    app = C.app_config("pinterest")
    if not (app.get("client_id") and app.get("client_secret")):
        raise C.ApiError("Pinterest app not configured — run `publish.py setup pinterest`\n" + SETUP)
    raw = f"{app['client_id']}:{app['client_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def auth(args) -> dict:
    app = C.app_config("pinterest")
    h = _basic()
    url = "https://www.pinterest.com/oauth/?" + urllib.parse.urlencode({
        "client_id": app["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES,
        "state": str(int(time.time()))})
    got = C.loopback_authorize(url, PORT, "/callback")
    tok = C.request("POST", f"{API}/oauth/token", headers=h, form={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT})
    _store(tok)
    boards = C.request("GET", f"{API}/boards?page_size=250", headers={"Authorization": f"Bearer {tok['access_token']}"})
    items = [{"id": b["id"], "name": b["name"]} for b in boards.get("items", [])]
    board = getattr(args, "board", None) or (items[0]["id"] if items else None)
    C.save_tokens("pinterest", {"boards": items, "default_board": board})
    return whoami()


def _store(tok: dict) -> None:
    C.save_tokens("pinterest", {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                                "expires_at": int(time.time()) + int(tok.get("expires_in", 2592000)) - 120})


def _token() -> str:
    t = C.tokens("pinterest")
    if not t.get("access_token"):
        raise C.ApiError("Pinterest not connected — run `publish.py auth pinterest`")
    if t.get("expires_at", 0) > time.time():
        return t["access_token"]
    _store(C.request("POST", f"{API}/oauth/token", headers=_basic(), form={
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"], "scope": SCOPES}))
    return C.tokens("pinterest")["access_token"]


def whoami() -> dict:
    t = C.tokens("pinterest")
    return {"platform": "pinterest", "connected": bool(t.get("access_token")), "default_board": t.get("default_board"),
            "boards": t.get("boards")}


def validate(entry: dict) -> list[str]:
    errs = []
    t = C.tokens("pinterest")
    if not t.get("access_token"):
        errs.append("Pinterest not connected — run `publish.py auth pinterest`")
    elif not (entry.get("options", {}).get("board_id") or t.get("default_board")):
        errs.append("pinterest needs options.board_id (see `whoami pinterest`)")
    if len(entry.get("text", "")) > 500:
        errs.append("pinterest description > 500 characters")
    return errs


def publish(entry: dict, media: Path) -> dict:
    h = {"Authorization": f"Bearer {_token()}"}
    reg = C.request("POST", f"{API}/media", headers=h, json_body={"media_type": "video"})
    params = {k: v for k, v in (reg.get("upload_parameters") or {}).items() if k}
    body, ctype = C.multipart(params, {"file": (media.name, media.read_bytes(), "video/mp4")})
    C.request("POST", reg["upload_url"], headers={"Content-Type": ctype}, data=body, timeout=1800)
    mid = reg["media_id"]
    for _ in range(120):
        st = C.request("GET", f"{API}/media/{mid}", headers=h).get("status")
        if st == "succeeded":
            break
        if st == "failed":
            raise C.ApiError("Pinterest video processing failed")
        time.sleep(5)
    else:
        raise C.ApiError("Pinterest video processing timed out")
    o = entry.get("options", {})
    cover = o.get("cover_url")
    pin = C.request("POST", f"{API}/pins", headers=h, json_body={
        **({"link": o["link"]} if o.get("link") else {}),
        "title": (o.get("title") or entry.get("title", ""))[:100], "description": entry.get("text", "")[:500],
        "board_id": o.get("board_id") or C.tokens("pinterest")["default_board"],
        "media_source": {"source_type": "video_id", "media_id": mid, **({"cover_image_url": cover} if cover else {})}})
    return {"id": pin["id"], "url": f"https://www.pinterest.com/pin/{pin['id']}", "status": "published"}


def status(entry: dict) -> dict:
    return {"note": "Pinterest pins publish once created."}


def stats(entry: dict) -> dict:
    import datetime as _dt
    today = _dt.date.today()
    start = today - _dt.timedelta(days=89)
    r = C.request("GET", f"{API}/pins/{entry['result']['id']}/analytics?start_date={start}&end_date={today}"
                         "&metric_types=IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK,VIDEO_MRC_VIEW",
                  headers={"Authorization": f"Bearer {_token()}"})
    tot = (r.get("all") or {}).get("lifetime_metrics") or {}
    if not tot:
        for day in (r.get("all") or {}).get("daily_metrics", []):
            for k, v in (day.get("metrics") or {}).items():
                tot[k] = tot.get(k, 0) + (v or 0)
    return {"views": tot.get("VIDEO_MRC_VIEW"), "impressions": tot.get("IMPRESSION"), "saves": tot.get("SAVE"),
            "clicks": tot.get("PIN_CLICK"), "outbound_clicks": tot.get("OUTBOUND_CLICK")}
