"""Tests for the reading interface: papers, the companion, and the HTTP surface.

The pipeline underneath is Atelier's and is tested there. What is tested here
is everything the interface adds on top — and the property worth defending most
is that a PDF which cannot be read is refused *before* anything is created, and
leaves nothing behind.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from deslopify.app import agents, library, ready, server
from deslopify.source import pdf_page_count

def _has_pdfium() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


# PDF text and metadata no longer need a program installed: PDFium (pypdfium2)
# is a dependency and is used when pdftotext, Ghostscript or pdfinfo is
# missing — as on a fresh Mac. So these run everywhere, and on a machine
# without Poppler they test that fallback.
needs_pdftotext = pytest.mark.skipif(
    shutil.which("pdftotext") is None and shutil.which("gs") is None
    and not _has_pdfium(),
    reason="PDF text extraction needs pdftotext, Ghostscript or pypdfium2")
needs_pdfinfo = pytest.mark.skipif(
    shutil.which("pdfinfo") is None and not _has_pdfium(),
    reason="PDF metadata extraction needs pdfinfo or pypdfium2")


# --------------------------------------------------------------------------
# a real PDF, without TeX
# --------------------------------------------------------------------------


def make_pdf(lines: list[str], *, title: str | None = None) -> bytes:
    """A minimal, valid PDF whose text layer holds `lines`, one per line.

    Built by hand so the tests need no TeX installation and run in
    milliseconds. The cross-reference offsets are computed, not faked, so any
    conforming reader accepts it.
    """
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = ["BT", "/F1 11 Tf", "14 TL", "72 760 Td"]
    for line in lines:
        stream.append(f"({esc(line)}) Tj T*")
    stream.append("ET")
    content = "\n".join(stream).encode("latin-1")

    info = f"<< /Title ({esc(title)}) >>" if title is not None else "<< >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        info.encode("latin-1"),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


PROSE = ("the argument proceeds by induction on the rank and the base case is "
         "settled by a direct computation of the invariant ").split()


def paper_pdf(title: str = "On sums of even integers", words: int = 400) -> bytes:
    body = [" ".join(PROSE[(i * 7 + j) % len(PROSE)] for j in range(12))
            for i in range(words // 12 + 1)]
    return make_pdf([title, "A. Author", "Abstract", *body])


# --------------------------------------------------------------------------
# naming a paper
# --------------------------------------------------------------------------


@pytest.mark.parametrize("title, slug", [
    ("On sums of even integers", "on-sums-of-even-integers"),
    # From a title, not a filename: `Path.stem` would cut at the full stop.
    ("A note on S^7. Part II", "a-note-on-s-7-part-ii"),
    # Greek has no ASCII decomposition and would simply vanish.
    ("Γ₇ and exotic spheres", "gamma7-and-exotic-spheres"),
    ("Théorie des nombres", "theorie-des-nombres"),
    ("∮∮∮", "paper"),
])
def test_a_slug_keeps_what_the_title_says(title, slug):
    assert library.slugify(title, set()) == slug


def test_a_slug_never_collides():
    assert library.slugify("paper", {"paper"}) == "paper-2"
    assert library.slugify("paper", {"paper", "paper-2"}) == "paper-3"


@pytest.mark.parametrize("text, filename, title", [
    ("On sums of even integers\nA. Author\nSeptember 28, 2026",
     "paper.pdf", "On sums of even integers"),
    # The arXiv stamp is not the title.
    ("arXiv:2301.04567v2 [math.GT] 12 Jan 2023\nExotic spheres and the "
     "Kervaire invariant\nA. Author", "2301.04567v2.pdf",
     "Exotic spheres and the Kervaire invariant"),
    # A title precedes the abstract; after it, the first line is prose.
    ("1\nAbstract\nWe prove the thing", "main.pdf", "main"),
    ("1 Introduction\nWe study", "main.pdf", "main"),
    ("", "Milnor_exotic-spheres.pdf", "Milnor exotic spheres"),
])
def test_a_paper_is_titled_by_its_contents_before_its_filename(text, filename, title):
    assert library.guess_title(Path("/nonexistent.pdf"), text, filename) == title


@needs_pdfinfo
def test_a_meaningful_title_field_beats_the_text(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(make_pdf(["Some running header", "Body"],
                             title="The Kervaire invariant problem"))
    assert library.guess_title(pdf, "Some running header\nBody", "x.pdf") \
        == "The Kervaire invariant problem"


@needs_pdfinfo
def test_a_build_artefact_title_field_is_ignored(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(make_pdf(["Real title of the paper"], title="main.tex"))
    assert library.guess_title(pdf, "Real title of the paper", "x.pdf") \
        == "Real title of the paper"


# --------------------------------------------------------------------------
# the companion
# --------------------------------------------------------------------------

GUIDE = """\
# Reading companion

> **Scope and correctness warning.** This explains; it does not verify.

### 8. Where the document is thin
The residue search is asserted, never shown.

## 1. Main results
The sum of two even integers is even.

## Main ideas
Parity survives addition.

#### A sub-heading inside the ideas
It must stay with its section.

### 7. Historical context
Euclid, Book IX.

#### References cited in this companion
- Euclid, *Elements*.
"""


def test_the_guide_is_cut_at_the_seven_known_headings_at_any_level():
    preamble, sections = library.split_guide(GUIDE)
    assert "Scope and correctness warning" in preamble
    # Put back in the method's order, whatever order the guide used, and the
    # bibliography last, where the method puts it.
    assert [s.key for s in sections] == [
        "main-results", "main-ideas", "history", "thin", "references"]
    # The sidebar label comes from the companion's heading, rather than a
    # separate registry phrase such as the former "What is proved".
    assert {section.key: section.label for section in sections}["main-results"] == \
        "Main results"
    assert {section.key: section.label for section in sections}["history"] == \
        "Historical context"


def test_an_applied_guide_keeps_method_and_validation_immediately_before_thin():
    guide = """\
# Reader's guide to X

## Main findings
R.

## Q&A
Q.

## Worked examples
E.

## Historical context
H.

## About the method and experimental validation
V, E, and P.

## Where the document is thin
T.
"""
    _, sections = library.split_guide(guide)
    assert [s.key for s in sections] == [
        "main-results", "qa", "examples", "history", "proofs", "thin",
    ]
    assert {s.key: s.label for s in sections}["proofs"] == \
        "About the method and experimental validation"
    assert {s.key: s.label for s in sections}["main-results"] == "Main findings"


def test_an_older_applied_validation_heading_remains_readable():
    guide = "## Main findings\nResults.\n\n## Historical context\nHistory.\n\n" \
        "## About the validation, experiments, and proofs\nEvidence.\n"
    _, sections = library.split_guide(guide)
    assert {s.key: s.label for s in sections}["proofs"] == \
        "About the validation, experiments, and proofs"


@pytest.mark.parametrize("applied", [False, True])
def test_a_companion_without_worked_examples_has_no_missing_section(tmp_path, applied):
    first_heading = "Main findings" if applied else "Main results"
    guide = (f"# Reader's guide to X\n\n## {first_heading}\nResult.\n\n"
             + ("" if applied else "## Main ideas\nIdea.\n\n## About the proofs\nProof.\n\n")
             + "## Q&A\nQuestion and answer.\n\n## Historical context\nHistory.\n\n"
             + ("## About the method and experimental validation\nValidation.\n\n"
                if applied else "")
             + "## Where the document is thin\nLimits.\n\n"
             + "## References cited in this companion\n- [Ex26] Example reference.\n")
    (tmp_path / "guide.md").write_text(guide)
    paper = SimpleNamespace(kind="paper", source=tmp_path / "source.pdf", title="X")
    companion = library.read_companion(paper, tmp_path)
    assert companion is not None and companion.missing == []


def test_legacy_proof_and_support_headings_are_folded_into_one_section():
    guide = """\
# Reader's guide to X

## Structure of the proofs
P.

## Supporting results
S.
"""
    _, sections = library.split_guide(guide)
    assert [s.key for s in sections] == ["proofs"]
    assert "P." in sections[0].markdown
    assert "Supporting results" in sections[0].markdown
    assert "S." in sections[0].markdown


@pytest.mark.parametrize("heading", ["About the proofs", "About the proof"])
def test_pure_proof_heading_is_a_known_section(heading):
    _, sections = library.split_guide(f"# Guide\n\n## {heading}\n\nRoadmap.\n")
    assert [(section.key, section.label) for section in sections] == [
        ("proofs", heading)]


def test_a_sub_heading_stays_inside_its_section():
    """The rail lists what a reader navigates by, not a guide's internal
    structure."""
    _, sections = library.split_guide(GUIDE)
    by = {s.key: s for s in sections}
    assert "A sub-heading inside the ideas" in by["main-ideas"].markdown


@needs_pdftotext
def test_a_copied_illustration_is_routed_from_its_reading(app_cfg, client):
    guide = GUIDE.replace(
        "Parity survives addition.",
        "Parity survives addition.\n\n![Parity diagram](figures/parity.png)")
    slug, paper = paper_with(app_cfg, client, guide=guide)
    figures = paper.reading / "figures"
    figures.mkdir()
    payload = b"\x89PNG\r\n\x1a\nfigure"
    (figures / "parity.png").write_bytes(payload)

    companion = client.get(f"/api/papers/{slug}/companion").json()
    ideas = next(s["html"] for s in companion["sections"]
                 if s["key"] == "main-ideas")
    match = re.search(r'<img src="([^"]+)"', ideas)
    assert match and "/asset/figures/parity.png?" in match.group(1)
    served = client.get(match.group(1))
    assert served.status_code == 200 and served.content == payload


def test_the_bibliography_is_a_section_of_its_own():
    """Since version 12 of the method one bibliography serves every section, so
    it is not part of the history — nor of Where the document is thin, which it
    now follows and which would otherwise colour it as a warning. A guide from
    before, with it under Historical context, is read the same way."""
    _, sections = library.split_guide(GUIDE)
    by = {s.key: s for s in sections}
    assert "Euclid, *Elements*" in by["references"].markdown
    assert "Elements" not in by["history"].markdown
    assert by["references"].label == "References cited in this companion" \
        and by["references"].tone == ""
    v12 = ("# Reader's guide to X\n\n> **Notice.** Explanatory.\n\n"
           "## 1. Main results\nA.\n\n## 8. Where the document is thin\nB.\n\n"
           "## References cited in this companion\n\n- [Mil56] J. Milnor, ...\n")
    _, sections = library.split_guide(v12)
    assert [s.key for s in sections] == ["main-results", "thin", "references"]
    assert "[Mil56]" not in sections[1].markdown


def test_a_paper_specific_section_keeps_its_title_and_its_place():
    """The method allows one more top-level section, placed where a reader
    needs it; it must not vanish into the section before it."""
    guide = ("# Reader's guide to X\n\n## 1. Main results\nA.\n\n"
             "## 6. Worked examples\nE.\n\n"
             "## Dictionary between the two formalisms\nD.\n\n### A sub-heading\nS.\n\n"
             "## 7. Historical context\nH.\n\n## 2. Main ideas\nI.\n")
    _, sections = library.split_guide(guide)
    assert [s.key for s in sections] == [
        "main-results", "main-ideas", "examples",
        "extra-dictionary-between-the-two-formalisms", "history"]
    extra = sections[3]
    assert extra.label == "Dictionary between the two formalisms"
    assert "A sub-heading" in extra.markdown and "E." not in extra.markdown


def test_where_the_document_is_thin_has_standard_tone():
    _, sections = library.split_guide(GUIDE)
    assert {s.key: s.tone for s in sections}["thin"] == ""


def test_a_guide_with_no_known_headings_is_all_preamble():
    preamble, sections = library.split_guide("# Title\n\nJust some text.")
    assert sections == [] and "Just some text" in preamble


# --------------------------------------------------------------------------
# the HTTP surface
# --------------------------------------------------------------------------


MODELS = (("claude", ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")),
          ("codex", ("gpt-5.5", "gpt-5.4-mini")))


@pytest.fixture
def app_cfg(tmp_cfg):
    from atelier.config import ProviderModels

    papers = tmp_cfg.root / "papers"
    papers.mkdir()
    return replace(tmp_cfg, problems_dir=papers,
                   models=ProviderModels(by_provider=MODELS))


@pytest.fixture
def client(app_cfg, monkeypatch, ready_stub):
    from fastapi.testclient import TestClient

    from atelier.db import reset_db
    from deslopify.app.server import create_app

    # Whether Claude Code and Codex are installed — and signed in, and LaTeX
    # ready — is a fact about the machine running the tests, not about the
    # code under test. A test that cares sets `client.ready`.
    monkeypatch.setattr(agents, "installed", lambda provider: True)
    # Nor whether an Ollama happens to be running on it (openweight.py): a test
    # that wants open-weight models gives the server its own.
    from deslopify.app import openweight
    monkeypatch.setattr(openweight, "ollama_status", lambda base=None, timeout=1.5: {
        "installed": False, "running": False, "version": "", "models": [], "other": []})
    # Nor which models its Claude Code and Codex offer (catalog.py): the menu
    # is the configuration's, unless a test gives the server a catalogue.
    from deslopify.app import catalog
    monkeypatch.setattr(catalog, "ask_program", lambda provider, exe, account, cfg: [])
    # Nor what OpenRouter's catalogue says today: a test that saves an
    # OpenRouter key and wants its models gives the server a catalogue.
    monkeypatch.setattr(openweight.Discovery, "catalogue", lambda self, fetch=True: [])
    # The database handle is process-wide: without this, every test after the
    # first shares its database, and one test's runs appear in the next.
    reset_db()

    stub = ready_stub(app_cfg)
    app = create_app(app_cfg, model="claude-haiku-4-5", effort="low",
                     max_usd=0.5, max_minutes=5, checker=stub,
                     trusted_hosts=("testserver",))
    # And on the server's own catalogue too, not only on the module: its
    # start-up question runs in a thread, which may outlive this test and its
    # monkeypatch — and must then still ask no real program.
    app.state.deslopify.catalog.ask = lambda provider, exe, account, cfg: []
    # The accounts the server reads with, open-weight ones included.
    stub.cfg = app.state.deslopify.cfg
    with TestClient(app) as c:
        c.headers["X-Unpacman-Token"] = app.state.control_token
        c.ready = stub
        yield c
    reset_db()


def upload(client, data: bytes, name: str = "paper.pdf"):
    return client.post("/api/papers",
                       files={"file": (name, data, "application/pdf")})


def test_api_needs_launch_secret_and_rejects_foreign_requests(client):
    from fastapi.testclient import TestClient

    app = client.app
    token = app.state.control_token
    with TestClient(app) as visitor:
        assert visitor.get("/api/hello").status_code == 403
        assert visitor.get("/api/papers").status_code == 403
        assert visitor.post("/api/ready/not-a-job").status_code == 403
        assert visitor.post("/api/session").status_code == 403

        login = visitor.post("/api/session", headers={"X-Unpacman-Token": token})
        assert login.status_code == 200
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=strict" in login.headers["set-cookie"].lower()
        assert token not in login.headers["set-cookie"]
        assert visitor.get("/api/papers").status_code == 200
        # The cookie allows downloads, not form-based state changes.
        assert visitor.post("/api/ready/not-a-job").status_code == 403
        assert visitor.post("/api/ready/not-a-job",
                            headers={"X-Unpacman-Token": token}).status_code == 404
        assert visitor.get("/api/papers", headers={"Host": "evil.test",
                                                 "X-Unpacman-Token": token}).status_code == 400
        assert visitor.post("/api/ready/not-a-job", headers={
            "X-Unpacman-Token": token,
            "Origin": "https://evil.test",
        }).status_code == 403
        assert visitor.get("/api/papers", headers={
            "X-Unpacman-Token": token,
            "Sec-Fetch-Site": "cross-site",
        }).status_code == 403


def test_a_refused_tab_of_its_own_gets_words_and_the_cookie_its_port(client):
    """2026-10-20: a tester saw the guard's JSON as a whole page (test_security.py)."""
    from fastapi.testclient import TestClient

    from deslopify.app import security

    app = client.app
    token = app.state.control_token
    with TestClient(app, base_url="http://testserver:7720") as visitor:
        shown = visitor.get("/api/papers/no-such/file/source.pdf", headers={
            "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin"})
        assert shown.status_code == 403 and "This is one of unpacman’s files" in shown.text
        assert "unpacman.controlToken" in shown.text
        assert visitor.get("/api/papers").json() == {"detail": security.DENIED}
        login = visitor.post("/api/session", headers={"X-Unpacman-Token": token})
        assert login.headers["set-cookie"].startswith("unpacman_session_7720=")
        assert visitor.get("/api/papers").status_code == 200


def test_the_page_and_its_assets_are_served(client):
    assert client.get("/").status_code == 200
    assert "Drop a paper here" in client.get("/").text
    assert 'src="/static/bootstrap.js"' in client.get("/").text
    for asset in ("/static/bootstrap.js", "/static/app.js", "/static/app.css", "/static/dom.js",
                  "/static/notes.js", "/static/paper.js", "/static/map.js",
                  "/static/settings.js"):
        assert client.get(asset).status_code == 200
    # The paper is drawn with the vendored PDF.js, and a module script is only
    # run if it is served as JavaScript.
    for asset in ("/vendor/pdfjs/pdf.min.mjs", "/vendor/pdfjs/pdf.worker.min.mjs"):
        r = client.get(asset)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]


@pytest.mark.parametrize("name, data, why", [
    ("notes.txt", b"hello", "reads PDFs"),
    ("fake.pdf", b"not really a pdf", "not a PDF"),
])
def test_what_is_not_a_pdf_is_refused(client, app_cfg, name, data, why):
    r = upload(client, data, name)
    assert r.status_code == 400 and why in r.json()["detail"]
    assert list(app_cfg.problems_dir.iterdir()) == []


def test_codex_prompt_too_large_is_refused_before_launch():
    plan = SimpleNamespace(
        first=SimpleNamespace(provider="codex", prompt="x" * 1_020_000,
                              system_append="method" * 3_000),
        extra={})
    why = server._too_long(SimpleNamespace(windows={}), plan)
    assert "more than ChatGPT can accept" in why
    assert "Nothing has been spent" in why


@needs_pdftotext
def test_source_pdf_page_limit_is_inclusive_at_upload(client, app_cfg, monkeypatch):
    data = paper_pdf()
    assert pdf_page_count(data) == 1
    monkeypatch.setattr(server, "pdf_page_count", lambda _: 10_001)
    refused = upload(client, data)
    assert refused.status_code == 400
    assert "10,001" in refused.json()["detail"]
    assert list(app_cfg.problems_dir.iterdir()) == []

    monkeypatch.setattr(server, "pdf_page_count", lambda _: 10_000)
    accepted = upload(client, data)
    assert accepted.status_code == 200, accepted.text


@needs_pdftotext
def test_a_scan_is_refused_before_anything_is_created(client, app_cfg):
    """A PDF with no usable text cannot be read by anything downstream. It is
    refused at the door — not four minutes into a session — and leaves no
    folder behind."""
    r = upload(client, make_pdf(["Figure 1"]))
    assert r.status_code == 400
    assert "OCR" in r.json()["detail"]
    assert list(app_cfg.problems_dir.iterdir()) == []


@needs_pdftotext
def test_an_upload_becomes_a_paper_named_by_its_title(client, app_cfg):
    r = upload(client, paper_pdf("On sums of even integers"), "main.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "on-sums-of-even-integers"
    assert body["title"] == "On sums of even integers"
    assert body["words"] >= 120

    root = app_cfg.problems_dir / body["slug"]
    assert (root / "progress" / "source.pdf").read_bytes().startswith(b"%PDF-")
    marker = json.loads((root / library.MARKER).read_text())
    assert marker["original_name"] == "main.pdf"


@needs_pdftotext
def test_a_new_paper_shows_every_step_still_to_come(client):
    slug = upload(client, paper_pdf()).json()["slug"]
    status = client.get(f"/api/papers/{slug}").json()
    assert status["run"] is None and status["companion"] is False
    assert [s["state"] for s in status["steps"]] == ["pending"] * 5
    assert [s["role"] for s in status["steps"]] == [
        "draft", "graph-builder", "verifier-blind", "verifier", "reviser"]


@needs_pdftotext
def test_the_library_knows_an_unread_paper_from_a_finished_one(client):
    slug = upload(client, paper_pdf()).json()["slug"]
    [row] = client.get("/api/papers").json()
    assert row["slug"] == slug and row["state"] == "unread"


def test_an_unknown_paper_is_spoken_of_as_a_paper(client):
    """The lab calls these problems; a reader should never meet the word."""
    r = client.get("/api/papers/nope")
    assert r.status_code == 404
    assert "paper" in r.json()["detail"] and "problem" not in r.json()["detail"]


@needs_pdftotext
def test_only_the_companion_files_can_be_fetched(client):
    slug = upload(client, paper_pdf()).json()["slug"]
    assert client.get(f"/api/papers/{slug}/file/source.pdf").status_code == 200
    for name in ("problem.toml", "..%2F..%2Fproblem.toml", "state.json",
                 "deslopify.json"):
        assert client.get(f"/api/papers/{slug}/file/{name}").status_code == 404


@needs_pdftotext
def test_the_companion_is_served_in_sections(client, app_cfg):
    import os

    slug = upload(client, paper_pdf()).json()["slug"]
    assert client.get(f"/api/papers/{slug}/companion").status_code == 404

    reading = app_cfg.problems_dir / slug / "progress" / "reading"
    reading.mkdir(parents=True, exist_ok=True)
    (reading / "guide.md").write_text(
        GUIDE.replace("# Reading companion",
                      "# Reader's companion to 'On sums' -- expert mode"),
        encoding="utf-8")
    os.utime(reading / "guide.md", (0, 0))

    doc = client.get(f"/api/papers/{slug}/companion").json()
    assert doc["title"] == "Reader's companion to “On sums” -- expert mode"
    assert "Generated on 1970-01-01 (UTC)." in doc["preamble"]
    assert "Generated on 1970-01-01 (UTC)." in client.get(
        f"/api/papers/{slug}/file/guide.md").text
    assert "<blockquote>" in doc["preamble"]
    assert [s["key"] for s in doc["sections"]] == [
        "main-results", "main-ideas", "history", "thin", "references"]
    assert {s["key"]: s["tone"] for s in doc["sections"]}["thin"] == ""
    # The rail should tell a reader what is absent rather than let it vanish.
    assert "About the proofs" in doc["missing"]
    assert "Worked examples" not in doc["missing"]
    assert doc["files"] == {"source.pdf": "source.pdf"}

    (reading / "guide.md").write_text(
        GUIDE.replace("# Reading companion",
                      '# Reader’s companion to "On sums" by Ada Example — expert edition — expert edition'),
        encoding="utf-8")
    assert client.get(f"/api/papers/{slug}/companion").json()["title"] == (
        'Reader’s companion to "On sums" by Ada Example — expert edition')


@needs_pdftotext
def test_a_paper_can_be_removed(client, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    gone = client.delete(f"/api/papers/{slug}").json()
    assert gone["ok"] is True and gone["freed"] > 0
    assert not (app_cfg.problems_dir / slug).exists()


# --------------------------------------------------------------------------
# pressing a button
# --------------------------------------------------------------------------


@needs_pdftotext
def test_a_bad_level_is_refused(client):
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read", json={"level": "wizard"})
    assert r.status_code == 400


@needs_pdftotext
@pytest.mark.parametrize("level", ["expert", "general"])
def test_either_button_starts_the_harness_on_the_uploaded_pdf(client, app_cfg, level):
    """Each button runs its own method — Antoine's Expert and Non-expert
    variants — on *this* PDF, on the model the server was started with (never
    the harness's expensive default), in a snapshot of the paper: the lab's
    isolated reading, so the paper's own folder is never written."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    captured = {}

    async def fake_launch(plan, session_number=1):
        captured["plan"] = plan

        class Handle:
            run_id = plan.spec.run_id
        return Handle()

    st.manager.launch = fake_launch
    r = client.post(f"/api/papers/{slug}/read", json={"level": level})
    assert r.status_code == 200, r.text

    plan = captured["plan"]
    variant = {"expert": "deslopify-expert", "general": "deslopify-nonexpert"}[level]
    assert plan.harness.id == variant and plan.harness.family == "deslopify"
    assert plan.model == "claude-haiku-4-5" and plan.effort == "low"
    assert plan.budget_usd == 0.5 and plan.budget_minutes == 5
    assert plan.extra["isolated_reading"] is True
    assert plan.cwd == app_cfg.runs_dir / plan.spec.run_id / "workspace"
    document = Path(plan.extra["document"])
    assert document.name == "source.pdf" and document.is_relative_to(plan.cwd)
    assert f"reader level: {level}" in plan.spec.notes
    assert library.load_paper(app_cfg, slug).level == level
    # The lock is held only for the snapshot.
    assert not (app_cfg.problems_dir / slug / ".atelier" / "lock.json").exists()


# --------------------------------------------------------------------------
# choosing the AI
# --------------------------------------------------------------------------


@pytest.fixture
def launched(client):
    """Stand in for the run manager: record the plan instead of running it."""
    captured: dict = {}

    async def fake_launch(plan, session_number=1):
        captured["plan"] = plan

        class Handle:
            run_id = plan.spec.run_id
        return Handle()

    client.app.state.deslopify.manager.launch = fake_launch
    return captured


@pytest.mark.parametrize("model, name", [
    ("claude-opus-5", "Claude Opus 5"),
    ("claude-opus-4-8", "Claude Opus 4.8"),
    # A snapshot date names a build, not a model.
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
    ("claude-opus-5[1m]", "Claude Opus 5 (1M)"),
    ("gpt-5.6-terra", "GPT-5.6 Terra"),
    ("gpt-5.4-mini", "GPT-5.4 mini"),
    ("gpt-5.5", "GPT-5.5"),
    # Nothing recognisable: shown as it is rather than confidently misnamed.
    ("o3", "o3"),
])
def test_a_model_is_named_the_way_people_name_it(model, name):
    provider = "claude" if model.startswith("claude") else "codex"
    assert agents.full_name(provider, model) == name


@pytest.mark.parametrize("model, small", [
    ("claude-haiku-4-5", True), ("gpt-5.4-mini", True), ("gpt-5-nano", True),
    ("claude-opus-5", False), ("claude-sonnet-5", False), ("gpt-5.6-terra", False),
    # "mini" inside a word is not a size.
    ("gemini-pro", False),
])
def test_small_models_are_told_apart(model, small):
    assert agents.is_small(model) is small


@needs_pdftotext
def test_the_menu_offers_what_can_run_here_and_starts_where_the_server_would(client):
    menu = client.get("/api/agents").json()
    assert [(g["account"], g["label"]) for g in menu["groups"]] == [
        ("default", "Claude"), ("codex", "ChatGPT")]
    claude, chatgpt = menu["groups"]
    assert [m["id"] for m in claude["models"]] == list(MODELS[0][1])
    assert [m["id"] for m in chatgpt["models"]] == list(MODELS[1][1])
    # Started with --model and --effort: that is the choice, and it outranks
    # whatever the browser remembers.
    assert menu["default"] == {"account": "default", "model": "claude-haiku-4-5",
                               "effort": "low"}
    assert menu["pinned"] is True
    assert "xhigh" in [e["id"] for e in claude["efforts"]]


def test_a_provider_that_is_not_installed_is_not_offered(app_cfg, monkeypatch):
    from atelier.harnesses import get_harness

    monkeypatch.setattr(agents, "installed", lambda provider: provider == "codex")
    harness = get_harness(app_cfg.harnesses_dir, "deslopify")
    offers = agents.offered(app_cfg, harness)
    assert [o.account for o in offers] == ["codex"]
    # With Claude missing, the default falls to what is there.
    default = agents.default_agent(app_cfg, harness, offers)
    assert (default.account, default.model) == ("codex", "gpt-5.5")


def test_a_model_named_on_the_command_line_is_offered_under_its_own_provider(
        app_cfg, monkeypatch):
    """Otherwise `--model gpt-…` would be handed to the Claude account."""
    from atelier.harnesses import get_harness

    monkeypatch.setattr(agents, "installed", lambda provider: True)
    harness = get_harness(app_cfg.harnesses_dir, "deslopify")
    offers = agents.offered(app_cfg, harness, extra="gpt-5.6-terra")
    assert "gpt-5.6-terra" in offers[1].models
    assert "gpt-5.6-terra" not in offers[0].models
    default = agents.default_agent(app_cfg, harness, offers, model="gpt-5.6-terra")
    assert (default.account, default.provider) == ("codex", "codex")


def test_a_custom_model_remains_the_default_for_followup_explanations(
        app_cfg, monkeypatch):
    from atelier.harnesses import get_harness

    monkeypatch.setattr(agents, "installed", lambda provider: True)
    harness = get_harness(app_cfg.harnesses_dir, "deslopify")
    offers = agents.offered(app_cfg, harness)
    custom = agents.Agent(account="codex", provider="codex", model="gpt-5.6-sol",
                          effort="low", custom=True)
    picked = agents.choose(offers, custom)
    assert picked == custom


@needs_pdftotext
@pytest.mark.parametrize("pick, provider, model, effort", [
    ({"account": "codex", "model": "gpt-5.5", "effort": "medium"},
     "codex", "gpt-5.5", "medium"),
    ({"account": "default", "model": "claude-opus-5", "effort": "max"},
     "claude", "claude-opus-5", "max"),
    # A model alone finds its own account; effort left out keeps the default.
    ({"model": "gpt-5.4-mini"}, "codex", "gpt-5.4-mini", "low"),
    # A model need not be in atelier.toml when the reader explicitly marks it
    # custom and chooses the installed account that will run it.
    ({"account": "codex", "model": "gpt-5.6-sol", "effort": "xhigh", "custom": True},
     "codex", "gpt-5.6-sol", "xhigh"),
])
def test_the_ai_chosen_on_the_page_is_the_one_that_runs(
        client, launched, pick, provider, model, effort):
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert", **pick})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert (plan.spec.provider, plan.account.id, plan.model, plan.effort) == (
        provider, pick.get("account") or provider.replace("claude", "default"),
        model, effort)
    assert plan.passes and all(model in p.argv for p in plan.passes)


@needs_pdftotext
@pytest.mark.parametrize("pick, why", [
    ({"account": "somebody-else"}, "not available"),
    ({"account": "default", "model": "gpt-5.5"}, "not offered for Claude"),
    ({"model": "claude-imaginary-9"}, "not offered"),
    ({"account": "default", "model": "gpt-5.6-sol", "custom": True},
     "cannot be run with"),
    ({"account": "codex", "model": "gpt-5.6-sol;echo", "custom": True},
     "custom model ID"),
    ({"effort": "ludicrous"}, "Effort must be one of"),
])
def test_an_ai_that_cannot_run_here_is_refused_before_anything_starts(
        client, launched, app_cfg, pick, why):
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert", **pick})
    assert r.status_code == 400 and why in r.json()["detail"]
    assert "plan" not in launched
    # Refused before the lock is taken, so the paper is free to be read.
    assert not (app_cfg.problems_dir / slug / ".atelier" / "lock.json").exists()


@needs_pdftotext
@pytest.mark.parametrize("model", ["claude-haiku-4-5", "gpt-5.5"])
def test_the_cap_the_page_states_is_the_cap_the_reading_gets(client, launched, model):
    """A priced model is held to dollars and minutes; an unpriced one — a
    ChatGPT subscription — to minutes alone, because its cost reads as zero and
    a dollar cap on it could never trip. The page must say which."""
    slug = upload(client, paper_pdf()).json()["slug"]
    menu = client.get("/api/agents").json()
    [stated] = [m["cap"] for g in menu["groups"] for m in g["models"] if m["id"] == model]
    client.post(f"/api/papers/{slug}/read", json={"level": "expert", "model": model})
    plan = launched["plan"]
    assert stated == {"usd": plan.budget_usd, "minutes": plan.budget_minutes}
    assert (stated["usd"] is None) is (model == "gpt-5.5")


def legacy_run(st, app_cfg, slug, run_id, started, status, model, *, drafted, level):
    """A reading as the reader ran them before Antoine's change: in the paper's
    own progress/reading/, with no snapshot."""
    st.db.upsert_run({"id": run_id, "problem": slug, "harness": "deslopify",
                      "provider": "claude", "account": "default", "model": model,
                      "effort": "high", "mode": "standard",
                      "started_at": started, "status": status})
    rd = app_cfg.runs_dir / run_id
    rd.mkdir(parents=True)
    (rd / "config.json").write_text(json.dumps(
        {"spec": {"notes": f"reader level: {level}"}}))
    if drafted:
        (rd / "loop.json").write_text(json.dumps(
            {"passes": [{"role": "draft", "index": 1}]}))


def isolated_run(st, app_cfg, slug, run_id, started, status, model, *, level,
                 guide: str | None = GUIDE, archived: bool = True):
    """A reading as they run now: a snapshot, archived to runs/<id>/reading/."""
    variant = {"expert": "deslopify-expert", "general": "deslopify-nonexpert"}[level]
    st.db.upsert_run({"id": run_id, "problem": slug, "harness": variant,
                      "provider": "claude", "account": "default", "model": model,
                      "effort": "high", "mode": "standard",
                      "started_at": started, "status": status})
    rd = app_cfg.runs_dir / run_id
    workspace = rd / "workspace"
    (workspace / "progress" / "reading").mkdir(parents=True)
    (rd / "config.json").write_text(json.dumps({
        "spec": {"notes": f"reader level: {level}"},
        "resolved": {"cwd": str(workspace),
                     "problem_root": str(app_cfg.problems_dir / slug)}}))
    (rd / "loop.json").write_text(json.dumps({"passes": [{"role": "draft"}]}))
    where = rd / "reading" if archived else workspace / "progress" / "reading"
    where.mkdir(parents=True, exist_ok=True)
    if guide is not None:
        (where / "guide.md").write_text(guide, encoding="utf-8")
    return where


def continued_draft_fixture(client, app_cfg):
    from atelier.compile import plan_run
    from atelier.harnesses import get_harness
    from atelier.spec import RunSpec
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    paper = library.load_paper(app_cfg, slug)
    harness = get_harness(app_cfg.harnesses_dir, "deslopify-nonexpert")
    parent = plan_run(app_cfg, paper.problem, harness, RunSpec(
        problem=slug, harness=harness.id, model="claude-haiku-4-5", provider="claude",
        account="default", effort="low", max_minutes=30,
        reader_profile="Reader background: Topology.\n\n"
                       "Explain unfamiliar terminology for this reader."), isolate_reading=True)
    reading = parent.cwd / "progress/reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text(GUIDE)
    (parent.cwd / "work/sources.txt").write_text("Keep the checked research.")
    (parent.run_dir / "notes.md").write_text("Prior notes.")
    (parent.run_dir / "reader-profile.json").write_text(json.dumps({
        "key": "previous-questions", "questions": [], "answers": {}, "asked": 0,
        "answered": 0, "cv": "Topology."}))
    (parent.run_dir / "result.json").write_text('{"state":"incomplete"}')
    st.db.upsert_run({"id": parent.spec.run_id, "problem": slug, "harness": harness.id,
                     "provider": "claude", "account": "default", "model": "claude-haiku-4-5",
                     "effort": "low", "mode": "standard", "status": "incomplete",
                     "started_at": "2026-10-14T10:00:00+00:00"})
    return slug, parent


@needs_pdftotext
def test_continue_draft_uses_selected_additional_time_and_saved_work(client, app_cfg, monkeypatch):
    slug, parent = continued_draft_fixture(client, app_cfg)
    st = client.app.state.deslopify
    launched = []
    async def launch(plan, **kwargs):
        launched.append(plan)
        return SimpleNamespace(run_id=plan.spec.run_id)
    monkeypatch.setattr(st.manager, "launch", launch)
    status = client.get(f"/api/papers/{slug}?run={parent.spec.run_id}").json()
    assert status["run"]["delivery"]["can_continue"]
    response = client.post(f"/api/papers/{slug}/continue", json={
        "run": parent.spec.run_id, "max_minutes": 12})
    assert response.status_code == 200, response.text
    [plan] = launched
    assert plan.budget_minutes == 12
    assert plan.spec.parent_run_id == parent.spec.run_id
    assert plan.model == parent.model and plan.spec.mode == "continue"
    assert plan.spec.reader_profile == parent.spec.reader_profile
    assert json.loads((plan.run_dir / "reader-profile.json").read_text())["cv"] == "Topology."
    assert (plan.cwd / "progress/reading/guide.md").read_text() == GUIDE
    assert (plan.cwd / "work/sources.txt").read_text() == "Keep the checked research."
    assert (parent.cwd / "progress/reading/guide.md").read_text() == GUIDE


def test_continue_keeps_a_private_copy_of_the_parents_edited_method(client, app_cfg, monkeypatch):
    from deslopify.app import method
    slug, parent = continued_draft_fixture(client, app_cfg)
    st = client.app.state.deslopify
    skill = client.get("/api/method/file", params={"path": "harness/SKILL-nonexpert.md"}).json()["text"]
    assert client.put("/api/method/file", json={
        "path": "harness/SKILL-nonexpert.md", "text": skill + "\nMY SAVED METHOD.\n"}).status_code == 200
    st.method.materialise(parent.run_dir / "method")
    launched = []
    async def launch(plan, **kwargs):
        launched.append(plan)
        return SimpleNamespace(run_id=plan.spec.run_id)
    monkeypatch.setattr(st.manager, "launch", launch)
    response = client.post(f"/api/papers/{slug}/continue", json={"run": parent.spec.run_id, "max_minutes": 12})
    assert response.status_code == 200, response.text
    [plan] = launched
    assert plan.harness.root == plan.run_dir / "method"
    assert "MY SAVED METHOD." in plan.harness.skill
    assert method.used_by(plan.run_dir) == ["harness/SKILL-nonexpert.md"]
    (parent.run_dir / "method/SKILL-nonexpert.md").write_text("Later changed.")
    assert "MY SAVED METHOD." in (plan.run_dir / "method/SKILL-nonexpert.md").read_text()


@pytest.mark.parametrize("minutes", [None, "", 0, -1, 601, "bad", True])
def test_continue_requires_valid_additional_time_before_launch(client, app_cfg, monkeypatch, minutes):
    slug, parent = continued_draft_fixture(client, app_cfg)
    async def forbidden(*args, **kwargs): pytest.fail("A rejected request must not launch an AI")
    monkeypatch.setattr(client.app.state.deslopify.manager, "launch", forbidden)
    response = client.post(f"/api/papers/{slug}/continue", json={
        "run": parent.spec.run_id, "max_minutes": minutes})
    assert response.status_code == 400, response.text


def test_continue_rejects_a_run_from_another_paper(client, app_cfg, monkeypatch):
    slug, parent = continued_draft_fixture(client, app_cfg)
    other = upload(client, paper_pdf("Another paper", words=420)).json()["slug"]
    async def forbidden(*args, **kwargs): pytest.fail("Must not launch")
    monkeypatch.setattr(client.app.state.deslopify.manager, "launch", forbidden)
    response = client.post(f"/api/papers/{other}/continue", json={
        "run": parent.spec.run_id, "max_minutes": 10})
    assert response.status_code == 404


def test_delivery_status_distinguishes_unchecked_content(client, app_cfg):
    slug, parent = continued_draft_fixture(client, app_cfg)
    (parent.run_dir / "delivery.json").write_text(json.dumps({
        "status": "review_unfinished", "label": "Draft — review unfinished",
        "missing": [], "issues": ["Revision did not finish."], "can_continue": True}))
    status = client.get(f"/api/papers/{slug}").json()
    assert status["run"]["delivery"]["label"] == "Draft — review unfinished"
    assert status["run"]["delivery"]["missing"] == []
    assert status["run"]["delivery"]["can_continue"]
    assert status["readings"]["general"]["state"] == "review_unfinished"
    assert client.get("/api/papers").json()[0]["state"] == "review_unfinished"


@needs_pdftotext
def test_the_companion_is_credited_to_the_reading_that_wrote_it(client, app_cfg):
    """Not the latest reading: one that failed before its first pass ended
    left the earlier companion in place, and did not write it."""
    slug, _ = paper_with(app_cfg, client)
    st = client.app.state.deslopify
    legacy_run(st, app_cfg, slug, "R1", "2026-09-28T10:00:00+00:00", "done",
               "claude-opus-5", drafted=True, level="general")
    legacy_run(st, app_cfg, slug, "R2", "2026-09-29T10:00:00+00:00", "failed",
               "claude-haiku-4-5", drafted=False, level="general")

    doc = client.get(f"/api/papers/{slug}/companion").json()
    assert doc["by"]["level"] == "general" and doc["run_id"] == "R1"
    assert doc["by"]["agent"]["name"] == "Claude Opus 5"
    assert doc["by"]["agent"]["small"] is False
    # The page is told a later reading at this level stopped without replacing it.
    assert doc["newer"]["id"] == "R2" and doc["newer"]["state"] == "failed"
    # The progress view, by contrast, is about the latest reading at the level.
    assert client.get(f"/api/papers/{slug}").json()["run"]["agent"]["model"] == \
        "claude-haiku-4-5"


@needs_pdftotext
def test_an_expert_and_a_non_expert_reading_stand_side_by_side(client, app_cfg):
    """Each isolated reading owns its folder, so reading the paper at the other
    level adds a companion instead of replacing one."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "E1", "2026-09-30T10:00:00+00:00", "done",
                 "claude-opus-5", level="expert",
                 guide=GUIDE.replace("Parity survives addition.", "EXPERT TEXT"))
    isolated_run(st, app_cfg, slug, "N1", "2026-09-30T11:00:00+00:00", "done",
                 "claude-sonnet-5", level="general",
                 guide=GUIDE.replace("Parity survives addition.", "PLAIN TEXT"))

    def ideas(doc):
        return next(s["html"] for s in doc["sections"] if s["key"] == "main-ideas")

    expert = client.get(f"/api/papers/{slug}/companion?level=expert").json()
    plain = client.get(f"/api/papers/{slug}/companion?level=general").json()
    assert "EXPERT TEXT" in ideas(expert) and expert["run_id"] == "E1"
    assert "PLAIN TEXT" in ideas(plain) and plain["by"]["agent"]["model"] == "claude-sonnet-5"
    # The paper opens at its newest companion.
    assert client.get(f"/api/papers/{slug}/companion").json()["run_id"] == "N1"
    # The paper's own folder was never written.
    assert not (app_cfg.problems_dir / slug / "progress" / "reading" / "guide.md").exists()
    [row] = client.get("/api/papers").json()
    assert row["state"] == "ready" and row["levels"] == ["expert", "general"]
    status = client.get(f"/api/papers/{slug}?level=expert").json()
    assert status["readings"]["general"]["state"] == "ready"
    assert status["run"]["id"] == "E1" and status["companion_run"] == "E1"
    # Files and the map come from the reading asked for.
    files = expert["files"]
    assert "source.pdf" in files
    r = client.get(f"/api/papers/{slug}/file/guide.md?run=N1")
    assert r.status_code == 200 and "PLAIN TEXT" in r.text


@needs_pdftotext
def test_a_paper_lists_every_run_and_an_older_run_can_be_opened(client, app_cfg):
    """The paper is the durable object; each reading remains inspectable under it."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "E1", "2026-09-30T10:00:00+00:00", "done",
                 "claude-opus-5", level="expert",
                 guide=GUIDE.replace("Parity survives addition.", "FIRST RUN"))
    isolated_run(st, app_cfg, slug, "E2", "2026-09-30T11:00:00+00:00", "done",
                 "claude-sonnet-5", level="expert",
                 guide=GUIDE.replace("Parity survives addition.", "SECOND RUN"))

    status = client.get(f"/api/papers/{slug}").json()
    assert [run["id"] for run in status["runs"]] == ["E2", "E1"]
    assert all(run["companion"] for run in status["runs"])
    [row] = client.get("/api/papers").json()
    assert row["run_count"] == 2

    first = client.get(f"/api/papers/{slug}?level=expert&run=E1").json()
    assert first["run"]["id"] == "E1" and first["view_companion"] is True
    companion = client.get(f"/api/papers/{slug}/companion?run=E1").json()
    ideas_html = next(section["html"] for section in companion["sections"]
                      if section["key"] == "main-ideas")
    assert companion["run_id"] == "E1" and "FIRST RUN" in ideas_html
    assert client.get(f"/api/papers/{slug}?run=NOT-THIS-PAPER").status_code == 404
    assert client.get(f"/api/papers/{slug}/companion?run=NOT-THIS-PAPER").status_code == 404


@needs_pdftotext
def test_runs_are_listed_newest_first_and_numbered_from_the_first(client, app_cfg):
    """By when each started, however the database spells it, and the same way
    on every visit: a run rebuilt by `atelier reindex` kept its id's spelling,
    which sorted below a run started earlier in the same hour."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "2026-09-30T10-00-00Z-e001", "2026-09-30T10:00:00+00:00",
                 "done", "claude-opus-5", level="expert")
    isolated_run(st, app_cfg, slug, "2026-09-30T10-30-00Z-n001", "2026-09-30T10-30-00Z",
                 "failed", "claude-opus-5", level="general", guide=None)
    isolated_run(st, app_cfg, slug, "2026-09-30T10-45-00Z-e002", "2026-09-30T10:45:00.5+00:00",
                 "done", "claude-sonnet-5", level="expert")
    # Two started in the same instant: the run id decides, every time.
    isolated_run(st, app_cfg, slug, "2026-09-30T11-00-00Z-n00a", "2026-09-30T11:00:00+00:00",
                 "done", "claude-opus-5", level="general")
    isolated_run(st, app_cfg, slug, "2026-09-30T11-00-00Z-n00b", "2026-09-30T11:00:00+00:00",
                 "cancelled", "claude-opus-5", level="general", guide=None)

    runs = client.get(f"/api/papers/{slug}").json()["runs"]
    assert [(r["id"][-4:], r["number"]) for r in runs] == [
        ("n00b", 5), ("n00a", 4), ("e002", 3), ("n001", 2), ("e001", 1)]
    # One spelling of the time for the page, whatever the row held.
    assert runs[3]["started"] == "2026-09-30T10:30:00+00:00"
    # The companion each level opens with is marked; the earlier one is not.
    assert [r["id"][-4:] for r in runs if r["current"]] == ["n00a", "e002"]
    assert [r["id"][-4:] for r in runs if r["companion"]] == ["n00a", "e002", "e001"]
    assert client.get(f"/api/papers/{slug}").json()["runs"] == runs


@needs_pdftotext
def test_an_earlier_companion_is_called_earlier_not_replaced_by_a_failure(client, app_cfg):
    """Opened from the list of runs, an older companion is told apart from the
    one its level opens with. It once said a later reading had "stopped before
    it produced a companion" when that reading had finished perfectly well."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "E1", "2026-09-30T10:00:00+00:00", "done",
                 "claude-opus-5", level="expert")
    isolated_run(st, app_cfg, slug, "E2", "2026-09-30T11:00:00+00:00", "done",
                 "claude-sonnet-5", level="expert")

    first = client.get(f"/api/papers/{slug}/companion?run=E1").json()
    assert first["newer"] is None
    assert first["current"]["id"] == "E2" and first["current"]["number"] == 2
    assert (first["number"], first["runs"]) == (1, 2)
    shown = client.get(f"/api/papers/{slug}/companion?level=expert").json()
    assert shown["run_id"] == "E2" and shown["current"] is None and shown["newer"] is None

    # A third that stopped before writing anything: the current companion says
    # so; the earlier one still names the current, and only that.
    isolated_run(st, app_cfg, slug, "E3", "2026-09-30T12:00:00+00:00", "failed",
                 "claude-haiku-4-5", level="expert", guide=None)
    shown = client.get(f"/api/papers/{slug}/companion?level=expert").json()
    assert shown["run_id"] == "E2" and shown["newer"]["id"] == "E3"
    assert shown["newer"]["number"] == 3
    first = client.get(f"/api/papers/{slug}/companion?run=E1").json()
    assert first["current"]["id"] == "E2" and first["newer"] is None


@needs_pdftotext
def test_a_reading_that_ended_before_it_was_archived_is_found_in_its_snapshot(client, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "X1", "2026-09-30T10:00:00+00:00", "cancelled",
                 "claude-opus-5", level="expert", archived=False)
    doc = client.get(f"/api/papers/{slug}/companion?level=expert").json()
    assert doc["run_id"] == "X1"
    # A draft nothing finished checking is not called ready.
    [row] = client.get("/api/papers").json()
    assert row["state"] == "incomplete"


@needs_pdftotext
def test_a_latex_source_is_offered_as_a_file_to_keep(client, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    where = isolated_run(st, app_cfg, slug, "T1", "2026-09-30T10:00:00+00:00", "done",
                         "claude-opus-5", level="expert")
    (where / "companion.tex").write_text("\\documentclass{article}", encoding="utf-8")
    assert client.get(f"/api/papers/{slug}/companion").json()["files"]["companion.tex"]
    r = client.get(f"/api/papers/{slug}/file/companion.tex?run=T1")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]


COMPANION_TEX = "\n".join([
    r"\documentclass{article}", r"\begin{document}", r"\maketitle",
    r"\subsection*{Main results}", "Parity survives addition.", r"\end{document}", ""])


@needs_pdftotext
@pytest.mark.parametrize("old_contents", [False, True])
def test_an_older_companion_pdf_is_rebuilt_once_with_contents(client, app_cfg, monkeypatch, old_contents):
    """A companion written before companions had contents is typeset again
    from its own source, once, and kept with the paper — not in the reading,
    which is the record of what the run made."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    where = isolated_run(st, app_cfg, slug, "T1", "2026-09-30T10:00:00+00:00", "done",
                         "claude-opus-5", level="expert")
    source = COMPANION_TEX.replace(r"\maketitle", "\\maketitle\n\\tableofcontents") \
        if old_contents else COMPANION_TEX
    (where / "companion.tex").write_text(source, encoding="utf-8")
    (where / "companion.pdf").write_bytes(b"%PDF-1.4 the run's own\n%%EOF\n")
    built = []

    def fake_rebuild(tex, output):
        built.append(Path(tex).read_text(encoding="utf-8"))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"%PDF-1.4 with contents\n%%EOF\n")
        return Path(output)

    monkeypatch.setattr("deslopify.summary_pdf.rebuild_with_contents", fake_rebuild)
    for _ in range(2):
        r = client.get(f"/api/papers/{slug}/file/companion.pdf?run=T1")
        assert r.status_code == 200 and b"with contents" in r.content
    assert len(built) == 1
    assert (where / "companion.pdf").read_bytes().startswith(b"%PDF-1.4 the run's own")
    kept = list((app_cfg.problems_dir / slug / ".atelier" / "companions").glob("T1-*.pdf"))
    assert len(kept) == 1
    # The source offered beside it is the one that typesets it.
    tex = client.get(f"/api/papers/{slug}/file/companion.tex?run=T1").text
    assert r"\tableofcontents" in tex and r"\section{Main results}" in tex


@needs_pdftotext
def test_a_companion_from_before_latex_sources_is_typeset_from_its_guide(
        client, app_cfg, monkeypatch):
    """Readings older than the method's LaTeX sources have only the guide their
    PDF was made from: the contents come from typesetting that again."""
    slug, paper = paper_with(app_cfg, client)
    (paper.reading / "companion.pdf").write_bytes(b"%PDF-1.4 the old layout\n%%EOF\n")
    asked = []

    def fake_guide(guide, output, *, audience, paper_title=None):
        asked.append((Path(guide).name, audience, paper_title))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"%PDF-1.4 with contents\n%%EOF\n")
        return Path(output)

    monkeypatch.setattr("deslopify.summary_pdf.companion_from_guide", fake_guide)
    r = client.get(f"/api/papers/{slug}/file/companion.pdf")
    assert r.status_code == 200 and b"with contents" in r.content
    assert asked == [("guide.md", "expert", paper.title)]


@needs_pdftotext
def test_a_companion_pdf_that_cannot_be_rebuilt_is_served_as_it_was(client, app_cfg, monkeypatch):
    from deslopify.summary_pdf import SummaryPdfError

    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    where = isolated_run(st, app_cfg, slug, "T1", "2026-09-30T10:00:00+00:00", "done",
                         "claude-opus-5", level="expert")
    (where / "companion.tex").write_text(COMPANION_TEX, encoding="utf-8")
    (where / "companion.pdf").write_bytes(b"%PDF-1.4 the run's own\n%%EOF\n")

    def broken(tex, output):
        raise SummaryPdfError("latexmk is required")

    monkeypatch.setattr("deslopify.summary_pdf.rebuild_with_contents", broken)
    r = client.get(f"/api/papers/{slug}/file/companion.pdf?run=T1")
    assert r.status_code == 200 and b"the run's own" in r.content


@needs_pdftotext
def test_a_companion_with_current_heading_style_is_served_untouched(client, app_cfg, monkeypatch):
    from deslopify.summary_pdf import generation_date, with_contents
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    where = isolated_run(st, app_cfg, slug, "T1", "2026-09-30T10:00:00+00:00", "done",
                         "claude-opus-5", level="expert")
    (where / "companion.tex").write_text(
        with_contents(COMPANION_TEX, generated_on=generation_date(where / "guide.md")),
        encoding="utf-8")
    (where / "companion.pdf").write_bytes(b"%PDF-1.4 the run's own\n%%EOF\n")
    monkeypatch.setattr("deslopify.summary_pdf.rebuild_with_contents",
                        lambda *a: pytest.fail("rebuilt a PDF with the current heading format"))
    r = client.get(f"/api/papers/{slug}/file/companion.pdf?run=T1")
    assert b"the run's own" in r.content


@needs_pdftotext
def test_a_second_reading_at_the_same_level_waits_for_the_first(client, app_cfg, launched):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify

    class Live:
        run_id = "LIVE"
        plan = SimpleNamespace(problem=SimpleNamespace(slug=slug),
                               account=SimpleNamespace(id="default"),
                               harness=SimpleNamespace(id="deslopify-expert"),
                               spec=SimpleNamespace(notes="reader level: expert"))
        state = SimpleNamespace(terminal=False)

    st.manager.active = lambda: [Live()]
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert r.status_code == 409 and "at this level" in r.json()["detail"]
    # The other level is free.
    r = client.post(f"/api/papers/{slug}/read", json={"level": "general"})
    assert r.status_code == 200, r.text
    assert launched["plan"].harness.id == "deslopify-nonexpert"


# --------------------------------------------------------------------------
# the map, as the page draws it
# --------------------------------------------------------------------------

GRAPH = {
    "document": "progress/source.pdf",
    "nodes": [
        {"id": "H1", "label": "a and b are even", "quote": "Let a and b be even integers."},
        {"id": "H2", "label": "integers are closed", "quote": "m + n is an integer by closure"},
        {"id": "C1", "label": "Lemma 1", "quote": "If a is even there is an integer m"},
        {"id": "Q1", "label": "the sum is even", "quote": "so a + b is even."},
        {"id": "!1", "label": "search not shown", "quote": ""},
    ],
    "edges": [
        {"source": "H1", "target": "C1", "type": "SUPPORTS", "justification": "applies"},
        {"source": "C1", "target": "Q1", "type": "SUPPORTS", "justification": "factor"},
        {"source": "H2", "target": "Q1", "type": "SUPPORTS", "justification": "closure"},
    ],
}


def paper_with(app_cfg, client, *, graph=None, review=None, dispositions=None,
               guide=GUIDE):
    slug = upload(client, paper_pdf()).json()["slug"]
    reading = app_cfg.problems_dir / slug / "progress" / "reading"
    reading.mkdir(parents=True, exist_ok=True)
    if guide is not None:
        (reading / "guide.md").write_text(guide, encoding="utf-8")
    if graph is not None:
        (reading / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    if review is not None:
        (reading / "review-1.json").write_text(json.dumps(review), encoding="utf-8")
    if dispositions is not None:
        (reading / "dispositions-1.json").write_text(json.dumps(dispositions),
                                                     encoding="utf-8")
    return slug, library.load_paper(app_cfg, slug)


@needs_pdftotext
def test_gaps_are_drawn_beneath_the_argument_not_among_its_hypotheses(app_cfg, client):
    """A gap has no edges, so layering alone would put it on the top row beside
    the hypotheses, as if it were something the proof starts from."""
    _, paper = paper_with(app_cfg, client, graph=GRAPH)
    drawn = library.read_map(paper.reading)
    assert drawn["gaps"] == ["!1"]
    assert all("!1" not in row for row in drawn["rows"])
    assert drawn["rows"][0] == ["H1", "H2"] and drawn["rows"][-1] == ["Q1"]
    assert drawn["nodes"]["H1"]["role"] == "Hypothesis"
    assert drawn["nodes"]["!1"]["role"] == "Gap"


def test_each_row_is_ordered_under_what_it_rests_on():
    """Alphabetical order would put B under X and A under Y — crossing both."""
    rows = [["X", "Y"], ["A", "B"]]
    parents = {"A": {"Y"}, "B": {"X"}}
    assert library._order_rows(rows, parents) == [["X", "Y"], ["B", "A"]]


@needs_pdftotext
def test_the_map_endpoint_serves_the_graph_only_once_there_is_one(app_cfg, client):
    slug, _ = paper_with(app_cfg, client)
    assert client.get(f"/api/papers/{slug}/map").status_code == 404
    reading = app_cfg.problems_dir / slug / "progress" / "reading"
    (reading / "graph.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    assert client.get(f"/api/papers/{slug}/map").json()["gaps"] == ["!1"]
    assert client.get(f"/api/papers/{slug}/companion").json()["has_map"] is True


# --------------------------------------------------------------------------
# what the check found
# --------------------------------------------------------------------------

REVIEW = {
    "round": 1, "summary": "mostly faithful",
    "findings": [
        {"id": "F1", "kind": "unclear", "severity": "minor",
         "where": "guide:8. Where the document is thin",
         "problem": "Is the odd case defined?", "fix": "say whether it is"},
        {"id": "F2", "kind": "unfaithful", "severity": "blocking",
         "where": "document:12", "quotation": "Let a and b be even integers.",
         "problem": "the guide claims more than the paper", "fix": "narrow it"},
        {"id": "F3", "kind": "ungrounded", "severity": "worth-fixing",
         "where": "graph:C1->Q1", "problem": "the edge is not supported",
         "fix": "ground it or drop it"},
    ],
}


@needs_pdftotext
def test_the_review_is_ordered_by_severity_and_said_plainly(app_cfg, client):
    _, paper = paper_with(app_cfg, client, review=REVIEW)
    review = library.read_review(paper.reading)
    assert [f["id"] for f in review["findings"]] == ["F2", "F3", "F1"]
    f2, f3, f1 = review["findings"]
    assert f2["severity_label"] == "Serious"
    assert f2["kind_label"] == "Says something the paper does not"
    assert review["counts"] == {"blocking": 1, "worth-fixing": 1, "minor": 1}


@needs_pdftotext
def test_each_finding_points_at_a_section_a_reader_can_jump_to(app_cfg, client):
    _, paper = paper_with(app_cfg, client, review=REVIEW)
    by = {f["id"]: f["place"] for f in library.read_review(paper.reading)["findings"]}
    assert by["F1"] == {"section": "thin", "label": "Where it is thin"}
    assert by["F3"]["section"] == "map" and by["F3"]["node"] == "C1"
    assert by["F2"] == {"section": None, "label": "a passage in the paper"}


@needs_pdftotext
def test_a_misnamed_quotation_is_still_shown_to_the_reader(app_cfg, client):
    """Seen on a live Haiku run: "quotation" for "quote". The lab rejects it; the
    reader should still see the passage, which is the most useful part."""
    _, paper = paper_with(app_cfg, client, review=REVIEW)
    f2 = next(f for f in library.read_review(paper.reading)["findings"] if f["id"] == "F2")
    assert f2["quote"] == "Let a and b be even integers."


@needs_pdftotext
def test_an_unrevised_review_says_so_and_a_revised_one_carries_its_answers(app_cfg, client):
    _, paper = paper_with(app_cfg, client, review=REVIEW)
    before = library.read_review(paper.reading)
    assert before["revised"] is False
    assert all(f["answer"] is None for f in before["findings"])

    (paper.reading / "dispositions-1.json").write_text(json.dumps({"dispositions": [
        {"id": "F2", "disposition": "fixed", "where": "guide:Main results",
         "note": "narrowed the claim"},
        {"id": "F1", "disposition": "rejected", "note": "the paper never defines it"},
    ]}), encoding="utf-8")
    after = {f["id"]: f for f in library.read_review(paper.reading)["findings"]}
    assert library.read_review(paper.reading)["revised"] is True
    assert after["F2"]["answer"] == {"disposition": "fixed", "note": "narrowed the claim"}
    assert after["F3"]["answer"] is None


@pytest.mark.parametrize("finished, says", [
    ("the second reading raised nothing at worth-fixing or worse", "Not needed"),
    ("the run's budget was spent before the next pass could begin", "budget ran out"),
    ("stopped from the reader", "stopped"),
    ("", "Not reached"),
])
def test_a_skipped_step_says_why_and_the_why_is_true(finished, says):
    """A check that found nothing and a budget that ran out look alike from the
    outside and mean opposite things. Reporting the first when the second
    happened would tell someone an unreviewed guide had been cleared."""
    from deslopify.app.server import _why_skipped

    assert says in _why_skipped(finished)


@pytest.mark.parametrize("raised, says", [
    (0, "found nothing to fix"),
    (1, "the one point the check raised was not serious enough"),
    (3, "none of the 3 points the check raised was serious enough"),
])
def test_a_revision_not_needed_still_counts_what_was_left_open(raised, says):
    """The loop's "raised nothing at worth-fixing or worse" means nothing
    serious enough to revise for, not nothing at all."""
    from deslopify.app.server import _why_skipped

    said = _why_skipped("the second reading raised nothing at worth-fixing or worse", raised)
    assert says in said
    assert ("nothing to fix" in said) is (raised == 0)


@needs_pdftotext
def test_a_balanced_reading_with_only_minor_points_does_not_contradict_itself(
        client, app_cfg):
    """The audit's step counts the points it raised; the revision it made
    unnecessary must not then say there were none."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    where = isolated_run(st, app_cfg, slug, "B1", "2026-10-04T10:00:00+00:00", "done",
                         "claude-haiku-4-5", level="expert")
    minor = [f for f in REVIEW["findings"] if f["severity"] == "minor"]
    (where / "review-1.json").write_text(json.dumps({**REVIEW, "findings": minor * 2}))
    rd = app_cfg.runs_dir / "B1"
    config = json.loads((rd / "config.json").read_text())
    config["resolved"]["loop"] = {"passes": [
        {"role": "draft"}, {"role": "auditor"}, {"role": "balanced-reviser"}]}
    (rd / "config.json").write_text(json.dumps(config))
    (rd / "loop.json").write_text(json.dumps({
        "passes": [{"role": "draft"}, {"role": "auditor"}],
        "finished": "the second reading raised nothing at worth-fixing or worse "
                    "(2 minor finding(s) recorded) — the companion is as good as "
                    "this loop can make it"}))

    steps = client.get(f"/api/papers/{slug}?run=B1").json()["steps"]
    assert [s["role"] for s in steps] == ["draft", "auditor", "balanced-reviser"]
    _, audit, revision = steps
    assert audit["state"] == "done" and "found 2 things to fix" in audit["summary"]
    assert revision["state"] == "skipped"
    assert "none of the 2 points" in revision["summary"]
    assert "nothing to fix" not in revision["summary"]


# --------------------------------------------------------------------------
# what a pass did, said as a sentence
# --------------------------------------------------------------------------


@needs_pdftotext
def test_each_pass_is_summarised_from_what_it_left_behind(app_cfg, client):
    from deslopify.app.server import _plain_summary

    _, paper = paper_with(app_cfg, client, graph=GRAPH, review=REVIEW,
                          dispositions={"dispositions": [
                              {"id": "F1", "disposition": "fixed", "note": "x"},
                              {"id": "F2", "disposition": "rejected", "note": "y"}]})
    (paper.reading / "clarity-review-1.md").write_text(
        "# Clarity\n\n### 1. First\nq\n\n### 2. Second\nq\n", encoding="utf-8")
    run_dir = app_cfg.runs_dir / "R"
    summary = {role: _plain_summary(role, paper.reading, run_dir) for role in
               ("draft", "graph-builder", "verifier-blind", "verifier", "reviser")}
    assert summary["draft"].startswith("Wrote a ") and "word explanation" in summary["draft"]
    assert summary["graph-builder"] == (
        "Mapped the argument in 4 steps, and marked 1 place where the paper skips work.")
    assert summary["verifier-blind"].startswith("Came back with 2 questions")
    assert summary["verifier"] == "Found 3 things to fix, 1 of them serious."
    assert summary["reviser"] == "Fixed 1, and disagreed with 1, giving reasons."


def test_an_older_reading_keeps_the_pipeline_it_actually_ran(app_cfg):
    from deslopify.app.server import _pipeline

    run = app_cfg.runs_dir / "OLD-FIVE-PASS"
    run.mkdir(parents=True)
    roles = ["draft", "graph-builder", "verifier-blind", "verifier", "reviser"]
    (run / "config.json").write_text(json.dumps({
        "resolved": {"loop": {"passes": [{"role": role} for role in roles]}},
    }), encoding="utf-8")
    assert _pipeline(app_cfg, "deslopify-expert", "OLD-FIVE-PASS") == roles


# --------------------------------------------------------------------------
# the same paper twice
# --------------------------------------------------------------------------


@needs_pdftotext
def test_the_same_pdf_dropped_twice_is_the_same_paper(client, app_cfg):
    """Otherwise the reader pays for the same companion twice."""
    data = paper_pdf()
    first = upload(client, data).json()
    again = upload(client, data, "a-different-name.pdf").json()
    assert again["existing"] is True and again["slug"] == first["slug"]
    assert len(list(app_cfg.problems_dir.iterdir())) == 1


@needs_pdftotext
def test_a_guide_on_disk_is_not_yet_a_finished_reading(client, app_cfg):
    """The first pass writes guide.md and four more follow. Calling the paper
    ready the moment the guide appears would hand over the draft as though it
    had been checked: a reading that stopped after its draft is visibly incomplete, and
    only one that finished is "ready"."""
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    [row] = client.get("/api/papers").json()
    assert row["state"] == "unread"

    reading = app_cfg.problems_dir / slug / "progress" / "reading"
    reading.mkdir(parents=True, exist_ok=True)
    (reading / "guide.md").write_text(GUIDE, encoding="utf-8")
    legacy_run(st, app_cfg, slug, "R1", "2026-09-28T10:00:00+00:00", "cancelled",
               "claude-opus-5", drafted=True, level="expert")
    assert client.get("/api/papers").json()[0]["state"] == "incomplete"

    st.db.upsert_run({"id": "R1", "problem": slug, "harness": "deslopify",
                      "provider": "claude", "mode": "standard",
                      "started_at": "2026-09-28T10:00:00+00:00", "status": "done"})
    [row] = client.get("/api/papers").json()
    assert row["state"] == "ready" and row["levels"] == ["expert"]


# --------------------------------------------------------------------------
# advanced settings
# --------------------------------------------------------------------------


@needs_pdftotext
def test_incomplete_reading_keeps_its_draft_without_claiming_a_completed_step(client, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "partial-run", "2026-10-14T10:00:00+00:00",
                 "incomplete", "claude-opus-5", level="expert")
    (app_cfg.runs_dir / "partial-run" / "loop.json").write_text(json.dumps({
        "finished": "the guide is incomplete, so independent review did not begin",
        "completed": False,
        "passes": [{"role": "draft", "index": 1, "completed": False,
                    "problems": ["The bibliography is missing."]}],
    }))
    response = client.get(f"/api/papers/{slug}?run=partial-run")
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["run"]["state"] == "incomplete"
    assert status["view_companion"] is True
    assert status["steps"][0]["state"] == "incomplete"
    assert status["steps"][0]["summary"].startswith("Incomplete.")
    assert status["steps"][0]["problems"] == ["The bibliography is missing."]
    assert status["readings"]["expert"]["state"] == "incomplete"
    assert all(step["state"] == "skipped" for step in status["steps"][1:])
    assert client.get(f"/api/papers/{slug}/companion?run=partial-run").status_code == 200
    assert client.get("/api/papers").json()[0]["state"] == "incomplete"

    # The older finished companion at another level does not replace the newest run.
    isolated_run(st, app_cfg, slug, "finished-run", "2026-10-13T10:00:00+00:00",
                 "done", "claude-opus-5", level="general")
    row = client.get("/api/papers").json()[0]
    assert row["state"] == "incomplete" and row["levels"] == ["general"]
    finished = client.get(f"/api/papers/{slug}?run=finished-run").json()
    assert finished["run"]["state"] == "done"

    # When a later run completes, the list reports it even though the other
    # level's latest draft is still incomplete.
    isolated_run(st, app_cfg, slug, "newest-run", "2026-10-15T10:00:00+00:00",
                 "done", "claude-opus-5", level="general")
    row = client.get("/api/papers").json()[0]
    assert row["state"] == "ready"
    assert client.get(f"/api/papers/{slug}").json()["run"]["id"] == "newest-run"


@needs_pdftotext
def test_the_caps_set_on_the_page_are_the_caps_the_reading_gets(client, launched):
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read",
                    json={"level": "expert", "max_usd": 2.5, "max_minutes": 12})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert (plan.budget_usd, plan.budget_minutes) == (2.5, 12)


@needs_pdftotext
def test_the_parallel_cap_set_on_the_page_is_enforced_across_papers(client, launched):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    original_active = st.manager.active
    other = SimpleNamespace(plan=SimpleNamespace(
        problem=SimpleNamespace(slug="another-paper"), account=None))
    st.manager.active = lambda: [other]
    try:
        blocked = client.post(
            f"/api/papers/{slug}/read",
            json={"level": "expert", "max_concurrent": 1})
        allowed = client.post(
            f"/api/papers/{slug}/read",
            json={"level": "expert", "max_concurrent": 2})
    finally:
        st.manager.active = original_active

    assert blocked.status_code == 429
    assert "webpage cap allows 1 parallel reading" in blocked.json()["detail"]
    assert allowed.status_code == 200, allowed.text
    assert "plan" in launched


@needs_pdftotext
@pytest.mark.parametrize("value", [0, 7, 1.5, True, "many"])
def test_a_parallel_cap_outside_the_server_range_is_refused(client, launched, value):
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(
        f"/api/papers/{slug}/read",
        json={"level": "expert", "max_concurrent": value})
    assert r.status_code == 400
    assert "parallel-reading cap" in r.json()["detail"]
    assert "plan" not in launched


@needs_pdftotext
def test_reader_uses_balanced_pipeline_and_adds_the_map_without_an_extra_pass(
        client, launched):
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read",
                    json={"level": "expert", "include_map": True})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    harness = plan.harness
    assert harness.loop.sequence == ["auditor", "balanced-reviser"]
    assert harness.budget.max_minutes == 50
    assert harness.metadata["include_proof_map"] is True
    assert "# Optional one-page diagram" in plan.system_append
    assert "progress/reading/graph.json" not in harness.roles["auditor"].writes
    from atelier.compile import plan_next_pass
    revision = plan_next_pass(client.app.state.deslopify.cfg, plan,
                              role_name="balanced-reviser", round_no=1, index=3)
    assert "progress/reading/map.pdf" in revision.writes
    assert len(harness.loop.sequence) == 2


@needs_pdftotext
def test_an_unpriced_model_keeps_the_time_cap_set_on_the_page(client, launched):
    """A dollar cap cannot bind on a model with no per-token price; the time
    cap asked for is the one it gets."""
    slug = upload(client, paper_pdf()).json()["slug"]
    client.post(f"/api/papers/{slug}/read",
                json={"level": "expert", "model": "gpt-5.5", "max_usd": 3, "max_minutes": 20})
    plan = launched["plan"]
    assert plan.budget_usd is None and plan.budget_minutes == 20


@needs_pdftotext
@pytest.mark.parametrize("caps, why", [
    ({"max_usd": 0}, "money cap"),
    ({"max_usd": 10_000}, "money cap"),
    ({"max_usd": "lots"}, "money cap"),
    ({"max_minutes": 0}, "time cap"),
    ({"max_minutes": 100_000}, "time cap"),
])
def test_a_cap_out_of_bounds_is_refused_before_anything_starts(client, launched, caps, why):
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert", **caps})
    assert r.status_code == 400 and why in r.json()["detail"].lower()
    assert "plan" not in launched


def test_the_menu_says_which_models_a_dollar_cap_can_bind_and_the_defaults(client):
    menu = client.get("/api/agents").json()
    priced = {m["id"]: m["priced"] for g in menu["groups"] for m in g["models"]}
    assert priced["claude-haiku-4-5"] is True and priced["gpt-5.5"] is False
    limits = menu["limits"]
    # The server was started with --max-usd 0.5 --max-minutes 5.
    assert (limits["max_usd"], limits["max_minutes"]) == (0.5, 5)
    assert limits["usd_range"] == [0.1, 200.0] and limits["minutes_range"] == [1, 600]
    assert limits["max_concurrent_runs"] == 6
    assert limits["concurrent_range"] == [1, 6]
    assert {group["max_concurrent"] for group in menu["groups"]} == {2}
    settings_js = client.get("/static/settings.js").text
    assert "Parallel readings" in settings_js and "max_concurrent" in settings_js


# --------------------------------------------------------------------------
# getting ready, updates, quitting, the report (2026-10-06)
# --------------------------------------------------------------------------


@needs_pdftotext
def test_a_reading_that_could_not_finish_is_refused_before_anything_is_spent(
        client, launched, app_cfg):
    """No LaTeX, or an AI that is not signed in: the reading would stop after
    its first pass, which has already been paid for. It never starts."""
    slug = upload(client, paper_pdf()).json()["slug"]
    client.ready.tex = False
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert r.status_code == 409 and "LaTeX is not installed" in r.json()["detail"]
    assert "nothing has been spent" in r.json()["detail"]
    client.ready.tex = True
    client.ready.signed_in = {"codex"}
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert r.status_code == 409 and "Claude is not signed in" in r.json()["detail"]
    assert "plan" not in launched
    runs = list(app_cfg.runs_dir.iterdir()) if app_cfg.runs_dir.exists() else []
    assert runs == []                        # not even a folder for the run
    # Signed in after all: the same request starts it.
    client.ready.signed_in = True
    assert client.post(f"/api/papers/{slug}/read", json={"level": "expert"}).status_code == 200
    assert "plan" in launched


def test_the_ai_menu_says_who_is_signed_in_and_starts_on_one_that_is(client):
    menu = client.get("/api/agents").json()
    assert all(g["signed_in"] for g in menu["groups"])
    assert menu["default"]["account"] == "default" and menu["ready"]["ok"]
    client.ready.signed_in = {"codex"}
    menu = client.get("/api/agents").json()
    signed = {g["provider"]: g["signed_in"] for g in menu["groups"]}
    assert signed == {"claude": False, "codex": True}
    assert menu["default"]["account"] == "codex"
    assert menu["default"]["model"] in [m["id"] for g in menu["groups"]
                                        if g["provider"] == "codex" for m in g["models"]]
    client.ready.tex = False
    assert client.get("/api/agents").json()["ready"]["tex"] is False


def test_getting_ready_reports_and_starts_jobs(client):
    now = client.get("/api/ready").json()
    assert now["ok"] and now["pdf"] == "PDFium" and now["jobs"] == []
    job = client.post("/api/ready/install-latex").json()
    assert job["kind"] == "install-latex" and client.ready.started == ["install-latex"]
    assert client.get(f"/api/ready/job/{job['id']}").json()["id"] == job["id"]
    assert client.get("/api/ready/job/nope").status_code == 404
    assert client.post("/api/ready/install-everything").status_code == 404
    # A code goes only to a Claude sign-in, and only if it looks like one.
    assert client.post(f"/api/ready/job/{job['id']}/code",
                       json={"code": "ABC"}).status_code == 400
    signin = client.post("/api/ready/signin-claude").json()
    assert client.post(f"/api/ready/job/{signin['id']}/code",
                       json={"code": "two words"}).status_code == 400


def test_another_account_waits_for_the_readings_that_use_it(client):
    """Another Claude or ChatGPT account for unpacman would take over a
    reading's next steps halfway, and going back to the Mac's sign-in signs
    out the account its step runs on (ready.Checker._switch, _revert): not
    while a reading on that account is running."""
    from types import SimpleNamespace

    st = client.app.state.deslopify
    reading = SimpleNamespace(plan=SimpleNamespace(
        account=SimpleNamespace(id="default"), passes=[], extra={}))
    st.manager.active = lambda: [reading]
    r = client.post("/api/ready/switch-claude")
    assert r.status_code == 409 and "A reading is using Claude now" in r.json()["detail"]
    assert "Change account once it has finished" in r.json()["detail"]
    r = client.post("/api/ready/revert-claude")
    assert r.status_code == 409 and "Go back to this Mac's sign-in once" in r.json()["detail"]
    assert client.ready.started == []
    # ChatGPT's account is not in use: it may change.
    job = client.post("/api/ready/switch-codex").json()
    assert job["kind"] == "switch-codex" and client.ready.started == ["switch-codex"]
    # Claude's, once the reading has ended; its sign-in page's code is taken.
    st.manager.active = lambda: []
    job = client.post("/api/ready/switch-claude").json()
    assert client.ready.started == ["switch-codex", "switch-claude"]
    assert client.post(f"/api/ready/job/{job['id']}/code",
                       json={"code": "two words"}).status_code == 400   # shaped, then sent
    assert client.post(f"/api/ready/job/{job['id']}/code",
                       json={"code": "OK-1"}).status_code == 409        # no program to send to


def test_a_switch_refused_by_getting_ready_says_why(client, monkeypatch):
    from deslopify.app import ready as ready_module

    def busy(kind):
        raise ready_module.Busy("Claude is being installed or signed in already. Wait for that "
                                "to finish.")
    monkeypatch.setattr(client.ready, "start", busy)
    r = client.post("/api/ready/switch-claude")
    assert r.status_code == 409 and "being installed or signed in" in r.json()["detail"]


@needs_pdftotext
def test_a_failed_reading_says_plainly_why(client, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "F1", "2026-10-06T10:00:00+00:00", "failed",
                 "claude-haiku-4-5", level="expert", guide=None)
    (app_cfg.runs_dir / "F1" / "events.jsonl").write_text(json.dumps(
        {"type": "error", "payload": {"message": "Not logged in · Please run /login"}}) + "\n")
    status = client.get(f"/api/papers/{slug}?level=expert&run=F1").json()
    assert "not signed in" in status["plain_failure"]


@needs_pdftotext
def test_a_reading_codex_could_not_act_in_says_why(client, app_cfg):
    # 2026-10-14: a Codex installed without its code-mode host failed every
    # command; the draft wrote nothing, the run ended "done" with the check and
    # the revision "Not reached" — and the page said no more than that.
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "C1", "2026-10-14T21:01:54+00:00", "done",
                 "gpt-5.6-luna", level="general", guide=None)
    (app_cfg.runs_dir / "C1" / "events.jsonl").write_text(json.dumps(
        {"type": "error", "payload": {"message": (
            "2026-10-14T21:02:01Z ERROR codex_core::tools::router: error=failed to spawn "
            "code-mode host /Users/x/.local/bin/codex-code-mode-host: No such file or "
            "directory (os error 2)")}}) + "\n")
    status = client.get(f"/api/papers/{slug}?level=general&run=C1").json()
    assert "codex-code-mode-host" in status["plain_failure"]
    assert "Getting ready" in status["plain_failure"]


def test_the_launch_panel_says_why_codex_cannot_read(client, monkeypatch):
    measured = client.ready.get

    def get(*, fresh=False):
        now = measured(fresh=fresh)
        for ai in now.ais:
            if ai.provider == "codex":
                ai.outdated = ready.CODE_MODE_HOST
        return now

    monkeypatch.setattr(client.ready, "get", get)
    [codex] = [g for g in client.get("/api/agents").json()["groups"]
               if g["provider"] == "codex"]
    assert codex["outdated"] is True and codex["signed_in"] is False
    assert codex["outdated_why"] == ready.codex_trouble(ready.CODE_MODE_HOST)
    assert "codex-code-mode-host" in codex["not_ready"]


def test_hello_names_this_copy(client, app_cfg):
    hello = client.get("/api/hello").json()
    assert hello["app"] == "unpacman" and Path(hello["root"]) == app_cfg.root
    assert hello["background"] is False


def test_quit_and_update_need_a_server_that_can_stop_itself(client):
    assert client.post("/api/quit", json={}).status_code == 409
    assert client.post("/api/update").status_code == 409
    status = client.get("/api/update").json()
    assert "available" in status and status["state"] == "idle"


def test_quit_asks_before_stopping_a_reading(client, monkeypatch):
    import time as _time

    from deslopify.app.server import Control

    class FakeServer:
        should_exit = False
    st = client.app.state.deslopify
    st.control = Control(server=FakeServer(), background=True, port=7720)
    monkeypatch.setattr(st.manager, "active", lambda: [object()])
    r = client.post("/api/quit", json={})
    assert r.status_code == 409 and "1 reading is running" in r.json()["detail"]
    assert client.post("/api/quit", json={"force": True}).json() == \
        {"stopping": True, "background": True}
    for _ in range(40):
        if st.control.server.should_exit:
            break
        _time.sleep(0.05)
    assert st.control.server.should_exit


@needs_pdftotext
def test_the_report_names_versions_and_states_never_a_paper(client):
    slug = upload(client, paper_pdf(title="A Very Private Conjecture")).json()["slug"]
    report = client.get("/api/report").text
    assert report.startswith("unpacman report")
    assert "getting ready" in report and "Papers: 1" in report
    assert "Private Conjecture" not in report and slug not in report


def test_feedback_says_who_it_goes_to(client):
    to = client.get("/api/feedback").json()["to"]
    assert re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]+", to)
    # unpacman's own address (2026-10-15), not a person's.
    assert to == "unpacman.support@gmail.com"


def test_the_report_can_end_with_fewer_lines_of_the_log(client, app_cfg):
    """Send feedback puts the report in an email, where space is short."""
    from deslopify.app import instance

    log = instance.log_path(app_cfg.root)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f"line {i}\n" for i in range(1, 101)))
    full = client.get("/api/report").text
    assert "line 41\n" in full and "line 40\n" not in full and full.endswith("line 100\n")
    short = client.get("/api/report?log=5").text
    assert "line 96\n" in short and "line 95\n" not in short
    assert short.splitlines()[:3] == full.splitlines()[:3]
    none = client.get("/api/report?log=0").text
    assert "Log (the last lines)" not in none and "line 100" not in none
    # Asked for more than there is room for: what there is, no more.
    assert client.get("/api/report?log=100000").text.count("\nline ") == 100


def test_the_page_files_are_checked_again_on_each_load(client):
    for asset in ("/static/app.js", "/static/ready.js", "/static/lifecycle.js",
                  "/vendor/katex/katex.min.js"):
        r = client.get(asset)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
