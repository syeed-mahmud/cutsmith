"""Telegram channel/group posts via a bot (free).

Flow: `/connect <word>` handshake through getUpdates, admin check,
single-media send with caption (HTML), t.me link building for public and private chats.
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path

import core as C

PLATFORMS = ("telegram",)
NATIVE_SCHEDULE = False
SETUP = """Telegram (free):
 1. In Telegram, talk to @BotFather → /newbot → copy the bot token.
 2. Add the bot to your channel or group as an ADMIN (so it can post).
 3. Run:  publish.py setup telegram --client-secret <bot token>
          publish.py auth telegram          (it prints a /connect code — send it in the channel/group)"""


def _bot() -> str:
    tok = C.app_config("telegram").get("client_secret")
    if not tok:
        raise C.ApiError("Telegram bot not configured — run `publish.py setup telegram`\n" + SETUP)
    return f"https://api.telegram.org/bot{tok}"


def _call(method: str, **params):
    r = C.request("POST", f"{_bot()}/{method}", json_body=params)
    if not r.get("ok"):
        raise C.ApiError(f"Telegram {method}: {r.get('description')}")
    return r["result"]


def auth(args) -> dict:
    word = secrets.token_hex(4)
    C.log(f"\nSend this message in your channel/group (bot must be admin):\n\n    /connect {word}\n\nWaiting up to 3 minutes…")
    offset, deadline = None, time.time() + 180
    while time.time() < deadline:
        upd = _call("getUpdates", allowed_updates=["message", "channel_post"], timeout=20,
                    **({"offset": offset} if offset else {}))
        for u in upd:
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("channel_post") or {}
            if msg.get("text") == f"/connect {word}":
                chat = msg["chat"]
                me = _call("getMe")
                member = _call("getChatMember", chat_id=chat["id"], user_id=me["id"])
                if member.get("status") in ("administrator", "creator") and member.get("can_delete_messages"):
                    try:
                        _call("deleteMessage", chat_id=chat["id"], message_id=msg["message_id"])
                    except C.ApiError:
                        pass
                C.save_tokens("telegram", {"chat_id": str(chat["id"]), "username": chat.get("username"),
                                           "title": chat.get("title"), "bot": me.get("username")})
                return whoami()
        time.sleep(1)
    raise C.ApiError("no /connect message seen — is the bot an admin of that chat?")


def whoami() -> dict:
    t = C.tokens("telegram")
    return {"platform": "telegram", "connected": bool(t.get("chat_id")), "chat": t.get("title"),
            "username": t.get("username"), "bot": t.get("bot")}


def validate(entry: dict) -> list[str]:
    errs = []
    if not C.tokens("telegram").get("chat_id"):
        errs.append("Telegram not connected — run `publish.py auth telegram`")
    if len(entry.get("text", "")) > 1024:
        errs.append("telegram caption > 1024 characters")
    if Path(entry.get("media", "")).exists() and Path(entry["media"]).stat().st_size > 50 * 1024 * 1024:
        errs.append("telegram bots can upload at most 50 MB")
    return errs


def publish(entry: dict, media: Path) -> dict:
    t = C.tokens("telegram")
    body, ctype = C.multipart({"chat_id": t["chat_id"], "caption": entry.get("text", "")[:1024],
                               "supports_streaming": "true"},
                              {"video": (media.name, media.read_bytes(), "video/mp4")})
    r = C.request("POST", f"{_bot()}/sendVideo", headers={"Content-Type": ctype}, data=body, timeout=900)
    if not r.get("ok"):
        raise C.ApiError(f"Telegram sendVideo: {r.get('description')}")
    mid = r["result"]["message_id"]
    # public chats use the username; private ones use c/<id without -100>
    where = t.get("username") or f"c/{t['chat_id'].replace('-100', '', 1)}"
    return {"id": str(mid), "url": f"https://t.me/{where}/{mid}", "status": "published"}


def status(entry: dict) -> dict:
    return {"note": "Telegram messages publish immediately."}


def stats(entry: dict) -> dict:
    return {"note": "The Telegram Bot API does not expose view counts; see the channel's own statistics."}
