"""Tests for the argument graph, its checks, and its queries.

The grounding check and the conjunctive-support semantics are the two things
that must not regress: the first is what stops a companion inventing evidence,
the second is what makes "which step would fail without A?" mean anything.
"""

from __future__ import annotations

import pytest

from deslopify.analysis import analyse, crux, downstream, supported, without
from deslopify.graph import Graph, quote_found, validate
from deslopify.render import to_tikz
from deslopify.summary_pdf import write_companion, write_summary_pdf
from deslopify.source import SourceError, select_source

DOC = """\
Let $a$ and $b$ be even integers. By definition,
there exist integers $m$ and $n$ with $a = 2m$ and $b = 2n$.
Then $a + b = 2(m + n)$, which is even.
"""


def build(nodes, edges, document="doc.tex") -> Graph:
    return Graph.from_dict({
        "document": document,
        "nodes": [{"id": i, "label": lab, "quote": q} for i, lab, q in nodes],
        "edges": [{"source": s, "target": t, "type": ty, "justification": "because"}
                  for s, t, ty in edges],
    })


def test_pdf_source_contains_every_node_and_edge_safely():
    g = build(
        [("H0", "use 100% of a_b", "Let $a$ and $b$ be even integers."),
         ("Q0", "finish", "Then $a + b = 2(m + n)$, which is even.")],
        [("H0", "Q0", "SUPPORTS")],
    )

    tex = to_tikz(g, analyse(g))

    assert r"\textbf{H0} use 100\% of a\_b" in tex
    assert r"\draw[->,black!65] (n0) -- (n1);" in tex
    assert tex.startswith(r"\documentclass")
    assert tex.rstrip().endswith(r"\end{document}")


def test_summary_pdf_rejects_a_missing_markdown_file(tmp_path):
    from deslopify.summary_pdf import SummaryPdfError

    with pytest.raises(SummaryPdfError, match="does not exist"):
        write_summary_pdf(tmp_path / "missing.md", tmp_path / "summary.pdf")


def test_companion_is_the_reader_facing_guide(tmp_path, monkeypatch):
    import os

    guide = tmp_path / "guide.md"
    markdown = tmp_path / "companion.md"
    tex = tmp_path / "companion.tex"
    pdf = tmp_path / "companion.pdf"
    guide.write_text(
        "# Reader's companion to ``A paper on $S^2$'' -- non-expert mode\n\n"
        "Second: $x_1^2=1$ [Abc89].\n\n"
        "**Algorithm (paper, Algorithm 1).** *Input: $x$. Output: $x^2$.*\n\n"
        "## References cited in this companion\n\n"
        "- [Abc89] M. Abcdefg, *A paper*, Journal 1 (1989), 1--20.\n",
        encoding="utf-8")
    os.utime(guide, (0, 0))

    def fake_compile(source, output):
        Path(output).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return Path(output)

    from pathlib import Path
    monkeypatch.setattr("deslopify.summary_pdf._compile_tex", fake_compile)
    assert write_companion(
        guide, markdown, tex, pdf, audience="non-expert") == (markdown, tex, pdf)
    notice = "Generated on 1970-01-01 (UTC)."
    assert markdown.read_text(encoding="utf-8") == guide.read_text(encoding="utf-8").replace(
        "\n\n", f"\n\n{notice}\n\n", 1)
    latex = tex.read_text(encoding="utf-8")
    assert rf"\date{{{notice}}}" in latex
    from atelier.hooks.check_reading import check_companion
    missing = []
    check_companion(tmp_path, missing)
    assert missing == []
    assert "Reader's companion to" in latex
    assert "A paper on $S^2$" in latex
    assert "$x_1^2=1$" in latex
    assert "non-expert mode" in latex
    assert r"\begin{thebibliography}{ABC+99}" in latex
    assert r"\bibitem[Abc89]{Abc89}" in latex
    assert r"\cite{Abc89}" in latex
    assert r"\begin{statementgreen}" in latex
    assert r"\end{statementgreen}" in latex
    assert r"\usepackage{tabularx}" in latex
    # A table of contents, with the bibliography in it.
    assert latex.index(r"\maketitle") < latex.index(r"\tableofcontents")
    assert r"\usepackage[nottoc]{tocbibind}" in latex


def test_companion_stages_source_pdf_graphics_for_restricted_latex(tmp_path, monkeypatch):
    import re

    progress = tmp_path / "progress"
    reading = progress / "reading"
    reading.mkdir(parents=True)
    source = progress / "source.pdf"
    source.write_bytes(b"%PDF-1.4 source graphic")
    guide = reading / "guide.md"
    guide.write_text(
        "# Reader's guide to A paper\n\n"
        f"$$\n\\includegraphics[page=1]{{{source}}}\n$$\n",
        encoding="utf-8")

    def fake_compile(tex, output):
        latex = tex.read_text(encoding="utf-8")
        assert str(source) not in latex
        match = re.search(r"\\includegraphics\[page=1\]\{(figures/[^}]+)\}", latex)
        assert match is not None
        assert (tex.parent / match.group(1)).read_bytes() == source.read_bytes()
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return output

    monkeypatch.setattr("deslopify.summary_pdf._compile_tex", fake_compile)
    write_companion(guide, reading / "companion.md", reading / "companion.tex",
                    reading / "companion.pdf", audience="expert")
    assert str(source) in (reading / "companion.md").read_text(encoding="utf-8")
    assert len(list((reading / "figures").glob("source-*.pdf"))) == 1


@pytest.mark.parametrize("layout", ["progress/reading", "reading"])
def test_companion_does_not_copy_graphics_outside_the_paper(tmp_path, layout):
    from deslopify.summary_pdf import SummaryPdfError, _materialize_local_graphics

    reading = tmp_path / layout
    reading.mkdir(parents=True)
    unrelated = tmp_path / "private.pdf"
    unrelated.write_bytes(b"%PDF-1.4 unrelated")
    with pytest.raises(SummaryPdfError, match="outside this paper"):
        _materialize_local_graphics(
            rf"\includegraphics{{{unrelated}}}", reading, reading)
    assert not (reading / "figures").exists()


@pytest.mark.parametrize("audience", ["expert", "non-expert"])
def test_companion_title_has_one_edition_in_markdown_and_pdf(tmp_path, audience):
    from deslopify.summary_pdf import _companion_document, dated_guide

    edition = f"{audience} edition"
    for heading in (
        f"Reader's guide to Example — {edition} — {edition}",
        f'Reader’s companion to "Example" by Ada Example — {edition} — {edition}',
    ):
        guide = tmp_path / "guide.md"
        guide.write_text(f"# {heading}\n\n## Main results\nA result.\n", encoding="utf-8")
        expected = heading.removesuffix(f" — {edition}")
        assert dated_guide(guide).startswith(f"# {expected}\n")
        title, latex = _companion_document(guide.read_text(), audience=audience)
        assert title == expected
        assert latex.count(f"{edition}") == 1


@pytest.mark.parametrize("audience", ["expert", "non-expert"])
@pytest.mark.parametrize("quotes", [("'", "'"), ("``", "''"), ('"', '"'), ("“", "”")])
@pytest.mark.parametrize("paper", ["Xyz", "Euler's theorem on $S^2$"])
def test_companion_pdf_preserves_mode_title(tmp_path, audience, quotes, paper):
    from deslopify.summary_pdf import _companion_document, dated_guide

    heading = f"Reader's companion to {quotes[0]}{paper}{quotes[1]} -- {audience} mode"
    expected = f"Reader's companion to ``{paper}'' -- {audience} mode"
    guide = tmp_path / "guide.md"
    guide.write_text(f"# {heading}\n\n## Main results\nA result.\n", encoding="utf-8")
    assert dated_guide(guide).startswith(f"# {expected}\n")
    title, latex = _companion_document(
        guide.read_text(), audience=audience)
    assert title == expected
    assert rf"\title{{{expected}}}" in latex
    assert r"\texttt" not in latex
    assert "edition" not in latex


def test_companion_drops_obsolete_empty_html_anchors(tmp_path, monkeypatch):
    """Old collection navigation markup must not leak into its PDF artefacts."""
    import os
    from pathlib import Path

    guide = tmp_path / "guide.md"
    markdown = tmp_path / "companion.md"
    tex = tmp_path / "companion.tex"
    pdf = tmp_path / "companion.pdf"
    guide.write_text(
        "# Two papers\n\n## Contents\n\n1. Paper 1\n\n"
        "<a id=\"paper-1\"></a>\n\n## Paper 1\n\nText.\n",
        encoding="utf-8")
    os.utime(guide, (0, 0))

    def fake_compile(source, output):
        Path(output).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return Path(output)

    monkeypatch.setattr("deslopify.summary_pdf._compile_tex", fake_compile)
    write_companion(guide, markdown, tex, pdf, audience="expert")
    notice = "Generated on 1970-01-01 (UTC)."
    assert notice in markdown.read_text(encoding="utf-8")
    assert rf"\date{{{notice}}}" in tex.read_text(encoding="utf-8")
    assert "<a id=" not in markdown.read_text(encoding="utf-8")
    assert "paper-1\"></a>" not in tex.read_text(encoding="utf-8")


def test_companion_keeps_a_statement_equation_inside_its_theorem_box(
        tmp_path, monkeypatch):
    """Adjacent displays stay boxed; a source note does not, even without blanks."""
    guide = tmp_path / "guide.md"
    markdown = tmp_path / "companion.md"
    tex = tmp_path / "companion.tex"
    pdf = tmp_path / "companion.pdf"
    guide.write_text(
        "# Reader's guide to A paper\n\n"
        "**Theorem (Main theorem; paper, Theorem 2).** "
        "*For every admissible $x$, the sharp estimate is:*\n"
        "$$\nQ(x)\\leq C R(x).\n\\tag{C.1}\n$$\n"
        "$$\nC>0.\n\\tag{C.2}\n$$\n"
        "**Source correspondence.** Companion equation (C.1) reproduces "
        "equation (7) of the paper.\n",
        encoding="utf-8")

    def fake_compile(source, output):
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return output

    monkeypatch.setattr("deslopify.summary_pdf._compile_tex", fake_compile)
    write_companion(guide, markdown, tex, pdf, audience="expert")
    latex = tex.read_text(encoding="utf-8")
    start = latex.index(r"\begin{statementblue}")
    equation = latex.index(r"\[")
    second = latex.index(r"\tag{C.2}")
    end = latex.index(r"\end{statementblue}")
    correspondence = latex.index(r"\textbf{Source correspondence.}")
    assert start < equation < second < end < correspondence


def test_statement_source_note_without_equation_stays_outside_box():
    from deslopify.summary_pdf import _companion_document

    _, latex = _companion_document(
        "**Definition.** A term is admissible.\n"
        "**Source correspondence.** This is Definition 1 in the paper.\n",
        audience="expert")
    assert latex.index(r"\end{statementgreen}") < latex.index(
        r"\textbf{Source correspondence.}")


def test_source_remark_keeps_identification_and_notice_in_one_box():
    from atelier.render import render_markdown
    from deslopify.summary_pdf import _companion_document

    remark = (
        "**Remark (source and scope).** The source is *An example paper* by "
        "A. Author and B. Writer, 20 October 2026. This companion is explanatory, "
        "not a certification of correctness; readers remain responsible for "
        "checking the source.\n")
    _, latex = _companion_document("# A companion\n\n" + remark, audience="expert")
    boxed = latex.split(r"\begin{statementamber}", 1)[1].split(
        r"\end{statementamber}", 1)[0]
    for content in ("An example paper", "A. Author and B. Writer", "20 October 2026",
                    "not a certification of correctness", "checking the source"):
        assert content in boxed
    html = render_markdown(remark)
    assert html.startswith("<p><strong>Remark (source and scope).</strong>")
    assert "checking the source" in html


@pytest.mark.parametrize("kind,tone", [
    ("Theorem", "blue"), ("Algorithm", "green"), ("Question", "violet"),
])
@pytest.mark.parametrize("references", [
    "[Abc00, {kind} 1]",
    "[Abc00]",
    "[Abc00, {kind} 1][Xyz00]",
    "[Abc00][Xyz00]",
    "[Abc00, {kind} 1] (informal)",
])
def test_statement_leading_references_and_formula_stay_inside_box(kind, tone, references):
    from atelier.render import render_markdown
    from deslopify.summary_pdf import _companion_document

    references = references.format(kind=kind)
    label = f"{kind} {references}."
    statement = (
        f"**{label}** *For every $x$ in the stated domain, the relation is:*\n"
        "$$\nQ(x)=R(x).\n$$\n\n"
        "This explanation belongs outside the box.\n")
    guide = ("# A paper\n\n" + statement
             + "\n## References cited in this companion\n\n"
               "- [Abc00] A source, 2000.\n"
               "- [Xyz00] Another source, 2000.\n")

    _, latex = _companion_document(guide, audience="expert")

    assert latex.count(rf"\begin{{statement{tone}}}") == 1
    boxed = latex.split(rf"\begin{{statement{tone}}}", 1)[1].split(
        rf"\end{{statement{tone}}}", 1)[0]
    assert rf"\textbf{{{kind} " in boxed
    assert "For every" in boxed and "Q(x)=R(x)" in boxed
    assert "This explanation" not in boxed
    assert "Abc00" in boxed
    if "Xyz00" in references:
        assert r"\cite{Xyz00}" in boxed
        assert "Abc00,Xyz00" not in boxed
    # The web renderer retains the whole label in the leading strong element,
    # which the statement-box decorator recognises.
    assert render_markdown(statement).startswith(f"<p><strong>{label}</strong>")


def test_companion_headings_are_numbered_at_their_academic_levels():
    """Promote the body, preserve math, and avoid doubling manual numbers."""
    from deslopify.summary_pdf import with_contents

    document = "\n".join([
        r"\documentclass{article}", r"\begin{document}", r"\maketitle",
        r"\subsection*{1. Main results}", "text",
        r"\subsubsection*{1.1 A detail of $S^7$}", "more",
        r"\paragraph*{1.1.1 A proof detail}", "x",
        r"\subsection*{Historical context}",
        r"\subsection*{References cited in this companion}", r"\end{document}", ""])
    out = with_contents(document)
    assert out.count(r"\tableofcontents") == 1
    assert r"\section{Main results}" in out
    assert r"\subsection{A detail of $S^7$}" in out
    assert r"\subsubsection{A proof detail}" in out
    assert r"\section{Historical context}" in out
    assert r"\section*{References cited in this companion}" in out
    assert out.count(r"\addcontentsline") == 1  # only the unnumbered bibliography
    assert r"\setcounter{secnumdepth}{3}" in out
    assert r"\normalfont\LARGE\bfseries" in out
    # The packages go in the preamble, before the document begins.
    assert out.index("tocbibind") < out.index(r"\begin{document}")
    # Given twice, nothing is added twice.
    assert with_contents(out) == out


@pytest.mark.parametrize("audience", ["expert", "non-expert"])
@pytest.mark.parametrize("connected", [False, True])
def test_collection_paper_headings_start_new_pdf_pages(audience, connected):
    from deslopify.summary_pdf import _companion_document, with_contents

    connections = (
        "## How the papers are related\n\n### A shared construction\n\n"
        "Papers 1 and 3 develop the same construction in different settings.\n\n"
        if connected else "")
    titles = ["Paper 1: ``A construction on $S^2$''", "Paper 2: ``Another topic''",
              "Paper 3: ``A related construction''"]
    guide = "# Three papers\n\n" + connections + "\n\n".join(
        f"## {title}\n\n### Main results\n\nA result.\n\n"
        "### Historical context\n\nA predecessor.\n\n"
        "### Bibliography\n\n- [Abc26] A source."
        for title in titles)

    _, latex = _companion_document(guide, audience=audience)

    assert latex.count(r"\clearpage") == len(titles)
    for title in titles:
        assert f"\\clearpage\n\\section{{{title}}}" in latex
    assert latex.count(r"\tableofcontents") == 1
    assert r"\section{Contents}" not in latex
    if connected:
        assert latex.index(r"\tableofcontents") < latex.index(r"\section{How the papers are related}")
        assert latex.index(r"\section{How the papers are related}") < latex.index(r"\clearpage")
    assert with_contents(latex) == latex


def test_solo_companion_sections_do_not_get_collection_page_breaks():
    from deslopify.summary_pdf import _companion_document

    _, latex = _companion_document(
        "# One paper\n\n## Main results\n\nA result.\n\n"
        "### Paper 1: A comparison\n\nA related paper.\n\n"
        "## Historical context\n\nIts predecessors.\n",
        audience="expert")

    assert r"\clearpage" not in latex


def test_old_contents_upgrade_without_duplicate_entries():
    from deslopify.summary_pdf import with_contents
    old = "\n".join([
        r"\documentclass{article}", r"\usepackage[nottoc]{tocbibind}",
        r"\begin{document}", r"\tableofcontents", r"\phantomsection",
        r"\subsection*{Main results}", r"\addcontentsline{toc}{section}{Main results}",
        r"\subsubsection*{The first idea}", r"\end{document}"])
    out = with_contents(old)
    assert out.count(r"\tableofcontents") == 1
    assert out.count("{tocbibind}") == 1
    assert r"\section{Main results}" in out
    assert r"\subsection{The first idea}" in out
    assert r"\addcontentsline" not in out
    assert with_contents(out) == out


def test_a_document_without_a_body_is_left_alone():
    from deslopify.summary_pdf import with_contents

    assert with_contents("not a LaTeX document") == "not a LaTeX document"


def test_claim_table_header_is_not_mistaken_for_a_statement(tmp_path, monkeypatch):
    guide = tmp_path / "guide.md"
    markdown = tmp_path / "companion.md"
    tex = tmp_path / "companion.tex"
    pdf = tmp_path / "companion.pdf"
    guide.write_text(
        "# Reader's guide to A paper\n\n"
        "**Claim.** *The standalone claim is boxed.*\n\n"
        "| **Claim** | **Classification** | **Evidence and scope** |\n"
        "|---|---|---|\n"
        "| A numerical observation | computed | one experiment |\n",
        encoding="utf-8")

    def fake_compile(source, output):
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return output

    monkeypatch.setattr("deslopify.summary_pdf._compile_tex", fake_compile)
    write_companion(guide, markdown, tex, pdf, audience="expert")
    latex = tex.read_text(encoding="utf-8")
    assert latex.count(r"\begin{statementblue}") == 1
    assert r"\textbf{\textbf{Claim}} & \textbf{\textbf{Classification}}" in latex
    assert r"\begin{tabularx}{\linewidth}" in latex


def test_companion_retries_without_statement_boxes(tmp_path, monkeypatch):
    from deslopify.summary_pdf import SummaryPdfError

    guide = tmp_path / "guide.md"
    markdown = tmp_path / "companion.md"
    tex = tmp_path / "companion.tex"
    pdf = tmp_path / "companion.pdf"
    guide.write_text(
        "# Reader's guide to A paper\n\n"
        "**Theorem.** *The content survives the fallback.*\n",
        encoding="utf-8")
    attempts = []

    def fail_styled_then_compile(source, output):
        latex = source.read_text(encoding="utf-8")
        attempts.append(latex)
        if len(attempts) == 1:
            raise SummaryPdfError("the optional box failed")
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return output

    monkeypatch.setattr(
        "deslopify.summary_pdf._compile_tex", fail_styled_then_compile)
    with pytest.warns(RuntimeWarning, match="retrying without optional statement boxes"):
        write_companion(guide, markdown, tex, pdf, audience="expert")

    assert len(attempts) == 2
    assert r"\begin{statementblue}" in attempts[0]
    assert r"\begin{statementblue}" not in attempts[1]
    assert "The content survives the fallback" in attempts[1]
    assert "Statement boxes disabled" in tex.read_text(encoding="utf-8")
    assert pdf.read_bytes().startswith(b"%PDF-")


def test_companion_defines_sha_in_both_export_styles():
    from deslopify.summary_pdf import _companion_document
    for boxes in (True, False):
        _, tex = _companion_document(
            r"The Tate--Shafarevich group is $\Sha(E)$.",
            audience="expert", box_statements=boxes)
        assert r'\DeclareMathSymbol{\Sha}{\mathord}{companioncyrillic}{"58}' in tex
        assert tex.index(r"\DeclareMathSymbol{\Sha}") < tex.index(r"\begin{document}")
        assert r"$\Sha(E)$" in tex


def test_unknown_command_error_keeps_the_command_and_repair_instructions(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from deslopify.summary_pdf import _compile_tex, SummaryPdfError
    monkeypatch.setattr("deslopify.summary_pdf._latex_engine", lambda: "latexmk")
    monkeypatch.setattr("atelier.access.sandbox_command", lambda argv, **kw: argv)
    log = "! Undefined control sequence.\nl.55 $\\UnknownScientificMacro(x)$\n" + "summary\n" * 40
    monkeypatch.setattr("deslopify.summary_pdf.subprocess.run",
                        lambda *a, **kw: SimpleNamespace(returncode=1, stdout=log, stderr=""))
    tex = tmp_path / "companion.tex"
    tex.write_text(r"\documentclass{article}\begin{document}$\UnknownScientificMacro(x)$\end{document}")
    with pytest.raises(SummaryPdfError) as error:
        _compile_tex(tex, tmp_path / "companion.pdf")
    assert r"\UnknownScientificMacro" in str(error.value)
    assert "guide.md" in str(error.value)
    assert "Do not invent a definition" in str(error.value)
    assert not (tmp_path / "companion.pdf").exists()


def test_source_discovery_prefers_named_markdown_or_pdf(tmp_path):
    progress = tmp_path / "progress"
    progress.mkdir()
    legacy = progress / "math.tex"
    legacy.write_text("legacy", encoding="utf-8")
    assert select_source(tmp_path) == legacy

    markdown = progress / "source.md"
    markdown.write_text("paper", encoding="utf-8")
    assert select_source(tmp_path) == markdown

    pdf = progress / "source.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(SourceError, match="both progress/source"):
        select_source(tmp_path)


# --- grounding --------------------------------------------------------------

@pytest.mark.parametrize("quote, expected", [
    ("Let $a$ and $b$ be even integers.", True),
    ("By definition, there exist integers $m$", True),     # across a line break
    ("be   even    integers", True),                        # collapsed whitespace
    ("Let $a$ ... $a = 2m$", True),                         # ellipsis, in order
    ("$a = 2m$ ... Let $a$", False),                        # ellipsis, out of order
    ("there exist rationals $m$", False),                   # a word changed
    ("$a + b = 2(m - n)$", False),                          # a symbol changed
])
def test_quotation_matching(quote, expected):
    assert quote_found(quote, DOC) is expected


def test_a_tidied_quotation_is_a_fabricated_one():
    """Truncating mid-expression and closing the maths is invention, and was
    committed by hand the first time this was exercised."""
    assert quote_found("$a + b = 2(m + n)$", DOC)
    assert not quote_found("$a + b = 2(m$", DOC)


def test_validate_rejects_ungrounded_nodes():
    g = build([("C0", "a claim", "a sentence that is not in the document at all")], [])
    errors = [p for p in validate(g, DOC) if p.severity == "error"]
    assert any("does not appear in the document" in p.message for p in errors)


def test_validate_rejects_a_quotation_too_short_to_locate():
    g = build([("C0", "a claim", "Then")], [])
    assert any("too short" in p.message for p in validate(g, DOC))


def test_validate_rejects_a_node_with_no_quotation():
    g = build([("C0", "a claim", "")], [])
    assert any("no quotation" in p.message for p in validate(g, DOC))


def test_a_gap_needs_no_quotation_but_may_have_no_edges():
    g = Graph.from_dict({"document": "d", "nodes": [
        {"id": "!0", "label": "an unshown computation"},
        {"id": "C0", "label": "a claim", "quote": "Then $a + b = 2(m + n)$"}],
        "edges": [{"source": "!0", "target": "C0", "type": "SUPPORTS",
                   "justification": "x"}]})
    problems = validate(g, DOC)
    assert not any("no quotation" in p.message for p in problems)
    assert any("annotates a path" in p.message for p in problems)


# --- structure --------------------------------------------------------------

def test_validate_rejects_a_cycle():
    g = build([("C0", "one", "Let $a$ and $b$ be even integers."),
               ("C1", "two", "Then $a + b = 2(m + n)$")],
              [("C0", "C1", "SUPPORTS"), ("C1", "C0", "SUPPORTS")])
    assert any("acyclic" in p.message for p in validate(g, DOC))


def test_validate_rejects_unknown_ids_and_edge_types():
    g = build([("Z9", "wrong prefix", "Let $a$ and $b$ be even integers.")], [])
    assert any("type letter" in p.message for p in validate(g, DOC))
    g2 = build([("C0", "one", "Let $a$ and $b$ be even integers.")],
               [("C0", "C7", "ENABLES")])
    messages = " ".join(p.message for p in validate(g2, DOC))
    assert "C7 is not a node" in messages and "type must be one of" in messages


# --- the semantics that makes the questions answerable ----------------------

def test_support_is_conjunctive_not_reachability():
    """A proof step needs every premise it invokes. Under reachability, removing
    one of two supports leaves the target 'still reachable' and the question
    'which step fails without A?' silently answers nothing."""
    g = build([("H0", "premise one", "Let $a$ and $b$ be even integers."),
               ("H1", "premise two", "there exist integers $m$ and $n$"),
               ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even.")],
              [("H0", "Q0", "SUPPORTS"), ("H1", "Q0", "SUPPORTS")])
    assert supported(g) == {"H0", "H1", "Q0"}
    assert without(g, "H0") == {"Q0"}      # not empty: Q0 needed both
    assert without(g, "H1") == {"Q0"}


def test_removing_an_unused_hypothesis_costs_nothing():
    g = build([("H0", "used", "Let $a$ and $b$ be even integers."),
               ("H1", "never used", "there exist integers $m$ and $n$"),
               ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even.")],
              [("H0", "Q0", "SUPPORTS")])
    assert without(g, "H1") == set()


def test_downstream_answers_where_a_hypothesis_is_used():
    g = build([("H0", "the definition", "Let $a$ and $b$ be even integers."),
               ("C0", "a step", "there exist integers $m$ and $n$"),
               ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even.")],
              [("H0", "C0", "SUPPORTS"), ("C0", "Q0", "SUPPORTS")])
    assert downstream(g, "H0") == {"C0", "Q0"}


def test_crux_is_the_node_every_route_passes_through():
    g = build([("H0", "start", "Let $a$ and $b$ be even integers."),
               ("C0", "the only bridge", "there exist integers $m$ and $n$"),
               ("Q0", "the end", "Then $a + b = 2(m + n)$, which is even.")],
              [("H0", "C0", "SUPPORTS"), ("C0", "Q0", "SUPPORTS")])
    assert crux(g) == ["C0"]


# --- findings ---------------------------------------------------------------

def test_an_unsupported_claim_is_reported():
    g = build([("C0", "asserted from nothing", "Then $a + b = 2(m + n)$")], [])
    assert any(f.kind == "unsupported" for f in analyse(g).findings)


def test_material_that_reaches_no_goal_is_adrift():
    """Scope creep has a structural signature: it goes nowhere."""
    g = build([("H0", "used", "Let $a$ and $b$ be even integers."),
               ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even."),
               ("X0", "an aside", "there exist integers $m$ and $n$")],
              [("H0", "Q0", "SUPPORTS")])
    adrift = {f.node_ids[0] for f in analyse(g).findings if f.kind == "adrift"}
    assert adrift == {"X0"}


def test_a_goal_is_a_destination_and_never_adrift():
    g = build([("G0", "what is to be proved", "Let $a$ and $b$ be even integers."),
               ("H0", "a premise", "there exist integers $m$ and $n$"),
               ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even.")],
              [("H0", "Q0", "SUPPORTS")])
    kinds = {(f.kind, f.node_ids[0]) for f in analyse(g).findings}
    assert ("adrift", "G0") not in kinds


def test_centrality_is_computed_for_the_agent():
    """The one structural signal no serialization made legible to a model."""
    g = build([("H0", "start", "Let $a$ and $b$ be even integers."),
               ("Q0", "the end", "Then $a + b = 2(m + n)$, which is even.")],
              [("H0", "Q0", "SUPPORTS")])
    cent = analyse(g).centrality
    assert cent["Q0"] == 1.0 and cent["H0"] < cent["Q0"]


# --- reporting --------------------------------------------------------------

def test_focus_measures_how_much_of_a_document_serves_its_goal():
    """A document that accumulated material session after session is mostly not
    about its own stated problem, and that is the single most useful number to
    put in front of a reader."""
    nodes = [("G0", "the goal", "Let $a$ and $b$ be even integers."),
             ("H0", "a premise", "there exist integers $m$ and $n$"),
             ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even.")]
    nodes += [(f"C{i}", f"an aside {i}", "$a = 2m$ and $b = 2n$") for i in range(7)]
    g = build(nodes, [("H0", "Q0", "SUPPORTS")])
    result = analyse(g)
    assert result.total == 10
    assert result.on_topic == 3            # G0, H0, Q0
    assert 0.29 < result.focus < 0.31


def test_a_finding_that_fires_many_times_is_reported_once():
    from deslopify.render import to_compact_text

    nodes = [("G0", "the goal", "Let $a$ and $b$ be even integers."),
             ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even.")]
    nodes += [(f"C{i}", f"an aside {i}", "$a = 2m$ and $b = 2n$") for i in range(8)]
    g = build(nodes, [])
    text = to_compact_text(g, analyse(g))
    assert text.count("leads nowhere") == 0          # not once per node
    assert "8 nodes:" in text
    assert "not part of the argument" in text


def test_a_finding_that_fires_rarely_is_still_reported_in_full():
    g = build([("G0", "the goal", "Let $a$ and $b$ be even integers."),
               ("Q0", "the theorem", "Then $a + b = 2(m + n)$, which is even."),
               ("C0", "one aside", "$a = 2m$ and $b = 2n$")], [])
    from deslopify.render import to_compact_text
    assert "leads nowhere" in to_compact_text(g, analyse(g))


# --- where work was skipped -------------------------------------------------

def test_appeals_are_found_where_a_phrase_replaces_an_argument():
    from deslopify.lint import find_appeals

    doc = (
        "We verify by exhaustive computation.\n"
        "A direct search of all $n$ from 1 to 100 yields no other solutions.\n"
        "It is easy to see that the remainder vanishes.\n"
        "The rest follows similarly.\n"
        "Let $a$ be an even integer.\n"          # an ordinary line
        "% clearly this comment is not the document\n"
    )
    found = find_appeals(doc)
    assert [a.line for a in found] == [1, 2, 3, 4]
    assert "asserted" in found[0].why


def test_a_gapless_graph_over_a_hedging_document_is_questioned():
    """Observed live: a session mapped a document whose central claim rested on
    an unshown computation, recorded no gap, and then explained in the guide why
    such a computation would have worked."""
    from deslopify.lint import find_appeals, gap_warning

    appeals = find_appeals("A direct search of all $n$ yields no other solutions.")
    assert gap_warning(appeals, gap_nodes=0) is not None
    assert gap_warning(appeals, gap_nodes=1) is None      # it was considered
    assert gap_warning([], gap_nodes=0) is None           # nothing to consider


def test_every_appeal_must_be_adjudicated_but_the_verdict_is_free():
    """Twice on live runs a session read a dozen appeals, recorded none, and
    reported the document sound. The checker therefore requires that each was
    looked at — and accepts 'routine' as readily as 'gap'."""
    doc = ("We verify by exhaustive computation.\n"
           "Let $a$ and $b$ be even integers. By definition,\n")
    g = build([("C0", "a claim", "Let $a$ and $b$ be even integers.")], [])

    errors = [p for p in validate(g, doc) if p.severity == "error"]
    assert any("have not been looked at" in p.message for p in errors)

    judged = Graph.from_dict({
        "document": "d",
        "nodes": [{"id": "C0", "label": "a claim",
                   "quote": "Let $a$ and $b$ be even integers."}],
        "edges": [],
        "appeals": [{"line": 1, "verdict": "routine", "note": "shown in full below"}],
    })
    assert not [p for p in validate(judged, doc)
                if p.severity == "error" and "looked at" in p.message]


def test_an_adjudication_needs_a_real_verdict():
    doc = "We verify by exhaustive computation.\n"
    g = Graph.from_dict({
        "document": "d",
        "nodes": [{"id": "C0", "label": "c", "quote": "We verify by exhaustive computation."}],
        "edges": [], "appeals": [{"line": 1, "verdict": "probably fine"}]})
    assert any("must be 'routine' or 'gap'" in p.message for p in validate(g, doc))


def test_trivial_as_a_technical_term_is_not_an_appeal():
    """'the extra trivial line' is a trivial line bundle, not a hedge. The first
    version flagged it, which is the kind of noise that teaches a reader to skip
    the report."""
    from deslopify.lint import find_appeals

    assert find_appeals("Adjoin a scalar $s$, representing the extra trivial line.") == []
    assert find_appeals("This reduces to the trivial group.") == []
    assert len(find_appeals("The remaining case is trivial.")) == 1
    assert len(find_appeals("The identity follows trivially.")) == 1


def test_a_citation_with_no_statement_is_an_appeal():
    from deslopify.lint import find_appeals

    found = find_appeals("Milnor's theorem shows this is exotic. "
                         "(\\href{https://example.org/x.pdf}{example.org})")
    assert len(found) == 1 and "cited by link" in found[0].why
