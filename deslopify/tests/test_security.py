"""The request guard (security.py) with two servers on one computer, and what a
tab of its own is shown when it is refused (2026-10-20).

A tester saw {"detail": "Open unpacman from its launcher or the URL printed by
its server."} as a whole page: a link that opens one of unpacman's files in a
tab of its own (the paper's PDF) rests on the session cookie, and that cookie
was lost — a browser drops it on quitting — or replaced by another unpacman's
of the same name, opened in the same browser on another port.
"""

from __future__ import annotations

import base64
import re
from hashlib import sha256

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from deslopify.app import security

PDF = b"%PDF-1.4 a paper"
NAVIGATE = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def server(token: str, port: int) -> TestClient:
    """An unpacman's guard in front of the three kinds of request that matter
    here, at http://127.0.0.1:PORT; the session cookie is set as the real
    server sets it (server.api_session)."""
    async def session(request: Request) -> Response:
        response = JSONResponse({"ok": True})
        response.set_cookie(security.cookie_name(request.scope), security.read_cookie(token),
                            httponly=True, samesite="strict", path="/api")
        return response

    async def papers(request: Request) -> Response:
        return JSONResponse([])

    async def pdf(request: Request) -> Response:
        return Response(PDF, media_type="application/pdf")

    app = Starlette(routes=[Route("/api/session", session, methods=["POST"]),
                            Route("/api/papers", papers),
                            Route("/api/papers/p/file/source.pdf", pdf)])
    return TestClient(security.LocalControlSecurity(app, token=token),
                      base_url=f"http://127.0.0.1:{port}")


def cookie_of(response) -> tuple[str, str]:
    name, _, rest = response.headers["set-cookie"].partition("=")
    return name, rest.split(";", 1)[0]


def test_the_session_cookie_is_named_for_the_server_s_port():
    assert security.cookie_name({"server": ("127.0.0.1", 7720)}) == "unpacman_session_7720"
    assert security.cookie_name({"server": ("127.0.0.1", 7721)}) == "unpacman_session_7721"
    assert security.cookie_name({"server": None}) == security.SESSION_COOKIE
    assert security.cookie_name({}) == security.SESSION_COOKIE


def test_two_unpacmans_in_one_browser_keep_their_own_cookies():
    first, second = server("first-secret", 7720), server("second-secret", 7721)
    a = cookie_of(first.post("/api/session", headers={"X-Unpacman-Token": "first-secret"}))
    b = cookie_of(second.post("/api/session", headers={"X-Unpacman-Token": "second-secret"}))
    assert a[0] == "unpacman_session_7720" and b[0] == "unpacman_session_7721"
    # The browser sends every cookie of 127.0.0.1 to both: each finds its own.
    jar = f"{a[0]}={a[1]}; {b[0]}={b[1]}"
    for client in (first, second):
        assert client.get("/api/papers/p/file/source.pdf", headers={"Cookie": jar}).content == PDF
    # Each refuses the other's cookie, whatever it is called.
    assert first.get("/api/papers", headers={"Cookie": f"{a[0]}={b[1]}"}).status_code == 403
    assert first.get("/api/papers",
                     headers={"Cookie": f"{security.SESSION_COOKIE}={b[1]}"}).status_code == 403


def test_a_cookie_set_before_the_port_was_in_its_name_still_reads():
    # A page open while unpacman updated itself keeps its cookie until it reloads.
    first = server("first-secret", 7720)
    old = security.read_cookie("first-secret")
    assert first.get("/api/papers",
                     headers={"Cookie": f"{security.SESSION_COOKIE}={old}"}).status_code == 200


def test_the_page_s_own_requests_need_no_cookie():
    first = server("first-secret", 7720)
    assert first.get("/api/papers", headers={"X-Unpacman-Token": "first-secret"}).status_code == 200
    assert first.get("/api/papers").status_code == 403


def page_script(html: str) -> str | None:
    found = re.search(r"<script>(.*?)</script>", html, re.S)
    return found.group(1) if found else None


def test_a_refused_tab_is_shown_words_and_tries_again_from_unpacman_itself():
    first = server("first-secret", 7720)
    for site in ("same-origin", "none"):          # a link on unpacman's page; typed
        shown = first.get("/api/papers/p/file/source.pdf", headers=NAVIGATE | {"Sec-Fetch-Site": site})
        assert shown.status_code == 403
        assert shown.headers["content-type"].startswith("text/html")
        assert "This is one of unpacman’s files" in shown.text
        assert "Open unpacman from its icon" in shown.text
        assert "{" not in shown.text.split("<body>")[1].split("<script>")[0]   # no JSON
        script = page_script(shown.text)
        assert script and "unpacman.controlToken" in script and "/api/session" in script
        # The browser runs it: its hash is the one the page's policy allows.
        digest = base64.b64encode(sha256(script.encode("utf-8")).digest()).decode()
        policy = shown.headers["content-security-policy"]
        assert f"'sha256-{digest}'" in policy and "default-src 'none'" in policy
        assert shown.headers["cache-control"] == "no-store"
    # Never helped through when another site opened it, or when the browser
    # does not say who did.
    for headers in (NAVIGATE | {"Sec-Fetch-Site": "cross-site"},
                    NAVIGATE | {"Sec-Fetch-Site": "same-site"},
                    {"Accept": "text/html,*/*"}):
        shown = first.get("/api/papers/p/file/source.pdf", headers=headers)
        assert shown.status_code == 403 and "This is one of unpacman’s files" in shown.text
        assert page_script(shown.text) is None


def test_the_page_s_own_refused_requests_still_get_json():
    first = server("first-secret", 7720)
    for headers in ({}, {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"},
                    {"Accept": "*/*"}):
        refused = first.get("/api/papers", headers=headers)
        assert refused.status_code == 403
        assert refused.json() == {"detail": security.DENIED}
    assert first.post("/api/session").json() == {"detail": security.DENIED}


def test_the_words_name_the_launcher_they_are_given():
    app = Starlette(routes=[])
    guard = TestClient(security.LocalControlSecurity(app, token="t", launcher="from the Start menu"),
                       base_url="http://127.0.0.1:7720")
    shown = guard.get("/api/papers", headers=NAVIGATE | {"Sec-Fetch-Site": "none"})
    assert "Open unpacman from the Start menu" in shown.text
