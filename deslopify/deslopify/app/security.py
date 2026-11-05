"""Keep the browser-facing control API local and tied to this server launch."""

from __future__ import annotations

import base64
import hmac
from hashlib import sha256
from html import escape
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

TOKEN_HEADER = "X-Unpacman-Token"
SESSION_COOKIE = "unpacman_session"
DENIED = "Open unpacman from its launcher or the URL printed by its server."


def read_cookie(token: str) -> str:
    """A stolen download cookie must not double as the control token."""
    return hmac.new(token.encode("ascii"), b"browser-read", sha256).hexdigest()


def cookie_name(scope: Any) -> str:
    """The session cookie of the server answering `scope`, named for its port
    (2026-10-20). A browser keeps cookies per computer, not per port: under
    one name, a second unpacman opened in the same browser — another copy, a
    test server — replaced the first one's cookie, and every link of the first
    that opens a tab of its own (the paper's PDF) was refused."""
    server = scope.get("server") if isinstance(scope, dict) else None
    port = server[1] if server and len(server) > 1 else None
    return f"{SESSION_COOKIE}_{port}" if port else SESSION_COOKIE


# What a tab of its own is shown when it is refused, instead of the JSON the
# page's own requests read (2026-10-20: a tester saw that JSON as a whole
# page). Opened from unpacman's own page, or typed, its script asks for a new
# session cookie with the key this browser keeps for unpacman (bootstrap.js),
# and loads the address again — once per address in ten seconds, so that a
# browser that keeps no cookies is not reloaded for ever. A request from
# another site is never helped through: that page only says what to do.
_RECOVER = """(async () => {
  document.documentElement.classList.add('trying');
  let token = null;
  try { token = localStorage.getItem('unpacman.controlToken'); } catch (e) { /* storage off */ }
  const mark = 'unpacman.reopened:' + location.pathname + location.search;
  let fresh = false;
  try {
    fresh = Date.now() - Number(sessionStorage.getItem(mark) || 0) > 10000;
    if (fresh) sessionStorage.setItem(mark, String(Date.now()));
  } catch (e) { fresh = false; }
  if (token && fresh) {
    try {
      const r = await fetch('/api/session', { method: 'POST', headers: { 'X-Unpacman-Token': token } });
      if (r.ok) { location.reload(); return; }
    } catch (e) { /* unpacman is not running */ }
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
<title>unpacman</title>
<style>
body {{ margin: 0; padding: 12vh 24px; background: Canvas; color: CanvasText;
       font: 17px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }}
main {{ max-width: 34em; margin: 0 auto; }}
h1 {{ font-size: 1.3em; margin: 0 0 .6em; }}
p {{ margin: 0 0 1em; }}
.opening {{ display: none; color: GrayText; }}
.trying .opening {{ display: block; }}
.trying .lost {{ display: none; }}
</style>
</head>
<body>
<main>
<p class="opening">Opening…</p>
<div class="lost">
<h1>This is one of unpacman’s files</h1>
<p>It can be shown only while unpacman is open in this browser. Open unpacman {launcher},
then reload this page — or open the file again from unpacman’s own page.</p>
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


class LocalControlSecurity:
    """Authenticate API calls without interfering with streamed responses."""

    def __init__(self, app: Any, token: str, launcher: str = "from its icon") -> None:
        self.app = app
        self.token = token
        self.cookie = read_cookie(token)
        self.launcher = launcher

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

        path = str(scope["path"])
        supplied = request.headers.get(TOKEN_HEADER, "")
        header_ok = self._matches(supplied, self.token)
        if path == "/api/session":
            allowed = scope.get("method") == "POST" and header_ok
        elif scope.get("method") in ("GET", "HEAD"):
            # This server's own cookie — or, set before 2026-10-20, the one of
            # the same name for every port; either must be this launch's.
            allowed = header_ok or any(
                self._matches(request.cookies.get(name, ""), self.cookie)
                for name in (cookie_name(scope), SESSION_COOKIE))
        else:
            # A cookie alone is not enough for a state-changing browser request.
            # Cross-site forms cannot supply this custom header.
            allowed = header_ok
        if not allowed:
            # Only a tab opened from unpacman's own page, or typed, may renew
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
                _PAGE.format(launcher=escape(self.launcher), script=script),
                status_code=403, headers=headers | {
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy":
                        "default-src 'none'; style-src 'unsafe-inline'; connect-src 'self'; "
                        f"script-src 'sha256-{_RECOVER_HASH}'; base-uri 'none'; "
                        "form-action 'none'; frame-ancestors 'self'"})
        else:
            response = JSONResponse({"detail": DENIED}, status_code=403, headers=headers)
        await response(scope, receive, send)
