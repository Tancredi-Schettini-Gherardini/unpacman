"""Source remarks precede the first section of each paper, on every surface."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from deslopify.companion_layout import place_source_remarks, place_source_remarks_tex
from deslopify.summary_pdf import _companion_document, dated_guide, generation_date, with_contents
from test_app import app_cfg, client, isolated_run, needs_pdftotext, paper_pdf, upload  # noqa: F401
from test_kinds import run_for


def remark(title="Source A"):
    return (f"**Remark (source and scope).** The source is *{title}* by A. Author, 2026. "
            "This companion is explanatory, not a certification of correctness; "
            "readers remain responsible for checking the source.")


@pytest.mark.parametrize("audience", ["expert", "non-expert"])
@pytest.mark.parametrize("main", ["Main results", "Main findings"])
@pytest.mark.parametrize("lead", ["", "A short opening lead.\n\n"])
def test_solo_remark_follows_the_title_and_lead_before_the_first_section(audience, main, lead):
    original = f"# A companion\n\n{lead}## {main}\n\nThe main mathematics.\n\n{remark()}\n\n## Historical context\n\nHistory.\n"
    moved = place_source_remarks(original)
    assert moved.index("# A companion") < moved.index(remark()) < moved.index(f"## {main}")
    assert moved[moved.index(remark()) + len(remark()):moved.index(f"## {main}")].isspace()
    if lead:
        assert moved.index(lead.strip()) < moved.index(remark())
    assert moved.count(remark()) == 1
    assert "The main mathematics." in moved and "History." in moved
    assert place_source_remarks(moved) == moved
    _, tex = _companion_document(original, audience=audience)
    assert tex.index(r"\maketitle") < tex.index(r"\textbf{Remark") < tex.index(r"\section{" + main + "}")
    assert tex.count(r"\begin{statementamber}") == 1
    assert with_contents(tex) == tex


@pytest.mark.parametrize("audience", ["expert", "non-expert"])
@pytest.mark.parametrize("overview", [False, True])
def test_mixed_collection_remark_stays_with_its_paper_before_its_first_subsection(audience, overview):
    original = "# A mixed collection\n\n"
    if overview:
        original += "## How the papers are related\n\n### A common idea\n\nConnections.\n\n"
    for number, main in enumerate(("Main results", "Main findings"), 1):
        original += (f"## Paper {number}: Title {number}\n\n### {main}\n\nMathematics {number}.\n\n"
                     f"{remark(f'Source {number}')}\n\n### Historical context\n\nHistory {number}.\n\n"
                     "### Bibliography\n\nA reference.\n\n")
    moved = place_source_remarks(original)
    _, tex = _companion_document(original, audience=audience)
    for number, main in enumerate(("Main results", "Main findings"), 1):
        begin = moved.index(f"## Paper {number}:")
        end = moved.find(f"## Paper {number + 1}:", begin)
        part = moved[begin:end if end >= 0 else None]
        assert part.index(remark(f'Source {number}')) < part.index(f"### {main}")
        assert part.count("Remark (source and scope)") == 1
        assert f"Mathematics {number}." in part
        begin_tex = tex.index(r"\section{Paper " + str(number))
        assert begin_tex < tex.index(f"Source {number}", begin_tex) < tex.index(r"\subsection{" + main + "}", begin_tex)
        assert tex[begin_tex - 11:begin_tex].endswith("\\clearpage\n")
    assert place_source_remarks(moved) == moved
    assert place_source_remarks_tex(tex) == tex


def test_code_examples_and_quotations_do_not_become_source_remarks():
    examples = f"```markdown\n{remark('Code')}\n```\n\n> {remark('Quotation')}\n\n"
    original = "# Companion\n\n## Main results\n\n" + examples + remark() + "\n\n## Historical context\n"
    moved = place_source_remarks(original)
    assert moved.index(remark()) < moved.index("## Main results") < moved.index(examples)
    assert examples in moved and moved.count("Remark (source and scope)") == 3


@pytest.mark.parametrize("prefix", ["1 ", "1. ", "1) ", "1.2 "])
def test_older_numbered_headings_keep_the_remark_before_the_heading(prefix):
    original = f"# Companion\n\n## {prefix}Main results\n\nMathematics.\n\n{remark()}\n"
    moved = place_source_remarks(original)
    assert moved.index(remark()) < moved.index(f"## {prefix}Main results")
    _, tex = _companion_document(original, audience="expert")
    assert tex.index(r"\textbf{Remark") < tex.index(r"\section{Main results}")


@pytest.mark.parametrize("boxed", [False, True])
@pytest.mark.parametrize("starred", [False, True])
def test_stored_latex_is_repositioned_even_with_current_contents_styles(boxed, starred):
    label = r"\textbf{Remark (source and scope).} The source is Source A, 2026. Check the source."
    block = "\\begin{statementamber}\n" + label + "\n\\end{statementamber}" if boxed else label
    section = r"\section*{Main results}" if starred else r"\section{Main results}"
    # First give it current heading styles, then simulate a legacy late remark.
    current = with_contents("\\documentclass{article}\n\\begin{document}\n\\maketitle\n"
                            + section + "\n\nMathematics.\n\n\\end{document}\n")
    late = current.replace("Mathematics.", "Mathematics.\n\n" + block)
    moved = with_contents(late)
    assert moved.index(label) < moved.index(r"\section{Main results}")
    assert moved.count(label) == 1 and moved.count(r"\tableofcontents") == 1
    assert with_contents(moved) == moved


def test_dated_downloads_and_web_sections_use_the_placement_without_rewriting_the_run(tmp_path):
    from deslopify.app import library

    original = "# Companion\n\n## Main results\n\nMathematics.\n\n" + remark() + "\n\n## Historical context\nHistory.\n"
    guide = tmp_path / "guide.md"
    guide.write_text(original)
    stamp = guide.stat().st_mtime_ns
    shown = dated_guide(guide)
    preamble, sections = library.split_guide(shown)
    assert remark() in preamble
    assert all("Remark (source and scope)" not in s.markdown for s in sections)
    assert guide.read_text() == original and guide.stat().st_mtime_ns == stamp


@needs_pdftotext
def test_current_solo_pdf_with_a_late_remark_is_rebuilt_once_without_changing_its_source(client, app_cfg, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    where = isolated_run(client.app.state.deslopify, app_cfg, slug, "remark-solo",
                         "2026-09-30T10:00:00+00:00", "done", "claude-opus-5", level="expert")
    _, current = _companion_document("# Companion\n\n" + remark() + "\n\n## Main results\n\nMathematics.\n", audience="expert")
    block = re.search(r"\\begin\{statementamber\}[\s\S]*?\\end\{statementamber\}", current).group()
    late = current.replace(block, "").replace("Mathematics.", "Mathematics.\n\n" + block)
    late = late.replace(r"\date{}", r"\date{Generated on 2026-09-30 (UTC).}")
    (where / "companion.tex").write_text(late)
    (where / "companion.pdf").write_bytes(b"%PDF-1.4 legacy\n%%EOF\n")
    calls = []
    def rebuild(tex, output):
        calls.append(tex)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"%PDF-1.4 moved remark\n%%EOF\n")
        return Path(output)
    monkeypatch.setattr("deslopify.summary_pdf.rebuild_with_contents", rebuild)
    for _ in range(2):
        response = client.get(f"/api/papers/{slug}/file/companion.pdf?run=remark-solo")
        assert response.status_code == 200 and b"moved remark" in response.content
    assert len(calls) == 1 and (where / "companion.tex").read_text() == late
    assert place_source_remarks_tex(late).index(r"\textbf{Remark") < place_source_remarks_tex(late).index(r"\section{Main results}")


@needs_pdftotext
@pytest.mark.parametrize("level", ["collection-expert", "collection-nonexpert"])
def test_cached_collection_pdf_and_web_move_each_remark_once(client, app_cfg, monkeypatch, level):
    a = upload(client, paper_pdf("First source")).json()["slug"]
    b = upload(client, paper_pdf("Second source", words=420)).json()["slug"]
    slug = client.post("/api/collections", json={"papers": [a, b]}).json()["slug"]
    guide = "# Collection\n\n"
    for number, main in enumerate(("Main results", "Main findings"), 1):
        guide += f"## Paper {number}: Title {number}\n\n### {main}\n\nMathematics.\n\n{remark(str(number))}\n\n"
    reading = run_for(client.app.state.deslopify, app_cfg, slug, "remark-collection", level,
                      "deslopify-multi-expert" if level == "collection-expert" else "deslopify-multi-nonexpert", guide=guide)
    (reading / "companion.pdf").write_bytes(b"%PDF-1.4 legacy\n%%EOF\n")
    calls = []
    def write(guide, markdown, tex, pdf, *, audience, paper_title=None):
        calls.append(audience)
        Path(markdown).write_text(dated_guide(Path(guide)))
        _, latex = _companion_document(Path(guide).read_text(), audience=audience)
        latex = with_contents(latex, generated_on=generation_date(Path(guide)))
        Path(tex).write_text(latex)
        Path(pdf).write_bytes(b"%PDF-1.4 moved collection\n%%EOF\n")
    monkeypatch.setattr("deslopify.summary_pdf.write_companion", write)
    for _ in range(2):
        doc = client.get(f"/api/papers/{slug}/companion?level={level}").json()
        for section in doc["sections"]:
            assert section["html"].index("Remark (source and scope)") < section["html"].index("<h3>")
        response = client.get(f"/api/papers/{slug}/file/companion.pdf?level={level}")
        assert response.status_code == 200 and b"moved collection" in response.content
    assert len(calls) == 1 and (reading / "guide.md").read_text() == guide
