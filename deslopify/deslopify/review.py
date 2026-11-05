"""The critique a second reader writes, and the checks that keep it honest.

A companion is improved by being read by someone who did not write it. That is
the whole of the idea. The difficulty is that the obvious way to run such a loop
makes the artefact worse, and does so invisibly.

Ask a model to judge a piece of writing for "clarity, quality and smoothness"
and it will reward fluency, because fluency is what those words name. Fluency is
the one thing machine-written mathematics never lacks. A round of revision
against that rubric returns a companion that is better organised, more
confident, and no more faithful — which is precisely the artefact this package
exists to prevent, now with a second signature on it.

So the critique is pointed somewhere else. A finding here is a claim about a
*discrepancy* — between the companion and the document, or between the companion
and its own graph — and every such claim can be located and, for the kinds that
assert something about the document, quoted. What cannot be located cannot be
checked, cannot be disposed of, and is not admitted:

  * `where` must resolve against the graph, the guide or the document;
  * a finding about the document must carry a verbatim quotation from it;
  * `fix` states the obligation, never the replacement text.

The last of those is the one that stops the loop collapsing. If the critic may
supply prose, the reviser will paste it, and what comes out is the critic's
guide laundered through a second pass — with the critic's own errors now
carrying the authority of revision. The critic says what must be established.
Someone who has read the document decides how to say it.

Dispositions close the loop. Every finding must be answered `fixed` or
`rejected`, and rejection is free: a critic can be wrong, and often is. What is
not free is silence, which is how an inconvenient finding disappears.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .graph import Graph, GraphError, Problem, normalise, quote_found

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------

# Each kind names a discrepancy that can be pointed at. There is deliberately
# no kind for "could be better written": a finding that cannot be settled by
# looking at the document is a matter of taste, and taste is what makes this
# loop polish slop instead of exposing it.
FINDING_KINDS: dict[str, str] = {
    "unfaithful": "the companion asserts something the document does not",
    "ungrounded": "a node or edge whose justification cannot be read off its quotation",
    "missing":    "something the document does that the companion never accounts for",
    "misjudged":  "an appeal to skipped work adjudicated the wrong way",
    "unanswered": "a question this document forces on a reader that the guide never answers",
    "flattened":  "a step that is genuinely deep, presented as routine",
    "unclear":    "a passage of the companion a competent reader cannot follow",
}

# Kinds that assert something about the *document*. These carry a quotation, on
# exactly the grounds that graph nodes do: a claim about a text that cannot be
# traced to the text is the failure mode this package was built against, and a
# critic is no more exempt from it than a drafter.
NEEDS_QUOTE = ("unfaithful", "missing", "misjudged", "flattened")

SEVERITIES = ("blocking", "worth-fixing", "minor")
DISPOSITIONS = ("fixed", "rejected")

_FINDING_ID = re.compile(r"^F\d+$")
_NODE_ID = re.compile(r"^([HGXMCQ!])(\d+)$")

# where: graph:C12 | graph:C12->Q1 | guide:<heading> | document:104 | appeal:104
_WHERE = re.compile(
    r"^(?:graph:(?P<node>[HGXMCQ!]\d+)(?:->(?P<target>[HGXMCQ!]\d+))?"
    r"|guide:(?P<heading>.+)"
    r"|document:(?P<line>\d+)"
    r"|appeal:(?P<appeal>\d+))$"
)

# A `fix` is an obligation in a sentence or two. These caps are not style
# policing: they are the mechanical half of "do not write the guide for them".
MAX_FIX_WORDS = 60
MAX_PROBLEM_WORDS = 120
MIN_REJECTION_WORDS = 5

_PROSE_MARKERS = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+\.\s|```)", re.M)


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Finding:
    id: str
    kind: str
    severity: str
    where: str
    problem: str
    fix: str
    quote: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(slots=True)
class Review:
    round: int
    document: str = ""
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)

    def by_id(self) -> dict[str, Finding]:
        return {f.id: f for f in self.findings}

    def at_least(self, severity: str) -> list[Finding]:
        """Findings at `severity` or worse. Severities are ordered, worst first.

        A severity outside the vocabulary counts as the worst, so a misspelling
        still buys the finding a hearing rather than quietly ending the loop.
        """
        cut = SEVERITIES.index(severity)
        return [f for f in self.findings
                if f.severity not in SEVERITIES or SEVERITIES.index(f.severity) <= cut]

    @classmethod
    def from_dict(cls, data: dict) -> Review:
        if not isinstance(data, dict):
            raise GraphError("the review file must contain a JSON object")
        findings = []
        for raw in data.get("findings", []):
            if not isinstance(raw, dict):
                raise GraphError(f"a finding must be an object, not {type(raw).__name__}")
            findings.append(Finding(
                id=str(raw.get("id", "")),
                kind=str(raw.get("kind", "")),
                severity=str(raw.get("severity", "")),
                where=str(raw.get("where", "")),
                problem=str(raw.get("problem", "")),
                fix=str(raw.get("fix", "")),
                quote=str(raw.get("quote", "")),
            ))
        return cls(round=int(data.get("round", 1) or 1),
                   document=str(data.get("document", "")),
                   summary=str(data.get("summary", "")),
                   findings=findings)

    @classmethod
    def load(cls, path: Path) -> Review:
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise GraphError(f"could not read {path}: {exc}") from exc

    def to_dict(self) -> dict:
        return {
            "round": self.round,
            "document": self.document,
            "summary": self.summary,
            "findings": [
                {k: v for k, v in {
                    "id": f.id, "kind": f.kind, "severity": f.severity,
                    "where": f.where, "quote": f.quote,
                    "problem": f.problem, "fix": f.fix,
                }.items() if v}
                for f in self.findings
            ],
        }


@dataclass(slots=True)
class Disposition:
    id: str
    disposition: str
    note: str
    where: str = ""


@dataclass(slots=True)
class Dispositions:
    round: int
    items: list[Disposition] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Dispositions:
        if not isinstance(data, dict):
            raise GraphError("the dispositions file must contain a JSON object")
        items = []
        for raw in data.get("dispositions", []):
            if not isinstance(raw, dict):
                raise GraphError("a disposition must be an object")
            items.append(Disposition(
                id=str(raw.get("id", "")),
                disposition=str(raw.get("disposition", "")),
                note=str(raw.get("note", "")),
                where=str(raw.get("where", "")),
            ))
        return cls(round=int(data.get("round", 1) or 1), items=items)

    @classmethod
    def load(cls, path: Path) -> Dispositions:
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise GraphError(f"could not read {path}: {exc}") from exc


# --------------------------------------------------------------------------
# locating a finding
# --------------------------------------------------------------------------


def headings(guide: str) -> list[str]:
    """Every ATX heading in the guide, stripped of its markers and numbering."""
    out = []
    for line in guide.splitlines():
        if (m := re.match(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$", line)):
            out.append(m.group(2).strip())
    return out


# Substring matching needs a floor. Without one, `guide:a` matched any heading
# containing the letter a — and a location that vague is exactly what the
# requirement to give one exists to exclude.
MIN_HEADING_MATCH = 4


def _heading_matches(needle: str, found: list[str]) -> bool:
    """Tolerant heading match: ignore case, numbering, and trailing punctuation.

    Tolerant, because a reviewer should not have to transcribe a heading
    character for character. Not unbounded: a partial match has to be long
    enough to name something.
    """
    def key(s: str) -> str:
        s = re.sub(r"^\s*\d+[.)]?\s*", "", s.strip().lower())
        s = re.sub(r"[*_`#]", "", s)
        return re.sub(r"[^a-z0-9 ]+", "", s).strip()

    want = key(needle)
    if not want:
        return False
    for heading in found:
        got = key(heading)
        if want == got:
            return True
        if len(want) >= MIN_HEADING_MATCH and len(got) >= MIN_HEADING_MATCH \
                and (want in got or got in want):
            return True
    return False


def resolve_where(where: str, *, graph: Graph | None = None,
                  guide: str | None = None,
                  document: str | None = None) -> str | None:
    """None if `where` points at something real, else why it does not.

    This is the check that keeps a critique from evaporating. "The exposition in
    the middle drags" cannot be fixed, cannot be rejected on grounds, and cannot
    be shown to have been dealt with. A location can do all three.
    """
    m = _WHERE.match(where.strip())
    if not m:
        return ("must be one of graph:<node>, graph:<node>-><node>, "
                "guide:<heading>, document:<line>, appeal:<line>")

    if (node := m.group("node")):
        if graph is None:
            return None
        ids = {n.id for n in graph.nodes}
        if node not in ids:
            return f"{node} is not a node of the graph"
        if (target := m.group("target")):
            if target not in ids:
                return f"{target} is not a node of the graph"
            if not any(e.source == node and e.target == target for e in graph.edges):
                return f"the graph has no edge {node}->{target}"
        return None

    if (heading := m.group("heading")) is not None:
        if guide is None:
            return None
        if not _heading_matches(heading, headings(guide)):
            return f"the guide has no heading matching {heading.strip()!r}"
        return None

    if (line := m.group("line")):
        if document is None:
            return None
        total = len(document.splitlines())
        if not 1 <= int(line) <= total:
            return f"line {line} is outside the document (1-{total})"
        return None

    if (appeal := m.group("appeal")):
        if graph is None:
            return None
        if not any(a.line == int(appeal) for a in graph.appeals):
            return (f"no appeal is adjudicated at line {appeal}; the graph "
                    f"records verdicts for lines "
                    f"{', '.join(str(a.line) for a in graph.appeals[:8]) or '(none)'}")
        return None

    return None


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate_review(review: Review, *, graph: Graph | None = None,
                    guide: str | None = None,
                    document: str | None = None) -> list[Problem]:
    """Every way a critique can fail to be a critique."""
    out: list[Problem] = []
    seen: set[str] = set()

    for f in review.findings:
        where = f"finding {f.id or '(no id)'}"

        if not _FINDING_ID.match(f.id):
            out.append(Problem("error", where, "id must be F followed by digits, e.g. F3"))
        elif f.id in seen:
            out.append(Problem("error", where, "duplicate id"))
        seen.add(f.id)

        if f.kind not in FINDING_KINDS:
            out.append(Problem(
                "error", where,
                f"kind must be one of {', '.join(FINDING_KINDS)} — these are the "
                f"discrepancies that can be settled by looking at the document"))
        if f.severity not in SEVERITIES:
            out.append(Problem("error", where,
                               f"severity must be one of {', '.join(SEVERITIES)}"))

        if not f.problem.strip():
            out.append(Problem("error", where, "no statement of the problem"))
        elif len(f.problem.split()) > MAX_PROBLEM_WORDS:
            out.append(Problem("warning", where,
                               f"the problem runs to {len(f.problem.split())} words; "
                               f"a finding that needs an essay is usually several findings"))

        if not f.fix.strip():
            out.append(Problem("error", where,
                               "no fix — say what must be established for this to be "
                               "discharged"))
        elif len(f.fix.split()) > MAX_FIX_WORDS:
            out.append(Problem(
                "error", where,
                f"the fix runs to {len(f.fix.split())} words (limit {MAX_FIX_WORDS}). "
                f"State the obligation, not the replacement text: prose supplied "
                f"here gets pasted, and the loop becomes the critic writing the "
                f"guide with an extra step"))
        elif _PROSE_MARKERS.search(f.fix):
            out.append(Problem(
                "error", where,
                "the fix contains headings, list items or a code block — that is "
                "replacement text. Say what must be established and leave the "
                "writing to someone who has read the document"))

        if (why := resolve_where(f.where, graph=graph, guide=guide, document=document)):
            out.append(Problem("error", where, f"where {f.where!r}: {why}"))

        if f.kind in NEEDS_QUOTE:
            if not f.quote.strip():
                out.append(Problem(
                    "error", where,
                    f"a {f.kind} finding is a claim about the document, so it must "
                    f"quote the passage it is about"))
            elif len(normalise(f.quote)) < 12:
                out.append(Problem("error", where,
                                   "quotation is too short to locate"))
            elif document is not None and not quote_found(f.quote, document):
                out.append(Problem(
                    "error", where,
                    "the quotation does not appear in the document. A critique "
                    "resting on text that is not there is worse than no critique"))
        elif f.quote.strip() and document is not None and not quote_found(f.quote, document):
            out.append(Problem("error", where,
                               "the quotation does not appear in the document"))

        # The one taste-based kind is admitted only in a form that can be
        # discharged: name what the reader cannot work out. "This is confusing"
        # is unanswerable; "what is a here?" has an answer or it does not.
        if f.kind == "unclear" and not f.problem.strip().endswith("?"):
            out.append(Problem(
                "error", where,
                "an `unclear` finding must state the question the reader is left "
                "with, ending in a question mark. Otherwise there is no way to "
                "tell whether a revision has answered it"))

    if not review.findings and not review.summary.strip():
        out.append(Problem("warning", "review",
                           "no findings and no summary — say plainly that the "
                           "companion holds up, if that is the judgement"))
    return out


def validate_dispositions(review: Review, dispositions: Dispositions, *,
                          graph: Graph | None = None,
                          guide: str | None = None,
                          document: str | None = None,
                          before: Graph | None = None,
                          before_guide: str | None = None) -> list[Problem]:
    """Every finding answered once, on the record, either way.

    `before` and `before_guide` are the companion as it stood when the findings
    were written, and they are not optional decoration. A revision is checked
    against the state it produced, but the natural way to record a fix is to
    name what you acted on — and if the fix was to *delete* a wrong edge or drop
    a section, that location no longer exists. Demanding it resolve in the new
    state would reject exactly the cleanest kind of repair. A location is
    accepted if it resolves in either state.
    """
    out: list[Problem] = []
    known = review.by_id()
    seen: set[str] = set()

    # `by_id` is a dict, so two findings sharing an id become one and answering
    # it answers both. `validate_review` calls that an error, but on a provider
    # that runs no hooks the error is recorded rather than corrected — so it is
    # said again here, where the consequence actually lands.
    counts: dict[str, int] = {}
    for f in review.findings:
        counts[f.id] = counts.get(f.id, 0) + 1
    for dup in sorted(k for k, n in counts.items() if n > 1):
        out.append(Problem(
            "error", f"finding {dup}",
            f"the review raises {counts[dup]} findings with this id, so one "
            f"disposition appears to answer all of them. Give each its own id "
            f"and answer each"))

    for d in dispositions.items:
        where = f"disposition {d.id or '(no id)'}"
        if d.id not in known:
            out.append(Problem("error", where,
                               f"{d.id!r} is not a finding in this review"))
            continue
        if d.id in seen:
            out.append(Problem("error", where, "answered twice"))
            continue
        seen.add(d.id)

        if d.disposition not in DISPOSITIONS:
            out.append(Problem("error", where,
                               f"disposition must be {' or '.join(DISPOSITIONS)}"))
        if not d.note.strip():
            out.append(Problem("error", where, "no note"))

        if d.disposition == "fixed":
            if not d.where.strip():
                out.append(Problem(
                    "error", where,
                    "a finding recorded as fixed must say where the change is, so "
                    "a reader can go and look at it"))
            elif (why := resolve_where(d.where, graph=graph, guide=guide,
                                       document=document)):
                # It may name something the fix removed. That is a fix, not a
                # dangling reference, so the earlier state counts too.
                if before is None and before_guide is None:
                    out.append(Problem("error", where, f"where {d.where!r}: {why}"))
                elif resolve_where(d.where, graph=before or graph,
                                   guide=before_guide or guide, document=document):
                    out.append(Problem("error", where, f"where {d.where!r}: {why}"))
        elif d.disposition == "rejected" and len(d.note.split()) < MIN_REJECTION_WORDS:
            out.append(Problem(
                "error", where,
                "rejecting a finding is allowed and costs nothing, but it needs a "
                "reason. Say why the critic is wrong"))

    for missing in sorted(set(known) - seen):
        f = known[missing]
        out.append(Problem(
            "error", f"finding {missing}",
            f"never answered ({f.severity}, {f.kind}) — every finding must be "
            f"recorded as fixed or rejected. Disagreeing is fine; passing over "
            f"it in silence is how an inconvenient one disappears"))
    return out
