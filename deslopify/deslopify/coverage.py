"""Does the guide actually deliver what the graph found?

The two artefacts are written in one sitting by one reader, and they drift apart
in a characteristic way: the graph records a gap at `!4`, the guide ends on a
confident summary, and nobody notices that the hole was found and then never
mentioned to the person the companion is for. That is not a lapse of judgement
and it does not need judgement to catch. It needs a list.

So everything here is arithmetic on two files, and it runs before a critic is
asked for an opinion. A reviewer's attention is the expensive thing in this
loop; spending it on "you did not mention !4" wastes it on work a regular
expression does perfectly, and leaves less of it for the questions only a reader
can answer.

Nothing here blocks a session. A guide can have unusual headings and still be a
good guide, and a checker that wedges a run over a heading would cost more than
it saves. These are observations, handed to the critic as a starting list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .analysis import Analysis, analyse
from .graph import Graph

# The sections the method asks for, each with the ways a reasonable writer might
# name it. Matching is deliberately loose: the point is to notice a section that
# is *absent*, not to insist on a wording.
REQUIRED_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("main results",
     ("main results", "principal results", "principal theorems", "main theorems")),
    ("main ideas",
     ("main ideas", "main idea", "core ideas", "key ideas", "big picture")),
    ("about the proofs",
     ("about the proofs", "about the proof", "structure of the proofs", "proof structure",
      "proof architecture", "shape of the argument", "structure of the argument",
      "supporting results", "supporting lemmas", "auxiliary results",
      "what it rests on", "ingredients")),
    ("questions and answers",
     ("questions and answers", "q&a", "qa", "questions", "asked and answered")),
    ("historical context",
     ("historical context", "historical notes", "history and context",
      "intellectual history", "related work")),
)

# Ids as a guide writes them when pointing at the map. Only maths is stripped:
# a `C2` inside $...$ is a constant, not node C2. Code spans are deliberately
# kept — `!1` in backticks is how a careful writer names a node in Markdown, and
# stripping those had the audit accuse a guide of never mentioning the gaps it
# named on every other line.
_REFERENCE = re.compile(r"(?<![A-Za-z0-9_])((?:[HGXMCQ]|!)\d{1,4})(?![A-Za-z0-9_])")
_MATH = re.compile(r"\$\$.*?\$\$|\$[^$\n]*\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)


@dataclass(slots=True)
class Observation:
    severity: str            # "error" | "warning" | "note"
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.where}: {self.message}"


@dataclass(slots=True)
class Coverage:
    observations: list[Observation] = field(default_factory=list)
    referenced: set[str] = field(default_factory=set)
    sections: list[str] = field(default_factory=list)
    words: int = 0
    questions: int = 0

    @property
    def errors(self) -> list[Observation]:
        return [o for o in self.observations if o.severity == "error"]


def prose_only(guide: str) -> str:
    """The guide with maths removed, so id matching means something."""
    return _MATH.sub(" ", guide)


def references(guide: str) -> set[str]:
    """Node ids the guide points at."""
    return set(_REFERENCE.findall(prose_only(guide)))


def _normalise_heading(s: str) -> str:
    s = re.sub(r"^\s*\d+[.)]?\s*", "", s.strip().lower())
    s = re.sub(r"[*_`#]", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]+", " ", s)).strip()


def coverage(graph: Graph, guide: str, analysis: Analysis | None = None) -> Coverage:
    """What the guide owes the graph, and whether it paid."""
    from .review import headings

    result = analysis or analyse(graph)
    found = [h for h in headings(guide)]
    normalised = [_normalise_heading(h) for h in found]
    out: list[Observation] = []

    validation = "about the method and experimental validation"
    legacy_validation = "about the validation experiments and proofs"
    applied = validation in normalised or legacy_validation in normalised
    for canonical, aliases in REQUIRED_SECTIONS:
        if applied and canonical == "main results":
            canonical, aliases = "main findings", ("main findings", "main results")
        if applied and canonical == "main ideas":
            continue
        if applied and canonical == "about the proofs":
            canonical, aliases = validation, (validation, legacy_validation)
        if not any(alias in h for h in normalised for alias in aliases):
            out.append(Observation(
                "warning", "guide",
                f"no section on {canonical!r} — the method asks for one, and a "
                f"reader who wants it will not find it"))

    cited = references(guide)
    ids = {n.id for n in graph.nodes}
    by_id = graph.by_id()

    for dangling in sorted(cited - ids):
        out.append(Observation(
            "error", f"guide -> {dangling}",
            f"the guide points at {dangling}, which is not a node of the graph. "
            f"A reader following that reference to the map finds nothing"))

    # A gap that was found and then never told to the reader is the failure this
    # whole package is built against, arrived at by accident instead of by
    # evasion. It is still the same artefact.
    for node in graph.of_type("!"):
        if node.id not in cited:
            out.append(Observation(
                "error", f"gap {node.id}",
                f"the graph records a gap ({node.label or 'unlabelled'}) that the "
                f"guide never mentions. Finding a hole and not reporting it "
                f"leaves the reader exactly where they started"))

    for node_id in result.crux:
        if node_id not in cited:
            out.append(Observation(
                "warning", f"crux {node_id}",
                f"{node_id} ({by_id[node_id].label}) is a node every route to the "
                f"conclusion passes through, and the guide never names it"))

    for finding in result.by_kind("adrift"):
        node_id = finding.node_ids[0]
        if node_id not in cited:
            out.append(Observation(
                "note", f"adrift {node_id}",
                f"{node_id} ({by_id[node_id].label}) reaches no goal and no "
                f"conclusion; a reader is better off being told not to spend "
                f"attention there"))

    for node in graph.of_type("H"):
        if node.id not in cited:
            out.append(Observation(
                "warning", f"hypothesis {node.id}",
                f"{node.id} ({node.label}) is a standing assumption the guide "
                f"never locates. 'Where is H used?' is one of the questions the "
                f"companion exists to answer"))

    # Consistency between the two records of skipped work: the method asks that
    # an appeal judged a gap also appear as a `!` node, so that it lands on the
    # map where a reader meets it rather than in a list at the back.
    gap_lines = [a.line for a in graph.appeals if a.verdict == "gap"]
    if gap_lines and not graph.of_type("!"):
        out.append(Observation(
            "error", "appeals",
            f"{len(gap_lines)} appeal(s) were adjudicated as gaps (lines "
            f"{', '.join(str(n) for n in gap_lines[:8])}) but the graph has no "
            f"`!` node. A gap recorded only in the appeals list never reaches "
            f"the map"))

    prose = prose_only(guide)
    return Coverage(
        observations=out,
        referenced=cited,
        sections=found,
        words=len(prose.split()),
        questions=sum(1 for line in guide.splitlines() if line.strip().endswith("?")),
    )
