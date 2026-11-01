"""Discord channel posts via a bot (free).

Flow: message with
file attachments to POST /api/channels/{channel}/messages using `Bot <token>`, multipart
`payload_json` + `files[n]`, release URL https://discord.com/channels/{guild}/{channel}/{msg}.
"""
from __future__ import annotations

import json
from pathlib import Path

import core as C

PLATFORMS = ("discord",)
NATIVE_SCHEDULE = False
API = "https://discord.com/api"
SETUP = """Discord (free):
 1. discord.com/developers/applications → New Application → Bot → Reset Token (copy it).
 2. OAuth2 → URL Generator: scopes "bot", permissions "Send Messages" + "Attach Files" → open the
    URL and add the bot to your server.
 3. In Discord enable Developer Mode, right-click the channel → Copy Channel ID.
 4. Run:  publish.py setup discord --client-secret <bot token>
          publish.py auth discord --channel <channel id>"""


def _h() -> dict:
    tok = C.app_config("discord").get("client_secret")
    if not tok:
        raise C.ApiError("Discord bot not configured — run `publish.py setup discord`\n" + SETUP)
    return {"Authorization": f"Bot {tok}"}


def auth(args) -> dict:
    channel = getattr(args, "channel", None)
    if not channel:
        raise C.ApiError("pass --channel <channel id>\n" + SETUP)
    ch = C.request("GET", f"{API}/channels/{channel}", headers=_h())
    C.save_tokens("discord", {"channel_id": ch["id"], "guild_id": ch.get("guild_id"), "name": ch.get("name")})
    return whoami()


def whoami() -> dict:
    t = C.tokens("discord")
    return {"platform": "discord", "connected": bool(t.get("channel_id")), "channel": t.get("name")}


def validate(entry: dict) -> list[str]:
    errs = []
    if not C.tokens("discord").get("channel_id"):
        errs.append("Discord not connected — run `publish.py auth discord --channel …`")
    if len(entry.get("text", "")) > 2000:
        errs.append("discord message > 2000 characters")
    if Path(entry.get("media", "")).exists() and Path(entry["media"]).stat().st_size > 25 * 1024 * 1024:
        errs.append("discord bot uploads are limited (25 MB on unboosted servers)")
    return errs


def publish(entry: dict, media: Path) -> dict:
    t = C.tokens("discord")
    payload = {"content": entry.get("text", "")[:2000],
               "attachments": [{"id": 0, "description": entry.get("title", "")[:100], "filename": media.name}]}
    body, ctype = C.multipart({"payload_json": json.dumps(payload)},
                              {"files[0]": (media.name, media.read_bytes(), "video/mp4")})
    msg = C.request("POST", f"{API}/channels/{t['channel_id']}/messages", headers={**_h(), "Content-Type": ctype},
                    data=body, timeout=900)
    return {"id": msg["id"], "status": "published",
            "url": f"https://discord.com/channels/{t.get('guild_id')}/{t['channel_id']}/{msg['id']}"}


def status(entry: dict) -> dict:
    return {"note": "Discord messages publish immediately."}


def stats(entry: dict) -> dict:
    t = C.tokens("discord")
    m = C.request("GET", f"{API}/channels/{t['channel_id']}/messages/{entry['result']['id']}", headers=_h())
    return {"reactions": sum(r.get("count", 0) for r in m.get("reactions", []))}
