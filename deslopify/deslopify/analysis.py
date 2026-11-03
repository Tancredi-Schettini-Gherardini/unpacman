"""Structural analysis of an argument graph.

Every question the reader asks is a query here, which is why the graph is built
before any prose is written. The same computations that explain a sound argument
expose an unsound one: a construction with no outgoing edge was introduced for
nothing, a claim with no incoming support rests on nothing, and a component that
never reaches the goal is not part of this proof at all.

Centrality is computed rather than left to the model. In the Syrup thesis it was
the one structural signal that no serialization made legible — accuracy 0.07-0.29
when a model had to derive it, 1.00 once injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import Graph

SUPPORT = "SUPPORTS"


# --------------------------------------------------------------------------
# reachability
# --------------------------------------------------------------------------


def _adjacency(graph: Graph, kinds: tuple[str, ...] | None = None,
               reverse: bool = False) -> dict[str, set[str]]:
    ids = {n.id for n in graph.nodes}
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for e in graph.edges:
        if kinds and e.type not in kinds:
            continue
        if e.source not in ids or e.target not in ids:
            continue
        a, b = (e.target, e.source) if reverse else (e.source, e.target)
        adj[a].add(b)
    return adj


def _reach(adj: dict[str, set[str]], starts: set[str], skip: set[str] = frozenset()) -> set[str]:
    seen: set[str] = set()
    stack = [s for s in starts if s not in skip]
    while stack:
        node = stack.pop()
        if node in seen or node in skip:
            continue
        seen.add(node)
        stack.extend(adj.get(node, ()) - seen)
    return seen


def downstream(graph: Graph, node_id: str) -> set[str]:
    """Everything that depends on `node_id`. Answers 'where is H used?'."""
    adj = _adjacency(graph, kinds=(SUPPORT,))
    return _reach(adj, {node_id}) - {node_id}


def upstream(graph: Graph, node_id: str) -> set[str]:
    """Everything `node_id` rests on."""
    adj = _adjacency(graph, kinds=(SUPPORT,), reverse=True)
    return _reach(adj, {node_id}) - {node_id}


def paths(graph: Graph, source: str, target: str, limit: int = 12) -> list[list[str]]:
    """Every route from one node to another. Answers 'how do these relate?'."""
    adj = _adjacency(graph)
    found: list[list[str]] = []

    def walk(node: str, trail: list[str]) -> None:
        if len(found) >= limit:
            return
        if node == target:
            found.append([*trail, node])
            return
        for nxt in sorted(adj.get(node, ())):
            if nxt not in trail:
                walk(nxt, [*trail, node])

    walk(source, [])
    return found


def roots(graph: Graph) -> set[str]:
    """Where the argument starts: hypotheses and goals."""
    return {n.id for n in graph.nodes if n.id[0] in ("H", "G")}


def sinks(graph: Graph) -> set[str]:
    """What the argument is for: conclusions, or claims if it never concludes."""
    ends = {n.id for n in graph.nodes if n.id[0] == "Q"}
    return ends or {n.id for n in graph.nodes if n.id[0] == "C"}


def supported(graph: Graph, removed: set[str] = frozenset()) -> set[str]:
    """Which nodes still stand, treating support as conjunctive.

    This is where a proof differs from an empirical argument, and where
    borrowing the paper-graph semantics wholesale would have been wrong. A
    paper's conclusion may rest on several findings *any* of which lends it
    weight; a proof step needs **every** premise it invokes. So a node holds
    only when all of its supports hold — which is what makes "which step would
    fail without A?" answerable at all.

    A node with no incoming support is taken as given: hypotheses, definitions,
    and anything the document simply asserts.
    """
    ids = {n.id for n in graph.nodes} - set(removed)
    premises: dict[str, set[str]] = {i: set() for i in ids}
    for e in graph.edges:
        if e.type == SUPPORT and e.target in ids:
            premises[e.target].add(e.source)

    holds = {i for i in ids if not premises[i]}
    changed = True
    while changed:                       # monotone, so it settles
        changed = False
        for i in ids - holds:
            if premises[i] <= holds:
                holds.add(i)
                changed = True
    return holds


def without(graph: Graph, node_id: str) -> set[str]:
    """What loses its support if `node_id` is removed.

    The honest form of "which step would fail without assumption A?": take A
    out and report every step that no longer stands, support being conjunctive.
    """
    return (supported(graph) - supported(graph, {node_id})) - {node_id}


# --------------------------------------------------------------------------
# centrality and the crux
# --------------------------------------------------------------------------


def katz_centrality(graph: Graph, alpha: float = 0.35,
                    iterations: int = 100, tol: float = 1e-9) -> dict[str, float]:
    """Katz centrality by power iteration, normalised to a maximum of 1.

    No numpy: the graphs are tens of nodes, and a dependency is not worth it.
    """
    ids = [n.id for n in graph.nodes]
    if not ids:
        return {}
    incoming = _adjacency(graph, reverse=True)
    score = dict.fromkeys(ids, 1.0)
    for _ in range(iterations):
        nxt = {i: 1.0 + alpha * sum(score[j] for j in incoming.get(i, ())) for i in ids}
        if max(abs(nxt[i] - score[i]) for i in ids) < tol:
            score = nxt
            break
        score = nxt
    top = max(score.values()) or 1.0
    return {i: round(v / top, 4) for i, v in score.items()}


def crux(graph: Graph) -> list[str]:
    """Nodes every route to the conclusion passes through.

    Remove a node; if nothing the argument is for is reachable any more, the
    proof turns on it. This is where a reader's attention is worth most, and
    where a gap costs most.
    """
    start, end = roots(graph), sinks(graph)
    if not start or not end:
        return []
    adj = _adjacency(graph, kinds=(SUPPORT,))
    if not (_reach(adj, start) & end):
        return []                      # nothing reaches the end to begin with
    out = []
    for n in graph.nodes:
        if n.id in start or n.id in end or n.id[0] == "!":
            continue
        if not (_reach(adj, start - {n.id}, skip={n.id}) & end):
            out.append(n.id)
    return out


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Finding:
    kind: str
    node_ids: list[str]
    message: str


@dataclass(slots=True)
class Analysis:
    centrality: dict[str, float] = field(default_factory=dict)
    crux: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    on_topic: int = 0                 # nodes that reach the goal or a conclusion
    total: int = 0

    def by_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    @property
    def focus(self) -> float:
        """The share of the document that serves its stated problem.

        A low number is the headline finding about a document that accumulated
        material session after session: most of what is on the page is not part
        of the argument it claims to make.
        """
        return (self.on_topic / self.total) if self.total else 0.0


def analyse(graph: Graph) -> Analysis:
    """Everything the structure says about the argument, without reading it."""
    findings: list[Finding] = []
    ids = {n.id for n in graph.nodes}
    supported = {e.target for e in graph.edges if e.type == SUPPORT}
    has_outgoing = {e.source for e in graph.edges}

    for n in graph.nodes:
        if n.id[0] in ("C", "Q") and n.id not in supported:
            findings.append(Finding(
                "unsupported", [n.id],
                f"{n.id} asserts something but nothing in the document supports it"))
        if n.id[0] in ("X", "M") and n.id not in has_outgoing:
            findings.append(Finding(
                "unused", [n.id],
                f"{n.id} is introduced and then used for nothing"))

    # Anything that reaches neither a conclusion nor a goal, by any edge, is not
    # part of this argument whatever it looks like on the page. Goals are
    # destinations themselves and are never adrift; a hypothesis that motivates
    # one has arrived.
    end = sinks(graph) | {n.id for n in graph.nodes if n.id[0] == "G"}
    forward = _adjacency(graph)
    if end:
        for n in graph.nodes:
            if n.id[0] in ("!", "G") or n.id in end:
                continue
            if not (_reach(forward, {n.id}) & end):
                findings.append(Finding(
                    "adrift", [n.id],
                    f"{n.id} leads nowhere: it reaches no conclusion and no goal"))

    start = roots(graph)
    if start and end and not (_reach(_adjacency(graph, kinds=(SUPPORT,)), start) & end):
        findings.append(Finding(
            "broken", sorted(end),
            "no chain of support runs from the hypotheses to the conclusion"))

    for n in graph.of_type("!"):
        findings.append(Finding("gap", [n.id], n.label or "a gap in the argument"))

    for n in graph.nodes:
        if n.uncertain:
            findings.append(Finding(
                "uncertain", [n.id],
                f"{n.id} was read out of the document with low confidence"))
    for e in graph.edges:
        if e.uncertain:
            findings.append(Finding(
                "uncertain", [e.source, e.target],
                f"{e.source} -> {e.target} was inferred rather than read"))

    substantive = [n for n in graph.nodes if n.id[0] != "!"]
    adrift_ids = {f.node_ids[0] for f in findings if f.kind == "adrift"}
    return Analysis(
        centrality=katz_centrality(graph), crux=crux(graph), findings=findings,
        on_topic=sum(1 for n in substantive if n.id not in adrift_ids),
        total=len(substantive),
    )
