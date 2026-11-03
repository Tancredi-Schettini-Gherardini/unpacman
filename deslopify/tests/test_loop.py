"""Tests for the critique loop: the review, its coverage audit, and convergence.

Two properties here are load-bearing, and both exist because the obvious version
of a propose/judge/revise loop makes the artefact worse:

  * a finding must be *locatable*, and one that asserts something about the
    document must quote it — otherwise the critique cannot be checked, cannot be
    rejected on grounds, and cannot be shown to have been dealt with;
  * every finding must be answered — silence is how an inconvenient one goes
    away, and it is invisible in the finished guide.
"""

from __future__ import annotations

import pytest

from deslopify.compare import diff_graphs, diff_text, summarise
from deslopify.coverage import coverage, references
from deslopify.graph import Graph
from deslopify.review import (Dispositions, Review, validate_dispositions,
                              validate_review)

DOC = """\
Let $a$ and $b$ be even integers. By definition,
there exist integers $m$ and $n$ with $a = 2m$ and $b = 2n$.
Then $a + b = 2(m + n)$, which is even.
A direct search shows there are no other cases.
"""

GRAPH = Graph.from_dict({
    "document": "doc.tex",
    "nodes": [
        {"id": "H0", "label": "a and b are even",
         "quote": "Let $a$ and $b$ be even integers."},
        {"id": "C1", "label": "a + b is twice an integer",
         "quote": "Then $a + b = 2(m + n)$, which is even."},
        {"id": "Q1", "label": "the sum is even",
         "quote": "which is even"},
        {"id": "!1", "label": "the search is asserted, not shown", "quote": ""},
    ],
    "edges": [
        {"source": "H0", "target": "C1", "type": "SUPPORTS", "justification": "definition"},
        {"source": "C1", "target": "Q1", "type": "SUPPORTS", "justification": "by inspection"},
    ],
    "appeals": [{"line": 4, "verdict": "gap", "note": "no search is shown"}],
})

GUIDE = """\
# Companion guide

> **Notice.** This companion is explanatory, not a certification of
> correctness; readers remain responsible for checking the source.

## Main results

The sum of two even integers is even (Q1).

## Main ideas

Parity is preserved by addition (H0, C1, Q1).

## About the proofs

H0 supplies the parity input to C1, which closes at Q1.

## Q&A

Where is H0 used? In C1.

## Worked examples

Take $a = 2$, $b = 4$.

## Historical context

This elementary source is the document under discussion [Tes26].

## Where the document is thin

!1 — the final case check is asserted and never shown.

## References cited in this companion

- [Tes26] A. Test, “Even integer addition,” unpublished mathematical test fixture, 2026.
"""


def review_of(*findings, summary="looks broadly sound") -> Review:
    return Review.from_dict({"round": 1, "summary": summary,
                             "findings": list(findings)})


def finding(**kw) -> dict:
    base = {"id": "F1", "kind": "missing", "severity": "blocking",
            "where": "graph:C1", "problem": "the collar is never built",
            "fix": "construct it or record it as a gap",
            "quote": "A direct search shows there are no other cases."}
    return base | kw


def errors(problems) -> list[str]:
    return [p.message for p in problems if p.severity == "error"]


def check(*findings, **kw):
    return validate_review(review_of(*findings), graph=GRAPH, guide=GUIDE,
                           document=DOC, **kw)


# --- a finding must be locatable -------------------------------------------

@pytest.mark.parametrize("where", [
    "graph:C1", "graph:H0->C1", "guide:About the proofs",
    "guide:main ideas", "document:2", "appeal:4",
])
def test_every_form_of_location_resolves(where):
    assert not errors(check(finding(where=where, kind="unanswered", quote="")))


@pytest.mark.parametrize("where, why", [
    ("graph:C9", "not a node"),
    ("graph:H0->Q1", "no edge"),
    ("guide:Acknowledgements", "no heading matching"),
    ("document:900", "outside the document"),
    ("appeal:2", "no appeal is adjudicated"),
    ("somewhere in the middle", "must be one of"),
])
def test_a_finding_that_points_at_nothing_is_rejected(where, why):
    assert any(why in m for m in errors(check(finding(where=where))))


def test_a_critique_without_a_location_cannot_be_disposed_of():
    """The whole point: 'the exposition drags' can be neither fixed nor refused."""
    problems = check(finding(where="", problem="the exposition drags"))
    assert any("must be one of" in m for m in errors(problems))


# --- a claim about the document must quote it ------------------------------

@pytest.mark.parametrize("kind", ["unfaithful", "missing", "misjudged", "flattened"])
def test_a_claim_about_the_document_must_quote_it(kind):
    problems = check(finding(kind=kind, quote=""))
    assert any("must quote the passage" in m for m in errors(problems))


def test_a_critic_may_not_invent_the_passage_it_criticises():
    problems = check(finding(quote="The author constructs an open collar."))
    assert any("does not appear in the document" in m for m in errors(problems))


@pytest.mark.parametrize("kind", ["unanswered", "ungrounded", "unclear"])
def test_a_claim_about_the_companion_needs_no_quotation(kind):
    problem = "what is $m$ here?" if kind == "unclear" else "H0 is never located"
    assert not errors(check(finding(kind=kind, quote="", problem=problem)))


# --- the critic must not write the guide -----------------------------------

def test_a_fix_that_is_really_replacement_prose_is_rejected():
    problems = check(finding(fix=" ".join(["insert the following paragraph"] * 20)))
    assert any("State the obligation, not the replacement text" in m
               for m in errors(problems))


@pytest.mark.parametrize("fix", [
    "## The collar\n\nAdd this section.",
    "- say that the collar exists\n- then say why",
    "```\nA collar is a map c: M x [0,1) -> W\n```",
])
def test_structured_prose_in_a_fix_is_rejected(fix):
    assert any("replacement text" in m for m in errors(check(finding(fix=fix))))


def test_an_unclear_finding_must_name_what_the_reader_cannot_work_out():
    """Taste is admitted only in a form a revision can be measured against."""
    vague = check(finding(kind="unclear", quote="", problem="this section is confusing"))
    assert any("ending in a question mark" in m for m in errors(vague))
    sharp = check(finding(kind="unclear", quote="",
                          problem="which of the two maps does $T$ denote here?"))
    assert not errors(sharp)


# --- shape ------------------------------------------------------------------

def test_ids_must_be_well_formed_and_unique():
    assert any("id must be F" in m for m in errors(check(finding(id="bug-1"))))
    assert any("duplicate id" in m
               for m in errors(check(finding(), finding(where="graph:H0"))))


@pytest.mark.parametrize("field, value, why", [
    ("kind", "nitpick", "kind must be one of"),
    ("severity", "critical", "severity must be one of"),
    ("problem", "", "no statement of the problem"),
    ("fix", "", "no fix"),
])
def test_the_vocabulary_is_closed(field, value, why):
    assert any(why in m for m in errors(check(finding(**{field: value}))))


def test_a_clean_review_is_valid_and_says_so():
    empty = Review.from_dict({"round": 2, "summary": "nothing further to raise"})
    assert not errors(validate_review(empty, graph=GRAPH, guide=GUIDE, document=DOC))
    assert empty.at_least("blocking") == []


def test_severity_is_ordered_so_a_threshold_means_something():
    r = review_of(finding(id="F1", severity="blocking"),
                  finding(id="F2", severity="minor", where="graph:H0"))
    assert [f.id for f in r.at_least("blocking")] == ["F1"]
    assert [f.id for f in r.at_least("worth-fixing")] == ["F1"]
    assert [f.id for f in r.at_least("minor")] == ["F1", "F2"]


# --- dispositions -----------------------------------------------------------

def dispose(*items, review=None):
    review = review or review_of(finding(id="F1"), finding(id="F2", where="graph:H0"))
    return validate_dispositions(
        review, Dispositions.from_dict({"round": 1, "dispositions": list(items)}),
        graph=GRAPH, guide=GUIDE, document=DOC)


FIXED = {"id": "F1", "disposition": "fixed", "note": "added a gap node",
         "where": "graph:!1"}
REJECTED = {"id": "F2", "disposition": "rejected",
            "note": "the passage the critic quotes is a definition, not a claim"}


def test_a_finding_passed_over_in_silence_is_an_error():
    problems = dispose(FIXED)
    assert any("never answered" in m for m in errors(problems))


def test_rejecting_a_finding_is_free_but_must_be_argued():
    assert not errors(dispose(FIXED, REJECTED))
    assert any("it needs a reason" in m
               for m in errors(dispose(FIXED, REJECTED | {"note": "no"})))


def test_a_fix_must_say_where_it_landed():
    assert any("must say where the change is" in m
               for m in errors(dispose(FIXED | {"where": ""}, REJECTED)))
    assert any("not a node" in m
               for m in errors(dispose(FIXED | {"where": "graph:C9"}, REJECTED)))


def test_answering_a_finding_that_was_never_raised_is_an_error():
    problems = dispose(FIXED, REJECTED, {"id": "F7", "disposition": "fixed",
                                         "note": "x", "where": "graph:H0"})
    assert any("not a finding in this review" in m for m in errors(problems))


def test_a_finding_answered_twice_is_an_error():
    assert any("answered twice" in m for m in errors(dispose(FIXED, FIXED, REJECTED)))


# --- coverage ---------------------------------------------------------------

def test_a_gap_the_guide_never_mentions_is_an_error():
    stripped = GUIDE.replace("!1 — the final case check is asserted and never shown.", "")
    result = coverage(GRAPH, stripped)
    assert any(o.severity == "error" and "records a gap" in o.message
               for o in result.observations)


def test_a_guide_that_accounts_for_everything_is_quiet():
    assert not [o for o in coverage(GRAPH, GUIDE).observations if o.severity == "error"]


def test_applied_coverage_does_not_restore_pure_sections():
    applied = GUIDE.replace(
        "## Main ideas\n\nParity is preserved by addition (H0, C1, Q1).\n\n", ""
    ).replace("## Main results", "## Main findings").replace(
        "## About the proofs", "## About the method and experimental validation")
    assert not coverage(GRAPH, applied).observations
    legacy = applied.replace("## About the method and experimental validation",
                             "## About the validation, experiments, and proofs")
    assert not coverage(GRAPH, legacy).observations
    no_thin = applied.replace("## Where the document is thin", "## Limits")
    assert not coverage(GRAPH, no_thin).observations
    missing = coverage(GRAPH, applied.replace("## Q&A", "## Notation"))
    assert any("questions and answers" in o.message for o in missing.observations)


def test_a_reference_to_a_node_that_does_not_exist_is_an_error():
    result = coverage(GRAPH, GUIDE + "\nSee also C14, which closes the argument.\n")
    assert any("not a node of the graph" in o.message for o in result.observations)


def test_a_missing_section_is_noticed():
    result = coverage(GRAPH, GUIDE.replace("## Historical context", "## Notation"))
    assert any("historical context" in o.message for o in result.observations)


def test_worked_examples_section_is_optional():
    result = coverage(GRAPH, GUIDE.replace("## Worked examples", "## Notation"))
    assert not any("worked examples" in o.message for o in result.observations)


def test_maths_is_not_mistaken_for_a_reference_to_the_map():
    """`$C2$` in a formula is a curve, not node C2 — and flagging it would train
    a reader to ignore the report."""
    assert references("the constant $C2$ and the class $[M3]$ satisfy") == set()
    assert references("this is established at C2 and used by M3") == {"C2", "M3"}


def test_a_hypothesis_the_guide_never_locates_is_flagged():
    result = coverage(GRAPH, GUIDE.replace("H0", "the hypothesis"))
    assert any("standing assumption the guide never locates" in o.message
               for o in result.observations)


# --- convergence ------------------------------------------------------------

def revised(**changes) -> Graph:
    data = GRAPH.to_dict()
    data.update(changes)
    return Graph.from_dict(data)


def test_a_round_that_only_rewrites_prose_says_so():
    """Counted apart and stated plainly. Not called a failure: a finding about
    what the reader is told is answered in the guide and nowhere else."""
    gd = diff_graphs(GRAPH, GRAPH)
    td = diff_text(GUIDE, GUIDE.replace("Parity is preserved", "Parity is plainly preserved"))
    assert gd.structural == 0
    assert "the graph is unchanged" in summarise(gd, td)


def test_a_round_that_moves_the_analysis_is_counted_as_such():
    data = GRAPH.to_dict()
    data["nodes"].append({"id": "!2", "label": "the collar is never built", "quote": ""})
    gd = diff_graphs(GRAPH, Graph.from_dict(data))
    assert gd.nodes_added == ["!2"]
    assert gd.gaps_before == 1 and gd.gaps_after == 2
    assert gd.structural == 1
    assert "the graph is unchanged" not in summarise(gd, diff_text(GUIDE, GUIDE))


def test_a_changed_verdict_is_structural_not_cosmetic():
    data = GRAPH.to_dict()
    data["appeals"] = [{"line": 4, "verdict": "routine", "note": "reconsidered"}]
    gd = diff_graphs(GRAPH, Graph.from_dict(data))
    assert gd.appeals_flipped == [(4, "gap", "routine")]
    assert gd.structural == 1


def test_relabelling_a_node_is_cosmetic():
    data = GRAPH.to_dict()
    data["nodes"][0]["label"] = "both numbers are even"
    gd = diff_graphs(GRAPH, Graph.from_dict(data))
    assert gd.relabelled == ["H0"] and gd.structural == 0 and gd.cosmetic == 1


def test_a_requoted_node_is_structural_because_the_grounding_changed():
    data = GRAPH.to_dict()
    data["nodes"][0]["quote"] = "there exist integers $m$ and $n$"
    gd = diff_graphs(GRAPH, Graph.from_dict(data))
    assert gd.requoted == ["H0"] and gd.structural == 1


def test_nothing_at_all_changed_is_reported_plainly():
    assert summarise(diff_graphs(GRAPH, GRAPH), diff_text(GUIDE, GUIDE)) == "nothing changed"


# --- a fix may name what it removed -----------------------------------------

def test_a_fix_may_point_at_the_edge_it_deleted():
    """Found by running the checker over a finished run. The reviser's answer to
    'this edge is ungrounded' was to delete the edge — and the location it named
    then failed to resolve, because the thing it correctly removed was gone."""
    review = review_of(finding(id="F1", kind="ungrounded", where="graph:H0->C1",
                               quote=""))
    after = Graph.from_dict({**GRAPH.to_dict(), "edges": [
        {"source": "C1", "target": "Q1", "type": "SUPPORTS",
         "justification": "by inspection"}]})
    disposed = Dispositions.from_dict({"round": 1, "dispositions": [
        {"id": "F1", "disposition": "fixed", "where": "graph:H0->C1",
         "note": "the quoted text does not support it; edge removed"}]})

    # Judged against the state it produced alone, the honest answer is rejected.
    strict = validate_dispositions(review, disposed, graph=after, guide=GUIDE,
                                   document=DOC)
    assert any("no edge" in m for m in errors(strict))

    # Given what the critique was looking at, it stands.
    lenient = validate_dispositions(review, disposed, graph=after, guide=GUIDE,
                                    document=DOC, before=GRAPH, before_guide=GUIDE)
    assert not errors(lenient)


def test_a_location_that_never_existed_is_still_rejected():
    review = review_of(finding(id="F1"))
    disposed = Dispositions.from_dict({"round": 1, "dispositions": [
        {"id": "F1", "disposition": "fixed", "where": "graph:C9",
         "note": "handled"}]})
    problems = validate_dispositions(review, disposed, graph=GRAPH, guide=GUIDE,
                                     document=DOC, before=GRAPH, before_guide=GUIDE)
    assert any("not a node" in m for m in errors(problems))


def test_a_node_id_in_backticks_still_counts_as_a_reference():
    """Backticks are how a careful writer names an identifier in Markdown.
    Stripping code spans had the audit accuse a guide of never mentioning the
    gaps it named on every other line."""
    assert references("the gap `!1` is where the search should be") == {"!1"}
    assert references("```\nflowchart\n  C21 --> Q1\n```") == {"C21", "Q1"}
    # Maths is still stripped: a constant is not a node.
    assert references("the constant $C2$ appears throughout") == set()


def test_a_guide_that_names_its_gaps_in_backticks_is_not_accused():
    guide = GUIDE.replace("!1 — the final case check", "`!1` — the final case check")
    assert not [o for o in coverage(GRAPH, guide).observations if o.severity == "error"]


def test_a_misspelled_severity_buys_a_hearing_rather_than_ending_the_loop():
    """validate_review reports it, but on a provider that runs no hooks that
    report is recorded rather than acted on — so the decision must not quietly
    drop the finding."""
    r = review_of(finding(id="F1", severity="critical"))
    assert [f.id for f in r.at_least("worth-fixing")] == ["F1"]
    assert any("severity must be one of" in m
               for m in errors(validate_review(r, graph=GRAPH, guide=GUIDE, document=DOC)))


def test_withdrawing_a_verdict_counts_as_changing_the_analysis():
    """Left out of the diff, a round that quietly dropped an inconvenient "gap"
    would have read as having changed nothing at all."""
    data = GRAPH.to_dict()
    data["appeals"] = []
    gd = diff_graphs(GRAPH, Graph.from_dict(data))
    assert gd.appeals_removed == [4]
    assert gd.structural == 1
    assert "withdrawn" in summarise(gd, diff_text(GUIDE, GUIDE))


def test_two_findings_sharing_an_id_cannot_be_answered_once():
    review = review_of(finding(id="F1"), finding(id="F1", where="graph:H0"))
    disposed = Dispositions.from_dict({"round": 1, "dispositions": [
        {"id": "F1", "disposition": "rejected",
         "note": "the critic has misread the definition here"}]})
    problems = validate_dispositions(review, disposed, graph=GRAPH, guide=GUIDE,
                                     document=DOC)
    assert any("appears to answer all of them" in m for m in errors(problems))


# --- the cheapest way to defeat the grounding check --------------------------

def test_a_graph_naming_a_document_that_does_not_exist_is_an_error(tmp_path):
    """The `document` field is written by whoever wrote the graph. Naming a file
    that is not there had every quotation accepted unread and the check exit 0 —
    which a Stop hook reads as "grounded"."""
    import json as _json
    import subprocess
    import sys

    (tmp_path / "math.tex").write_text("Let $a$ and $b$ be even integers.\n")
    graph = tmp_path / "graph.json"
    graph.write_text(_json.dumps({
        "document": "no-such-file.tex",
        "nodes": [{"id": "H0", "label": "a hypothesis",
                   "quote": "A sentence that appears nowhere in the document."}],
        "edges": []}))

    def check(*extra):
        return subprocess.run([sys.executable, "-m", "deslopify", "check",
                               str(graph), *extra],
                              capture_output=True, text=True).returncode

    assert check() == 1                                   # refused, not warned
    assert check("--document", str(tmp_path / "math.tex")) == 1   # and the quote is false
    assert check("--allow-missing-document") == 0         # structure only, on purpose


def test_an_unreadable_override_does_not_hide_the_graph_document(tmp_path):
    """A stale override is only one candidate, not a reason to skip the
    document recorded by the graph."""
    from deslopify.__main__ import _document_text

    reading = tmp_path / "reading"
    reading.mkdir()
    graph_path = reading / "graph.json"
    document = reading / "paper.tex"
    document.write_text(DOC, encoding="utf-8")
    unsupported = tmp_path / "old-source.csv"
    unsupported.write_text("not a supported source format", encoding="utf-8")

    graph = Graph.from_dict({
        "document": "paper.tex",
        "nodes": [],
        "edges": [],
    })

    assert _document_text(graph, graph_path, str(unsupported)) == DOC


@pytest.mark.parametrize("where, resolves", [
    ("guide:Where the document is thin", True),    # exact
    ("guide:where the document is thin", True),    # case
    ("guide:## 8. Where the document is thin", True),   # numbering and markers
    ("guide:thin", True),                           # a real partial
    ("guide:a", False),                             # not a location
    ("guide:e", False),
    ("guide:of", False),
])
def test_a_partial_heading_match_must_name_something(where, resolves):
    """`guide:a` matched any heading containing the letter a — a location as
    vague as the ones the requirement exists to exclude."""
    from deslopify.review import resolve_where
    assert (resolve_where(where, guide=GUIDE) is None) is resolves
