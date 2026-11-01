"""Render one clip: seek -> reframe -> jump-cut -> captions -> progress bar ->
loudness -> encode. One ffmpeg pass per clip."""
from __future__ import annotations

import random
import re
import shutil
from functools import lru_cache
from pathlib import Path

import captions as C
import profanity as P
import framing as FR
import jumpcut as J
from common import ffprobe, fonts_dir, log, run


@lru_cache(maxsize=None)
def pick_encoder(pref: str) -> list[str]:
    """Video encoder args. 'auto' tries NVENC / VideoToolbox, falls back to x264."""
    def works(name: str) -> bool:
        p = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=256x256:d=0.2", "-c:v", name, "-f", "null", "-"], check=False)
        return p.returncode == 0

    order = [pref] if pref != "auto" else ["h264_nvenc", "h264_videotoolbox", "libx264"]
    for enc in order:
        if enc == "libx264" or works(enc):
            return [enc]
    return ["libx264"]


def encoder_args(cfg: dict) -> list[str]:
    enc = pick_encoder(cfg["encoder"])[0]
    if enc == "h264_nvenc":
        v = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(cfg["crf"] + 1),
             "-b:v", "10M", "-maxrate", "14M", "-bufsize", "20M", "-profile:v", "high"]
    elif enc == "h264_videotoolbox":
        v = ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-maxrate", "14M", "-profile:v", "high"]
    else:
        v = ["-c:v", "libx264", "-preset", cfg["x264_preset"], "-crf", str(cfg["crf"]),
             "-maxrate", "14M", "-bufsize", "24M", "-profile:v", "high", "-level", "4.2"]
    return v + ["-pix_fmt", "yuv420p", "-r", str(cfg["fps"]), "-c:a", "aac", "-b:a", cfg["audio_bitrate"],
                "-ar", "48000", "-movflags", "+faststart"]


def _filter_path(p: Path) -> str:
    """Quote a path for an ffmpeg filter option (paths with ' or : are rejected)."""
    s = str(p).replace("\\", "/")
    if "'" in s or ":" in s[2:]:
        raise ValueError(f"path not usable inside an ffmpeg filter: {s}")
    return f"'{s}'"


# ----------------------------------------------------------------- caption text pipeline
def _norm_tok(t: str) -> str:
    return re.sub(r"[^\w$%]", "", t.lower())


def _src_to_out(t: float, timemap: list[list[float]]) -> float | None:
    """Source time -> output time through the clip's timemap [[out0, src0, length], ...].
    Times inside a removed stretch snap to the next kept moment."""
    best = None
    for o, s0, ln, *_ in timemap:
        if s0 <= t <= s0 + ln:
            return o + t - s0
        if t < s0 and (best is None or s0 < best[1]):
            best = (o, s0)
    return best[0] if best else None


def translated_words(translation: list[dict], timemap: list[list[float]]) -> list[dict]:
    """Turn translated sentences (source-timed) into pseudo word timings on the
    output timeline, spreading each sentence's span by word length."""
    out = []
    for seg in translation:
        text = (seg.get("text_translated") or seg.get("translation") or "").strip()
        if not text:
            continue
        a, b = _src_to_out(float(seg["start"]), timemap), _src_to_out(float(seg["end"]), timemap)
        if a is None or b is None or b - a < 0.2:
            continue
        toks = text.split()
        weights = [len(t) + 2 for t in toks]
        tot, t = sum(weights), a
        for tok, wgt in zip(toks, weights):
            d = (b - a) * wgt / tot
            out.append({"text": tok, "start": round(t, 3), "end": round(t + d, 3)})
            t += d
    return out


def replace_phrase(words: list[dict], phrase: str, replacement: str, occurrence=None) -> tuple[list[dict], int]:
    """Caption-only text fix (timing untouched): matched words collapse into one
    caption word carrying the replacement text."""
    target = [_norm_tok(x) for x in phrase.split() if _norm_tok(x)]
    if not target:
        return words, 0
    out, i, hits = [], 0, 0
    while i < len(words):
        window = [_norm_tok(w["text"]) for w in words[i:i + len(target)]]
        if window == target:
            hits += 1
            if occurrence in (None, "all") or int(occurrence) == hits:
                first, last = words[i], words[i + len(target) - 1]
                out.append({"text": replacement, "start": first["start"], "end": last["end"]})
                i += len(target)
                continue
        out.append(words[i])
        i += 1
    return out, hits


def caption_words(out_words: list[dict], clip: dict, cfg: dict, timemap: list) -> tuple[list[dict], list]:
    """Words actually shown as captions + output-time ranges to censor in the audio."""
    if clip.get("no_captions"):
        words = []
    elif clip.get("translation"):
        words = translated_words(clip["translation"], timemap)
    else:
        words = [dict(w) for w in out_words]
    for fix in clip.get("caption_fixes") or []:
        words, _ = replace_phrase(words, fix["phrase"], fix["replacement"], fix.get("occurrence"))
    ranges = []
    if clip.get("censor") or cfg.get("censor"):
        extra = {w.lower() for w in cfg.get("censor_extra_words") or []}
        # Whisper often gives a word a too-short end; cover at least 0.35 s so the whole word is hidden
        ranges = [[max(0.0, w["start"] - 0.05), max(w["end"], w["start"] + 0.35) + 0.05]
                  for w in out_words if P.is_profane(w["text"], extra)]
        for w in words:
            if P.is_profane(w["text"], extra):
                w["text"] = P.mask(w["text"])
    return words, ranges


# ----------------------------------------------------------------- filter pieces
def _enable(ranges: list[list[float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges)


def _finish_graph(v_in: str, a_in: str, ass_name: str, out_dur: float, clip: dict, cfg: dict,
                  censor_ranges: list, logo_idx: int | None) -> list[str]:
    """captions -> logo -> progress bar ; censor -> loudness. Ends in [vout] / [aout]."""
    g = []
    fontsdir = f":fontsdir={_filter_path(fonts_dir())}" if fonts_dir().exists() else ""
    g.append(f"{v_in}subtitles={ass_name}{fontsdir}[vcap]")
    v = "[vcap]"
    W, H = cfg["out_w"], cfg["out_h"]
    if logo_idx is not None:
        lw = max(16, int(W * float(cfg["logo_scale"])) // 2 * 2)
        m = round(W * 0.04)
        pos = {"top-left": (f"{m}", f"{round(H * 0.05)}"), "top-right": (f"W-w-{m}", f"{round(H * 0.05)}"),
               "bottom-left": (f"{m}", f"H-h-{round(H * 0.05)}"),
               "bottom-right": (f"W-w-{m}", f"H-h-{round(H * 0.05)}")}.get(cfg["logo_position"], (f"W-w-{m}", "40"))
        g.append(f"[{logo_idx}:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={float(cfg['logo_opacity']):.2f}[logo]")
        g.append(f"{v}[logo]overlay=x={pos[0]}:y={pos[1]}:shortest=1[vlogo]")
        v = "[vlogo]"
    if cfg.get("progress_bar"):
        col = cfg["progress_color"].lstrip("#")
        g.append(f"color=c=0x{col}:s={W}x14:r={cfg['fps']}[bar]")
        g.append(f"{v}[bar]overlay=x='-w+w*t/{max(out_dur, 0.1):.3f}':y=H-h:shortest=1[vbar]")
        v = "[vbar]"
    g.append(f"{v}null[vout]")

    a = a_in
    mode = clip.get("censor") or cfg.get("censor")
    if censor_ranges:
        en = _enable(censor_ranges)
        g.append(f"{a}volume=0:enable='{en}'[amute]")
        a = "[amute]"
        if mode == "beep":
            g.append(f"sine=f=1000:sample_rate=48000,volume=1.6,volume=0:enable='not({en})'[beep]")
            g.append(f"{a}[beep]amix=inputs=2:duration=first:normalize=0[abeep]")
            a = "[abeep]"
    if cfg.get("loudnorm"):
        g.append(f"{a}loudnorm=I={cfg['loudnorm_i']}:TP={cfg['loudnorm_tp']}:LRA={cfg['loudnorm_lra']},"
                 f"aresample=48000[aout]")
    else:
        g.append(f"{a}anull[aout]")
    return g


def _write_ass(clip: dict, words: list[dict], out_dur: float, plan: dict, cfg: dict, work: Path) -> str:
    name = f"clip_{clip['id']:02d}.ass"
    (work / name).write_text(C.build_ass(words, out_dur, cfg, clip.get("style"), cfg["out_w"], cfg["out_h"],
                                         FR.caption_anchor(plan, cfg), clip.get("hook"),
                                         FR.hook_position(plan, cfg), clip.get("overlays"),
                                         clip.get("caption_look")), encoding="utf-8")
    return name


# ----------------------------------------------------------------- rendering
def _cut_graph(plan: dict, meta: dict, cfg: dict, keeps: list, dur: float) -> list[str]:
    """reframe + jump-cut. Ends in [vj] / [aj] on the output timeline."""
    g = []
    if plan["layout"] == "audio":
        g.append("[0:a]asplit=2[aw][am]")
        g.append(FR.video_filter(plan, meta, cfg).replace("[0:a]", "[aw]"))
        a_in = "[am]"
    else:
        g.append(FR.video_filter(plan, meta, cfg))
        a_in = "[0:a]"
    sel = J.select_expr(keeps)
    jump = len(keeps) > 1 or keeps[0][0] > 0.01
    v = f"[rf]fps={cfg['fps']},trim=duration={dur:.3f}"
    a = f"{a_in}aresample=48000,atrim=duration={dur:.3f}"
    if jump:
        v += f",select='{sel}',setpts=N/{cfg['fps']}/TB"
        a += f",asetnsamples=n={48000 // cfg['fps']}:p=0,aselect='{sel}',asetpts=N/SR/TB"
    g += [f"{v}[vj]", f"{a}[aj]"]
    return g


def _inputs_for(src: str, meta: dict, start: float, dur: float, plan: dict) -> list[str]:
    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src]
    broll = plan.get("broll") if plan["layout"] == "stack" else None
    if broll:
        bdur = ffprobe(broll)["duration"]
        off = random.uniform(0, max(0.0, bdur - dur - 1)) if bdur > dur + 2 else 0
        inputs += ["-stream_loop", "-1", "-ss", f"{off:.2f}", "-i", broll]
    return inputs


def _section(src, meta, clip, words, pauses, cfg, start, end, plan, remove):
    dur = end - start
    keeps = clip["keeps"] if clip.get("keeps") else J.plan_keeps(words, pauses, start, end, cfg, remove)
    out_words, out_dur = J.remap_words(words, keeps, start, end, set(cfg["fillers"]))
    timemap, off = [], 0.0
    for a, b in keeps:
        timemap.append([round(off, 3), round(start + a, 3), round(b - a, 3), 0])
        off += b - a
    return keeps, out_words, out_dur, timemap, dur


def render_clip(src: str, meta: dict, clip: dict, words: list[dict], pauses: list,
                cfg: dict, out_path: Path, work: Path) -> dict:
    """Render one clip. `clip["sections"]` (list of [start, end] source ranges, any
    order) makes a multi-section clip; otherwise [start, end] is one section."""
    sections = clip.get("sections") or [[clip["start"], clip["end"]]]
    frames = clip.get("frames") or [clip["frame"]]
    remove = clip.get("remove_ranges")
    logo = cfg.get("logo") if cfg.get("logo") and Path(cfg["logo"]).exists() else None

    if len(sections) == 1:
        start, end = sections[0]
        plan = frames[0]
        keeps, out_words, out_dur, timemap, dur = _section(src, meta, clip, words, pauses, cfg,
                                                           start, end, plan, remove)
        cap, cens = caption_words(out_words, clip, cfg, timemap)
        ass = _write_ass(clip, cap, out_dur, plan, cfg, work)
        inputs = _inputs_for(src, meta, start, dur, plan)
        logo_idx = None
        if logo:
            logo_idx = inputs.count("-i")
            inputs += ["-loop", "1", "-i", logo]
        graph = _cut_graph(plan, meta, cfg, keeps, dur) + \
            _finish_graph("[vj]", "[aj]", ass, out_dur, clip, cfg, cens, logo_idx)
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
               "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
               *encoder_args(cfg), "-t", f"{out_dur:.3f}", str(out_path)]
        (work / f"clip_{clip['id']:02d}.cmd.txt").write_text(" ".join(cmd), encoding="utf-8")
        run(cmd, cwd=work)
        return {"out_duration": out_dur, "cuts": len(keeps) - 1, "removed_sec": round(max(0.0, dur - out_dur), 2),
                "words": out_words, "timemap": timemap, "censored": len(cens), "sections": 1}

    # ---- multi-section: cut+frame each section (lossless-ish), concat, then one finishing pass
    inter_cfg = dict(cfg, crf=12, x264_preset="veryfast", encoder="libx264")
    parts, all_words, timemap, off, cuts, removed = [], [], [], 0.0, 0, 0.0
    for i, (start, end) in enumerate(sections):
        plan = frames[i] if i < len(frames) else frames[-1]
        keeps, out_words, out_dur, tm, dur = _section(src, meta, clip, words, pauses, cfg, start, end, plan, remove)
        part = work / f"clip_{clip['id']:02d}_s{i}.mp4"
        graph = _cut_graph(plan, meta, cfg, keeps, dur)
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *_inputs_for(src, meta, start, dur, plan),
             "-filter_complex", ";".join(graph), "-map", "[vj]", "-map", "[aj]", *encoder_args(inter_cfg),
             "-t", f"{out_dur:.3f}", str(part)], cwd=work)
        real = ffprobe(part)["duration"]
        parts.append(part)
        all_words += [dict(w, start=round(w["start"] + off, 3), end=round(w["end"] + off, 3)) for w in out_words]
        timemap += [[round(o + off, 3), s0, ln, i] for o, s0, ln, _ in tm]
        off += real
        cuts += len(keeps) - 1
        removed += max(0.0, dur - out_dur)
    listing = work / f"clip_{clip['id']:02d}_concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    joined = work / f"clip_{clip['id']:02d}_joined.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing.name,
         "-c", "copy", joined.name], cwd=work)
    total = ffprobe(joined)["duration"]
    cap, cens = caption_words(all_words, clip, cfg, timemap)
    ass = _write_ass(clip, cap, total, frames[0], cfg, work)
    inputs = ["-i", joined.name]
    logo_idx = None
    if logo:
        logo_idx = 1
        inputs += ["-loop", "1", "-i", logo]
    graph = _finish_graph("[0:v]", "[0:a]", ass, total, clip, cfg, cens, logo_idx)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
         "-map", "[vout]", "-map", "[aout]", *encoder_args(cfg), "-t", f"{total:.3f}", str(out_path)], cwd=work)
    for p in parts + [joined, listing]:
        p.unlink(missing_ok=True)
    return {"out_duration": round(total, 3), "cuts": cuts, "removed_sec": round(removed, 2), "words": all_words,
            "timemap": timemap, "censored": len(cens), "sections": len(sections)}


def thumbnail(clip_mp4: Path, out_jpg: Path, at: float) -> None:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(clip_mp4),
         "-frames:v", "1", "-q:v", "3", str(out_jpg)])


def contact_sheet(clip_mp4: Path, out_jpg: Path, duration: float, n: int = 8) -> None:
    """n frames in a 4x2 grid at phone-ish size, for a quick visual QA by Claude."""
    rate = n / max(duration, 0.5)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_mp4), "-vf",
         f"fps={rate:.4f},scale=270:480,drawtext=text='%{{pts\\:hms}}':x=8:y=8:fontsize=20:"
         f"fontcolor=white:box=1:boxcolor=black@0.5,tile=4x{(n + 3) // 4}",
         "-frames:v", "1", "-q:v", "4", str(out_jpg)], check=False)
    if not out_jpg.exists():  # drawtext may be missing from some ffmpeg builds
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_mp4), "-vf",
             f"fps={rate:.4f},scale=270:480,tile=4x{(n + 3) // 4}", "-frames:v", "1", "-q:v", "4",
             str(out_jpg)])


def validate(path: Path, cfg: dict) -> list[str]:
    issues = []
    try:
        m = ffprobe(path)
    except Exception as e:  # noqa: BLE001
        return [f"unreadable: {e}"]
    if (m.get("width"), m.get("height")) != (cfg["out_w"], cfg["out_h"]):
        issues.append(f"resolution {m.get('width')}x{m.get('height')}")
    if not m["has_audio"]:
        issues.append("no audio track")
    if m["duration"] < 3:
        issues.append(f"too short ({m['duration']:.1f}s)")
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-vn",
             "-f", "null", "-"], check=False)
    mv = re.search(r"mean_volume:\s*(-?[0-9.]+)", p.stderr)
    if mv and float(mv.group(1)) < -45:
        issues.append(f"audio nearly silent (mean {mv.group(1)} dB)")
    return issues


def have_libass() -> bool:
    p = run(["ffmpeg", "-hide_banner", "-filters"], check=False)
    return " subtitles " in p.stdout and shutil.which("ffmpeg") is not None
