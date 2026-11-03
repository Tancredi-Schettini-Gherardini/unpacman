"""The argument graph: schema, loading, and validation.

The graph is the analysis, not a picture drawn after it — every question a
reader asks is a query against it, so it has to be an object that can be checked
rather than prose that merely looks structured.

One check here is the fidelity spine. Every node carries a quotation, and
`validate` confirms that quotation appears **verbatim in the document**. An
audit of 319 quotations in the Syrup thesis found none fabricated once grounding
was mandatory; making it mechanical rather than a matter of instruction is what
turns that from a tendency into a guarantee.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Type-prefixed ids, so an answer can point at the map and a reader can follow.
NODE_TYPES: dict[str, str] = {
    "H": "hypothesis",      # a standing assumption or hypothesis of the theorem
    "G": "goal",            # what is to be proved; a sub-goal; a case obligation
    "X": "construction",    # an object, map or quantity brought into existence
    "M": "move",            # a technique applied: induction, substitution, a cited theorem
    "C": "claim",           # a lemma or intermediate assertion
    "Q": "conclusion",      # the theorem, as established
    "!": "gap",             # an unproved step, an appeal to unshown work, a hand-wave
}
EDGE_TYPES = ("SUPPORTS", "MOTIVATES", "CONTRADICTS")

_ID = re.compile(r"^([HGXMCQ!])(\d+)$")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")


class GraphError(Exception):
    """A graph that cannot be loaded at all, as opposed to one that fails checks."""


@dataclass(slots=True)
class Node:
    id: str
    type: str
    label: str
    description: str
    quote: str
    parent: str | None = None
    uncertain: bool = False          # the author of the graph was not sure

    @property
    def role(self) -> str:
        return NODE_TYPES.get(self.id[0], "unknown")


@dataclass(slots=True)
class Edge:
    source: str
    target: str
    type: str
    justification: str
    uncertain: bool = False


@dataclass(slots=True)
class Adjudication:
    """A decision about one place where the document skipped work.

    The verdict is not dictated — most appeals in mathematics are genuinely
    routine. What is required is that each one was *looked at*, because the
    alternative, observed twice on live runs, is a companion that reads twelve
    appeals and reports that the document is sound.
    """
    line: int
    verdict: str                     # "routine" | "gap"
    note: str = ""


@dataclass(slots=True)
class Graph:
    document: str                    # path of the document this describes
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    appeals: list[Adjudication] = field(default_factory=list)
    notes: str = ""

    # -- lookups -----------------------------------------------------------

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def of_type(self, letter: str) -> list[Node]:
        return [n for n in self.nodes if n.id.startswith(letter)]

    def out_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node_id]

    # -- io ----------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> Graph:
        if not isinstance(data, dict):
            raise GraphError("the graph file must contain a JSON object")
        try:
            nodes = [
                Node(
                    id=str(n["id"]),
                    type=NODE_TYPES.get(str(n["id"])[:1], "unknown"),
                    label=str(n.get("label", "")),
                    description=str(n.get("description", "")),
                    quote=str(n.get("quote", "")),
                    parent=(str(n["parent"]) if n.get("parent") else None),
                    uncertain=bool(n.get("uncertain", False)),
                )
                for n in data.get("nodes", [])
            ]
            edges = [
                Edge(
                    source=str(e["source"]), target=str(e["target"]),
                    type=str(e.get("type", "")),
                    justification=str(e.get("justification", "")),
                    uncertain=bool(e.get("uncertain", False)),
                )
                for e in data.get("edges", [])
            ]
        except (KeyError, TypeError) as exc:
            raise GraphError(f"malformed node or edge: {exc}") from exc
        appeals = [
            Adjudication(line=int(a.get("line", 0)),
                         verdict=str(a.get("verdict", "")),
                         note=str(a.get("note", "")))
            for a in data.get("appeals", []) if isinstance(a, dict)
        ]
        return cls(document=str(data.get("document", "")), nodes=nodes,
                   edges=edges, appeals=appeals,
                   notes=str(data.get("notes", "")))

    @classmethod
    def load(cls, path: Path) -> Graph:
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise GraphError(f"could not read {path}: {exc}") from exc

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "notes": self.notes,
            "nodes": [
                {k: v for k, v in {
                    "id": n.id, "label": n.label, "description": n.description,
                    "quote": n.quote, "parent": n.parent,
                    "uncertain": n.uncertain or None,
                }.items() if v not in (None, False)}
                for n in self.nodes
            ],
            "edges": [
                {k: v for k, v in {
                    "source": e.source, "target": e.target, "type": e.type,
                    "justification": e.justification,
                    "uncertain": e.uncertain or None,
                }.items() if v not in (None, False)}
                for e in self.edges
            ],
            "appeals": [
                {"line": a.line, "verdict": a.verdict, "note": a.note}
                for a in self.appeals
            ],
        }


# --------------------------------------------------------------------------
# quotation matching
# --------------------------------------------------------------------------


def normalise(text: str) -> str:
    """Collapse the differences that do not change what the text says.

    A quotation lifted out of LaTeX will differ from the source in line breaks
    and run of spaces, and often in the shape of its quotes and dashes. None of
    that is fabrication, so none of it should fail the check — while anything
    that alters a word, a symbol or a number still will.
    """
    text = unicodedata.normalize("NFKC", text)
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'),
                 ("”", '"'), ("–", "-"), ("—", "-"),
                 ("−", "-"), (" ", " ")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def quote_found(quote: str, document: str) -> bool:
    """Is `quote` present in `document`, allowing spans joined by an ellipsis?

    An ellipsis is a legitimate way to quote two parts of one sentence. It is
    not a licence to invent the join, so the parts must appear *in order*.
    """
    haystack = normalise(document)
    parts = [p for p in _ELLIPSIS.split(normalise(quote)) if p]
    if not parts:
        return False
    at = 0
    for part in parts:
        found = haystack.find(part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Problem:
    severity: str        # "error" blocks; "warning" is reported and allowed
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.where}: {self.message}"


MIN_QUOTE_CHARS = 12


def validate(graph: Graph, document: str | None = None) -> list[Problem]:
    """Every way the graph can be wrong, in one pass.

    `document` is the text the graph claims to describe. Without it the
    structural checks still run, but grounding cannot be verified — which is the
    check that matters most, so a caller that can supply it always should.
    """
    out: list[Problem] = []
    seen: dict[str, Node] = {}

    for n in graph.nodes:
        where = f"node {n.id}"
        if not _ID.match(n.id):
            out.append(Problem("error", where,
                               f"id must be a type letter {''.join(NODE_TYPES)} "
                               f"followed by digits, e.g. C3"))
            continue
        if n.id in seen:
            out.append(Problem("error", where, "duplicate id"))
            continue
        seen[n.id] = n

        if not n.label.strip():
            out.append(Problem("error", where, "no label"))
        if len(n.label.split()) > 20:
            out.append(Problem("warning", where,
                               "label is long enough to be a description"))

        # Gaps annotate a path and are deliberately edgeless, so they are the one
        # node type that may exist without being quotable from a single span.
        if n.id[0] != "!":
            if not n.quote.strip():
                out.append(Problem("error", where,
                                   "no quotation — a node with no quotation was "
                                   "inferred, not read"))
            elif len(normalise(n.quote)) < MIN_QUOTE_CHARS:
                out.append(Problem("error", where,
                                   f"quotation is too short to locate "
                                   f"({len(normalise(n.quote))} chars)"))
            elif document is not None and not quote_found(n.quote, document):
                out.append(Problem("error", where,
                                   "quotation does not appear in the document"))

        if n.parent:
            if n.parent not in {m.id for m in graph.nodes}:
                out.append(Problem("error", where, f"parent {n.parent} does not exist"))
            elif n.parent[0] != n.id[0]:
                out.append(Problem("error", where,
                                   "containment must be between nodes of one type"))
            elif any(m.id == n.parent and m.parent for m in graph.nodes):
                out.append(Problem("error", where,
                                   "containment is limited to a single level"))

    for i, e in enumerate(graph.edges):
        where = f"edge {i} ({e.source}->{e.target})"
        if e.type not in EDGE_TYPES:
            out.append(Problem("error", where,
                               f"type must be one of {', '.join(EDGE_TYPES)}"))
        for end in (e.source, e.target):
            if end not in seen:
                out.append(Problem("error", where, f"{end} is not a node"))
        if e.source == e.target:
            out.append(Problem("error", where, "an edge from a node to itself"))
        if not e.justification.strip():
            out.append(Problem("error", where, "no justification"))
        if e.source in seen and e.source[0] == "!":
            out.append(Problem("error", where, "a gap annotates a path; it has no edges"))
        if e.target in seen and e.target[0] == "!":
            out.append(Problem("error", where, "a gap annotates a path; it has no edges"))

    if cycle := find_cycle(graph):
        out.append(Problem("error", "graph",
                           "the argument must be acyclic, but it contains the cycle "
                           + " -> ".join(cycle)))

    if document is not None:
        from .lint import find_appeals

        verdicts = {a.line: a for a in graph.appeals}
        for a in graph.appeals:
            if a.verdict not in ("routine", "gap"):
                out.append(Problem("error", f"appeal at line {a.line}",
                                   "verdict must be 'routine' or 'gap'"))
        unjudged = [ap.line for ap in find_appeals(document)
                    if ap.line not in verdicts]
        if unjudged:
            shown = ", ".join(str(n) for n in unjudged[:12])
            out.append(Problem(
                "error", "appeals",
                f"{len(unjudged)} place(s) where the document stands a phrase in "
                f"for an argument have not been looked at (lines {shown}). Run "
                f"`deslopify appeals <document>` and record a verdict for each in "
                f"the graph's \"appeals\" list: \"routine\" if the omitted work "
                f"genuinely is, \"gap\" if the claim rests on it. Deciding they "
                f"are routine is a fine answer; not deciding is not."))

    if not graph.nodes:
        out.append(Problem("error", "graph", "no nodes"))
    elif not graph.of_type("Q") and not graph.of_type("G"):
        out.append(Problem("warning", "graph",
                           "no goal and no conclusion — what was this proving?"))
    return out


def find_cycle(graph: Graph) -> list[str] | None:
    """Return one cycle as a list of ids, or None. Depth-first, colour-marked."""
    adjacency: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        if e.source in adjacency and e.target in adjacency:
            adjacency[e.source].append(e.target)

    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(adjacency, WHITE)
    stack: list[str] = []

    def walk(node: str) -> list[str] | None:
        colour[node] = GREY
        stack.append(node)
        for nxt in adjacency[node]:
            if colour[nxt] == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if colour[nxt] == WHITE and (found := walk(nxt)):
                return found
        stack.pop()
        colour[node] = BLACK
        return None

    for node in adjacency:
        if colour[node] == WHITE and (found := walk(node)):
            return found
    return None
