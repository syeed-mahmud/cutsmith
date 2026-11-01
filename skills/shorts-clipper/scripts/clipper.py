#!/usr/bin/env python3
"""shorts-clipper: long video -> ranked, captioned, reframed vertical clips.

Commands
  doctor                         check ffmpeg, python deps, fonts, face model
  prep <video|url>               transcribe + analyse + propose candidates
  candidates <project>           re-rank candidate windows (new length/keywords)
  render <project>               render the clips listed in picks.json
  auto <video|url> --clips N     prep + pick by signal score + render (no review)
  edit <video>                   whole-video tools: captions / 9:16 reframe / jump-cut
  export <project>               per-platform re-encodes + size/codec checks
  action <video>                 montage cut on every impact sound (sports, chops, drums)
  edit-clip <project> --clip N    edit ops (trim/split/reorder/delete phrase/overlays/style/censor)
  preview <project>              HTML gallery of the ranked clips
  thumbnail <project>            designed thumbnails (best face frame + headline)
  translate <project>            export sentences for caption translation
  brand save|list|show NAME      brand kits: style preset + logo watermark
  style-scan <video>             measure a reference video's editing style
  sheet <clip.mp4>               contact sheet for visual QA
  board <project>                print the leaderboard

Run `clipper.py <command> -h` for options. Paths printed on stdout are JSON.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze as A           # noqa: E402
import config as CFG          # noqa: E402
from common import (Project, die, ffprobe, fmt_ts, fonts_dir, home_dir, is_url,  # noqa: E402
                    load_json, log, parse_range, save_json, slugify)


def emit(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# ===================================================================== doctor
def cmd_doctor(args) -> None:
    import importlib
    report, ok = {}, True
    for b in ("ffmpeg", "ffprobe"):
        report[b] = bool(shutil.which(b))
        ok &= report[b]
    if report["ffmpeg"]:
        from common import run
        filters = run(["ffmpeg", "-hide_banner", "-filters"], check=False).stdout
        for f in ("subtitles", "loudnorm", "silencedetect", "gblur"):
            report[f"ffmpeg_filter_{f}"] = f" {f} " in filters
            ok &= report[f"ffmpeg_filter_{f}"]
    for mod in ("numpy", "cv2", "faster_whisper", "yt_dlp"):
        try:
            importlib.import_module(mod)
            report[f"python_{mod}"] = True
        except Exception:
            report[f"python_{mod}"] = False
            ok &= mod == "yt_dlp"          # yt-dlp only needed for URLs
    from faces import model_path
    report["face_model"] = model_path(download=False).exists()
    fonts = sorted(p.name for p in fonts_dir().glob("*.ttf")) if fonts_dir().exists() else []
    report["fonts"] = fonts or "none (system fallback fonts will be used)"
    if report["ffmpeg"]:
        from render import pick_encoder
        report["video_encoder"] = pick_encoder("auto")[0]
    try:
        import ctranslate2
        report["cuda_devices"] = ctranslate2.get_cuda_device_count()
    except Exception:
        report["cuda_devices"] = 0
    report["home"] = str(home_dir())
    report["ready"] = bool(ok)
    emit(report)
    if not ok:
        sys.exit(1)


# ===================================================================== prep
def _default_project_dir(source: str) -> Path:
    if is_url(source):
        tail = slugify(source.split("v=")[-1].split("/")[-1].split("?")[0], 24)
        return Path.cwd() / f"shorts-{tail}"
    p = Path(source).expanduser().resolve()
    return p.parent / f"{p.stem}-shorts"


def cmd_prep(args) -> dict:
    import ingest
    import scoring
    from transcribe import transcribe

    proj = Project(args.out or _default_project_dir(args.source)).ensure()
    changes = CFG.apply_length(CFG.parse_set(args.set), args.length)
    if args.lang:
        changes["language"] = args.lang
    if getattr(args, "aspect", None):
        changes["aspect"] = args.aspect
    cfg = CFG.update(proj.work, changes)
    src = ingest.resolve_source(args.source, proj)
    meta = ingest.probe_source(src)
    rng = parse_range(args.range)
    if rng:
        rng = (max(0.0, rng[0]), min(meta["duration"], rng[1]))

    st = proj.state()
    key = {"source": str(src), "size": meta["size_bytes"], "range": rng, "model": args.model,
           "language": cfg.get("language")}
    words_path = proj.p("words.json")
    if words_path.exists() and st.get("transcript_key") == key and not args.force:
        log("[prep] transcript cached — reusing (use --force to redo)")
        tr = load_json(words_path)
    else:
        wav = proj.p("audio16k.wav")
        ingest.extract_audio(src, wav, rng)
        tr = transcribe(wav, cfg, args.model, cfg.get("language"), offset=rng[0] if rng else 0.0)
        save_json(words_path, tr)
        st["transcript_key"] = key
    wav = proj.p("audio16k.wav")
    if not wav.exists():
        ingest.extract_audio(src, wav, rng)
    off = rng[0] if rng else 0.0
    pauses = A.detect_silences(wav, cfg["silence_min"], offset=off, fixed_db=cfg["silence_noise_db"])
    energy = A.energy_curve(wav, offset=off)
    words = tr["words"]
    if not words:
        die("no speech found in the source")
    sents = A.sentences(words, cfg["sentence_pause"], cfg["max_sentence_words"])
    save_json(proj.p("pauses.json"), pauses)
    save_json(proj.p("energy.json"), energy)
    save_json(proj.p("sentences.json"), sents)
    A.write_transcript_md(proj.p("transcript.md"), sents, energy, meta, len(words), tr["language"])
    from lib import mistakes as MK
    mistakes = MK.find_candidates(words)
    save_json(proj.p("mistakes.json"), mistakes)
    proj.p("mistakes.md").write_text(MK.to_markdown(mistakes), encoding="utf-8")

    analysed = (rng[1] - rng[0]) if rng else meta["duration"]
    n = args.clips or CFG.default_clip_count(cfg, analysed)
    st.update({"source": str(src), "source_input": args.source, "meta": meta, "range": rng,
               "language": tr["language"], "clips_requested": n,
               "keywords": args.keywords.split(",") if args.keywords else None,
               "prompt": args.prompt or st.get("prompt"), "genre": args.genre or st.get("genre"),
               "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
    proj.save_state(st)
    cands = _write_candidates(proj, cfg, words, sents, pauses, energy, n, st.get("keywords"), rng)
    summary = {
        "project": str(proj.root),
        "source": str(src),
        "duration": fmt_ts(meta["duration"]),
        "analysed": fmt_ts(analysed),
        "resolution": f"{meta.get('width')}x{meta.get('height')}" if meta.get("has_video") else "audio-only",
        "language": tr["language"],
        "language_confidence": tr.get("language_probability"),
        "words": len(words),
        "clips_requested": n,
        "clip_length": f"{cfg['clip_min']:.0f}-{cfg['clip_max']:.0f}s",
        "candidates": len(cands),
        "read_next": [str(proj.p("transcript.md")), str(proj.p("candidates.md"))],
        "spoken_mistakes": {"counts": mistakes["counts"], "review": str(proj.p("mistakes.md"))},
        "write_picks_to": str(proj.p("picks.json")),
    }
    emit(summary)
    return summary


def _write_candidates(proj, cfg, words, sents, pauses, energy, n, keywords, rng):
    st = proj.state()
    import scoring
    top = max(12, n * 3)
    cands = scoring.generate(words, sents, pauses, energy, cfg, top, keywords, rng)
    save_json(proj.p("candidates.json"), cands)
    brief = [f"**User brief:** {st['prompt']}" if st.get("prompt") else "",
             f"**Genre:** {st['genre']}" if st.get("genre") else "",
             f"**Focus keywords:** {', '.join(keywords)}" if keywords else ""]
    lines = [x for x in brief if x] + ["# Candidate windows (signal-ranked prior — YOU decide)",
             f"{len(cands)} sentence-aligned windows, {cfg['clip_min']:.0f}-{cfg['clip_max']:.0f}s, "
             "sorted by time. signal = hook/standalone/payoff/energy/info/pace (0-100).", "",
             "| id | start | end | dur | signal | opening … ending |", "|---|---|---|---|---|---|"]
    for c in cands:
        lines.append(f"| {c['id']} | {fmt_ts(c['start'])} | {fmt_ts(c['end'])} | {c['duration']:.0f}s | "
                     f"{c['signal']:.0f} | {c['opening']} … {c['ending']} |")
    proj.p("candidates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cands


def cmd_candidates(args) -> None:
    proj = Project.find(args.project)
    changes = CFG.apply_length(CFG.parse_set(args.set), args.length)
    cfg = CFG.update(proj.work, changes)
    st = proj.state()
    if args.keywords is not None:
        st["keywords"] = [k for k in args.keywords.split(",") if k] or None
    if args.clips:
        st["clips_requested"] = args.clips
    proj.save_state(st)
    words = load_json(proj.p("words.json"))["words"]
    sents = A.sentences(words, cfg["sentence_pause"], cfg["max_sentence_words"])
    cands = _write_candidates(proj, cfg, words, sents, load_json(proj.p("pauses.json")),
                              load_json(proj.p("energy.json")), st["clips_requested"],
                              st.get("keywords"), tuple(st["range"]) if st.get("range") else None)
    emit({"candidates": len(cands), "read_next": str(proj.p("candidates.md"))})


# ===================================================================== render
def _load_picks(path: Path) -> list[dict]:
    data = load_json(path)
    picks = data.get("clips", data) if isinstance(data, dict) else data
    if not isinstance(picks, list) or not picks:
        die(f"{path} must be a JSON list of picks (see SKILL.md)")
    from common import parse_ts
    out = []
    for i, p in enumerate(picks, 1):
        q = dict(p)
        if q.get("sections"):
            q["sections"] = [[parse_ts(a), parse_ts(b)] for a, b in q["sections"]]
            q["start"] = min(a for a, _ in q["sections"])
            q["end"] = max(b for _, b in q["sections"])
        elif "start" not in p or "end" not in p:
            die(f"pick #{i} needs start and end (or sections)")
        else:
            q["start"], q["end"] = parse_ts(p["start"]), parse_ts(p["end"])
        out.append(q)
    return out


def cmd_render(args) -> None:
    import framing as FR
    import render as R
    import scoring
    from snap import snap

    proj = Project.find(args.project)
    changes = CFG.parse_set(args.set)
    for k, v in (("caption_style", args.style), ("two_face_mode", args.two_face), ("pace", args.pace),
                 ("aspect", getattr(args, "aspect", None)), ("brand", getattr(args, "brand", None))):
        if v:
            changes[k] = v
    if args.hook is not None:
        changes["hook_overlay"] = args.hook
    if getattr(args, "censor", None):
        changes["censor"] = None if args.censor == "off" else args.censor
    if args.progress_bar is not None:
        changes["progress_bar"] = args.progress_bar
    if getattr(args, "brand", None):
        # a newly chosen brand kit beats older project-level choices for the keys it defines;
        # flags given in this same command still win (they are applied after)
        brand_keys = set(CFG.load_brand(args.brand).get("set", {}))
        current = load_json(proj.work / "settings.json", default={})
        save_json(proj.work / "settings.json", {k: v for k, v in current.items() if k not in brand_keys})
    cfg = CFG.update(proj.work, changes)
    if not R.have_libass():
        die("this ffmpeg has no 'subtitles' filter (libass). Install a full ffmpeg build.")

    st = proj.state()
    src, meta = st["source"], st["meta"]
    words = load_json(proj.p("words.json"))["words"]
    pauses = load_json(proj.p("pauses.json"), default=[])
    energy = load_json(proj.p("energy.json"), default={})
    picks = _load_picks(Path(args.picks) if args.picks else proj.p("picks.json"))
    mistakes = load_json(proj.p("mistakes.json"), default={"candidates": []})["candidates"]
    from common import parse_ts
    only = {int(x) for x in args.only.split(",")} if args.only else None
    broll = str(Path(args.broll).expanduser().resolve()) if args.broll else None
    if broll and not Path(broll).exists():
        die(f"b-roll not found: {broll}")

    prev = {c["id"]: c for c in load_json(proj.root / "clips.json", default={"clips": []})["clips"]}
    clips_out = []
    for i, p in enumerate(picks, 1):
        if only and i not in only:
            if i in prev:
                clips_out.append(prev[i])
            continue
        t0 = time.time()
        if p.get("sections"):
            # each section snaps to word/sentence edges; length limits apply to the whole clip only
            loose = dict(cfg, clip_min=0.3, clip_max=1e9)
            sections = [list(snap(words, a, b, loose, meta["duration"])[:2]) for a, b in p["sections"]]
            s, e, how = min(a for a, _ in sections), max(b for _, b in sections), {"sections": len(sections)}
        else:
            s, e, how = snap(words, p["start"], p["end"], cfg, meta["duration"])
            sections = [[s, e]]
        layout = p.get("layout") or args.layout
        remove = [[parse_ts(a), parse_ts(b)] for a, b in (p.get("remove") or [])]
        if cfg.get("auto_cut_stutters"):
            remove += [[m["cut"]["start"], m["cut"]["end"]] for m in mistakes
                       if m["type"] == "stutter" and m["confidence"] == "high"]
        clip = {"id": i, "title": p.get("title") or f"Clip {i}", "hook": p.get("hook"),
                "remove_ranges": remove,
                "reason": p.get("reason", ""), "caption": p.get("caption", ""),
                "hashtags": p.get("hashtags", []), "judge": p.get("judge"),
                "style": p.get("style") or cfg["caption_style"],
                "scores": p.get("scores"), "posts": p.get("posts"), "thumbnail_text": p.get("thumbnail_text"),
                "overlays": p.get("overlays"), "caption_look": p.get("caption_look"),
                "caption_fixes": p.get("caption_fixes"), "censor": p.get("censor"),
                "translation": _load_translation(p.get("translation"), proj),
                "raw_start": p["start"], "raw_end": p["end"], "start": s, "end": e, "snap": how,
                "sections": sections}
        if clip["judge"] is None and clip["scores"]:
            sc = clip["scores"]
            clip["judge"] = round(0.35 * sc.get("hook", 50) + 0.25 * sc.get("coherence", 50)
                                  + 0.2 * sc.get("connection", 50) + 0.2 * sc.get("trend", 50))
        if not cfg.get("hook_overlay"):
            clip["hook"] = None
        cached = prev.get(i) or {}
        clip_broll = broll or cached.get("broll")
        frames = []
        cached_frames = {tuple(sec): f for sec, f in zip(cached.get("sections") or [], cached.get("frames") or [])}
        for a, b in sections:
            f = cached_frames.get((a, b))
            if (f and f.get("v") == FR.PLAN_VERSION and cached.get("layout_req") == layout
                    and cached.get("broll") == clip_broll and cached.get("aspect") == cfg["aspect"]):
                frames.append(f)
            else:
                log(f"[render] clip {i}: framing ({fmt_ts(a)}–{fmt_ts(b)})")
                frames.append(FR.plan(src, meta, a, b, cfg, None if layout == "auto" else layout, clip_broll))
        clip["frames"], clip["frame"] = frames, frames[0]
        clip["layout_req"], clip["broll"], clip["aspect"] = layout, clip_broll, cfg["aspect"]

        sig, parts = scoring.signal_score(words, pauses, energy, s, e, cfg, st.get("keywords"))
        clip["signal"], clip["signal_parts"] = sig, parts
        j = clip["judge"]
        clip["score"] = round(cfg["judge_weight"] * float(j) + (1 - cfg["judge_weight"]) * sig) \
            if j is not None else round(sig)

        name = f"{i:02d}_{slugify(clip['title'])}"
        out_mp4 = proj.root / f"{name}.mp4"
        for old in proj.root.glob(f"{i:02d}_*"):          # replace an older render of this id
            if old.suffix in (".mp4", ".jpg"):
                old.unlink()
        log(f"[render] clip {i}: {clip['frame']['layout']} · style={clip['style']} · rendering…")
        res = R.render_clip(src, meta, clip, words, pauses, cfg, out_mp4, proj.work)
        clip.update(file=str(out_mp4), out_duration=res["out_duration"], jumpcuts=res["cuts"],
                    removed_sec=res["removed_sec"], timemap=res["timemap"], censored=res["censored"])
        clip.pop("translation", None)
        thumb = proj.root / f"{name}.jpg"
        if clip.get("thumbnail_text"):
            import thumbs
            thumbs.design(src, meta, clip, cfg, thumb, clip["thumbnail_text"], proj.work,
                          "9:16" if cfg["out_h"] > cfg["out_w"] else cfg["aspect"])
        else:
            R.thumbnail(out_mp4, thumb, min(1.2, res["out_duration"] / 3))
        sheet = proj.work / f"sheet_{i:02d}.jpg"
        R.contact_sheet(out_mp4, sheet, res["out_duration"])
        clip.update(thumbnail=str(thumb), sheet=str(sheet), issues=R.validate(out_mp4, cfg),
                    render_seconds=round(time.time() - t0, 1))
        log(f"[render] clip {i}: {res['out_duration']:.1f}s ({res['cuts']} jump-cuts, "
            f"-{res['removed_sec']:.1f}s) in {clip['render_seconds']:.0f}s"
            + (f"  ISSUES: {clip['issues']}" if clip["issues"] else ""))
        clips_out.append(clip)

    clips_out.sort(key=lambda c: c["id"])
    save_json(proj.root / "clips.json", {"source": src, "clips": clips_out})
    _write_report(proj, clips_out, cfg)
    board = _board(clips_out)
    emit({"project": str(proj.root), "report": str(proj.root / "REPORT.md"),
          "leaderboard": board,
          "contact_sheets": [c["sheet"] for c in clips_out if (not only or c["id"] in only)]})


def _load_translation(value, proj: Project):
    """picks.json "translation": a list of {start,end,text_translated} or a path to one."""
    if not value:
        return None
    if isinstance(value, str):
        path = Path(value)
        path = path if path.is_absolute() else proj.work / path
        value = load_json(path)
        value = value.get("sentences", value) if isinstance(value, dict) else value
    return value


def _board(clips: list[dict]) -> list[dict]:
    ranked = sorted(clips, key=lambda c: -c.get("score", 0))
    return [{"rank": r, "id": c["id"], "score": c.get("score"), "judge": c.get("judge"),
             "signal": round(c.get("signal", 0)), "duration": c.get("out_duration"),
             "layout": c["frame"]["layout"], "title": c["title"], "file": c.get("file"),
             "issues": c.get("issues") or None} for r, c in enumerate(ranked, 1)]


def _write_report(proj: Project, clips: list[dict], cfg: dict) -> None:
    lines = ["# Shorts report", "", "Ranked by score (judge × "
             f"{cfg['judge_weight']} + signal × {1 - cfg['judge_weight']:.1f}). Post the top ones first.", "",
             "| rank | clip | score | length | source time | layout | file |",
             "|---|---|---|---|---|---|---|"]
    for r in _board(clips):
        c = next(x for x in clips if x["id"] == r["id"])
        lines.append(f"| {r['rank']} | **{c['title']}** | {r['score']} | {c.get('out_duration', 0):.0f}s | "
                     f"{fmt_ts(c['start'])}–{fmt_ts(c['end'])} | {r['layout']} | `{Path(c['file']).name}` |")
    lines.append("")
    for c in sorted(clips, key=lambda c: -c.get("score", 0)):
        lines += [f"## {c['id']:02d} · {c['title']}", ""]
        if c.get("hook"):
            lines.append(f"- **Hook overlay:** {c['hook']}")
        if c.get("reason"):
            lines.append(f"- **Why it works:** {c['reason']}")
        if c.get("caption"):
            tags = " ".join(c.get("hashtags") or [])
            lines.append(f"- **Post caption:** {c['caption']} {tags}".rstrip())
        lines += _report_posts(c.get("posts"))
        lines.append(f"- **Edit:** {c.get('jumpcuts', 0)} jump-cuts, {c.get('removed_sec', 0):.1f}s of dead air removed")
        if c.get("issues"):
            lines.append(f"- **⚠ Issues:** {', '.join(c['issues'])}")
        lines.append("")
    (proj.root / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def _report_posts(posts) -> list[str]:
    """Per-platform copy (picks.json "posts") as Markdown list items for REPORT.md."""
    lines = []
    for platform, v in (posts or {}).items():
        lines.append(f"- **{platform}:**")
        if not isinstance(v, dict):
            lines.append(f"  - {v}")
            continue
        for key, val in v.items():
            if isinstance(val, list):
                val = ", ".join(str(x) for x in val)
            val = str(val)
            if "\n" in val:
                body = "\n".join(f"    {ln}" for ln in val.splitlines())
                lines.append(f"  - {key}:\n\n    ```text\n{body}\n    ```")
            else:
                lines.append(f"  - {key}: {val}")
    return lines


# text-only pick fields that can change without re-rendering the video
_META_FIELDS = ("posts", "caption", "hashtags", "reason", "judge", "scores")


def _sync_metadata(proj: Project, clips: list[dict]) -> bool:
    """Copy text-only fields from picks.json into rendered clips (matched by id).

    Lets `preview` pick up post copy (e.g. youtube-seo output) added after `render`
    without re-encoding anything. Returns True if any clip changed."""
    path = proj.p("picks.json")
    if not path.exists():
        return False
    try:
        picks = _load_picks(path)
    except SystemExit:
        return False
    cfg = CFG.load(proj.work)
    changed = False
    by_id = {c["id"]: c for c in clips}
    for i, p in enumerate(picks, 1):
        c = by_id.get(i)
        if not c:
            continue
        for k in _META_FIELDS:
            if k in p and p[k] != c.get(k):
                c[k] = p[k]
                changed = True
        if c.get("judge") is not None and "signal" in c:
            score = round(cfg["judge_weight"] * float(c["judge"]) + (1 - cfg["judge_weight"]) * c["signal"])
            changed |= score != c.get("score")
            c["score"] = score
    return changed


def cmd_board(args) -> None:
    proj = Project.find(args.project)
    emit(_board(load_json(proj.root / "clips.json")["clips"]))


# ===================================================================== auto
def cmd_auto(args) -> None:
    summary = cmd_prep(args)
    proj = Project(summary["project"])
    cands = load_json(proj.p("candidates.json"))
    n = summary["clips_requested"]
    best = sorted(cands, key=lambda c: -c["signal"])[:n]
    picks = [{"start": c["start"], "end": c["end"],
              "title": " ".join(c["opening"].split()[:7]).rstrip(",.") or f"Clip {k}",
              "reason": f"auto: signal {c['signal']:.0f}"} for k, c in enumerate(sorted(best, key=lambda c: c["start"]), 1)]
    save_json(proj.p("picks.json"), picks)
    args.project, args.picks, args.only = str(proj.root), None, None
    if args.hook is None:
        args.hook = False                    # no AI-written hook text in unattended mode
    cmd_render(args)


# ===================================================================== edit (whole video)
def cmd_edit(args) -> None:
    import framing as FR
    import ingest
    import render as R
    from transcribe import transcribe

    src = Path(args.video).expanduser().resolve()
    meta = ffprobe(src)
    out = Path(args.output).expanduser().resolve() if args.output else \
        src.with_name(f"{src.stem}.{'vertical' if args.vertical else 'edited'}.mp4")
    proj = Project(src.parent / f".{src.stem}-edit").ensure()
    changes = CFG.parse_set(args.set)
    changes["pace"] = args.pace
    changes["hook_overlay"] = False
    if args.censor:
        changes["censor"] = None if args.censor == "off" else args.censor
    if args.aspect:
        changes["aspect"] = args.aspect
        args.vertical = True
    if args.captions != "none":
        changes["caption_style"] = args.captions
    if args.two_face:
        changes["two_face_mode"] = args.two_face
    cfg = CFG.update(proj.work, changes)
    words, pauses = [], []
    if args.captions != "none" or args.pace != "off":
        wav = proj.p("audio16k.wav")
        ingest.extract_audio(src, wav, None)
        words = transcribe(wav, cfg, args.model, args.lang)["words"]
        pauses = A.detect_silences(wav, cfg["silence_min"], fixed_db=cfg["silence_noise_db"])
    if not args.vertical:
        w, h = meta["width"] - meta["width"] % 2, meta["height"] - meta["height"] % 2
        cfg.update(out_w=w, out_h=h, caption_y=round(1920 * 0.86))
        plan = {"layout": "native"}
    else:
        plan = FR.plan(str(src), meta, 0.0, meta["duration"], cfg,
                       None if args.layout == "auto" else args.layout, None)
    clip = {"id": 0, "start": 0.0, "end": meta["duration"], "frame": plan,
            "style": cfg["caption_style"], "hook": None, "no_captions": args.captions == "none"}
    res = R.render_clip(str(src), meta, clip, words, pauses, cfg, out, proj.work)
    emit({"output": str(out), "duration": res["out_duration"], "layout": plan["layout"],
          "jump_cuts": res["cuts"], "removed_sec": res["removed_sec"],
          "issues": R.validate(out, cfg)})


# ===================================================================== export
# Platform encode presets + checks
PLATFORMS = {
    "youtube": {"suffix": "yt", "limit_mb": 256,
                "v": ["-b:v", "12M", "-maxrate", "14M", "-bufsize", "24M", "-profile:v", "high", "-level", "4.2"],
                "x264": ["-preset", "slow"], "a": ["-b:a", "192k", "-ar", "48000"]},
    "tiktok": {"suffix": "tt", "limit_mb": 287,
               "v": ["-maxrate", "10M", "-bufsize", "20M"],
               "x264": ["-preset", "slow", "-crf", "18"], "nv": ["-cq", "18"], "a": ["-b:a", "128k", "-ar", "44100"]},
    "instagram": {"suffix": "ig", "limit_mb": 250,
                  "v": ["-b:v", "4500k", "-maxrate", "5000k", "-bufsize", "10M", "-profile:v", "high", "-level", "4.2"],
                  "x264": ["-preset", "slow"], "a": ["-b:a", "128k", "-ar", "44100"]},
}


def cmd_export(args) -> None:
    import render as R
    from common import run
    proj = Project.find(args.project)
    clips = load_json(proj.root / "clips.json")["clips"]
    out_dir = Path(args.out).expanduser().resolve() if args.out else proj.root / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    plats = list(PLATFORMS) if args.platform == "all" else [args.platform]
    enc = R.pick_encoder("auto")[0]
    results = []
    for c in clips:
        src = Path(c["file"])
        for name in plats:
            spec = PLATFORMS[name]
            dst = out_dir / f"{src.stem}_{spec['suffix']}.mp4"
            if enc == "h264_nvenc":
                vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", *spec.get("nv", [])]
            else:
                vcodec = ["-c:v", "libx264", *spec["x264"]]
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *vcodec, *spec["v"],
                 "-c:a", "aac", *spec["a"], "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)])
            size_mb = dst.stat().st_size / 1048576
            issues = []
            m = ffprobe(dst)
            if m.get("vcodec") != "h264":
                issues.append(f"codec {m.get('vcodec')}")
            if size_mb > spec["limit_mb"]:
                issues.append(f"{size_mb:.0f} MB exceeds {name} limit {spec['limit_mb']} MB")
            if not 3 <= m["duration"] <= 180:
                issues.append(f"duration {m['duration']:.0f}s outside 3-180 s")
            results.append({"file": str(dst), "platform": name, "size_mb": round(size_mb, 1),
                            "duration": round(m["duration"], 1), "issues": issues})
            log(f"[export] {dst.name} {size_mb:.1f} MB" + (f" ISSUES {issues}" if issues else ""))
    emit({"output_dir": str(out_dir), "encoder": enc, "files": results})


# ===================================================================== action montage
def cmd_action(args) -> None:
    """Cut on every impact sound (chops, hits, drums)."""
    import ingest
    import framing as FR
    import render as R
    from common import run
    from lib import detect_transients as DT

    src = Path(args.video).expanduser().resolve()
    meta = ffprobe(src)
    proj = Project(src.parent / f"{src.stem}-action").ensure()
    cfg = CFG.update(proj.work, CFG.parse_set(args.set))
    strikes_path = proj.p("strikes.json")
    if not strikes_path.exists() or args.redetect:
        wav = proj.p("audio16k.wav")
        ingest.extract_audio(src, wav, None)
        lo, hi = (int(x) for x in args.band.split(":"))
        strikes = DT.detect(str(wav), lo, hi, args.min_flux, args.min_gap)
        strikes = [dict(s, id=k) for k, s in enumerate(strikes, 1)]
        save_json(strikes_path, strikes)
        # verify-in-frame sheet: one frame per strike
        frames_dir = proj.p("strike_frames")
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir()
        for k, st_ in enumerate(strikes[:48], 1):
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{st_['time']:.2f}", "-i", str(src),
                 "-frames:v", "1", "-vf", f"scale=320:-2,drawtext=text='{k}':x=8:y=8:fontsize=28:fontcolor=yellow:"
                 "box=1:boxcolor=black@0.6", str(frames_dir / f"f_{k:03d}.jpg")], check=False)
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(frames_dir / "f_%03d.jpg"),
             "-vf", f"tile=6x{max(1, (min(len(strikes), 48) + 5) // 6)}:padding=4", "-frames:v", "1",
             str(proj.p("strikes_sheet.jpg"))], check=False)
    strikes = load_json(strikes_path)
    if not args.render:
        emit({"project": str(proj.root), "strikes": len(strikes), "strikes_file": str(strikes_path),
              "verify_sheet": str(proj.p("strikes_sheet.jpg")),
              "next": "Read the sheet, drop strikes where the action isn't visible, then re-run with --render [--drop 3,7]"})
        return
    drop = {int(x) for x in args.drop.split(",")} if args.drop else set()
    keep_t = [s["time"] for s in strikes if s["id"] not in drop]
    if args.max_shots:
        keep_t = keep_t[: args.max_shots]
    if not keep_t:
        die("no strikes left to cut on")
    fps = cfg["fps"]
    grid = lambda t: round(round(t * fps) / fps, 4)
    shots = []
    for t in keep_t:
        a, b = grid(max(0.0, t - args.pre)), grid(min(meta["duration"], t + args.post))
        if shots and a <= shots[-1][1]:
            shots[-1][1] = b
        else:
            shots.append([a, b])
    plan = FR.plan(str(src), meta, 0.0, meta["duration"], cfg, None if args.layout == "auto" else args.layout, None) \
        if args.vertical else {"layout": "native"}
    if not args.vertical:
        cfg.update(out_w=meta["width"] - meta["width"] % 2, out_h=meta["height"] - meta["height"] % 2)
    cfg.update(loudnorm=False)
    out = Path(args.output).expanduser().resolve() if args.output else src.with_name(f"{src.stem}.montage.mp4")
    clip = {"id": 0, "start": 0.0, "end": meta["duration"], "frame": plan, "keeps": shots,
            "no_captions": True, "hook": None}
    res = R.render_clip(str(src), meta, clip, [], [], cfg, out, proj.work)
    emit({"output": str(out), "shots": len(shots), "duration": res["out_duration"], "layout": plan["layout"]})


# ===================================================================== edit-clip
def _render_ns(project: str, only: str) -> argparse.Namespace:
    return argparse.Namespace(project=project, picks=None, only=only, set=None, style=None, layout="auto",
                              two_face=None, pace=None, hook=None, progress_bar=None, broll=None,
                              censor=None, brand=None, aspect=None)


def cmd_edit_clip(args) -> None:
    import copy
    import editops as E
    proj = Project.find(args.project)
    cfg = CFG.load(proj.work)
    picks_path = proj.p("picks.json")
    raw = load_json(picks_path)
    picks = raw.get("clips", raw) if isinstance(raw, dict) else raw
    n = args.clip
    if not 1 <= n <= len(picks):
        die(f"--clip must be 1..{len(picks)}")
    rendered = {c["id"]: c for c in load_json(proj.root / "clips.json", default={"clips": []})["clips"]}.get(n, {})
    words = load_json(proj.p("words.json"))["words"]
    try:
        ops = [E.parse_op(o) for o in args.op or []]
        if not ops:
            emit({"clip": n, "editable": E.describe(picks[n - 1], rendered, cfg), "ops": list(E.OPS)})
            return
        pick = copy.deepcopy(picks[n - 1])
        before = len(picks)
        steps = E.apply(pick, rendered, words, ops, cfg, picks)
    except E.OpError as e:
        die(str(e))
    if args.dry_run:
        emit({"clip": n, "would_apply": steps, "pick_after": pick})
        del picks[before:]
        return
    picks[n - 1] = pick
    save_json(proj.p("picks.json.bak"), raw)
    save_json(picks_path, picks)
    only = ",".join(str(k) for k in [n, *range(before + 1, len(picks) + 1)])
    log("[edit-clip] " + " · ".join(steps))
    cmd_render(_render_ns(str(proj.root), only))


# ===================================================================== preview (HTML gallery)
def cmd_preview(args) -> None:
    import html as H
    proj = Project.find(args.project)
    data = load_json(proj.root / "clips.json")
    if _sync_metadata(proj, data["clips"]):
        save_json(proj.root / "clips.json", data)
        _write_report(proj, data["clips"], CFG.load(proj.work))
    clips = sorted(data["clips"], key=lambda c: -c.get("score", 0))
    cards = []
    for r, c in enumerate(clips, 1):
        sc = c.get("scores") or {}
        bars = "".join(f'<div class="sc"><span>{k}</span><i style="width:{int(v)}%"></i><b>{int(v)}</b></div>'
                       for k, v in sc.items() if isinstance(v, (int, float)))
        posts = "".join(f"<details><summary>{H.escape(pl)}</summary><pre>{H.escape(json.dumps(v, indent=1, ensure_ascii=False) if not isinstance(v, str) else v)}</pre></details>"
                        for pl, v in (c.get("posts") or {}).items())
        tags = " ".join(c.get("hashtags") or [])
        cards.append(f"""<article><div class="rank">#{r}</div>
<video controls preload="metadata" poster="{H.escape(Path(c.get('thumbnail', '')).name)}" src="{H.escape(Path(c['file']).name)}"></video>
<div class="meta"><h2>{H.escape(c['title'])}</h2>
<div class="kpi"><span class="score">{c.get('score', '–')}</span><span>{c.get('out_duration', 0):.0f}s</span>
<span>{H.escape(c['frame']['layout'])}</span><span>{fmt_ts(c['start'])}–{fmt_ts(c['end'])}</span></div>
{f'<p class="hook">Hook: {H.escape(c["hook"])}</p>' if c.get('hook') else ''}
<p>{H.escape(c.get('reason') or '')}</p>{bars}
<p class="cap">{H.escape(c.get('caption') or '')} <em>{H.escape(tags)}</em></p>{posts}
<a href="{H.escape(Path(c['file']).name)}" download>Download</a></div></article>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shorts preview</title><style>
:root{{--bg:#0b0b0c;--card:#151517;--text:#f4f4f5;--dim:#a1a1aa;--acc:#facc15}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif;padding:32px 16px}}
h1{{margin:0 0 24px;font-size:22px}} main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px}}
article{{background:var(--card);border-radius:14px;overflow:hidden;position:relative}}
video{{width:100%;aspect-ratio:{cfg_aspect(proj)};background:#000;display:block}}
.rank{{position:absolute;top:10px;left:10px;background:var(--acc);color:#000;font-weight:700;border-radius:8px;padding:2px 8px;z-index:2}}
.meta{{padding:14px 16px 18px}} h2{{font-size:17px;margin:0 0 6px}} .kpi{{display:flex;gap:10px;color:var(--dim);font-size:13px;flex-wrap:wrap}}
.score{{color:var(--acc);font-weight:700}} .hook{{color:var(--acc)}} .cap{{color:var(--dim)}}
.sc{{display:grid;grid-template-columns:90px 1fr 28px;align-items:center;gap:8px;font-size:12px;color:var(--dim)}}
.sc i{{height:6px;background:var(--acc);border-radius:3px}} a{{color:var(--acc)}} pre{{white-space:pre-wrap;color:var(--dim)}}
</style></head><body><h1>{H.escape(proj.root.name)} — {len(clips)} clips, ranked</h1><main>{''.join(cards)}</main></body></html>"""
    out = proj.root / "preview.html"
    out.write_text(page, encoding="utf-8")
    if args.open:
        import subprocess as sp
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener):
            sp.Popen([opener, str(out)], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    emit({"preview": str(out)})


def cfg_aspect(proj: Project) -> str:
    a = CFG.load(proj.work)["aspect"]
    return a.replace(":", "/")


# ===================================================================== thumbnail (designed)
def cmd_thumbnail(args) -> None:
    import thumbs
    proj = Project.find(args.project)
    cfg = CFG.load(proj.work)
    st = proj.state()
    clips = load_json(proj.root / "clips.json")["clips"]
    targets = [c for c in clips if not args.clip or c["id"] == args.clip]
    out = []
    for c in targets:
        text = args.text or c.get("thumbnail_text") or c.get("hook") or c["title"]
        for aspect in (["16:9", "9:16"] if args.aspect == "both" else [args.aspect]):
            jpg = proj.root / f"{Path(c['file']).stem}.thumb-{aspect.replace(':', 'x')}.jpg"
            out.append(thumbs.design(st["source"], st["meta"], c, cfg, jpg, text, proj.work, aspect, args.at))
    emit({"thumbnails": out})


# ===================================================================== translate (export sentences)
def cmd_translate(args) -> None:
    proj = Project.find(args.project)
    clips = {c["id"]: c for c in load_json(proj.root / "clips.json")["clips"]}
    if args.clip not in clips:
        die(f"clip {args.clip} not rendered yet")
    c = clips[args.clip]
    secs = c.get("sections") or [[c["start"], c["end"]]]
    sents = load_json(proj.p("sentences.json"))
    rows = [{"start": x["start"], "end": x["end"], "text": x["text"], "text_translated": ""}
            for a, b in secs for x in sents if x["end"] > a and x["start"] < b]
    name = f"translate_{args.clip:02d}_{args.lang}.json"
    save_json(proj.p(name), {"language": args.lang, "sentences": rows})
    emit({"file": str(proj.p(name)), "sentences": len(rows),
          "next": f'fill every "text_translated" in {args.lang}, set "translation": "{name}" on pick '
                  f"{args.clip} in picks.json, then render --only {args.clip}"})


# ===================================================================== brand kits
def cmd_brand(args) -> None:
    d = CFG.brands_dir()
    d.mkdir(parents=True, exist_ok=True)
    if args.action == "list":
        emit(sorted(p.stem for p in d.glob("*.json")))
        return
    if not args.name:
        die("brand save/show/delete needs a name")
    path = d / f"{args.name}.json"
    if args.action == "show":
        emit(load_json(path))
    elif args.action == "delete":
        path.unlink(missing_ok=True)
        emit({"deleted": args.name})
    else:
        kit = load_json(path, default={"set": {}})
        kit["set"].update(CFG.parse_set(args.set))
        if args.logo:
            src = Path(args.logo).expanduser().resolve()
            if not src.exists():
                die(f"logo not found: {src}")
            dst = d / f"{args.name}-logo{src.suffix.lower()}"
            shutil.copy2(src, dst)
            kit["set"]["logo"] = str(dst)
        save_json(path, kit)
        emit({"saved": str(path), "kit": kit})


# ===================================================================== style-scan (copy a reference's style)
def cmd_style_scan(args) -> None:
    import re as _re
    from common import run
    src = Path(args.video).expanduser().resolve()
    meta = ffprobe(src)
    out = src.parent / f".{src.stem}-style"
    out.mkdir(exist_ok=True)
    dur = meta["duration"]
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-an", "-vf",
             "scale=320:-2,select='gt(scene,0.3)',showinfo", "-f", "null", "-"], check=False)
    cuts = sorted(float(t) for t in _re.findall(r"pts_time:([0-9.]+)", p.stderr))
    bounds = [0.0] + cuts + [dur]
    shots = [b - a for a, b in zip(bounds, bounds[1:]) if b - a > 0.05]
    lufs = None
    if meta["has_audio"]:
        p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-vn", "-af", "ebur128", "-f", "null", "-"],
                check=False)
        m = _re.findall(r"I:\s+(-?[0-9.]+) LUFS", p.stderr)
        lufs = float(m[-1]) if m else None
    n = 16
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf",
         f"fps={n / max(dur, 1):.4f},scale=240:-2,tile=4x4:padding=4", "-frames:v", "1", str(out / "sheet.jpg")],
        check=False)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf",
         f"fps={12 / max(dur, 1):.4f},crop=iw:ih*0.5:0:ih*0.5,scale=480:-2,tile=3x4:padding=4", "-frames:v", "1",
         str(out / "captions_band.jpg")], check=False)
    stats = {"duration": round(dur, 1), "size": f"{meta.get('width')}x{meta.get('height')}",
             "aspect": round(meta["width"] / meta["height"], 3) if meta.get("width") else None,
             "fps": meta.get("fps"), "cuts": len(cuts), "cuts_per_min": round(len(cuts) / max(dur / 60, 0.01), 1),
             "avg_shot_sec": round(float(np.mean(shots)), 2) if shots else None,
             "median_shot_sec": round(float(np.median(shots)), 2) if shots else None,
             "integrated_lufs": lufs}
    hint = {"aspect": "9:16" if (stats["aspect"] or 1) < 0.8 else ("1:1" if (stats["aspect"] or 1) < 1.2 else "16:9"),
            "pace": "tight" if (stats["median_shot_sec"] or 9) < 3 else "natural",
            "progress_bar": "check the sheet", "caption_style": "compare captions_band.jpg with references/caption-styles.md"}
    save_json(out / "style.json", {"stats": stats, "suggested": hint})
    emit({"stats": stats, "suggested": hint, "look_at": [str(out / "sheet.jpg"), str(out / "captions_band.jpg")]})


def cmd_sheet(args) -> None:
    import render as R
    p = Path(args.clip).expanduser().resolve()
    out = p.with_suffix(".sheet.jpg")
    R.contact_sheet(p, out, ffprobe(p)["duration"], args.frames)
    emit({"sheet": str(out)})


# ===================================================================== argparse
def main() -> None:
    ap = argparse.ArgumentParser(prog="clipper.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check the environment").set_defaults(fn=cmd_doctor)

    def prep_opts(p):
        p.add_argument("source", help="video file path or URL")
        p.add_argument("--out", help="project dir (default: <video-dir>/<name>-shorts)")
        p.add_argument("--clips", type=int, help="number of clips wanted")
        p.add_argument("--length", choices=list(CFG.LENGTH_PRESETS), help="clip length preset")
        p.add_argument("--range", help="only use part of the source, e.g. 10:00-45:00")
        p.add_argument("--keywords", help="comma-separated topics to favour")
        p.add_argument("--prompt", help="what kind of moments to find (written into candidates.md for Claude)")
        p.add_argument("--genre", help="genre hint, e.g. podcast, comedy, education, gaming")
        p.add_argument("--aspect", choices=list(CFG.ASPECTS), help="output aspect (default 9:16)")
        p.add_argument("--model", help="whisper model (tiny/base/small/medium/large-v3/large-v3-turbo)")
        p.add_argument("--lang", help="force language code, e.g. en, bn, hi, es")
        p.add_argument("--force", action="store_true", help="re-transcribe even if cached")
        p.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a config value")

    def render_opts(p):
        p.add_argument("--style", choices=list(__import__("captions").STYLES), help="caption style")
        p.add_argument("--layout", default="auto", choices=["auto", "crop", "split", "fit", "screen", "stack", "center"])
        p.add_argument("--two-face", choices=["speaker", "split", "wide"], help="two-person framing")
        p.add_argument("--pace", choices=["off", "natural", "tight"], help="jump-cut strength")
        p.add_argument("--hook", dest="hook", action="store_true", default=None, help="hook title overlay on")
        p.add_argument("--no-hook", dest="hook", action="store_false", help="hook title overlay off")
        p.add_argument("--progress-bar", dest="progress_bar", action="store_true", default=None)
        p.add_argument("--no-progress-bar", dest="progress_bar", action="store_false")
        p.add_argument("--broll", help="video for the bottom half (stack layout, e.g. gameplay)")
        p.add_argument("--censor", choices=["beep", "mute", "off"], help="profanity filter for all clips")
        p.add_argument("--brand", help="apply a saved brand kit (see `brand`)")
        if not any(a.dest == "aspect" for a in p._actions):
            p.add_argument("--aspect", choices=list(CFG.ASPECTS), help="output aspect (default 9:16)")

    p = sub.add_parser("prep", help="transcribe + analyse + candidates")
    prep_opts(p)
    p.set_defaults(fn=cmd_prep)

    p = sub.add_parser("candidates", help="re-rank candidate windows")
    p.add_argument("project")
    p.add_argument("--clips", type=int)
    p.add_argument("--length", choices=list(CFG.LENGTH_PRESETS))
    p.add_argument("--keywords")
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.set_defaults(fn=cmd_candidates)

    p = sub.add_parser("render", help="render picks.json")
    p.add_argument("project")
    p.add_argument("--picks", help="picks file (default: <project>/.work/picks.json)")
    p.add_argument("--only", help="re-render only these pick numbers, e.g. 2,5")
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    render_opts(p)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("auto", help="prep + auto-pick + render, no review")
    prep_opts(p)
    render_opts(p)
    p.set_defaults(fn=cmd_auto)

    p = sub.add_parser("edit", help="captions / reframe / jump-cut a whole video")
    p.add_argument("video")
    p.add_argument("--output")
    p.add_argument("--vertical", action="store_true", help="reframe to 9:16")
    p.add_argument("--layout", default="auto", choices=["auto", "crop", "split", "fit", "screen", "center"])
    p.add_argument("--two-face", choices=["speaker", "split", "wide"])
    p.add_argument("--captions", default="bold", choices=["none", *__import__("captions").STYLES])
    p.add_argument("--pace", default="off", choices=["off", "natural", "tight"])
    p.add_argument("--censor", choices=["beep", "mute", "off"], help="profanity filter")
    p.add_argument("--aspect", choices=list(CFG.ASPECTS), help="reframe to this aspect (implies --vertical)")
    p.add_argument("--model")
    p.add_argument("--lang")
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("export", help="platform-specific re-encodes (YouTube/TikTok/Instagram)")
    p.add_argument("project")
    p.add_argument("--platform", default="all", choices=["all", *PLATFORMS])
    p.add_argument("--out", help="output dir (default <project>/export)")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("action", help="montage cut on every impact sound (sports, chopping, drums)")
    p.add_argument("video")
    p.add_argument("--render", action="store_true", help="render the montage (otherwise only detect + sheet)")
    p.add_argument("--drop", help="strike ids to skip, e.g. 3,7,12")
    p.add_argument("--band", default="1000:6000", help="Hz band: 1000:6000 wood/metal, 200:2000 thuds, 4000:10000 clinks")
    p.add_argument("--min-flux", type=float, default=30.0)
    p.add_argument("--min-gap", type=float, default=0.6)
    p.add_argument("--pre", type=float, default=0.4, help="seconds kept before each strike")
    p.add_argument("--post", type=float, default=0.5, help="seconds kept after each strike")
    p.add_argument("--max-shots", type=int)
    p.add_argument("--vertical", action="store_true")
    p.add_argument("--layout", default="auto", choices=["auto", "crop", "fit", "center"])
    p.add_argument("--output")
    p.add_argument("--redetect", action="store_true")
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.set_defaults(fn=cmd_action)

    p = sub.add_parser("edit-clip", help="named edit ops on one rendered clip")
    p.add_argument("project")
    p.add_argument("--clip", type=int, required=True, help="clip number (1-based, as in picks.json)")
    p.add_argument("--op", action="append", help="NAME[:k=v,k=v] — repeatable, applied in order")
    p.add_argument("--dry-run", action="store_true", help="show the result without saving or rendering")
    p.set_defaults(fn=cmd_edit_clip)

    p = sub.add_parser("preview", help="HTML gallery of the rendered clips")
    p.add_argument("project")
    p.add_argument("--open", action="store_true")
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("thumbnail", help="designed thumbnail(s) with a big headline")
    p.add_argument("project")
    p.add_argument("--clip", type=int, help="only this clip (default all)")
    p.add_argument("--text", help="headline (default: thumbnail_text, hook, or title)")
    p.add_argument("--aspect", default="16:9", choices=["16:9", "9:16", "1:1", "4:5", "both"])
    p.add_argument("--at", type=float, help="source time of the frame (default: best face frame)")
    p.set_defaults(fn=cmd_thumbnail)

    p = sub.add_parser("translate", help="export a clip's sentences for caption translation")
    p.add_argument("project")
    p.add_argument("--clip", type=int, required=True)
    p.add_argument("--lang", required=True, help="target language code, e.g. es, hi, bn, fr")
    p.set_defaults(fn=cmd_translate)

    p = sub.add_parser("brand", help="save/list brand kits (style preset + logo watermark)")
    p.add_argument("action", choices=["save", "list", "show", "delete"])
    p.add_argument("name", nargs="?")
    p.add_argument("--logo", help="PNG logo to watermark")
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.set_defaults(fn=cmd_brand)

    p = sub.add_parser("style-scan", help="measure a reference video's style (cuts, loudness, caption look)")
    p.add_argument("video")
    p.set_defaults(fn=cmd_style_scan)

    p = sub.add_parser("sheet", help="contact sheet of a rendered clip")
    p.add_argument("clip")
    p.add_argument("--frames", type=int, default=8)
    p.set_defaults(fn=cmd_sheet)

    p = sub.add_parser("board", help="leaderboard of rendered clips")
    p.add_argument("project")
    p.set_defaults(fn=cmd_board)

    args = ap.parse_args()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        die("interrupted", 130)
    except RuntimeError as e:
        die(str(e))


if __name__ == "__main__":
    main()
