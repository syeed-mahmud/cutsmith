#!/usr/bin/env python3
"""youtube-seo: keyword research + title/description/tag scoring for YouTube.

Free, local keyword research built only on public signals:
  * YouTube search autocomplete  -> what people type (demand)
  * live YouTube search results  -> who you compete with (competition, competitor tags)
      - YouTube Data API v3 when YOUTUBE_API_KEY is set (exact stats, tags, subscribers)
      - otherwise yt-dlp (views, verified channels; tags for a few top videos)

All scores are transparent heuristics (see references/scoring.md), NOT real monthly
search volume — YouTube does not publish that.

Commands
  suggest  "<seed>"                 autocomplete expansion (a-z, questions, modifiers)
  keywords "<seed>" [--top 15]      expand + score demand / competition / opportunity
  analyze  "<keyword>"              deep dive: scores, top results, competitor tags, title patterns
  title    "<title>" [...] -k KW    0-100 title score with reasons (rank several)
  check    --title --description --tags -k KW [--shorts]   full metadata checklist
  trending [--region US]            most-popular videos + their tags (needs YOUTUBE_API_KEY)
Python 3.9+ standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
API = "https://www.googleapis.com/youtube/v3"
STOP = set("a an and are as at be but by for from how i if in is it of on or so that the this to was we "
           "what when where why with you your my me vs".split())
QUESTION_MODS = ["how to", "how do", "what is", "why", "best", "can you", "is it"]
SUFFIX_MODS = ["for beginners", "tutorial", "tips", "2026", "vs", "explained", "mistakes", "at home", "review"]
CURIOSITY = re.compile(r"\b(secret|nobody|no one|truth|mistake|wrong|myth|why|stop|never|actually|finally|"
                       r"hidden|real reason|surprising|shocking|crazy|insane|changed|ruined|caught|turning|"
                       r"before you|don't|doesn't|won't|warning)\b", re.I)
EMOTION = re.compile(r"\b(love|hate|scared|terrifying|amazing|best|worst|easy|fast|simple|ultimate|"
                     r"perfect|epic|brutal|honest|life|dream|regret)\b", re.I)
CLICHE = re.compile(r"(you won'?t believe|gone wrong|not clickbait|must watch|watch till the end|"
                    r"shocking truth|insane!+|omg)", re.I)
GENERIC_TAGS = {"shorts", "short", "viral", "fyp", "foryou", "trending", "youtube", "video", "subscribe",
                "funny", "new", "2026", "tiktok", "reels"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def emit(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def http_json(url: str, timeout: float = 10):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if "googleapis.com" in url:
            try:
                msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                msg = ""
            sys.exit(f"YouTube Data API error {e.code}: {msg or e.reason}"
                     + (" (quota used up for today?)" if e.code == 403 else ""))
        raise


# ======================================================================= autocomplete
_AC_CACHE: dict = {}


def autocomplete(q: str, hl: str = "en", gl: str = "US") -> list[str]:
    key = (q.lower(), hl, gl)
    if key in _AC_CACHE:
        return _AC_CACHE[key]
    url = ("https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&"
           + urllib.parse.urlencode({"q": q, "hl": hl, "gl": gl}))
    for attempt in range(3):
        try:
            data = http_json(url)
            out = [s for s in data[1] if isinstance(s, str)]
            _AC_CACHE[key] = out
            time.sleep(0.12)                      # be polite
            return out
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                log(f"[autocomplete] failed for {q!r}: {e}")
            time.sleep(0.6 * (attempt + 1))
    return []


def expand(seed: str, hl: str, gl: str, deep: bool) -> list[dict]:
    seed = seed.strip().lower()
    found: dict[str, dict] = {}

    def add(items, via):
        for rank, s in enumerate(items):
            s = s.strip().lower()
            if s and s not in found:
                found[s] = {"keyword": s, "via": via, "rank": rank}

    add(autocomplete(seed, hl, gl), "seed")
    for m in QUESTION_MODS:
        add(autocomplete(f"{m} {seed}", hl, gl), f"question:{m}")
    for m in SUFFIX_MODS:
        add(autocomplete(f"{seed} {m}", hl, gl), f"modifier:{m}")
    letters = "abcdefghijklmnopqrstuvwxyz" if deep else "abcdefghilmnoprstw"
    for ch in letters:
        add(autocomplete(f"{seed} {ch}", hl, gl), f"alphabet:{ch}")
    return list(found.values())


def demand_from_autocomplete(kw: str, hl: str, gl: str) -> dict:
    """How short a prefix still surfaces this exact keyword — popular searches appear
    after a few letters, rare ones only when typed in full."""
    kw = kw.strip().lower()
    own = [s.lower() for s in autocomplete(kw, hl, gl)]
    if kw not in own:
        return {"autocomplete": 8, "found": False, "min_prefix": None, "position": None}
    pos = own.index(kw)
    shortest = len(kw)
    for frac in (0.8, 0.62, 0.45, 0.3):
        n = max(2, int(len(kw) * frac))
        if n >= shortest:
            continue
        sugg = [s.lower() for s in autocomplete(kw[:n], hl, gl)]
        if kw in sugg:
            shortest = n
        else:
            break
    reach = 1 - shortest / len(kw)
    score = 100 * (0.25 + 0.6 * reach + 0.15 * (1 - pos / 10))
    return {"autocomplete": round(min(100, score)), "found": True, "min_prefix": kw[:shortest], "position": pos}


# ======================================================================= search results
def _ytdlp_cmd() -> list[str] | None:
    for py in (os.environ.get("SHORTS_CLIPPER_HOME", str(Path.home() / ".shorts-clipper")) + "/venv/bin/python",
               sys.executable):
        if Path(py).exists() and subprocess.run([py, "-c", "import yt_dlp"], capture_output=True).returncode == 0:
            return [py, "-m", "yt_dlp"]
    return [shutil.which("yt-dlp")] if shutil.which("yt-dlp") else None


def search_results(kw: str, n: int, region: str, with_details: int = 0) -> tuple[list[dict], str]:
    """Top-n results for a query: [{title, channel, views, verified, subs?, published?, tags?}]."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if key:
        return _search_api(kw, n, region, key), "youtube-data-api"
    cmd = _ytdlp_cmd()
    if not cmd:
        return [], "none"
    p = subprocess.run(cmd + ["--flat-playlist", "-J", "--no-warnings", f"ytsearch{n}:{kw}"],
                       capture_output=True, text=True, timeout=90)
    if p.returncode != 0:
        log(f"[search] yt-dlp failed: {p.stderr[-300:]}")
        return [], "none"
    rows = []
    for e in json.loads(p.stdout).get("entries", []):
        rows.append({"title": e.get("title") or "", "channel": e.get("channel") or "",
                     "views": e.get("view_count"), "verified": bool(e.get("channel_is_verified")),
                     "duration": e.get("duration"), "url": e.get("url") or f"https://youtu.be/{e.get('id')}"})
    for r in rows[:with_details]:               # tags / date / subscribers need a per-video lookup
        q = subprocess.run(cmd + ["-J", "--skip-download", "--no-warnings", "--no-playlist", r["url"]],
                           capture_output=True, text=True, timeout=90)
        if q.returncode == 0:
            info = json.loads(q.stdout)
            r.update(tags=info.get("tags") or [], published=info.get("upload_date"),
                     subs=info.get("channel_follower_count"))
    return rows, "yt-dlp"


def _search_api(kw: str, n: int, region: str, key: str) -> list[dict]:
    s = http_json(f"{API}/search?" + urllib.parse.urlencode(
        {"part": "snippet", "q": kw, "type": "video", "maxResults": min(n, 50), "regionCode": region, "key": key}))
    ids = [it["id"]["videoId"] for it in s.get("items", [])]
    if not ids:
        return []
    v = http_json(f"{API}/videos?" + urllib.parse.urlencode(
        {"part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": key}))
    chans = {it["snippet"]["channelId"] for it in v.get("items", [])}
    c = http_json(f"{API}/channels?" + urllib.parse.urlencode(
        {"part": "statistics", "id": ",".join(chans), "key": key}))
    subs = {it["id"]: int(it["statistics"].get("subscriberCount", 0) or 0) for it in c.get("items", [])}
    rows = []
    for it in v.get("items", []):
        sn, st = it["snippet"], it.get("statistics", {})
        rows.append({"title": sn["title"], "channel": sn["channelTitle"], "views": int(st.get("viewCount", 0) or 0),
                     "subs": subs.get(sn["channelId"]), "verified": None, "published": sn["publishedAt"][:10],
                     "tags": sn.get("tags", []), "url": f"https://youtu.be/{it['id']}"})
    return rows


def _log01(x: float, lo: float, hi: float) -> float:
    if not x or x <= 0:
        return 0.0
    return max(0.0, min(1.0, (math.log10(x) - lo) / (hi - lo)))


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", s.lower()) if t not in STOP]


def competition(kw: str, rows: list[dict]) -> dict:
    """0-100: how hard the first page is to break into."""
    if not rows:
        return {"competition": None}
    toks = set(_tokens(kw)) or set(kw.lower().split())
    match = sum(1 for r in rows if toks <= set(_tokens(r["title"]))) / len(rows)
    views = [r["views"] for r in rows if r.get("views")]
    med_views = statistics.median(views) if views else 0
    if any(r.get("subs") for r in rows):
        auth = statistics.mean(_log01(r.get("subs") or 0, 3, 7) for r in rows)        # 1k .. 10M subs
    else:
        auth = sum(1 for r in rows if r.get("verified")) / len(rows)
    strength = _log01(med_views, 3, 7)                                                  # 1k .. 10M views
    comp = 100 * (0.35 * match + 0.35 * auth + 0.30 * strength)
    return {"competition": round(comp), "title_match_share": round(match, 2), "authority": round(auth, 2),
            "median_views": int(med_views)}


def score_keyword(kw: str, hl: str, gl: str, n_results: int = 10, details: int = 0) -> dict:
    d = demand_from_autocomplete(kw, hl, gl)
    rows, source = search_results(kw, n_results, gl, details)
    c = competition(kw, rows)
    views_sig = _log01(c.get("median_views") or 0, 3, 7)
    demand = round(0.65 * d["autocomplete"] + 0.35 * 100 * views_sig) if rows else d["autocomplete"]
    comp = c.get("competition")
    opp = round(0.55 * demand + 0.45 * (100 - comp)) if comp is not None else None
    return {"keyword": kw, "demand": demand, "competition": comp, "keyword_score": opp,
            "verdict": _verdict(demand, comp), "autocomplete": d, "competition_detail": c,
            "source": source, "results": rows}


def _verdict(demand: int, comp: int | None) -> str:
    if comp is None:
        return "demand only (no search data)"
    if demand >= 60 and comp <= 55:
        return "great — real demand, beatable competition"
    if demand >= 60:
        return "popular but crowded — go more specific (long-tail)"
    if demand >= 35 and comp <= 50:
        return "good niche target"
    if demand < 35:
        return "low demand — use as a secondary tag, not the main keyword"
    return "ok"


# ======================================================================= title score
def score_title(title: str, kw: str | None) -> dict:
    t = title.strip()
    s, why = 50, []
    n = len(t)
    if 40 <= n <= 60:
        s += 15; why.append(f"+15 length {n} (40-60 sweet spot)")
    elif 61 <= n <= 70:
        s += 7; why.append(f"+7 length {n} (ok, may truncate on mobile >60)")
    elif n < 30:
        s -= 10; why.append(f"-10 length {n} (too short to be specific)")
    elif n > 70:
        s -= 15; why.append(f"-15 length {n} (gets cut off in search)")
    else:
        s += 4; why.append(f"+4 length {n}")
    if kw:
        kl, tl = kw.lower(), t.lower()
        ktoks = _tokens(kw)
        if kl in tl:
            pos = tl.index(kl)
            if pos <= len(tl) * 0.4:
                s += 18; why.append("+18 exact keyword, front-loaded")
            else:
                s += 10; why.append("+10 exact keyword (move it earlier)")
        elif ktoks and all(k in _tokens(t) for k in ktoks):
            s += 8; why.append("+8 keyword words present (not as a phrase)")
        else:
            s -= 20; why.append("-20 main keyword missing")
    if re.search(r"\d", t):
        s += 6; why.append("+6 specific number")
    if CURIOSITY.search(t):
        s += 8; why.append("+8 curiosity hook")
    if EMOTION.search(t):
        s += 4; why.append("+4 emotional/benefit word")
    caps = [w for w in re.findall(r"[A-Za-z]{3,}", t) if w.isupper()]
    if len(caps) > 2:
        s -= 12; why.append(f"-12 {len(caps)} ALL-CAPS words (shouting; 1-2 max)")
    if re.search(r"[!?]{2,}", t):
        s -= 8; why.append("-8 repeated punctuation")
    if CLICHE.search(t):
        s -= 12; why.append("-12 clickbait cliché (promise a specific payoff instead)")
    if t.endswith("?"):
        s -= 3; why.append("-3 ends with a question (tends to underperform)")
    if re.search(r"#\w+", t):
        s -= 4; why.append("-4 hashtag in title (put hashtags in the description)")
    s = max(0, min(100, s))
    band = "strong" if s >= 80 else "working" if s >= 40 else "weak"
    return {"title": t, "score": s, "band": band, "reasons": why}


# ======================================================================= metadata checklist
CHAPTER = re.compile(r"^\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s+\S", re.M)


def _secs(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def check_meta(title: str, description: str, tags: list[str], kw: str | None, shorts: bool) -> dict:
    items = []

    def item(ok, weight, label, fix=""):
        items.append({"ok": bool(ok), "weight": weight, "check": label, **({} if ok else {"fix": fix})})

    tscore = score_title(title, kw) if title else {"score": 0}
    item(tscore["score"] >= 60, 3, f"title score {tscore['score']}/100", "see `title` reasons")
    d = description or ""
    words = re.findall(r"\w+", d)
    first25 = " ".join(words[:25]).lower()
    body = re.sub(r"#\w+", "", d)
    if kw:
        item(kw.lower() in first25 or all(k in _tokens(first25) for k in _tokens(kw)), 3,
             "keyword in the first 25 words of the description", "open with a sentence that uses the keyword")
    item(len(body.strip().split("\n")[0]) >= 40, 2, "hook line before 'Show more' (first ~150 chars)",
         "first line should say what the viewer gets")
    if shorts:
        item(len(words) >= 15, 1, "description ≥ 15 words (Shorts)", "add 1-2 sentences of context")
    else:
        item(len(words) >= 200, 2, f"description ≥ 200 words (has {len(words)})", "expand: summary, key points, links")
    hashtags = re.findall(r"#\w+", d)
    item(1 <= len(hashtags) <= 5, 1, f"2-3 hashtags (has {len(hashtags)})",
         "add 2-3 specific hashtags at the end" if not hashtags else "keep 2-3; YouTube shows only 3")
    item(len(hashtags) <= 15, 2, "≤ 15 hashtags (more than 15 → YouTube ignores all)", "remove hashtags")
    chapters = [_secs(m) for m in CHAPTER.findall(d)]
    if chapters:
        valid = (chapters[0] == 0 and len(chapters) >= 3
                 and all(b - a >= 10 for a, b in zip(chapters, chapters[1:])))
        item(valid, 2, f"chapters valid ({len(chapters)} timestamps)",
             "first 00:00, at least 3, ascending, each ≥ 10 s apart")
    elif not shorts:
        item(False, 1, "chapters/timestamps", "add 00:00-style chapters for videos > 3 min")
    tl = [x.strip() for x in tags if x.strip()]
    lo, hi = (3, 8) if shorts else (5, 15)
    item(lo <= len(tl) <= hi, 2, f"{lo}-{hi} tags (has {len(tl)})", "relevant, specific tags only")
    item(sum(len(x) for x in tl) + max(0, len(tl) - 1) <= 500, 2, "tags ≤ 500 characters total", "trim tags")
    if kw:
        item(any(kw.lower() == x.lower() for x in tl), 2, "exact main keyword is a tag", f"add tag '{kw}'")
    dup = [x for x, c in Counter(x.lower() for x in tl).items() if c > 1]
    item(not dup, 1, "no duplicate tags", f"remove {dup}")
    generic = [x for x in tl if x.lower().strip("#") in GENERIC_TAGS]
    item(not generic, 1, "no generic tags (shorts, viral, fyp…)", f"replace {generic} with specific ones")
    total = sum(i["weight"] for i in items)
    got = sum(i["weight"] for i in items if i["ok"])
    return {"seo_score": round(100 * got / total), "checks": items, "title": tscore}


# ======================================================================= commands
def cmd_suggest(a):
    rows = expand(a.seed, a.lang, a.region, a.deep)
    emit({"seed": a.seed, "count": len(rows), "suggestions": rows})


def cmd_keywords(a):
    rows = expand(a.seed, a.lang, a.region, a.deep)
    seed = a.seed.strip().lower()
    # prefer suggestions from the seed itself + modifiers; keep variety
    rows.sort(key=lambda r: (r["via"] != "seed", r["rank"]))
    cands = [seed] + [r["keyword"] for r in rows if r["keyword"] != seed][: a.top - 1]
    log(f"[keywords] scoring {len(cands)} keywords (autocomplete + search results)…")
    scored = []
    for kw in cands:
        r = score_keyword(kw, a.lang, a.region, 10, 0)
        r.pop("results")
        scored.append(r)
        log(f"  {r['keyword_score'] if r['keyword_score'] is not None else '–':>3}  {kw}")
    scored.sort(key=lambda r: -(r["keyword_score"] if r["keyword_score"] is not None else r["demand"]))
    out = {"seed": a.seed, "region": a.region, "source": scored[0]["source"] if scored else None,
           "note": "demand/competition are 0-100 heuristics from autocomplete + live results, not search volume",
           "keywords": [{k: r[k] for k in ("keyword", "keyword_score", "demand", "competition", "verdict")}
                        for r in scored],
           "more_suggestions": [r["keyword"] for r in rows if r["keyword"] not in cands][:60]}
    if a.out:
        Path(a.out).write_text(_keywords_md(out), encoding="utf-8")
        out["markdown"] = a.out
    emit(out)


def _keywords_md(o):
    lines = [f"# Keyword research — {o['seed']} ({o['region']})", "", o["note"], "",
             "| keyword | score | demand | competition | verdict |", "|---|---|---|---|---|"]
    for k in o["keywords"]:
        lines.append(f"| {k['keyword']} | {k['keyword_score']} | {k['demand']} | {k['competition']} | {k['verdict']} |")
    lines += ["", "More ideas: " + ", ".join(o["more_suggestions"])]
    return "\n".join(lines) + "\n"


def cmd_analyze(a):
    r = score_keyword(a.keyword, a.lang, a.region, a.results, a.details)
    rows = r["results"]
    tags = Counter(t.lower() for row in rows for t in (row.get("tags") or []))
    words = Counter(w for row in rows for w in _tokens(row["title"]) if w not in _tokens(a.keyword))
    lengths = [len(row["title"]) for row in rows]
    r["competitor_tags"] = [{"tag": t, "videos": c} for t, c in tags.most_common(30)]
    r["title_words_in_top_results"] = [w for w, _ in words.most_common(15)]
    r["top_title_length_median"] = int(statistics.median(lengths)) if lengths else None
    r["related"] = autocomplete(a.keyword, a.lang, a.region)
    emit(r)


def cmd_title(a):
    res = sorted((score_title(t, a.keyword) for t in a.titles), key=lambda x: -x["score"])
    emit(res if len(res) > 1 else res[0])


def cmd_check(a):
    desc = Path(a.description_file).read_text(encoding="utf-8") if a.description_file else (a.description or "")
    tags = [t for t in re.split(r"[,\n]", a.tags or "") if t.strip()]
    emit(check_meta(a.title or "", desc, tags, a.keyword, a.shorts))


def cmd_trending(a):
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit("trending needs YOUTUBE_API_KEY (free from Google Cloud Console → YouTube Data API v3)")
    params = {"part": "snippet,statistics", "chart": "mostPopular", "regionCode": a.region, "maxResults": 50,
              "key": key}
    if a.category:
        params["videoCategoryId"] = a.category
    v = http_json(f"{API}/videos?" + urllib.parse.urlencode(params))
    rows = [{"title": it["snippet"]["title"], "channel": it["snippet"]["channelTitle"],
             "views": int(it["statistics"].get("viewCount", 0) or 0), "tags": it["snippet"].get("tags", [])[:15]}
            for it in v.get("items", [])]
    tags = Counter(t.lower() for r in rows for t in r["tags"])
    emit({"region": a.region, "videos": rows, "top_tags": tags.most_common(40)})


def main():
    ap = argparse.ArgumentParser(prog="ytseo.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def loc(p):
        p.add_argument("--lang", default="en", help="interface language for autocomplete (hl)")
        p.add_argument("--region", default="US", help="country code (gl / regionCode)")

    p = sub.add_parser("suggest"); p.add_argument("seed"); p.add_argument("--deep", action="store_true"); loc(p)
    p.set_defaults(fn=cmd_suggest)
    p = sub.add_parser("keywords"); p.add_argument("seed"); p.add_argument("--top", type=int, default=15)
    p.add_argument("--deep", action="store_true"); p.add_argument("--out", help="also write a markdown table here"); loc(p)
    p.set_defaults(fn=cmd_keywords)
    p = sub.add_parser("analyze"); p.add_argument("keyword"); p.add_argument("--results", type=int, default=10)
    p.add_argument("--details", type=int, default=5, help="videos to fetch tags/date for (yt-dlp mode)"); loc(p)
    p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("title"); p.add_argument("titles", nargs="+"); p.add_argument("-k", "--keyword")
    p.set_defaults(fn=cmd_title)
    p = sub.add_parser("check"); p.add_argument("--title"); p.add_argument("--description")
    p.add_argument("--description-file"); p.add_argument("--tags", help="comma-separated")
    p.add_argument("-k", "--keyword"); p.add_argument("--shorts", action="store_true")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("trending"); p.add_argument("--region", default="US"); p.add_argument("--category")
    p.set_defaults(fn=cmd_trending)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
