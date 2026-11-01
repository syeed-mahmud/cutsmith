"""Shared plumbing: HTTP, credential store, OAuth 2.0 loopback + PKCE."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

UA = "shorts-clipper-social-publish/1.0"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class ApiError(RuntimeError):
    pass


# ===================================================================== credential store
def home() -> Path:
    d = Path(os.environ.get("SHORTS_CLIPPER_HOME", Path.home() / ".shorts-clipper")).expanduser() / "social"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _load(name: str) -> dict:
    p = home() / name
    return json.loads(p.read_text()) if p.exists() else {}


def _save(name: str, data: dict) -> None:
    p = home() / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)          # 600: secrets
    tmp.replace(p)


def app_config(platform: str) -> dict:
    """Developer-app credentials (client id/secret…) from apps.json or env vars."""
    cfg = dict(_load("apps.json").get(platform, {}))
    env = {"youtube": {"client_id": "YOUTUBE_CLIENT_ID", "client_secret": "YOUTUBE_CLIENT_SECRET"},
           "meta": {"app_id": "META_APP_ID", "app_secret": "META_APP_SECRET"},
           "linkedin": {"client_id": "LINKEDIN_CLIENT_ID", "client_secret": "LINKEDIN_CLIENT_SECRET"},
           "tiktok": {"client_key": "TIKTOK_CLIENT_KEY", "client_secret": "TIKTOK_CLIENT_SECRET"},
           "x": {"client_id": "X_CLIENT_ID", "client_secret": "X_CLIENT_SECRET"},
           "bluesky": {"handle": "BLUESKY_HANDLE", "app_password": "BLUESKY_APP_PASSWORD"},
           "telegram": {"client_secret": "TELEGRAM_BOT_TOKEN"},
           "discord": {"client_secret": "DISCORD_BOT_TOKEN"},
           "mastodon": {"handle": "MASTODON_URL", "client_secret": "MASTODON_TOKEN"},
           "pinterest": {"client_id": "PINTEREST_CLIENT_ID", "client_secret": "PINTEREST_CLIENT_SECRET"},
           "threads": {"client_id": "THREADS_APP_ID", "client_secret": "THREADS_APP_SECRET"},
           "reddit": {"client_id": "REDDIT_CLIENT_ID", "client_secret": "REDDIT_CLIENT_SECRET"}}.get(platform, {})
    for k, var in env.items():
        if os.environ.get(var):
            cfg[k] = os.environ[var]
    return cfg


def save_app_config(platform: str, values: dict) -> None:
    data = _load("apps.json")
    data.setdefault(platform, {}).update({k: v for k, v in values.items() if v})
    _save("apps.json", data)


def tokens(platform: str) -> dict:
    return _load("tokens.json").get(platform, {})


def save_tokens(platform: str, values: dict) -> None:
    data = _load("tokens.json")
    data[platform] = {**data.get(platform, {}), **values, "saved_at": int(time.time())}
    _save("tokens.json", data)


# ===================================================================== HTTP
def request(method: str, url: str, *, headers: dict | None = None, data=None, json_body=None,
            form: dict | None = None, timeout: float = 120, want_headers: bool = False):
    h = {"User-Agent": UA, **(headers or {})}
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        h.setdefault("Content-Type", "application/json")
    elif form is not None:
        body = urllib.parse.urlencode(form).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif data is not None:
        body = data
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            parsed = _parse(raw)
            return (parsed, dict(r.headers), r.status) if want_headers else parsed
    except urllib.error.HTTPError as e:
        raw = e.read()
        if want_headers and e.code in (308,):
            return _parse(raw), dict(e.headers), e.code
        raise ApiError(f"{method} {url.split('?')[0]} → HTTP {e.code}: {raw.decode(errors='replace')[:600]}") from None


def _parse(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_text": raw.decode(errors="replace")}


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """files: name -> (filename, bytes, content_type)."""
    b = "----shortsclipper" + secrets.token_hex(8)
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (fn, content, ctype) in files.items():
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + content + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


# ===================================================================== OAuth (loopback + PKCE)
def pkce(hex_challenge: bool = False) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = digest.hex() if hex_challenge else base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def free_port(preferred: int | None = None) -> int:
    if preferred:
        return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def loopback_authorize(auth_url: str, port: int, path: str = "/callback", timeout: int = 300) -> dict:
    """Open the consent page, wait for the redirect to http://127.0.0.1:{port}{path}, return query params."""
    result: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.urlparse(self.path)
            if not q.path.rstrip("/").endswith(path.rstrip("/")):
                self.send_response(404); self.end_headers(); return
            result.update({k: v[0] for k, v in urllib.parse.parse_qs(q.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in result
            self.wfile.write(("<h2>%s</h2><p>You can close this tab and return to the terminal.</p>"
                              % ("Connected ✔" if ok else "Authorization failed")).encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    log(f"\nOpen this URL in your browser if it does not open automatically:\n\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:  # noqa: BLE001
        pass
    t.join(timeout)
    srv.server_close()
    if "code" not in result:
        raise ApiError(f"authorization did not complete: {result.get('error_description') or result.get('error') or 'timeout'}")
    return result
