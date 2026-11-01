"""Local profanity filter.

Dictionary-based: words are matched case-insensitively after
stripping punctuation. A stem matches any word that starts with it ("fuck" ->
"fucking", "fucked"); EXACT entries only match whole words (avoids "class",
"assume", "shitake"...). Extend per project with --set censor_extra_words='["x"]'.
"""
from __future__ import annotations

import re

STEMS = {
    "fuck", "motherfuck", "shit", "bullshit", "bitch", "bastard", "asshole", "dickhead", "cunt",
    "wank", "twat", "bollock", "pussy", "slut", "whore", "goddamn", "jackass", "dumbass",
    "fck", "fuk", "sh1t", "nigg", "fagg", "retard",
}
EXACT = {
    "ass", "asses", "dick", "dicks", "cock", "cocks", "piss", "pissed", "damn", "damned", "crap",
    "prick", "tits", "fag", "fags", "wtf", "stfu", "bs",
}


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def is_profane(word: str, extra: set[str] | None = None) -> bool:
    w = _norm(word)
    if not w:
        return False
    if w in EXACT or (extra and w in extra):
        return True
    return any(w.startswith(s) for s in STEMS)


def mask(word: str) -> str:
    """'fucking,' -> 'f*****g,' (first and last letter kept, punctuation kept)."""
    m = re.match(r"^(\W*)(\w+)(\W*)$", word)
    if not m:
        return "*" * len(word)
    pre, core, post = m.groups()
    if len(core) <= 2:
        return pre + core[0] + "*" * (len(core) - 1) + post
    return pre + core[0] + "*" * (len(core) - 2) + core[-1] + post
