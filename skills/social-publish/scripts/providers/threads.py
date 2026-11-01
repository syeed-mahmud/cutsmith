"""Threads video posts (free; your own Meta app with the Threads use case).

Flow: OAuth at
threads.net/oauth/authorize → graph.threads.net/oauth/access_token → th_exchange_token
(long-lived, ~60 days) → th_refresh_token; container POST /{user}/threads
media_type=VIDEO&video_url=…&text=… → poll ?fields=status,error_message until FINISHED
(ERROR/EXPIRED fail) → POST /{user}/threads_publish?creation_id=… → permalink.

Threads fetches the video from a PUBLIC URL (no local upload in its API). Give each entry
`options.public_url`, or configure `media_host_cmd` (a command that uploads {file} and prints
the URL) with `publish.py setup threads --media-host-cmd '...'`.
Threads only allows https redirect URIs, so login uses a paste-back flow: the browser lands
on https://localhost/... (page fails to load — that's expected) and you paste that URL.
"""
from __future__ import annotations

import shlex
import subprocess
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("threads",)
NATIVE_SCHEDULE = False
G = "https://graph.threads.net"
REDIRECT = "https://localhost:8770/threads-callback"
SCOPES = "threads_basic,threads_content_publish,threads_manage_insights"
SETUP = f"""Threads (free):
 1. developers.facebook.com → Create app → use case "Access the Threads API". In the Threads use
    case settings add redirect callback URL  {REDIRECT}  and add your Threads account as a
    Threads tester (App roles → Roles → Threads testers, then accept in Threads → Settings).
    Copy the Threads App ID and Threads App secret (Use cases → Threads → Settings).
 2. Threads needs the video at a PUBLIC URL. Either set options.public_url per post, or give a
    command that uploads a file and prints its URL, e.g. your own bucket:
      --media-host-cmd 'rclone copyto {{file}} r2:clips/{{name}} && echo https://cdn.example.com/clips/{{name}}'
 3. Run:  publish.py setup threads --client-id ... --client-secret ... [--media-host-cmd '...']
          publish.py auth threads"""


def auth(args) -> dict:
    app = C.app_config("threads")
    if not (app.get("client_id") and app.get("client_secret")):
        raise C.ApiError("Threads app not configured — run `publish.py setup threads`\n" + SETUP)
    url = "https://www.threads.net/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": app["client_id"], "redirect_uri": REDIRECT, "scope": SCOPES, "response_type": "code",
        "state": str(int(time.time()))})
    C.log(f"\n1) Open and approve:\n\n{url}\n\n2) The browser then fails to load a https://localhost/... page.\n"
          "   Copy that whole address and paste it here.\n")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    pasted = getattr(args, "redirect_url", None) or input("Redirected URL: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get("code", [""])[0].split("#")[0]
    if not code:
        raise C.ApiError("no ?code= in that URL")
    short = C.request("POST", f"{G}/oauth/access_token", form={
        "client_id": app["client_id"], "client_secret": app["client_secret"], "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code": code})
    long = C.request("GET", f"{G}/access_token?" + urllib.parse.urlencode({
        "grant_type": "th_exchange_token", "client_secret": app["client_secret"],
        "access_token": short["access_token"]}))
    _store(long)
    return whoami()


def _store(tok: dict) -> None:
    me = C.request("GET", f"{G}/v1.0/me?fields=id,username&access_token={tok['access_token']}")
    C.save_tokens("threads", {"access_token": tok["access_token"], "user_id": me["id"], "username": me.get("username"),
                              "expires_at": int(time.time()) + int(tok.get("expires_in", 5184000))})


def _token() -> tuple[str, str]:
    t = C.tokens("threads")
    if not t.get("access_token"):
        raise C.ApiError("Threads not connected — run `publish.py auth threads`")
    if t.get("expires_at", 0) - time.time() < 7 * 86400:          # refresh in the last week 
        try:
            _store(C.request("GET", f"{G}/refresh_access_token?grant_type=th_refresh_token"
                                    f"&access_token={t['access_token']}"))
            t = C.tokens("threads")
        except C.ApiError as e:
            if t.get("expires_at", 0) < time.time():
                raise C.ApiError(f"Threads token expired — run `publish.py auth threads` ({e})") from None
    return t["access_token"], t["user_id"]


def whoami() -> dict:
    t = C.tokens("threads")
    return {"platform": "threads", "connected": bool(t.get("access_token")), "username": t.get("username"),
            "media_host": "configured" if C.app_config("threads").get("media_host_cmd") else "options.public_url only"}


def public_url(entry: dict, media: Path) -> str:
    if entry.get("options", {}).get("public_url"):
        return entry["options"]["public_url"]
    cmd = C.app_config("threads").get("media_host_cmd")
    if not cmd:
        raise C.ApiError("Threads needs a public video URL: set options.public_url or `setup threads --media-host-cmd`")
    full = cmd.replace("{file}", shlex.quote(str(media))).replace("{name}", shlex.quote(media.name))
    out = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=1800)
    url = (out.stdout.strip().splitlines() or [""])[-1]
    if out.returncode != 0 or not url.startswith("https://"):
        raise C.ApiError(f"media_host_cmd failed: {out.stderr[-300:] or out.stdout[-300:]}")
    return url


def validate(entry: dict) -> list[str]:
    errs = []
    if not C.tokens("threads").get("access_token"):
        errs.append("Threads not connected — run `publish.py auth threads`")
    if not (entry.get("options", {}).get("public_url") or C.app_config("threads").get("media_host_cmd")):
        errs.append("threads needs options.public_url or a media_host_cmd")
    if len(entry.get("text", "")) > 500:
        errs.append("threads text > 500 characters")
    return errs


def publish(entry: dict, media: Path) -> dict:
    tok, uid = _token()
    params = {"media_type": "VIDEO", "video_url": public_url(entry, media), "text": entry.get("text", "")[:500],
              "access_token": tok}
    if entry.get("options", {}).get("ai_generated"):
        params["is_made_with_ai"] = "true"
    cid = C.request("POST", f"{G}/v1.0/{uid}/threads?" + urllib.parse.urlencode(params))["id"]
    for _ in range(120):
        st = C.request("GET", f"{G}/v1.0/{cid}?fields=status,error_message&access_token={tok}")
        if st.get("status") in ("FINISHED", "PUBLISHED"):
            break
        if st.get("status") in ("ERROR", "EXPIRED"):
            raise C.ApiError(f"Threads container {st.get('status')}: {st.get('error_message')}")
        time.sleep(5)
    else:
        raise C.ApiError("Threads video processing timed out")
    tid = C.request("POST", f"{G}/v1.0/{uid}/threads_publish?creation_id={cid}&access_token={tok}")["id"]
    link = C.request("GET", f"{G}/v1.0/{tid}?fields=id,permalink&access_token={tok}").get("permalink")
    return {"id": tid, "url": link, "status": "published"}


def status(entry: dict) -> dict:
    return {"note": "Threads posts publish immediately."}


def stats(entry: dict) -> dict:
    tok, _ = _token()
    r = C.request("GET", f"{G}/v1.0/{entry['result']['id']}/insights?metric=views,likes,replies,reposts,quotes,shares"
                         f"&access_token={tok}")
    out = {}
    for m in r.get("data", []):
        out[m["name"]] = (m.get("values") or [{}])[0].get("value", m.get("total_value", {}).get("value"))
    return {"views": out.get("views"), "likes": out.get("likes"), "comments": out.get("replies"),
            "reposts": out.get("reposts"), "quotes": out.get("quotes"), "shares": out.get("shares")}
