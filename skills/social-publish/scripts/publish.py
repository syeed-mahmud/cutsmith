#!/usr/bin/env python3
"""social-publish: post & schedule clips DIRECTLY to each platform with your own free
developer apps. Python 3.9+ standard library only.

  setup <platform> [creds]     save developer-app credentials (prints a guide without creds)
  auth <platform>              connect your account (browser login → tokens in ~/.shorts-clipper/social)
  whoami [platform]            which accounts are connected
  plan <shorts-project>        build publish_plan.json from rendered clips
  show <plan>                  numbered table for approval in chat
  approve <plan> --ids …|--all [--reject …]
  check <plan>                 validate (no network for content)
  run <plan> [--due] --yes     publish APPROVED entries (dry run without --yes)
  status <plan>                refresh results
  calendar <plan> [plan…]      local calendar UI (drag to reschedule, edit, approve) at 127.0.0.1:8790
  scheduler [plan] --install|--remove   one cron job (every 10 min) for due posts + autopilot + stats
  analytics <plan|project>     refresh metrics, write analytics.html (per platform, best clips, every post)
  autopilot add|list|pause|resume|remove|preview   standing approval: auto-schedule + post new clips
  tick                         what cron runs
  post --platform P --media F --text … --yes   one-off post

Platforms: youtube, facebook, instagram, linkedin, tiktok, bluesky, telegram, discord, mastodon,
pinterest, threads, reddit, twitter (X is pay-per-use).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402

PROVIDER = {"youtube": "youtube", "facebook": "meta", "instagram": "meta", "linkedin": "linkedin",
            "tiktok": "tiktok", "bluesky": "bluesky", "twitter": "x", "telegram": "telegram", "discord": "discord",
            "mastodon": "mastodon", "pinterest": "pinterest", "threads": "threads", "reddit": "reddit"}
ALIASES = {"x": "twitter", "yt": "youtube", "ig": "instagram", "fb": "facebook", "tt": "tiktok", "bsky": "bluesky",
           "meta": "facebook"}
SETUP_KEY = {"youtube": "youtube", "facebook": "meta", "instagram": "meta", "meta": "meta", "linkedin": "linkedin",
             "tiktok": "tiktok", "bluesky": "bluesky", "x": "x", "twitter": "x", "telegram": "telegram",
             "discord": "discord", "mastodon": "mastodon", "pinterest": "pinterest", "threads": "threads",
             "reddit": "reddit"}
TEXT_LIMIT = {"twitter": 280, "bluesky": 300, "instagram": 2200, "tiktok": 2200, "linkedin": 3000,
              "facebook": 5000, "youtube": 5000, "telegram": 1024, "discord": 2000, "mastodon": 500, "pinterest": 500,
              "threads": 500, "reddit": 40000}


def emit(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def die(msg: str) -> None:
    C.log(f"ERROR: {msg}")
    sys.exit(1)


def prov(platform: str):
    platform = ALIASES.get(platform, platform)
    if platform not in PROVIDER:
        die(f"unknown platform {platform!r}; use one of {', '.join(PROVIDER)}")
    return importlib.import_module(f"providers.{PROVIDER[platform]}")


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_when(s: str | None) -> dt.datetime | None:
    if not s or s == "now":
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.astimezone()


# ===================================================================== setup / auth
def cmd_setup(a):
    key = SETUP_KEY.get(a.platform)
    if not key:
        die(f"unknown platform {a.platform}")
    mod = importlib.import_module(f"providers.{key if key != 'meta' else 'meta'}")
    vals = {k: v for k, v in {"client_id": a.client_id, "client_secret": a.client_secret, "app_id": a.app_id,
                               "app_secret": a.app_secret, "client_key": a.client_key, "handle": a.handle,
                               "app_password": a.app_password, "media_host_cmd": a.media_host_cmd}.items() if v}
    if not vals:
        print(mod.SETUP)
        return
    C.save_app_config(key, vals)
    emit({"saved": key, "fields": sorted(vals), "file": str(C.home() / "apps.json"), "next": f"publish.py auth {a.platform}"})


def cmd_auth(a):
    try:
        emit(prov(a.platform).auth(a))
    except C.ApiError as e:
        die(str(e))


def cmd_whoami(a):
    plats = [ALIASES.get(a.platform, a.platform)] if a.platform else [
        "youtube", "facebook", "linkedin", "tiktok", "bluesky", "threads", "reddit", "telegram", "discord", "mastodon",
        "pinterest", "twitter"]
    out = []
    for p in plats:
        try:
            out.append(prov(p).whoami())
        except Exception as e:  # noqa: BLE001
            out.append({"platform": p, "connected": False, "error": str(e)[:200]})
    emit(out)


# ===================================================================== plan
def _clip_text(clip: dict, platform: str) -> tuple[str, dict]:
    posts = clip.get("posts") or {}
    v = posts.get(platform) or (posts.get("x") if platform == "twitter" else None)
    tags = " ".join(clip.get("hashtags") or [])
    base = (clip.get("caption") or clip.get("title") or "").strip()
    extra = {}
    if isinstance(v, dict):
        text = v.get("description") or v.get("text") or v.get("caption") or f"{base} {tags}".strip()
        extra = {k: v[k] for k in ("title", "tags") if v.get(k)}
    elif isinstance(v, str):
        text = v
    else:
        text = f"{base} {tags}".strip()
    if platform == "youtube" and "#shorts" not in text.lower():
        text = f"{text}\n\n#Shorts"
    return text, extra


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_slots(spec: str) -> list[tuple[set, int, int]]:
    """'mon-fri 18:00, sat 11:00 15:00' -> [({0..4}, 18, 0), ({5}, 11, 0), ({5}, 15, 0)]."""
    out = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        days_s, *times = part.split()
        days: set = set()
        for d in days_s.lower().split("+"):
            if d in ("daily", "every", "all"):
                days |= set(range(7))
            elif "-" in d:
                a, b = d.split("-")
                i, j = DAYS.index(a[:3]), DAYS.index(b[:3])
                days |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
            else:
                days.add(DAYS.index(d[:3]))
        if not times:
            raise SystemExit(f"slot '{part}' needs a time, e.g. 'mon-fri 18:00'")
        for t in times:
            h, m = (int(x) for x in t.split(":"))
            out.append((days, h, m))
    return out


def slot_times(slots, start: dt.datetime, n: int, taken: set) -> list[dt.datetime]:
    res, day = [], start.replace(hour=0, minute=0, second=0, microsecond=0)
    while len(res) < n:
        for days, h, m in sorted(slots, key=lambda s: (s[1], s[2])):
            t = day.replace(hour=h, minute=m)
            if day.weekday() in days and t > start and t.isoformat() not in taken and len(res) < n:
                res.append(t)
        day += dt.timedelta(days=1)
        if (day - start).days > 400:
            break
    return res


def cmd_plan(a):
    proj = Path(a.project).expanduser().resolve()
    cj = proj / "clips.json"
    if not cj.exists():
        die(f"no clips.json in {proj} — render clips with shorts-clipper first")
    clips = sorted(json.loads(cj.read_text())["clips"], key=lambda c: -c.get("score", 0))[: a.top or None]
    start = parse_when(a.start) if a.start else None
    slot_list = None
    if a.slots:
        base = start or dt.datetime.now().astimezone()
        taken = set()
        for other in Path(a.project).expanduser().resolve().glob("publish_plan*.json"):
            if other.resolve() != (Path(a.out).resolve() if a.out else None):
                try:
                    taken |= {e.get("schedule") for e in json.loads(other.read_text())["entries"]
                              if e.get("status") not in ("skipped",)}
                except Exception:  # noqa: BLE001
                    pass
        slot_list = slot_times(parse_slots(a.slots), base - dt.timedelta(seconds=1), len(clips), taken)
    entries = []
    for k, clip in enumerate(clips):
        if slot_list is not None:
            when = slot_list[k].isoformat() if k < len(slot_list) else "now"
        else:
            when = (start + dt.timedelta(hours=a.every * k)).isoformat() if start else "now"
        entries += build_entries(clip, a.platforms, when, vars(a))
    plan = {"created": now().isoformat(timespec="seconds"), "project": str(proj), "entries": entries}
    out = Path(a.out) if a.out else proj / "publish_plan.json"
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    emit({"plan": str(out), "entries": len(entries), "problems": _problems(plan, network=False),
          "next": "show the plan (`show`), get the user's approval in chat, then `approve` + `run --yes`"})


def build_entries(clip: dict, platforms: str, when: str, o: dict) -> list[dict]:
    """One plan entry per platform for a clip. `o` holds youtube_privacy, tiktok_mode,
    tiktok_privacy, page_id, subreddit (plan flags or an autopilot rule)."""
    entries = []
    for p in [ALIASES.get(x.strip(), x.strip()) for x in platforms.split(",") if x.strip()]:
        text, extra = _clip_text(clip, p)
        opts: dict = {"ai_generated": False}
        if p == "youtube":
            opts.update(title=(extra.get("title") or clip["title"])[:100], privacy=o.get("youtube_privacy"),
                        tags=extra.get("tags") or [], notify=True)
        elif p == "tiktok":
            mode = o.get("tiktok_mode") or "draft"
            opts.update(mode=mode, privacy=o.get("tiktok_privacy") if mode == "direct" else None)
        elif p in ("facebook", "instagram") and o.get("page_id"):
            opts["page_id"] = o["page_id"]
        elif p == "linkedin":
            opts["title"] = clip["title"]
        elif p == "reddit":
            opts.update(subreddit=o.get("subreddit"), title=clip["title"][:300])
            if clip.get("thumbnail"):
                opts["poster"] = clip["thumbnail"]
        entries.append({"id": f"{clip['id']:02d}-{p}", "clip": clip["id"], "title": clip["title"], "platform": p,
                        "media": clip["file"], "text": text, "options": opts, "schedule": when,
                        "approved": False, "status": "planned", "score": clip.get("score")})
    return entries


def _load(path: str) -> tuple[Path, dict]:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        die(f"plan not found: {p}")
    return p, json.loads(p.read_text())


def _save(p: Path, plan: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _validate(e: dict, network: bool = True, for_run: bool = False) -> list[str]:
    errs = []
    p = e.get("platform")
    if p not in PROVIDER:
        return [f"unknown platform {p!r}"]
    if not Path(e.get("media", "")).exists():
        errs.append(f"media missing: {e.get('media')}")
    if len(e.get("text", "")) > TEXT_LIMIT.get(p, 5000):
        errs.append(f"text {len(e['text'])} chars > {p} limit {TEXT_LIMIT[p]}")
    try:
        w = parse_when(e.get("schedule"))
        if w and not for_run and w < now() - dt.timedelta(minutes=1) and e.get("status") == "planned":
            errs.append("schedule is in the past")
    except ValueError:
        errs.append(f"bad schedule {e.get('schedule')!r} (ISO 8601 with offset, or 'now')")
    if network:
        try:
            errs += prov(p).validate(e)
        except Exception as ex:  # noqa: BLE001
            errs.append(str(ex)[:200])
    else:
        mod = prov(p)
        try:
            errs += [x for x in mod.validate(e) if "not connected" not in x and "not configured" not in x
                     and "run `publish.py" not in x]
        except Exception:  # noqa: BLE001
            pass
    return errs


def _problems(plan: dict, network: bool) -> dict:
    return {e["id"]: v for e in plan["entries"] if (v := _validate(e, network))}


def cmd_show(a):
    p, plan = _load(a.plan)
    rows = ["| # | id | platform | when | approved | status | text |", "|---|---|---|---|---|---|---|"]
    for i, e in enumerate(plan["entries"], 1):
        o = e.get("options", {})
        extra = f" [{o.get('privacy') or o.get('mode') or ''}]" if (o.get("privacy") or o.get("mode")) else ""
        rows.append(f"| {i} | {e['id']} | {e['platform']}{extra} | {e.get('schedule')} | "
                    f"{'✅' if e.get('approved') else '—'} | {e.get('status')} | {e['text'][:60].replace(chr(10), ' ')}… |")
    print("\n".join(rows))


def cmd_approve(a):
    p, plan = _load(a.plan)
    ids = set(x.strip() for x in (a.ids or "").split(",") if x.strip())
    rej = set(x.strip() for x in (a.reject or "").split(",") if x.strip())
    changed = []
    for i, e in enumerate(plan["entries"], 1):
        keys = {e["id"], str(i)}
        if a.all or keys & ids:
            e["approved"], e["approved_at"] = True, now().isoformat(timespec="seconds")
            changed.append(e["id"])
        if keys & rej:
            e["approved"] = False
            e["status"] = "skipped"
            changed.append(e["id"] + " (rejected)")
    _save(p, plan)
    emit({"updated": changed})


def cmd_check(a):
    _, plan = _load(a.plan)
    probs = _problems(plan, network=True)
    emit({"ok": not probs, "problems": probs})


# ===================================================================== run
def _due(e: dict, force_now: bool) -> bool:
    w = parse_when(e.get("schedule"))
    if w is None or w <= now():
        return True
    mod = prov(e["platform"])
    yt_public = e["platform"] == "youtube" and e.get("options", {}).get("privacy") == "public"
    if yt_public or (e["platform"] == "facebook" and w > now() + dt.timedelta(minutes=11)):
        return True                       # these platforms schedule natively; upload now
    return force_now


def run_plan(p: Path, plan: dict, yes: bool, only: str | None = None, retry_errors: bool = True) -> dict:
    """Publish approved, due entries of one plan. Returns a result dict (never exits)."""
    states = ("planned", "error") if retry_errors else ("planned",)
    todo = [e for e in plan["entries"] if e.get("approved") and e.get("status") in states]
    if only:
        ids = set(only.split(","))
        todo = [e for e in todo if e["id"] in ids]
    unapproved = [e["id"] for e in plan["entries"] if not e.get("approved") and e.get("status") == "planned"]
    ready = [e for e in todo if _due(e, force_now=False)]
    waiting = [e["id"] for e in todo if e not in ready]
    bad = {e["id"]: v for e in ready if (v := _validate(e, for_run=True))}
    if bad and not yes:
        return {"refused": "fix these first", "problems": bad}
    if not yes:
        return {"dry_run": True, "would_publish_now": [
            {"id": e["id"], "platform": e["platform"], "when": e.get("schedule"),
             "how": "native schedule" if parse_when(e.get("schedule")) else "now"} for e in ready],
            "waiting_for_local_scheduler": waiting, "not_approved": unapproved,
            "next": "only after the user approves: run --yes"}
    for e in [x for x in ready if x["id"] in bad]:       # unattended: mark, don't block the others
        e.update(status="error", error="; ".join(bad[e["id"]]))
    ready = [x for x in ready if x["id"] not in bad]
    results = []
    for e in ready:
        try:
            C.log(f"[publish] {e['id']} …")
            res = prov(e["platform"]).publish(e, Path(e["media"]))
            e.update(status=res.get("status", "published"), result=res, published_at=now().isoformat(timespec="seconds"))
            e.pop("error", None)
            C.log(f"[publish] {e['id']} → {res.get('status')} {res.get('url') or ''}")
        except Exception as ex:  # noqa: BLE001
            e.update(status="error", error=str(ex)[:600])
            C.log(f"[publish] {e['id']} FAILED: {ex}")
        _save(p, plan)                                   # resumable after every post
        results.append({"id": e["id"], "status": e["status"], "url": (e.get("result") or {}).get("url"),
                        "error": e.get("error")})
    _save(p, plan)
    return {"published": results, "waiting_for_local_scheduler": waiting, "not_approved": unapproved,
            **({"marked_error": bad} if bad else {})}


def cmd_run(a):
    p, plan = _load(a.plan)
    if a.yes:
        bad = {e["id"]: v for e in plan["entries"] if e.get("approved") and e.get("status") in ("planned", "error")
               and _due(e, False) and (v := _validate(e, for_run=True))}
        if bad:
            emit({"refused": "fix these first", "problems": bad})
            sys.exit(1)
    emit(run_plan(p, plan, a.yes, a.only))


def cmd_status(a):
    p, plan = _load(a.plan)
    out = []
    for e in plan["entries"]:
        if e.get("result") and e["status"] not in ("published",):
            try:
                e["remote"] = prov(e["platform"]).status(e)
            except Exception as ex:  # noqa: BLE001
                e["remote"] = {"error": str(ex)[:200]}
        out.append({"id": e["id"], "approved": e.get("approved"), "status": e.get("status"),
                    "url": (e.get("result") or {}).get("url"), "remote": e.get("remote"), "error": e.get("error")})
    _save(p, plan)
    emit(out)


def cmd_calendar(a):
    import calendar_ui
    paths = [Path(x).expanduser().resolve() for x in a.plans]
    for p in paths:
        if not p.exists():
            die(f"plan not found: {p}")
    calendar_ui.serve(paths, a.port, not a.no_open, validate=lambda e: _validate(e, network=False))


# ===================================================================== analytics
METRICS = ("views", "likes", "comments", "shares", "reposts", "saves", "score", "impressions", "reach", "reactions")


def refresh_stats(p: Path, plan: dict) -> list[dict]:
    done = []
    for e in plan["entries"]:
        res = e.get("result") or {}
        if e.get("status") in ("planned", "skipped", "error") or not (res.get("id") or res.get("url")):
            continue
        try:
            m = prov(e["platform"]).stats(e)
        except Exception as ex:  # noqa: BLE001
            m = {"note": f"stats failed: {str(ex)[:160]}"}
        snap = {"at": now().isoformat(timespec="seconds"), **{k: v for k, v in m.items() if v is not None}}
        e["metrics"] = snap
        e.setdefault("metrics_history", []).append(snap)
        e["metrics_history"] = e["metrics_history"][-60:]
        done.append({"id": e["id"], **snap})
    plan["analytics_at"] = now().isoformat(timespec="seconds")
    _save(p, plan)
    return done


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _plans_in(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(target.glob("publish_plan*.json"))
    return [target]


def cmd_analytics(a):
    target = Path(a.target).expanduser().resolve()
    plans = _plans_in(target)
    if not plans:
        die(f"no publish_plan*.json in {target}")
    rows = []
    for pp in plans:
        plan = json.loads(pp.read_text())
        if not a.no_refresh:
            refresh_stats(pp, plan)
        for e in plan["entries"]:
            if e.get("metrics"):
                rows.append({"plan": pp.name, "id": e["id"], "clip": e.get("clip"), "title": e.get("title"),
                             "platform": e["platform"], "url": (e.get("result") or {}).get("url"),
                             "published_at": e.get("published_at"), **e["metrics"]})
    by_platform: dict = {}
    by_clip: dict = {}
    for r in rows:
        bp = by_platform.setdefault(r["platform"], {"posts": 0, **{k: 0.0 for k in METRICS}})
        bc = by_clip.setdefault(r["title"], {"posts": 0, "platforms": [], **{k: 0.0 for k in METRICS}})
        bp["posts"] += 1
        bc["posts"] += 1
        bc["platforms"].append(r["platform"])
        for k in METRICS:
            bp[k] += _num(r.get(k))
            bc[k] += _num(r.get(k))
    eng = lambda d: _num(d.get("likes")) + _num(d.get("comments")) + _num(d.get("shares")) + _num(d.get("reposts")) \
        + _num(d.get("saves")) + _num(d.get("score"))
    top = sorted(rows, key=lambda r: (_num(r.get("views")), eng(r)), reverse=True)[:10]
    clips = sorted(({"title": t, **v} for t, v in by_clip.items()), key=lambda c: (c["views"], eng(c)), reverse=True)
    proj = target if target.is_dir() else target.parent
    report = {"generated": now().isoformat(timespec="seconds"), "posts_tracked": len(rows),
              "by_platform": {k: {m: round(x) if isinstance(x, float) else x for m, x in v.items()}
                              for k, v in by_platform.items()},
              "best_clips": [{**{m: round(x) if isinstance(x, float) else x for m, x in c.items()}} for c in clips[:10]],
              "top_posts": top, "notes": sorted({f"{r['platform']}: {r['note']}" for r in rows if r.get("note")})}
    (proj / "analytics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (proj / "analytics.html").write_text(_analytics_html(report, rows), encoding="utf-8")
    emit({k: report[k] for k in ("posts_tracked", "by_platform", "best_clips", "notes")} |
         {"report": str(proj / "analytics.html")})


def _analytics_html(rep_: dict, rows: list[dict]) -> str:
    import html as H
    mx = max([_num(r.get("views")) for r in rows] + [1])
    plat = "".join(f"<tr><td>{H.escape(k)}</td><td>{v['posts']}</td><td>{v['views']:,}</td><td>{v['likes']:,}</td>"
                   f"<td>{v['comments']:,}</td><td>{(v['shares'] + v['reposts']):,}</td></tr>"
                   for k, v in sorted(rep_["by_platform"].items(), key=lambda kv: -kv[1]["views"]))
    clips = "".join(f"<tr><td>{H.escape(c['title'] or '')}</td><td>{c['posts']}</td><td>{c['views']:,}</td>"
                    f"<td>{c['likes']:,}</td><td>{H.escape(', '.join(sorted(set(c['platforms']))))}</td></tr>"
                    for c in rep_["best_clips"])
    posts = "".join(
        f"<tr><td>{H.escape(r['platform'])}</td><td>{H.escape(r.get('title') or '')}</td>"
        f"<td><div class=bar style='width:{max(2, int(140 * _num(r.get('views')) / mx))}px'></div>{int(_num(r.get('views'))):,}</td>"
        f"<td>{int(_num(r.get('likes'))):,}</td><td>{int(_num(r.get('comments'))):,}</td>"
        f"<td>{('<a href=' + H.escape(r['url']) + ' target=_blank rel=noopener>open</a>') if r.get('url') else ''}</td>"
        f"<td class=dim>{H.escape(r.get('note') or '')}</td></tr>"
        for r in sorted(rows, key=lambda r: -_num(r.get("views"))))
    notes = "".join(f"<li>{H.escape(n)}</li>" for n in rep_["notes"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Post analytics</title><style>
:root{{--bg:#0d0d10;--card:#16161b;--line:#26262e;--text:#f2f2f5;--dim:#9a9aa6;--acc:#facc15}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,sans-serif;padding:24px 16px}}
h1{{font-size:20px}}h2{{font-size:15px;margin-top:28px}}table{{border-collapse:collapse;width:100%;max-width:1100px}}
td,th{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}}th{{color:var(--dim);font-weight:500}}
.bar{{display:inline-block;height:8px;background:var(--acc);border-radius:4px;margin-right:8px;vertical-align:middle}}
.dim{{color:var(--dim);font-size:12px}}a{{color:var(--acc)}}</style></head><body>
<h1>📊 Post analytics</h1><p class=dim>{rep_['posts_tracked']} posts tracked · updated {H.escape(rep_['generated'])}</p>
<h2>By platform</h2><table><tr><th>platform</th><th>posts</th><th>views</th><th>likes</th><th>comments</th><th>shares/reposts</th></tr>{plat}</table>
<h2>Best clips (all platforms)</h2><table><tr><th>clip</th><th>posts</th><th>views</th><th>likes</th><th>platforms</th></tr>{clips}</table>
<h2>Every post</h2><table><tr><th>platform</th><th>clip</th><th>views</th><th>likes</th><th>comments</th><th></th><th></th></tr>{posts}</table>
{('<h2>Notes</h2><ul class=dim>' + notes + '</ul>') if notes else ''}</body></html>"""


# ===================================================================== autopilot (standing approval)
def _ap() -> dict:
    return C._load("autopilot.json") or {"rules": []}


def _ap_save(d: dict) -> None:
    C._save("autopilot.json", d)


def _ap_log(msg: str) -> None:
    with open(C.home() / "autopilot.log", "a", encoding="utf-8") as f:
        f.write(f"{now().isoformat(timespec='seconds')} {msg}\n")


def autopilot_tick(rule: dict, dry: bool = False) -> dict:
    proj = Path(rule["project"])
    cj = proj / "clips.json"
    if not cj.exists():
        return {"rule": rule["id"], "skipped": "no clips.json"}
    pp = proj / "publish_plan.autopilot.json"
    plan = json.loads(pp.read_text()) if pp.exists() else {"created": now().isoformat(timespec="seconds"),
                                                            "project": str(proj), "autopilot": rule["id"], "entries": []}
    have = {e["clip"] for e in plan["entries"]}
    fresh = [c for c in sorted(json.loads(cj.read_text())["clips"], key=lambda c: -c.get("score", 0))
             if c["id"] not in have and c.get("score", 0) >= rule.get("min_score", 0) and Path(c["file"]).exists()
             and not c.get("issues")]
    added = []
    if fresh:
        taken: set = set()
        per_day: dict = {}
        for other in proj.glob("publish_plan*.json"):
            try:
                for e in json.loads(other.read_text())["entries"]:
                    if e.get("status") != "skipped" and e.get("schedule") not in (None, "now"):
                        taken.add(e["schedule"])
                        if other == pp:
                            per_day.setdefault(e["schedule"][:10], set()).add(e["clip"])
            except Exception:  # noqa: BLE001
                pass
        cands = slot_times(parse_slots(rule["slots"]), now().astimezone(), len(fresh) * 8 + 8, taken)
        cap = int(rule.get("max_per_day", 1))
        for clip in fresh:
            slot = next((t for t in cands if len(per_day.get(t.date().isoformat(), ())) < cap
                         and t.isoformat() not in taken), None)
            if not slot:
                break
            taken.add(slot.isoformat())
            per_day.setdefault(slot.date().isoformat(), set()).add(clip["id"])
            for e in build_entries(clip, rule["platforms"], slot.isoformat(), rule):
                e.update(approved=True, approved_at=now().isoformat(timespec="seconds"),
                         approved_via=f"autopilot:{rule['id']}")
                plan["entries"].append(e)
                added.append(e["id"])
    if dry:
        return {"rule": rule["id"], "would_schedule": added}
    _save(pp, plan)
    if added:
        _ap_log(f"rule {rule['id']}: scheduled {', '.join(added)}")
    res = run_plan(pp, plan, yes=True, retry_errors=False)
    for r in res.get("published", []):
        _ap_log(f"rule {rule['id']}: {r['id']} → {r['status']} {r.get('url') or r.get('error') or ''}")
    return {"rule": rule["id"], "scheduled": added, **res}


def cmd_autopilot(a):
    d = _ap()
    if a.action == "list":
        emit(d["rules"])
        return
    if a.action == "add":
        proj = Path(a.project or "").expanduser().resolve()
        if not (proj / "clips.json").exists():
            die("autopilot add needs a shorts-clipper project (folder with clips.json)")
        plats = ",".join(ALIASES.get(x.strip(), x.strip()) for x in a.platforms.split(",") if x.strip())
        for x in plats.split(","):
            prov(x)
        if not a.slots:
            die("--slots is required, e.g. 'mon-fri 18:00'")
        parse_slots(a.slots)
        if "youtube" in plats and a.youtube_privacy not in ("public", "unlisted", "private"):
            die("autopilot with youtube needs --youtube-privacy public|unlisted|private")
        if "reddit" in plats and not a.subreddit:
            die("autopilot with reddit needs --subreddit")
        rule = {"id": f"ap{len(d['rules']) + 1}-{now().strftime('%m%d%H%M')}", "project": str(proj), "platforms": plats,
                "slots": a.slots, "min_score": a.min_score, "max_per_day": a.max_per_day,
                "youtube_privacy": a.youtube_privacy, "tiktok_mode": a.tiktok_mode, "tiktok_privacy": a.tiktok_privacy,
                "page_id": a.page_id, "subreddit": a.subreddit, "enabled": True,
                "created_at": now().isoformat(timespec="seconds"),
                "consent": "standing approval given by the user for clips matching this rule"}
        d["rules"].append(rule)
        _ap_save(d)
        _ap_log(f"rule {rule['id']} created: {json.dumps({k: rule[k] for k in ('project', 'platforms', 'slots', 'min_score', 'max_per_day')})}")
        emit({"added": rule, "preview": autopilot_tick(rule, dry=True),
              "next": "install the scheduler (`scheduler --install`) so ticks run every 10 minutes"})
        return
    rule = next((r for r in d["rules"] if r["id"] == a.rule), None)
    if not rule:
        die(f"unknown rule {a.rule!r}; see `autopilot list`")
    if a.action in ("pause", "resume"):
        rule["enabled"] = a.action == "resume"
    elif a.action == "remove":
        d["rules"].remove(rule)
    elif a.action == "preview":
        emit(autopilot_tick(rule, dry=True))
        return
    _ap_save(d)
    _ap_log(f"rule {rule['id']} {a.action}")
    emit({a.action: rule["id"]})


# ===================================================================== tick (cron) + scheduler
def _registry() -> dict:
    return C._load("scheduled.json") or {"plans": []}


def cmd_tick(a):
    import fcntl
    lockf = open(C.home() / "tick.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                           # previous tick still running
    out = {"at": now().isoformat(timespec="seconds"), "autopilot": [], "plans": []}
    for rule in [r for r in _ap()["rules"] if r.get("enabled")]:
        try:
            out["autopilot"].append(autopilot_tick(rule))
        except Exception as ex:  # noqa: BLE001
            _ap_log(f"rule {rule['id']} ERROR {ex}")
            out["autopilot"].append({"rule": rule["id"], "error": str(ex)[:300]})
    plans = [Path(x) for x in _registry()["plans"]]
    plans += [Path(r["project"]) / "publish_plan.autopilot.json" for r in _ap()["rules"]]
    for pp in plans:
        if not pp.exists():
            continue
        plan = json.loads(pp.read_text())
        if not plan.get("autopilot") and str(pp) in _registry()["plans"]:
            out["plans"].append({"plan": str(pp), **run_plan(pp, plan, yes=True, retry_errors=False)})
            plan = json.loads(pp.read_text())
        last = parse_when(plan.get("analytics_at")) if plan.get("analytics_at") else None
        if a.analytics_every and (not last or now() - last > dt.timedelta(hours=a.analytics_every)):
            if any(e.get("result") for e in plan["entries"]):
                refresh_stats(pp, plan)
    print(json.dumps(out, ensure_ascii=False))


CRON_TAG = "# social-publish-tick"


def _cron_line() -> str:
    me = str(Path(__file__).resolve())
    return f'*/10 * * * * {sys.executable} "{me}" tick >> "{C.home() / "tick.log"}" 2>&1  {CRON_TAG}'


def _set_cron(enabled: bool) -> str:
    if not shutil.which("crontab"):
        die("crontab not found — run `publish.py tick` periodically yourself (e.g. a systemd timer)")
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    keep = [l for l in cur.splitlines() if CRON_TAG not in l and "# social-publish" not in l]
    if enabled:
        keep.append(_cron_line())
    subprocess.run(["crontab", "-"], input="\n".join(keep).strip() + "\n", text=True, check=True)
    return "installed" if enabled else "removed"


def cmd_scheduler(a):
    reg = _registry()
    if a.plan:
        plan = str(Path(a.plan).expanduser().resolve())
        if a.install and plan not in reg["plans"]:
            reg["plans"].append(plan)
        if a.remove and plan in reg["plans"]:
            reg["plans"].remove(plan)
        C._save("scheduled.json", reg)
    if a.install:
        state = _set_cron(True)
    elif a.remove and not reg["plans"] and not any(r.get("enabled") for r in _ap()["rules"]):
        state = _set_cron(False)
    else:
        state = "unchanged"
    emit({"cron": state, "line": _cron_line(), "plans": reg["plans"],
          "autopilot_rules": [r["id"] for r in _ap()["rules"] if r.get("enabled")], "log": str(C.home() / "tick.log")})


def cmd_post(a):
    platform = ALIASES.get(a.platform, a.platform)
    opts = {k: v for k, v in {"title": a.title, "privacy": a.privacy, "mode": a.mode, "page_id": a.page_id,
                               "tags": a.tags.split(",") if a.tags else None}.items() if v}
    if platform == "tiktok":
        opts.setdefault("mode", "draft")
    e = {"id": "one-off", "platform": platform, "media": str(Path(a.media).resolve()), "text": a.text or "",
         "options": opts, "schedule": a.at or "now", "title": a.title or ""}
    errs = _validate(e)
    if errs:
        die("; ".join(errs))
    if not a.yes:
        emit({"dry_run": True, "entry": e, "next": "after the user approves: same command with --yes"})
        return
    try:
        emit(prov(platform).publish(e, Path(e["media"])))
    except C.ApiError as ex:
        die(str(ex))


def main():
    ap = argparse.ArgumentParser(prog="publish.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup"); p.add_argument("platform")
    for f in ("--client-id", "--client-secret", "--app-id", "--app-secret", "--client-key", "--handle", "--app-password",
              "--media-host-cmd"):
        p.add_argument(f)
    p.set_defaults(fn=cmd_setup)
    p = sub.add_parser("auth"); p.add_argument("platform"); p.add_argument("--user-token", help="meta: Graph API Explorer token")
    p.add_argument("--page", help="meta: default Page id")
    p.add_argument("--channel", help="discord: channel id"); p.add_argument("--board", help="pinterest: default board id")
    p.add_argument("--redirect-url", help="threads: the https://localhost/... URL the browser landed on")
    p.set_defaults(fn=cmd_auth)
    p = sub.add_parser("whoami"); p.add_argument("platform", nargs="?"); p.set_defaults(fn=cmd_whoami)
    p = sub.add_parser("plan"); p.add_argument("project")
    p.add_argument("--platforms", default="youtube,tiktok,instagram,facebook,linkedin")
    p.add_argument("--top", type=int); p.add_argument("--start", help="first post time ISO 8601, e.g. 2026-11-05T18:00:00+06:00")
    p.add_argument("--every", type=float, default=24, help="hours between clips")
    p.add_argument("--slots", help="posting times, e.g. 'mon-fri 18:00, sat+sun 11:00' (next free slots, local time)")
    p.add_argument("--youtube-privacy", choices=["public", "unlisted", "private"])
    p.add_argument("--tiktok-mode", default="draft", choices=["draft", "direct"])
    p.add_argument("--tiktok-privacy", choices=["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"])
    p.add_argument("--page-id", help="facebook/instagram Page id (default: the one chosen at auth)")
    p.add_argument("--subreddit", help="reddit: target subreddit (without r/)")
    p.add_argument("--out"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("show"); p.add_argument("plan"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("approve"); p.add_argument("plan"); p.add_argument("--ids", help="ids or row numbers, comma-separated")
    p.add_argument("--all", action="store_true"); p.add_argument("--reject"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("check"); p.add_argument("plan"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("run"); p.add_argument("plan"); p.add_argument("--yes", action="store_true")
    p.add_argument("--only"); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("status"); p.add_argument("plan"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("calendar"); p.add_argument("plans", nargs="+"); p.add_argument("--port", type=int, default=8790)
    p.add_argument("--no-open", action="store_true"); p.set_defaults(fn=cmd_calendar)
    p = sub.add_parser("scheduler"); p.add_argument("plan", nargs="?")
    p.add_argument("--install", action="store_true"); p.add_argument("--remove", action="store_true")
    p.set_defaults(fn=cmd_scheduler)
    p = sub.add_parser("tick", help="cron entry point: autopilot + due posts + analytics refresh")
    p.add_argument("--analytics-every", type=float, default=6, help="hours between stats refreshes (0 = off)")
    p.set_defaults(fn=cmd_tick)
    p = sub.add_parser("analytics"); p.add_argument("target", help="plan file or project folder")
    p.add_argument("--no-refresh", action="store_true"); p.set_defaults(fn=cmd_analytics)
    p = sub.add_parser("autopilot"); p.add_argument("action", choices=["add", "list", "pause", "resume", "remove", "preview"])
    p.add_argument("rule", nargs="?"); p.add_argument("--project"); p.add_argument("--platforms", default="youtube")
    p.add_argument("--slots"); p.add_argument("--min-score", type=float, default=0)
    p.add_argument("--max-per-day", type=int, default=1)
    p.add_argument("--youtube-privacy", choices=["public", "unlisted", "private"])
    p.add_argument("--tiktok-mode", default="draft", choices=["draft", "direct"])
    p.add_argument("--tiktok-privacy"); p.add_argument("--page-id"); p.add_argument("--subreddit")
    p.set_defaults(fn=cmd_autopilot)
    p = sub.add_parser("post"); p.add_argument("--platform", required=True); p.add_argument("--media", required=True)
    p.add_argument("--text"); p.add_argument("--title"); p.add_argument("--privacy"); p.add_argument("--tags")
    p.add_argument("--mode", choices=["draft", "direct"]); p.add_argument("--page-id"); p.add_argument("--at")
    p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_post)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
