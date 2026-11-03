"""The lab's web interface answers the browser tab `atelier serve` opened, and
no website.

`atelier serve` listens on this computer only, yet any website open in the same
browser can send requests to 127.0.0.1 — and, by pointing a name of its own at
127.0.0.1 ("DNS rebinding"), read the answers. The lab holds unpublished
research, and its sessions run commands. So, as the reader's
`deslopify/app/security.py` does (2026-10-30):

- a key made at each start travels to the browser after `#` in the address the
  server prints and opens — a browser never sends that part to a server — and
  every request to /api/ must carry it, in a header the page's own requests
  add (web/js/bootstrap.js, web/js/api.js);
- what cannot carry a header — the live event stream, a file shown in a frame
  or in a tab of its own — may instead show a cookie derived from the key,
  which only ever reads;
- a request from another site is refused, and so is any Host but the names the
  server was started on.

The pages are a shell whose data comes from /api/, and they carry a content
security policy: they load nothing from another website, so text written by an
AI cannot make the browser call one. A file served as itself — an image, a page
or a drawing a session wrote — is shown in a sandbox, never run as the lab.
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
from hashlib import sha256
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

TOKEN_HEADER = "X-Atelier-Token"
SESSION_COOKIE = "atelier_session"
DENIED = ("Open Atelier from the address `atelier serve` printed in Terminal "
          "(it ends in #auth=…).")
# The names this computer goes by, whatever address the server listens on.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def read_cookie(token: str) -> str:
    """The browser's cookie: derived from the key, never the key itself, so a
    cookie that leaks cannot change anything."""
    return hmac.new(token.encode("ascii"), b"browser-read", sha256).hexdigest()


def cookie_name(scope: Any) -> str:
    """Named for the port: a browser keeps cookies per computer, not per port,
    and a lab and an unpacman — or two labs — must not replace each other's."""
    server = scope.get("server") if isinstance(scope, dict) else None
    port = server[1] if server and len(server) > 1 else None
    return f"{SESSION_COOKIE}_{port}" if port else SESSION_COOKIE


def allowed_hosts(host: str) -> list[str]:
    """The Host names a request may carry: this computer's own, and the
    address `atelier serve` was told to listen on. Listening on every address
    (0.0.0.0, ::) or on an IPv6 one leaves the name unchecked and the key alone
    in charge — Starlette's host check cannot read a bracketed IPv6 Host."""
    host = (host or "").strip().strip("[]")
    if host in ("", "0.0.0.0", "::"):
        return ["*"]
    try:
        if ipaddress.ip_address(host).version == 6:
            return ["*"]
    except ValueError:
        pass
    return list(dict.fromkeys([*LOOPBACK_HOSTS, host]))


def page_policy(nonce: str | None = None) -> str:
    """What a page of the lab may load: its own files and data only. The one
    script written into the page itself runs by its nonce; styles may be inline
    (the page and KaTeX use them), which reaches no other website."""
    script = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
    return ("default-src 'self'; "
            f"script-src {script}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


# A file shown as itself, in a frame or a tab: no script of its own, no
# page origin of the lab's, nothing from another website.
FILE_POLICY = ("sandbox; default-src 'none'; img-src 'self' data:; "
               "style-src 'unsafe-inline'; media-src 'self'")


def file_headers(path: Path) -> dict[str, str]:
    """Headers for a file served as itself. A PDF is left to the browser's own
    viewer — which runs nothing of the file's in the lab's page, and which
    refuses to show a PDF in a sandbox at all."""
    headers = {"X-Content-Type-Options": "nosniff"}
    if path.suffix.lower() != ".pdf":
        headers["Content-Security-Policy"] = FILE_POLICY
    return headers


# What a tab of its own is shown when refused — instead of the JSON the page's
# own requests read. Opened from the lab's page, or typed, its script asks for
# a new cookie with the key this browser keeps for the lab (bootstrap.js) and
# loads the address again — once per address in ten seconds, so that a browser
# that keeps no cookies is not reloaded for ever. A request from another site
# is never helped through: that page only says what to do.
_RECOVER = """(async () => {
  document.documentElement.classList.add('trying');
  let token = null;
  try { token = localStorage.getItem('atelier.controlToken'); } catch (e) { /* storage off */ }
  const mark = 'atelier.reopened:' + location.pathname + location.search;
  let fresh = false;
  try {
    fresh = Date.now() - Number(sessionStorage.getItem(mark) || 0) > 10000;
    if (fresh) sessionStorage.setItem(mark, String(Date.now()));
  } catch (e) { fresh = false; }
  if (token && fresh) {
    try {
      const r = await fetch('/api/session', { method: 'POST', headers: { 'X-Atelier-Token': token } });
      if (r.ok) { location.reload(); return; }
    } catch (e) { /* the lab is not running */ }
  }
  document.documentElement.classList.remove('trying');
})();"""
_RECOVER_HASH = base64.b64encode(sha256(_RECOVER.encode("utf-8")).digest()).decode("ascii")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Atelier</title>
<style>
body {{ margin: 0; padding: 12vh 24px; background: Canvas; color: CanvasText;
       font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }}
main {{ max-width: 34em; margin: 0 auto; }}
h1 {{ font-size: 1.25em; margin: 0 0 .6em; }}
p {{ margin: 0 0 1em; }}
code {{ font: .95em ui-monospace, Menlo, monospace; }}
.opening {{ display: none; color: GrayText; }}
.trying .opening {{ display: block; }}
.trying .lost {{ display: none; }}
</style>
</head>
<body>
<main>
<p class="opening">Opening…</p>
<div class="lost">
<h1>This is one of Atelier’s files</h1>
<p>It can be shown only while Atelier is open in this browser. Open the address
<code>atelier serve</code> printed in Terminal — it ends in <code>#auth=…</code> and
changes each time Atelier starts — then reload this page.</p>
</div>
</main>
{script}
</body>
</html>
"""


def _wants_page(request: Request) -> bool:
    """Whether the browser will show the answer as a page of its own — a tab,
    a frame — rather than hand it to the page's own script."""
    mode = request.headers.get("sec-fetch-mode")
    if mode is not None:
        return mode == "navigate"
    return "text/html" in request.headers.get("accept", "")


class LocalControl:
    """Every request to /api/ shows the launch key — or, only to read, its
    cookie — and comes from the lab's own page. Pages and static files are
    public code, and pass. Streamed answers pass through untouched."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token
        self.cookie = read_cookie(token)

    @staticmethod
    def _matches(value: str, expected: str) -> bool:
        return value.isascii() and hmac.compare_digest(value, expected)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/api/"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        if ((origin is not None and origin != str(request.base_url).rstrip("/"))
                or site not in (None, "same-origin", "none")):
            await self._deny(scope, receive, send, request, recover=False)
            return

        supplied = request.headers.get(TOKEN_HEADER, "")
        header_ok = self._matches(supplied, self.token)
        if scope["path"] == "/api/session":
            allowed = scope.get("method") == "POST" and header_ok
        elif scope.get("method") in ("GET", "HEAD"):
            allowed = header_ok or self._matches(
                request.cookies.get(cookie_name(scope), ""), self.cookie)
        else:
            # A cookie alone never changes anything: a form or a beacon from
            # another site cannot add this header.
            allowed = header_ok
        if not allowed:
            # Only a tab opened from the lab's own page, or typed, may renew
            # its cookie: never one another site opened.
            await self._deny(scope, receive, send, request,
                             recover=site in ("same-origin", "none")
                             and scope.get("method") in ("GET", "HEAD"))
            return
        await self.app(scope, receive, send)

    async def _deny(self, scope: Any, receive: Any, send: Any, request: Request, *,
                    recover: bool) -> None:
        headers = {"Cache-Control": "no-store"}
        if _wants_page(request):
            script = f"<script>{_RECOVER}</script>" if recover else ""
            response: Any = HTMLResponse(
                _PAGE.format(script=script), status_code=403, headers=headers | {
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy":
                        "default-src 'none'; style-src 'unsafe-inline'; connect-src 'self'; "
                        f"script-src 'sha256-{_RECOVER_HASH}'; base-uri 'none'; "
                        "form-action 'none'; frame-ancestors 'self'"})
        else:
            response = JSONResponse({"detail": DENIED}, status_code=403, headers=headers)
        await response(scope, receive, send)
