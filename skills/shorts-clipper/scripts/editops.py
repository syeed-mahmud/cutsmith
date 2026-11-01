"""Named edit operations on a rendered clip (see references/editing.md).

Ops mutate the clip's entry in picks.json; `clipper.py edit-clip` then re-renders
that one clip. Times a user sees ("at 0:21 in the clip") are OUTPUT seconds; the
clip's timemap (written by render) converts them back to source time, so ops stay
correct after jump-cuts and section reordering.
"""
from __future__ import annotations

import copy
import re

OPS = ("trim", "trim_section", "split_section", "drop_section", "reorder_sections", "delete_phrase",
       "replace_phrase", "set_style", "add_text_overlay", "set_text_overlay", "remove_text_overlay",
       "censor", "set_layout", "set_title", "set_hook", "duplicate")


class OpError(ValueError):
    pass


# ----------------------------------------------------------------- parsing
def parse_op(text: str) -> tuple[str, dict]:
    """'name:k=v,k2="a, b"' -> ("name", {"k": "v", "k2": "a, b"})."""
    name, _, rest = text.partition(":")
    name = name.strip()
    if name not in OPS:
        raise OpError(f"unknown op '{name}'. Known: {', '.join(OPS)}")
    params, buf, quote = {}, "", None
    parts = []
    for ch in rest:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf += ch
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf or rest.endswith(","):
        parts.append(buf)
    last = None
    for part in parts:
        if not part.strip():
            continue
        k, sep, v = part.partition("=")
        if not sep:
            if last is None:
                raise OpError(f"{name}: expected key=value, got {part!r}")
            params[last] += "," + part.strip()        # order=1,0,2 continues the previous value
            continue
        last = k.strip()
        params[last] = v.strip()
    return name, params


def _f(params: dict, key: str, default=None, required=True) -> float:
    if key not in params:
        if required and default is None:
            raise OpError(f"missing parameter '{key}'")
        return default
    try:
        return float(params[key])
    except ValueError:
        raise OpError(f"'{key}' must be a number, got {params[key]!r}") from None


def _i(params: dict, key: str) -> int:
    return int(_f(params, key))


# ----------------------------------------------------------------- timeline helpers
def section_spans(rendered: dict) -> list[dict]:
    """Output-time span of every section, from the clip's timemap."""
    rows = rendered.get("timemap") or []
    secs = rendered.get("sections") or [[rendered["start"], rendered["end"]]]
    spans = []
    for i, (a, b) in enumerate(secs):
        mine = [r for r in rows if (r[3] if len(r) > 3 else 0) == i]
        if mine:
            spans.append({"index": i, "start_sec": round(mine[0][0], 2),
                          "end_sec": round(mine[-1][0] + mine[-1][2], 2), "source": [a, b]})
        else:
            spans.append({"index": i, "start_sec": None, "end_sec": None, "source": [a, b]})
    return spans


def out_to_src(t: float, rendered: dict) -> tuple[float, int]:
    """Output seconds -> (source seconds, section index)."""
    rows = rendered.get("timemap") or []
    if not rows:
        raise OpError("clip has no timemap yet — render it first")
    for o, s0, ln, *rest in rows:
        if o <= t <= o + ln:
            return s0 + (t - o), (rest[0] if rest else 0)
    last = rows[-1]
    if t > last[0] + last[2]:
        raise OpError(f"{t:.2f}s is past the end of the clip ({last[0] + last[2]:.2f}s)")
    raise OpError(f"no source time at {t:.2f}s")


def _sections(pick: dict, rendered: dict) -> list[list[float]]:
    return copy.deepcopy(pick.get("sections") or rendered.get("sections")
                         or [[rendered["start"], rendered["end"]]])


def _set_sections(pick: dict, secs: list[list[float]]) -> None:
    if not secs:
        raise OpError("that would remove every section")
    pick["sections"] = [[round(a, 3), round(b, 3)] for a, b in secs]
    pick.pop("start", None)
    pick.pop("end", None)


def _norm(t: str) -> str:
    return re.sub(r"[^\w$%']", "", t.lower())


def _find_phrase(words: list[dict], phrase: str, secs: list[list[float]]) -> list[tuple[float, float]]:
    target = [_norm(x) for x in phrase.split() if _norm(x)]
    ws = [w for w in words if any(a - 0.05 <= w["start"] <= b for a, b in secs)]
    hits = []
    for i in range(len(ws) - len(target) + 1):
        if [_norm(w["text"]) for w in ws[i:i + len(target)]] == target:
            hits.append((ws[i]["start"], ws[i + len(target) - 1]["end"]))
    return hits


def _overlays(pick: dict, cfg: dict) -> list[dict]:
    if pick.get("overlays") is None:
        pick["overlays"] = ([{"text": pick["hook"], "at": 0.0, "duration": cfg["hook_seconds"], "position": "auto"}]
                            if pick.get("hook") else [])
        pick.pop("hook", None)
    return pick["overlays"]


# ----------------------------------------------------------------- ops
def apply(pick: dict, rendered: dict, words: list[dict], ops: list[tuple[str, dict]], cfg: dict,
          picks: list[dict]) -> list[str]:
    """Apply ops in order to `pick` (mutated). Returns a human log line per op."""
    log = []
    for name, prm in ops:
        secs = _sections(pick, rendered)
        if name == "trim":
            a_out, b_out = _f(prm, "fromSec", 0.0, False), _f(prm, "toSec", None, False)
            spans = section_spans(rendered)
            b_out = b_out if b_out is not None else spans[-1]["end_sec"]
            new = []
            for sp, (a, b) in zip(spans, secs):
                s0, s1 = sp["start_sec"], sp["end_sec"]
                if s1 is None or s1 <= a_out or s0 >= b_out:
                    continue
                na = out_to_src(max(a_out, s0), rendered)[0] if a_out > s0 else a
                nb = out_to_src(min(b_out, s1), rendered)[0] if b_out < s1 else b
                new.append([na, nb])
            _set_sections(pick, new)
            log.append(f"trim → keep {a_out:.2f}–{b_out:.2f}s of the clip")
        elif name == "trim_section":
            i = _i(prm, "sectionIndex")
            spans = section_spans(rendered)
            if not 0 <= i < len(secs):
                raise OpError(f"sectionIndex {i} out of range 0..{len(secs) - 1}")
            sp = spans[i]
            a_rel, b_rel = _f(prm, "keepFromSec", 0.0, False), _f(prm, "keepToSec", None, False)
            a, b = secs[i]
            if sp["start_sec"] is not None:
                if a_rel > 0:
                    a = out_to_src(sp["start_sec"] + a_rel, rendered)[0]
                if b_rel is not None and sp["start_sec"] + b_rel < sp["end_sec"]:
                    b = out_to_src(sp["start_sec"] + b_rel, rendered)[0]
            if b - a < 0.3:
                raise OpError("section would be shorter than 0.3 s")
            secs[i] = [a, b]
            _set_sections(pick, secs)
            log.append(f"trim_section {i} → source {a:.2f}–{b:.2f}")
        elif name == "split_section":
            t_src, i = out_to_src(_f(prm, "atClipSec"), rendered)
            a, b = secs[i]
            if not a + 0.3 < t_src < b - 0.3:
                raise OpError("split point too close to a section edge")
            secs[i:i + 1] = [[a, t_src], [t_src, b]]
            _set_sections(pick, secs)
            log.append(f"split_section {i} at source {t_src:.2f}")
        elif name == "drop_section":
            i = _i(prm, "sectionIndex")
            if not 0 <= i < len(secs):
                raise OpError(f"sectionIndex {i} out of range 0..{len(secs) - 1}")
            secs.pop(i)
            _set_sections(pick, secs)
            log.append(f"drop_section {i}")
        elif name == "reorder_sections":
            order = [int(x) for x in re.split(r"[ ;|/]+|,", prm.get("order", "")) if x.strip()]
            if sorted(order) != list(range(len(secs))):
                raise OpError(f"order must be a permutation of 0..{len(secs) - 1}")
            _set_sections(pick, [secs[k] for k in order])
            log.append(f"reorder_sections → {order}")
        elif name == "delete_phrase":
            phrase = prm.get("phrase") or ""
            hits = _find_phrase(words, phrase, secs)
            if not hits:
                raise OpError(f"phrase not found in this clip: {phrase!r}")
            occ = prm.get("occurrence")
            chosen = hits if occ in (None, "all") else [hits[int(occ) - 1]]
            pick.setdefault("remove", [])
            for a, b in chosen:
                pick["remove"].append([round(a - 0.02, 3), round(b + 0.04, 3)])
            log.append(f"delete_phrase {phrase!r} ×{len(chosen)} (of {len(hits)} found)")
        elif name == "replace_phrase":
            if not prm.get("phrase") or "replacement" not in prm:
                raise OpError("replace_phrase needs phrase and replacement")
            if not _find_phrase(words, prm["phrase"], secs):
                raise OpError(f"phrase not found in this clip: {prm['phrase']!r}")
            pick.setdefault("caption_fixes", []).append(
                {"phrase": prm["phrase"], "replacement": prm["replacement"], "occurrence": prm.get("occurrence")})
            log.append(f"replace_phrase {prm['phrase']!r} → {prm['replacement']!r} (captions only)")
        elif name == "set_style":
            look = pick.setdefault("caption_look", {})
            for k_in, k_out in (("captionColor", "color"), ("highlightColor", "highlight"),
                                ("captionPosition", "position"), ("uppercase", "uppercase"), ("size", "size")):
                if k_in in prm:
                    look[k_out] = prm[k_in]
            for c in ("color", "highlight"):
                if c in look and not re.match(r"^#[0-9A-Fa-f]{6}$", look[c]):
                    raise OpError(f"{c} must be #RRGGBB")
            if look.get("position") not in (None, "top", "middle", "bottom"):
                raise OpError("captionPosition must be top, middle or bottom")
            if "style" in prm:
                pick["style"] = prm["style"]
            log.append(f"set_style {look}" + (f" style={prm['style']}" if "style" in prm else ""))
        elif name == "add_text_overlay":
            if not prm.get("text"):
                raise OpError("add_text_overlay needs text")
            ov = _overlays(pick, cfg)
            ov.append({"text": prm["text"], "at": _f(prm, "atClipSec", 0.0, False),
                       "duration": _f(prm, "durationSec", 5.0, False), "position": prm.get("position", "top")})
            log.append(f"add_text_overlay #{len(ov) - 1} {prm['text']!r}")
        elif name == "set_text_overlay":
            ov = _overlays(pick, cfg)
            i = _i(prm, "overlayIndex")
            if not 0 <= i < len(ov):
                raise OpError(f"overlayIndex {i} out of range ({len(ov)} overlays)")
            for k in ("text", "position"):
                if k in prm:
                    ov[i][k] = prm[k]
            for k_in, k_out in (("atClipSec", "at"), ("durationSec", "duration")):
                if k_in in prm:
                    ov[i][k_out] = _f(prm, k_in)
            log.append(f"set_text_overlay #{i}")
        elif name == "remove_text_overlay":
            ov = _overlays(pick, cfg)
            i = _i(prm, "overlayIndex")
            if not 0 <= i < len(ov):
                raise OpError(f"overlayIndex {i} out of range ({len(ov)} overlays)")
            ov.pop(i)
            log.append(f"remove_text_overlay #{i}")
        elif name == "censor":
            mode = prm.get("mode", "beep" if str(prm.get("beep", "true")).lower() == "true" else "mute")
            pick["censor"] = None if mode == "off" else mode
            log.append(f"censor → {mode}")
        elif name == "set_layout":
            pick["layout"] = prm.get("layout")
            log.append(f"set_layout {pick['layout']}")
        elif name == "set_title":
            pick["title"] = prm.get("title", pick.get("title"))
            log.append(f"set_title {pick['title']!r}")
        elif name == "set_hook":
            ov = _overlays(pick, cfg)
            if ov and ov[0].get("position") == "auto" and ov[0].get("at", 0) == 0:
                ov[0]["text"] = prm.get("text", "")
            else:
                ov.insert(0, {"text": prm.get("text", ""), "at": 0.0, "duration": cfg["hook_seconds"],
                              "position": "auto"})
            log.append(f"set_hook {prm.get('text')!r}")
        elif name == "duplicate":
            dup = copy.deepcopy(pick)
            dup["title"] = f"{pick.get('title', 'Clip')} (Copy)"
            picks.append(dup)
            log.append(f"duplicate → new clip #{len(picks)}")
    return log


def describe(pick: dict, rendered: dict, cfg: dict) -> dict:
    """What can be edited on this clip (the `--dry-run` with no ops listing)."""
    p = copy.deepcopy(pick)
    return {"sections": section_spans(rendered) if rendered else p.get("sections"),
            "duration": rendered.get("out_duration") if rendered else None,
            "overlays": _overlays(p, cfg), "caption_style": p.get("style") or cfg["caption_style"],
            "caption_look": p.get("caption_look"), "caption_fixes": p.get("caption_fixes"),
            "removed_ranges": p.get("remove"), "censor": p.get("censor") or cfg.get("censor"),
            "layout": p.get("layout") or "auto"}
