"""Bluesky video posts with an app password (free).

Docs: bsky.network/docs/about-bluesky-content/video (recommended video-service flow):
service token (aud did:web:<pds host>, lxm com.atproto.repo.uploadBlob, 30 min) →
POST video.bsky.app app.bsky.video.uploadVideo → poll getJobStatus for the BlobRef →
createRecord app.bsky.feed.post with embed app.bsky.embed.video.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("bluesky",)
NATIVE_SCHEDULE = False
VIDEO = "https://video.bsky.app/xrpc"
SETUP = """Bluesky (free):
 1. bsky.app → Settings → Privacy and security → App passwords → Add (never use your main password).
 2. Run:  publish.py setup bluesky --handle you.bsky.social --app-password xxxx-xxxx-xxxx-xxxx
          publish.py auth bluesky"""


def _session() -> dict:
    app = C.app_config("bluesky")
    if not (app.get("handle") and app.get("app_password")):
        raise C.ApiError("Bluesky not configured — run `publish.py setup bluesky`\n" + SETUP)
    s = C.request("POST", "https://bsky.social/xrpc/com.atproto.server.createSession",
                  json_body={"identifier": app["handle"], "password": app["app_password"]})
    pds = next((x["serviceEndpoint"] for x in s.get("didDoc", {}).get("service", [])
                if x.get("id", "").endswith("#atproto_pds")), "https://bsky.social")
    return {"jwt": s["accessJwt"], "did": s["did"], "handle": s["handle"], "pds": pds.rstrip("/")}


def auth(args) -> dict:
    s = _session()
    C.save_tokens("bluesky", {"did": s["did"], "handle": s["handle"], "pds": s["pds"]})
    return whoami()


def whoami() -> dict:
    t = C.tokens("bluesky")
    return {"platform": "bluesky", "connected": bool(t.get("did")), "handle": t.get("handle")}


def validate(entry: dict) -> list[str]:
    errs = []
    if not C.app_config("bluesky").get("app_password"):
        errs.append("Bluesky not configured (publish.py setup bluesky)")
    if len(entry.get("text", "")) > 300:
        errs.append("bluesky text > 300 characters")
    return errs


def _aspect(media: Path) -> dict:
    if shutil.which("ffprobe"):
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "json", str(media)], capture_output=True, text=True)
        try:
            st = json.loads(p.stdout)["streams"][0]
            return {"width": int(st["width"]), "height": int(st["height"])}
        except Exception:  # noqa: BLE001
            pass
    return {"width": 1080, "height": 1920}


def publish(entry: dict, media: Path) -> dict:
    s = _session()
    host = urllib.parse.urlparse(s["pds"]).netloc
    svc = C.request("GET", f"{s['pds']}/xrpc/com.atproto.server.getServiceAuth?" + urllib.parse.urlencode({
        "aud": f"did:web:{host}", "lxm": "com.atproto.repo.uploadBlob", "exp": int(time.time()) + 1800}),
        headers={"Authorization": f"Bearer {s['jwt']}"})["token"]
    data = media.read_bytes()
    job = C.request("POST", f"{VIDEO}/app.bsky.video.uploadVideo?" + urllib.parse.urlencode(
        {"did": s["did"], "name": media.name}), data=data, timeout=1800,
        headers={"Authorization": f"Bearer {svc}", "Content-Type": "video/mp4", "Content-Length": str(len(data))})
    blob = job.get("blob")
    for _ in range(300):
        if blob:
            break
        st = C.request("GET", f"{VIDEO}/app.bsky.video.getJobStatus?jobId={job['jobId']}").get("jobStatus", {})
        blob = st.get("blob")
        if st.get("state") == "JOB_STATE_FAILED" and not blob:
            raise C.ApiError(f"Bluesky video processing failed: {st.get('error') or st}")
        time.sleep(2)
    if not blob:
        raise C.ApiError("Bluesky video processing timed out")
    rec = C.request("POST", f"{s['pds']}/xrpc/com.atproto.repo.createRecord",
                    headers={"Authorization": f"Bearer {s['jwt']}"}, json_body={
                        "repo": s["did"], "collection": "app.bsky.feed.post",
                        "record": {"$type": "app.bsky.feed.post", "text": entry.get("text", "")[:300],
                                   "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                                   "embed": {"$type": "app.bsky.embed.video", "video": blob,
                                             "aspectRatio": _aspect(media)}}})
    rkey = rec["uri"].rsplit("/", 1)[-1]
    return {"id": rec["uri"], "url": f"https://bsky.app/profile/{s['handle']}/post/{rkey}", "status": "published"}


def status(entry: dict) -> dict:
    return {"note": "Bluesky posts publish immediately."}


def stats(entry: dict) -> dict:
    uri = entry["result"]["id"]
    r = C.request("GET", "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts?uris=" + urllib.parse.quote(uri, safe=""))
    p = (r.get("posts") or [{}])[0]
    return {"likes": p.get("likeCount"), "reposts": p.get("repostCount"), "comments": p.get("replyCount"),
            "quotes": p.get("quoteCount")}
