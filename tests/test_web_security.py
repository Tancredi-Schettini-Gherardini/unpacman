"""The lab's web interface answers the tab `atelier serve` opened, and no
website (src/atelier/web/security.py, 2026-10-30).

Before, any website open in the same browser could send the lab requests — a
plain form created a problem folder — and, by pointing a name of its own at
127.0.0.1, read its answers and start or steer sessions."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from atelier.db import reset_db
from atelier.web import security
from atelier.web.app import WEB_DIR, create_app

KEY = "a-launch-key-for-these-tests"
BASE = "http://127.0.0.1:7717"
WITH_KEY = {security.TOKEN_HEADER: KEY}
COOKIE = f"{security.SESSION_COOKIE}_7717={security.read_cookie(KEY)}"


@pytest.fixture(autouse=True)
def _fresh_web_db():
    reset_db()
    yield
    reset_db()


def served(tmp_cfg, **kw):
    """The application as `atelier serve` builds it, and a client for it."""
    app = create_app(tmp_cfg, control_token=KEY, **kw)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


# ------------------------------------------------- the decisions, one by one

def _inner():
    async def answer(request):
        return PlainTextResponse(f"reached {request.method} {request.url.path}")
    return Starlette(routes=[
        Route("/api/problems", answer, methods=["GET", "POST", "HEAD"]),
        Route("/api/stream", answer, methods=["GET"]),
        Route("/api/session", answer, methods=["GET", "POST"]),
        Route("/problems", answer, methods=["GET"]),
    ], middleware=[Middleware(security.LocalControl, token=KEY)])


@pytest.mark.asyncio
@pytest.mark.parametrize("method, path, headers, reached", [
    # the page's own requests carry the key
    ("GET", "/api/problems", WITH_KEY, True),
    ("POST", "/api/problems", WITH_KEY, True),
    # nothing reaches /api/ without it
    ("GET", "/api/problems", {}, False),
    ("POST", "/api/problems", {}, False),
    ("GET", "/api/problems", {security.TOKEN_HEADER: "wrong"}, False),
    ("GET", "/api/problems", {security.TOKEN_HEADER: "é".encode("latin-1")}, False),
    # the cookie reads — the event stream, a file in a frame — and never changes
    ("GET", "/api/stream", {"Cookie": COOKIE}, True),
    ("HEAD", "/api/problems", {"Cookie": COOKIE}, True),
    ("POST", "/api/problems", {"Cookie": COOKIE}, False),
    ("GET", "/api/problems", {"Cookie": f"{security.SESSION_COOKIE}_7717={KEY}"}, False),
    ("GET", "/api/problems", {"Cookie": f"{security.SESSION_COOKIE}_7720="
                                        f"{security.read_cookie(KEY)}"}, False),
    # the cookie is had only for the key, and only by POST
    ("POST", "/api/session", WITH_KEY, True),
    ("POST", "/api/session", {"Cookie": COOKIE}, False),
    ("GET", "/api/session", WITH_KEY, False),
    # another site is refused, key or no key
    ("GET", "/api/problems", WITH_KEY | {"Origin": "https://attacker.example"}, False),
    ("POST", "/api/problems", WITH_KEY | {"Origin": "null"}, False),
    ("GET", "/api/problems", WITH_KEY | {"Sec-Fetch-Site": "cross-site"}, False),
    ("GET", "/api/problems", WITH_KEY | {"Sec-Fetch-Site": "same-site"}, False),
    ("POST", "/api/problems", WITH_KEY | {"Origin": BASE, "Sec-Fetch-Site": "same-origin"}, True),
    ("GET", "/api/problems", {"Cookie": COOKIE, "Sec-Fetch-Site": "none"}, True),
    # pages are public code: their data comes from /api/
    ("GET", "/problems", {}, True),
])
async def test_what_reaches_the_lab(method, path, headers, reached):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_inner()),
                                 base_url=BASE) as client:
        response = await client.request(method, path, headers=headers)
    assert (response.status_code == 200) is reached, response.text[:200]
    if not reached:
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"


# ------------------------------------------------------- the application

@pytest.mark.asyncio
async def test_the_bare_application_is_unchanged_for_tests_and_embedding(tmp_cfg):
    """Without a launch key — as Antoine's web tests and any embedding build it
    — the application answers as it always did."""
    app = create_app(tmp_cfg)
    assert app.state.control_token is None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        assert (await client.get("/api/problems")).status_code == 200
        session = await client.post("/api/session")
    assert session.status_code == 200 and "set-cookie" not in session.headers


@pytest.mark.asyncio
async def test_without_the_key_nothing_is_read_or_made(tmp_cfg):
    async with served(tmp_cfg) as client:
        listed = await client.get("/api/problems")
        made = await client.post("/api/problems", json={"slug": "planted", "title": "P"})
    assert listed.status_code == 403 and listed.json()["detail"] == security.DENIED
    assert made.status_code == 403
    assert not (tmp_cfg.problems_dir / "planted").exists()


@pytest.mark.asyncio
async def test_a_website_s_form_cannot_create_a_problem(tmp_cfg):
    """The cross-site form of the 2026-10-30 check: multipart, which any page
    may send to any address, with no header of its own."""
    async with served(tmp_cfg) as client:
        response = await client.post(
            "/api/problems",
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site",
                     "Sec-Fetch-Mode": "navigate", "Cookie": COOKIE},
            data={"slug": "planted-by-a-website", "title": "Planted",
                  "statement": "Text a website chose"})
    assert response.status_code == 403
    assert "<script" not in response.text      # never helped through
    assert not (tmp_cfg.problems_dir / "planted-by-a-website").exists()


@pytest.mark.asyncio
async def test_a_name_pointed_at_this_computer_is_refused_even_with_the_key(tmp_cfg):
    """DNS rebinding: the request arrives with the attacker's name as Host."""
    async with served(tmp_cfg) as client:
        page = await client.get("/", headers={"Host": "attacker.example:7717"})
        data = await client.get("/api/problems",
                                headers=WITH_KEY | {"Host": "attacker.example:7717"})
        ours = await client.get("/api/problems", headers=WITH_KEY | {"Host": "localhost:7717"})
    assert page.status_code == 400 and data.status_code == 400
    assert ours.status_code == 200


@pytest.mark.asyncio
async def test_the_key_opens_the_lab_and_its_cookie_only_reads(tmp_cfg):
    async with served(tmp_cfg) as client:
        session = await client.post("/api/session", headers=WITH_KEY)
        made = await client.post("/api/problems", headers=WITH_KEY,
                                 json={"slug": "made-here", "title": "Made", "statement": "S"})
        listed = await client.get("/api/problems", headers={"Cookie": COOKIE})
        changed = await client.post("/api/problems", headers={"Cookie": COOKIE},
                                    json={"slug": "by-cookie", "title": "No"})
    assert session.status_code == 200
    cookie = session.headers["set-cookie"]
    assert cookie.startswith(f"{security.SESSION_COOKIE}_7717={security.read_cookie(KEY)};")
    assert KEY not in cookie
    flags = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "path=/api", "samesite=strict"} <= flags
    assert made.status_code == 200 and (tmp_cfg.problems_dir / "made-here").is_dir()
    assert listed.status_code == 200 and "made-here" in listed.text
    assert changed.status_code == 403 and not (tmp_cfg.problems_dir / "by-cookie").exists()


@pytest.mark.asyncio
async def test_a_refused_tab_is_told_how_to_open_the_lab(tmp_cfg):
    """A file opened in a tab after the cookie was lost: words, not JSON — and,
    for a tab of the lab's own, a script that renews the cookie and reloads."""
    url = "/api/runs/r/file?name=x.svg"
    async with served(tmp_cfg) as client:
        own = await client.get(url, headers={"Sec-Fetch-Mode": "navigate",
                                             "Sec-Fetch-Site": "same-origin"})
        other = await client.get(url, headers={"Sec-Fetch-Mode": "navigate",
                                               "Sec-Fetch-Site": "cross-site"})
        script = await client.get(url, headers={"Sec-Fetch-Mode": "cors"})
        # Where a tab came from cannot be told without Sec-Fetch-Site; and a
        # refused form must not be sent again by a reload.
        unknown = await client.get(url, headers={"Accept": "text/html"})
        posted = await client.post(url, headers={"Sec-Fetch-Mode": "navigate",
                                                 "Sec-Fetch-Site": "same-origin"})
    assert own.status_code == other.status_code == script.status_code == 403
    assert unknown.status_code == posted.status_code == 403
    assert "text/html" in own.headers["content-type"]
    assert "atelier serve" in own.text and "#auth=" in own.text
    assert "<script>" in own.text and "<script>" not in other.text
    assert "atelier serve" in unknown.text and "<script>" not in unknown.text
    assert "atelier serve" in posted.text and "<script>" not in posted.text
    policy = own.headers["content-security-policy"]
    assert re.search(r"script-src 'sha256-[A-Za-z0-9+/=]+'", policy)
    assert "default-src 'none'" in policy
    assert script.json()["detail"] == security.DENIED


@pytest.mark.asyncio
async def test_pages_load_nothing_from_another_website(tmp_cfg):
    """Text an AI wrote, shown in the lab, cannot make the browser call another
    website: the page's policy allows its own files and data only."""
    async with served(tmp_cfg) as client:
        first = await client.get("/")
        second = await client.get("/problems/anything")
    assert first.status_code == second.status_code == 200
    policy = first.headers["content-security-policy"]
    directives = {d.split()[0]: d.split()[1:] for d in policy.split("; ")}
    assert directives["default-src"] == ["'self'"]
    allowed = {"'self'", "data:", "blob:", "'none'", "'unsafe-inline'"}
    for name in ("img-src", "font-src", "connect-src", "style-src", "default-src"):
        assert set(directives[name]) <= allowed, (name, directives[name])
    assert directives["connect-src"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'self'"]
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "'unsafe-eval'" not in directives["script-src"]
    assert first.headers["referrer-policy"] == "same-origin"
    # The page's one inline script runs by a nonce made for that page alone.
    nonces = [re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
              for r in (first, second)]
    assert nonces[0] != nonces[1]
    for response, nonce in zip((first, second), nonces, strict=True):
        inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", response.text)
        assert inline == [f' nonce="{nonce}"']
    assert '/js/bootstrap.js"' in first.text


@pytest.mark.asyncio
async def test_public_code_needs_no_key(tmp_cfg):
    async with served(tmp_cfg) as client:
        for url in ("/", "/runs", "/healthz", "/static/js/api.js",
                    "/asset/1/js/bootstrap.js", "/static/vendor/katex/katex.min.js"):
            assert (await client.get(url)).status_code == 200, url
        raw = await client.get("/static/templates/index.html")
    assert raw.headers["content-security-policy"] == security.page_policy()


def _problem_with_files(tmp_cfg) -> Path:
    root = tmp_cfg.problems_dir / "with-files"
    (root / "work").mkdir(parents=True)
    (root / "problem.md").write_text("Statement.\n")
    (root / "work" / "drawing.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    (root / "work" / "page.html").write_text("<script>alert(1)</script>")
    (root / "work" / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nplot")
    (root / "work" / "notes.pdf").write_bytes(b"%PDF-1.4\n% notes\n")
    return root


@pytest.mark.asyncio
async def test_a_file_is_shown_as_itself_and_never_runs_as_the_lab(tmp_cfg):
    """A page or a drawing a session wrote, opened in a tab of its own, would
    otherwise run as the lab — and could read its key. A PDF keeps the
    browser's own viewer, which refuses a sandbox."""
    _problem_with_files(tmp_cfg)
    run = tmp_cfg.runs_dir / "2026-10-30T09-00-00Z-a1b2"
    run.mkdir(parents=True)
    (run / "figure.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (run / "report.html").write_text("<p>report</p>")
    (run / "math.pdf").write_bytes(b"%PDF-1.4\n% math\n")
    async with served(tmp_cfg) as client:
        def problem_file(name, raw=True):
            return client.get("/api/problems/with-files/file",
                              params={"path": f"work/{name}", "raw": "1" if raw else "0"},
                              headers=WITH_KEY)

        def run_file(name):
            return client.get(f"/api/runs/{run.name}/file", params={"name": name},
                              headers=WITH_KEY)

        sandboxed = [await problem_file("drawing.svg"), await problem_file("page.html"),
                     await problem_file("plot.png"), await problem_file("plot.png", raw=False),
                     await run_file("figure.svg"), await run_file("report.html")]
        pdfs = [await problem_file("notes.pdf"), await problem_file("notes.pdf", raw=False),
                await run_file("math.pdf")]
    for response in sandboxed:
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == security.FILE_POLICY
        assert response.headers["content-security-policy"].startswith("sandbox;")
        assert response.headers["x-content-type-options"] == "nosniff"
    assert sandboxed[0].text.startswith("<svg")          # served as itself
    for response in pdfs:
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "content-security-policy" not in response.headers
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_an_error_page_never_turns_what_it_quotes_into_markup(tmp_cfg):
    app = create_app(tmp_cfg, control_token=KEY)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("no problem called <script>alert(1)</script>")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False), base_url=BASE) as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    assert "<script>alert(1)" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_the_names_a_request_may_carry():
    assert security.allowed_hosts("127.0.0.1") == ["127.0.0.1", "localhost"]
    assert security.allowed_hosts("localhost") == ["127.0.0.1", "localhost"]
    assert security.allowed_hosts("mymac.local") == ["127.0.0.1", "localhost", "mymac.local"]
    for everywhere in ("0.0.0.0", "::", "", "::1", "[::1]"):
        assert security.allowed_hosts(everywhere) == ["*"], everywhere


# ------------------------------------------------------------ atelier serve

def _serve(tmp_cfg, monkeypatch, capsys, **args):
    import uvicorn

    from atelier.__main__ import cmd_serve

    ran = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: ran.update(app=app, **kw))
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    cmd_serve(tmp_cfg, argparse.Namespace(**({"host": None, "port": 7717,
                                              "no_browser": True, "reload": False} | args)))
    printed = capsys.readouterr().out
    return ran, printed, opened


@pytest.mark.asyncio
async def test_atelier_serve_serves_only_the_protected_lab(tmp_cfg, monkeypatch, capsys):
    ran, printed, opened = _serve(tmp_cfg, monkeypatch, capsys)
    match = re.search(r"http://127\.0\.0\.1:7717/#auth=([A-Za-z0-9_-]{40,})", printed)
    assert match, printed
    key = match.group(1)
    assert ran["app"].state.control_token == key
    assert ran["host"] == "127.0.0.1" and ran["port"] == 7717 and opened == []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ran["app"]),
                                 base_url=BASE) as client:
        assert (await client.get("/api/problems")).status_code == 403
        assert (await client.get("/api/problems",
                                 headers={security.TOKEN_HEADER: key})).status_code == 200
        assert (await client.get("/api/problems", headers={
            security.TOKEN_HEADER: key, "Host": "attacker.example:7717"})).status_code == 400
    # A new start, a new key.
    again, printed_again, _ = _serve(tmp_cfg, monkeypatch, capsys)
    assert again["app"].state.control_token not in (None, key)


@pytest.mark.asyncio
async def test_a_lab_listening_everywhere_still_wants_its_key(tmp_cfg, monkeypatch, capsys):
    ran, printed, _ = _serve(tmp_cfg, monkeypatch, capsys, host="0.0.0.0")
    key = re.search(r"#auth=([A-Za-z0-9_-]+)", printed).group(1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ran["app"]),
                                 base_url="http://192.168.1.20:7717") as client:
        assert (await client.get("/api/problems")).status_code == 403
        assert (await client.get("/api/problems",
                                 headers={security.TOKEN_HEADER: key})).status_code == 200


def test_atelier_serve_opens_the_address_with_its_key(tmp_cfg, monkeypatch, capsys):
    from dataclasses import replace

    tmp_cfg = replace(tmp_cfg, server=replace(tmp_cfg.server, open_browser=True))
    monkeypatch.setattr("time.sleep", lambda s: None)
    started = []

    class Now:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            started.append(self.target)
            self.target()

    monkeypatch.setattr("threading.Thread", Now)
    ran, printed, opened = _serve(tmp_cfg, monkeypatch, capsys, no_browser=False)
    assert len(started) == 1 and len(opened) == 1
    assert opened[0].startswith("http://127.0.0.1:7717/#auth=")
    assert opened[0].split("#auth=")[1] == ran["app"].state.control_token


# ------------------------------------------- the page's side of the bargain

def test_every_request_of_the_page_carries_the_key():
    """A request the page makes without the key is refused: every fetch in the
    lab's scripts passes it, but the one that trades it for the cookie (which
    names the header itself); a beacon cannot carry it and is not used."""
    for path in sorted((WEB_DIR / "js").rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "sendBeacon" not in text, path
        for match in re.finditer(r"\bfetch\(", text):
            call = text[match.start():match.start() + 260]
            assert "authHeaders(" in call or "'X-Atelier-Token'" in call, (path.name, call)


def test_the_page_starts_only_once_the_key_is_accepted():
    boot = (WEB_DIR / "js" / "bootstrap.js").read_text(encoding="utf-8")
    assert "'/api/session'" in boot and "localStorage.setItem" in boot
    assert boot.index("'/api/session'") < boot.index("import('./app.js')")
    # Started only once the server accepted the key; else words, not a page
    # of failing requests.
    assert re.search(r"if \(opened\) import\('\./app\.js'\);\s*else locked\(\);", boot)
    assert boot.count("import('./app.js')") == 1
    assert "history.replaceState" in boot     # the key leaves the address bar
