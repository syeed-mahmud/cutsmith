"""Facebook Page Reels + Instagram Reels through your own Meta app (free).

Standard Access works without App Review for people who have a role on the app
(developers.facebook.com/docs/graph-api/overview/access-levels), i.e. your own Pages.
Docs: /docs/video-api/guides/reels-publishing, /docs/instagram-platform/content-publishing
Limits: FB Reels 30/24 h, 3-90 s; IG 100 API posts/24 h, Reels 3 s-15 min, 300 MB.
"""
from __future__ import annotations

import datetime as dt
import time
import urllib.parse
from pathlib import Path

import core as C

PLATFORMS = ("facebook", "instagram")
NATIVE_SCHEDULE = True          # facebook only; instagram goes through the local scheduler
V = "v26.0"
G = f"https://graph.facebook.com/{V}"
PERMS = ("pages_show_list,pages_read_engagement,pages_manage_posts,read_insights,instagram_basic,"
         "instagram_content_publish,instagram_manage_insights,business_management")
SETUP = f"""Facebook Page + Instagram (free, your own Meta app):
 1. developers.facebook.com → My Apps → Create app → type "Business". Note App ID + App Secret
    (App settings → Basic). You are the app's admin, so Standard Access is enough for YOUR Pages.
 2. Instagram must be a Professional (Business/Creator) account linked to the Facebook Page.
 3. Graph API Explorer (developers.facebook.com/tools/explorer): select your app, "Get User Access
    Token", tick: {PERMS}. Generate and copy the token.
 4. Run:  publish.py setup meta --app-id ... --app-secret ...
          publish.py auth meta --user-token <token from step 3>
    This swaps it for a 60-day user token and permanent Page tokens, and finds the linked Instagram account."""


def auth(args) -> dict:
    app = C.app_config("meta")
    if not (app.get("app_id") and app.get("app_secret")):
        raise C.ApiError("Meta app not configured — run `publish.py setup meta`\n" + SETUP)
    if not getattr(args, "user_token", None):
        raise C.ApiError("pass --user-token from Graph API Explorer\n" + SETUP)
    long = C.request("GET", f"{G}/oauth/access_token?" + urllib.parse.urlencode({
        "grant_type": "fb_exchange_token", "client_id": app["app_id"], "client_secret": app["app_secret"],
        "fb_exchange_token": args.user_token}))
    user_tok = long["access_token"]
    me = C.request("GET", f"{G}/me?fields=id,name&access_token={user_tok}")
    pages = C.request("GET", f"{G}/{me['id']}/accounts?fields=id,name,access_token,tasks&limit=100&access_token={user_tok}")
    out = []
    for p in pages.get("data", []):
        ig = C.request("GET", f"{G}/{p['id']}?fields=instagram_business_account%7Bid,username%7D"
                              f"&access_token={p['access_token']}").get("instagram_business_account")
        out.append({"id": p["id"], "name": p["name"], "token": p["access_token"], "tasks": p.get("tasks", []),
                    "instagram": ig})
    if not out:
        raise C.ApiError("no Pages returned — did you tick pages_show_list and select your Page in the dialog?")
    C.save_tokens("meta", {"user": me, "user_token": user_tok,
                           "user_expires_at": int(time.time()) + int(long.get("expires_in", 5184000)),
                           "pages": out, "default_page": getattr(args, "page", None) or out[0]["id"]})
    return whoami()


def _page(entry: dict | None = None) -> dict:
    t = C.tokens("meta")
    if not t.get("pages"):
        raise C.ApiError("Meta not connected — run `publish.py auth meta --user-token …`")
    want = (entry or {}).get("options", {}).get("page_id") or t.get("default_page")
    for p in t["pages"]:
        if p["id"] == str(want):
            return p
    raise C.ApiError(f"page {want} not among connected pages {[p['id'] for p in t['pages']]}")


def whoami() -> dict:
    t = C.tokens("meta")
    return {"platform": "facebook/instagram", "connected": bool(t.get("pages")), "user": t.get("user", {}).get("name"),
            "default_page": t.get("default_page"),
            "pages": [{"id": p["id"], "name": p["name"], "can_post": "CREATE_CONTENT" in p.get("tasks", []),
                       "instagram": (p.get("instagram") or {}).get("username")} for p in t.get("pages", [])]}


def validate(entry: dict) -> list[str]:
    errs = []
    if entry["platform"] == "instagram":
        try:
            if not _page(entry).get("instagram"):
                errs.append("the selected Page has no linked Instagram professional account")
        except C.ApiError as e:
            errs.append(str(e))
    elif entry["platform"] == "facebook":
        try:
            _page(entry)
        except C.ApiError as e:
            errs.append(str(e))
    return errs


def _wait(fn, ok, bad, every=10, limit=600):
    t0 = time.time()
    while time.time() - t0 < limit:
        v = fn()
        if v in ok:
            return v
        if v in bad:
            raise C.ApiError(f"processing failed: {v}")
        time.sleep(every)
    raise C.ApiError("timed out waiting for processing")


def publish(entry: dict, media: Path) -> dict:
    page = _page(entry)
    size = media.stat().st_size
    if entry["platform"] == "facebook":
        tok = page["token"]
        start = C.request("POST", f"{G}/{page['id']}/video_reels", form={"upload_phase": "start", "access_token": tok})
        vid = start["video_id"]
        C.request("POST", f"https://rupload.facebook.com/video-upload/{V}/{vid}",
                  headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)},
                  data=media.read_bytes(), timeout=1800)
        form = {"access_token": tok, "video_id": vid, "upload_phase": "finish", "video_state": "PUBLISHED",
                "description": entry.get("text", "")}
        if entry.get("options", {}).get("ai_generated"):
            form["is_ai_generated"] = "true"
        state = "published"
        when = entry.get("schedule")
        if when and when != "now":
            t = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
            if t > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=11):
                form.update(video_state="SCHEDULED", scheduled_publish_time=str(int(t.timestamp())))
                state = "scheduled"
        C.request("POST", f"{G}/{page['id']}/video_reels", form=form)
        return {"id": vid, "url": f"https://www.facebook.com/reel/{vid}", "status": state}

    # ---- instagram reel: container (resumable upload) → poll → publish
    ig = page["instagram"]["id"]
    tok = page["token"]
    q = {"media_type": "REELS", "upload_type": "resumable", "caption": entry.get("text", ""),
         "share_to_feed": "true", "access_token": tok}
    if entry.get("options", {}).get("ai_generated"):
        q["is_ai_generated"] = "true"
    cont = C.request("POST", f"{G}/{ig}/media", form=q)
    cid = cont["id"]
    C.request("POST", cont.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{V}/{cid}",
              headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)},
              data=media.read_bytes(), timeout=1800)
    _wait(lambda: C.request("GET", f"{G}/{cid}?fields=status_code&access_token={tok}").get("status_code"),
          ok=("FINISHED",), bad=("ERROR", "EXPIRED"), every=15, limit=900)
    pub = C.request("POST", f"{G}/{ig}/media_publish", form={"creation_id": cid, "access_token": tok})
    link = C.request("GET", f"{G}/{pub['id']}?fields=permalink&access_token={tok}").get("permalink")
    return {"id": pub["id"], "url": link, "status": "published"}


def status(entry: dict) -> dict:
    page = _page(entry)
    rid = entry.get("result", {}).get("id")
    if entry["platform"] == "facebook":
        return C.request("GET", f"{G}/{rid}?fields=status&access_token={page['token']}").get("status", {})
    return C.request("GET", f"{G}/{rid}?fields=permalink,timestamp&access_token={page['token']}")


def _insights(url: str) -> dict:
    out = {}
    try:
        for m in C.request("GET", url).get("data", []):
            vals = m.get("values") or [{}]
            v = vals[0].get("value", m.get("total_value", {}).get("value"))
            if isinstance(v, dict):
                v = sum(x for x in v.values() if isinstance(x, (int, float)))
            out[m["name"]] = v
    except C.ApiError as e:
        out["insights_error"] = str(e)[:160]
    return out


def stats(entry: dict) -> dict:
    page = _page(entry)
    tok, rid = page["token"], entry["result"]["id"]
    if entry["platform"] == "instagram":
        base = C.request("GET", f"{G}/{rid}?fields=like_count,comments_count&access_token={tok}")
        ins = _insights(f"{G}/{rid}/insights?metric=views,reach,saved,shares&access_token={tok}")
        return {"views": ins.get("views"), "reach": ins.get("reach"), "likes": base.get("like_count"),
                "comments": base.get("comments_count"), "saves": ins.get("saved"), "shares": ins.get("shares"),
                **({"note": ins["insights_error"]} if "insights_error" in ins else {})}
    ins = _insights(f"{G}/{rid}/video_insights?metric=blue_reels_play_count,post_impressions_unique,"
                    f"post_video_likes_by_reaction_type,post_video_social_actions&access_token={tok}")
    return {"views": ins.get("blue_reels_play_count"), "reach": ins.get("post_impressions_unique"),
            "likes": ins.get("post_video_likes_by_reaction_type"), "actions": ins.get("post_video_social_actions"),
            **({"note": ins["insights_error"]} if "insights_error" in ins else {})}
