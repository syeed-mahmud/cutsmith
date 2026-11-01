"""Animated word-by-word captions as ASS subtitles (rendered by libass in ffmpeg).

Styles are tuned for a 1080x1920 canvas and scale to any other size.
"""
from __future__ import annotations

import re

# font: family name as installed by setup.sh (falls back to a system font if missing)
STYLES: dict[str, dict] = {
    "bold": dict(font="Poppins ExtraBold", size=128, upper=True, words=3, chars=14,
                 base="#FFFFFF", active="#FFD400", emph="#3DF2FF", outline=7, shadow=2,
                 pop=112, enter=(78, 106), desc="ALL CAPS, yellow active word that pops"),
    "impact": dict(font="Anton", size=150, upper=True, words=2, chars=14,
                   base="#FFFFFF", active="#FFE600", emph="#FF3B5C", outline=9, shadow=3,
                   pop=110, enter=(60, 112), desc="huge condensed 1-2 words, punchy"),
    "bounce": dict(font="Bangers", size=136, upper=True, words=2, chars=14,
                   base="#FFFFFF", active=None, emph=None, outline=8, shadow=3, pop=100,
                   enter=(55, 125), rotate=["#00F0FF", "#FF2BD6", "#7CFF3B", "#FFE600", "#FF7A1A"],
                   desc="cartoon font, every chunk a new bright colour with a bouncy entrance"),
    "karaoke": dict(font="Poppins ExtraBold", size=112, upper=False, words=4, chars=20,
                    base="#FFFFFF", active="#39FF14", emph=None, outline=6, shadow=2, pop=106,
                    enter=None, desc="4-word lines, green karaoke highlight, natural case"),
    "boxed": dict(font="Poppins Bold", size=94, upper=False, words=5, chars=26,
                  base="#FFFFFF", active="#FFD400", emph=None, outline=14, shadow=0, pop=100,
                  enter=None, box="#000000", box_alpha=0.35,
                  desc="text on a dark rounded-look box, readable on busy footage"),
    "neon": dict(font="Poppins ExtraBold", size=124, upper=True, words=3, chars=14,
                 base="#FFFFFF", active="#00F0FF", emph=None, outline=4, shadow=0, pop=108,
                 enter=(85, 104), glow="#00C8FF", desc="white text with a cyan glow on the active word"),
    "clean": dict(font="Poppins SemiBold", size=88, upper=False, words=6, chars=30,
                  base="#FFFFFF", active="#FFE680", emph=None, outline=0, shadow=4, pop=100,
                  enter=None, desc="calm, professional, soft shadow — interviews & podcasts"),
    "minimal": dict(font="Poppins Medium", size=76, upper=False, words=7, chars=36,
                    base="#FFFFFF", active=None, emph=None, outline=3, shadow=1, pop=100,
                    enter=None, desc="small, no highlight, just readable subtitles"),
}


def hex2ass(h: str, alpha: float = 0.0) -> str:
    h = h.lstrip("#")
    a = int(round(alpha * 255))
    return f"&H{a:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _tc(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").strip()


def _strip_punct(text: str, upper: bool) -> str:
    t = text.strip()
    t = re.sub(r"^[\"'“‘(]+|[\"'”’),.;:]+$", "", t) if upper else t
    return t.upper() if upper else t


def _is_emph(word: str, emph_words: set[str]) -> bool:
    w = re.sub(r"[^a-z0-9$%]", "", word.lower())
    return bool(re.match(r"^\$?\d", w)) or w in emph_words or w.endswith("%")


def chunk_words(words: list[dict], max_words: int, max_chars: int, max_dur: float = 2.4) -> list[list[dict]]:
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur:
            length = sum(len(x["text"]) + 1 for x in cur) + len(w["text"])
            gap = w["start"] - cur[-1]["end"]
            if (len(cur) >= max_words or length > max_chars or gap > 0.6
                    or w["end"] - cur[0]["start"] > max_dur):
                chunks.append(cur)
                cur = []
        cur.append(w)
        if w["text"].endswith((".", "?", "!")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], duration: float, cfg: dict, style: str | None = None,
              play_w: int = 1080, play_h: int = 1920, anchor: tuple[int, int] | None = None,
              hook: str | None = None, hook_y: int | None = None,
              overlays: list[dict] | None = None, overrides: dict | None = None) -> str:
    """overlays: [{text, at, duration, position: top|middle|bottom|auto}] title cards on top
    of the video (the hook is overlay 0 when given via `hook`).
    overrides: per-clip caption style {color, highlight, position: top|middle|bottom, uppercase, size}."""
    st = dict(STYLES.get(style or cfg["caption_style"], STYLES["bold"]))
    ov = overrides or {}
    if cfg.get("caption_words"):
        st["words"] = int(cfg["caption_words"])
    if cfg.get("caption_uppercase") is not None:
        st["upper"] = bool(cfg["caption_uppercase"])
    if ov.get("uppercase") is not None:
        st["upper"] = str(ov["uppercase"]).lower() in ("1", "true", "yes")
    if ov.get("color"):
        st["base"] = ov["color"]
    if ov.get("highlight"):
        st["active"] = ov["highlight"]
        st.pop("rotate", None)
    if ov.get("size"):
        st["size"] = int(st["size"] * float(ov["size"]))
    k = min(play_w / 1080, play_h / 1350)   # 9:16 -> 1.0; square/landscape stay readable
    size = round(st["size"] * k)
    align, y = anchor or (2, cfg["caption_y"])
    y = round(y * play_h / 1920) if anchor is None or align == 2 else y
    if ov.get("position") in ("top", "middle", "bottom"):
        align, y = {"top": (8, round(play_h * 0.16)), "middle": (5, play_h // 2),
                    "bottom": (2, round(cfg["caption_y"] * play_h / 1920))}[ov["position"]]
    x = play_w // 2
    emph_words = set(cfg.get("emphasis_words", []))

    base_c = hex2ass(st["base"])
    box = "box" in st
    border_style = 3 if box else 1
    outline_c = hex2ass(st["box"], st.get("box_alpha", 0)) if box else "&H00000000"
    back_c = "&H96000000"

    styles = [
        f"Style: Cap,{st['font']},{size},{base_c},{base_c},{outline_c},{back_c},-1,0,0,0,100,100,1,0,"
        f"{border_style},{round(st['outline'] * k, 1)},{round(st['shadow'] * k, 1)},{align},"
        f"{round(70 * k)},{round(70 * k)},0,1",
        f"Style: Hook,Poppins ExtraBold,{round(96 * k)},&H00141414,&H00141414,&H00FFFFFF,&H64000000,"
        f"-1,0,0,0,100,100,0,0,3,{round(20 * k)},0,8,{round(90 * k)},{round(90 * k)},0,1",
    ]
    events: list[str] = []
    pos = rf"{{\an{align}\pos({x},{y})}}"

    chunks = chunk_words(words, st["words"], st["chars"])
    rotate = st.get("rotate")
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        chunk_end = min(ch[-1]["end"] + 0.45, nxt if nxt is not None else 1e9, duration)
        color_override = hex2ass(rotate[ci % len(rotate)]) if rotate else None
        for j, w in enumerate(ch):
            s = w["start"] if j else ch[0]["start"]
            e = ch[j + 1]["start"] if j + 1 < len(ch) else chunk_end
            if e - s < 0.05:
                e = s + 0.05
            parts = []
            for m, ww in enumerate(ch):
                txt = _clean(_strip_punct(ww["text"], st["upper"]))
                if not txt:
                    continue
                if m == j and st.get("active"):
                    pop = st["pop"]
                    pre = rf"{{\c{hex2ass(st['active'])}"
                    pre += rf"\fscx{pop}\fscy{pop}" if pop != 100 else ""
                    if st.get("glow"):
                        pre += rf"\3c{hex2ass(st['glow'])}\bord{round(st['outline'] * k * 2.2, 1)}\blur{round(6 * k, 1)}"
                    post = rf"\c{base_c}\fscx100\fscy100"
                    if st.get("glow"):
                        post += rf"\3c&H00000000&\bord{round(st['outline'] * k, 1)}\blur0"
                    parts.append(pre + "}" + txt + "{" + post + "}")
                elif st.get("emph") and _is_emph(ww["text"], emph_words):
                    parts.append(rf"{{\c{hex2ass(st['emph'])}}}" + txt + rf"{{\c{base_c}}}")
                else:
                    parts.append(txt)
            if not parts:
                continue
            lead = pos
            if color_override:
                lead += rf"{{\c{color_override}}}"
            if j == 0 and st.get("enter"):
                a, b = st["enter"]
                lead += rf"{{\fscx{a}\fscy{a}\t(0,70,\fscx{b}\fscy{b})\t(70,150,\fscx100\fscy100)}}"
            events.append(f"Dialogue: 1,{_tc(s)},{_tc(e)},Cap,,0,0,0,,{lead}{' '.join(parts)}")

    cards = list(overlays or [])
    if hook and cfg.get("hook_overlay", True) and not overlays:
        cards.insert(0, {"text": hook, "at": 0.0, "duration": cfg["hook_seconds"], "position": "auto"})
    for card in cards:
        if not card.get("text"):
            continue
        lines = _wrap(_clean(card["text"]), 16)
        pos_name = card.get("position", "auto")
        if pos_name == "auto":
            yy, an = (hook_y if hook_y is not None else cfg["hook_y"]), 8
        else:
            yy, an = {"top": (cfg["hook_y"], 8), "middle": (960, 5), "bottom": (1250, 2)}.get(pos_name, (cfg["hook_y"], 8))
        hy = round(yy * play_h / 1920)
        t0 = max(0.0, float(card.get("at", 0)))
        t1 = min(duration, t0 + float(card.get("duration", cfg["hook_seconds"])))
        if t1 <= t0:
            continue
        txt = r"\N".join(lines)
        events.append(f"Dialogue: 2,{_tc(t0)},{_tc(t1)},Hook,,0,0,0,,"
                      rf"{{\an{an}\pos({x},{hy})\fad(120,220)}}{txt}")

    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {play_w}\nPlayResY: {play_h}\n"
              f"WrapStyle: 0\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
              "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
              "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
              "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              + "\n".join(styles) +
              "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    return header + "\n".join(events) + "\n"


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words[:12]:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:3]
