"""What a round of revision actually changed.

The reason to measure this is that the failure mode of a critique loop is
invisible from inside it. Every round ends with an artefact that reads better
than the one before, because rereading and rewriting is what the round consists
of. Whether the *analysis* moved is a separate question, and the answer is not
available to anyone who only reads the prose.

So the two are counted apart. Changes to the graph are changes to what the
companion claims about the document: a node added, a support withdrawn, an
appeal that was routine and is now a gap. Changes to the guide alone are changes
to how it reads. Both are legitimate, and neither is reported here as a fault: a finding about
what the reader is *told* is answered in the guide and nowhere else, which a
first live run made plain by tripping an earlier, blunter version of this on an
entirely reasonable round. What this module does is count the two apart and say
so plainly. Whether an unchanged graph is a problem depends on what was claimed
to have been fixed, and that comparison belongs to whoever holds the
dispositions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .graph import Graph, normalise


@dataclass(slots=True)
class GraphDiff:
    nodes_added: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)
    relabelled: list[str] = field(default_factory=list)
    requoted: list[str] = field(default_factory=list)
    rewritten: list[str] = field(default_factory=list)      # description changed
    edges_added: list[str] = field(default_factory=list)
    edges_removed: list[str] = field(default_factory=list)
    rejustified: list[str] = field(default_factory=list)
    appeals_added: list[int] = field(default_factory=list)
    appeals_removed: list[int] = field(default_factory=list)
    appeals_flipped: list[tuple[int, str, str]] = field(default_factory=list)
    gaps_before: int = 0
    gaps_after: int = 0

    @property
    def structural(self) -> int:
        """Changes to what the companion claims, as opposed to how it reads."""
        return (len(self.nodes_added) + len(self.nodes_removed)
                + len(self.edges_added) + len(self.edges_removed)
                + len(self.requoted) + len(self.rejustified)
                + len(self.appeals_added) + len(self.appeals_removed)
                + len(self.appeals_flipped))

    @property
    def cosmetic(self) -> int:
        return len(self.relabelled) + len(self.rewritten)

    def as_dict(self) -> dict:
        return {
            "nodes_added": self.nodes_added, "nodes_removed": self.nodes_removed,
            "relabelled": self.relabelled, "requoted": self.requoted,
            "rewritten": self.rewritten,
            "edges_added": self.edges_added, "edges_removed": self.edges_removed,
            "rejustified": self.rejustified,
            "appeals_added": self.appeals_added,
            "appeals_removed": self.appeals_removed,
            "appeals_flipped": [list(x) for x in self.appeals_flipped],
            "gaps_before": self.gaps_before, "gaps_after": self.gaps_after,
            "structural": self.structural, "cosmetic": self.cosmetic,
        }


def _edge_key(source: str, target: str, kind: str) -> str:
    return f"{source}-{kind}->{target}"


def diff_graphs(before: Graph, after: Graph) -> GraphDiff:
    old, new = before.by_id(), after.by_id()
    d = GraphDiff(
        nodes_added=sorted(set(new) - set(old)),
        nodes_removed=sorted(set(old) - set(new)),
        gaps_before=len(before.of_type("!")),
        gaps_after=len(after.of_type("!")),
    )
    for node_id in sorted(set(old) & set(new)):
        a, b = old[node_id], new[node_id]
        if a.label.strip() != b.label.strip():
            d.relabelled.append(node_id)
        if normalise(a.quote) != normalise(b.quote):
            d.requoted.append(node_id)
        if a.description.strip() != b.description.strip():
            d.rewritten.append(node_id)

    old_edges = {_edge_key(e.source, e.target, e.type): e for e in before.edges}
    new_edges = {_edge_key(e.source, e.target, e.type): e for e in after.edges}
    d.edges_added = sorted(set(new_edges) - set(old_edges))
    d.edges_removed = sorted(set(old_edges) - set(new_edges))
    for key in sorted(set(old_edges) & set(new_edges)):
        if old_edges[key].justification.strip() != new_edges[key].justification.strip():
            d.rejustified.append(key)

    old_appeals = {a.line: a.verdict for a in before.appeals}
    new_appeals = {a.line: a.verdict for a in after.appeals}
    d.appeals_added = sorted(set(new_appeals) - set(old_appeals))
    # Withdrawing a verdict is as much a change to what the companion claims as
    # recording one. Left out, a round that quietly dropped an inconvenient
    # "gap" would have read as having changed nothing at all.
    d.appeals_removed = sorted(set(old_appeals) - set(new_appeals))
    d.appeals_flipped = [
        (line, old_appeals[line], new_appeals[line])
        for line in sorted(set(old_appeals) & set(new_appeals))
        if old_appeals[line] != new_appeals[line]
    ]
    return d


@dataclass(slots=True)
class TextDiff:
    words_before: int = 0
    words_after: int = 0
    sections_added: list[str] = field(default_factory=list)
    sections_removed: list[str] = field(default_factory=list)
    sections_changed: list[str] = field(default_factory=list)
    unchanged: bool = False

    @property
    def growth(self) -> int:
        return self.words_after - self.words_before

    def as_dict(self) -> dict:
        return {
            "words_before": self.words_before, "words_after": self.words_after,
            "growth": self.growth,
            "sections_added": self.sections_added,
            "sections_removed": self.sections_removed,
            "sections_changed": self.sections_changed,
            "unchanged": self.unchanged,
        }


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


def _sections(text: str) -> dict[str, str]:
    """Body text by heading. Anything before the first heading is the preamble."""
    out: dict[str, list[str]] = {"(preamble)": []}
    current = "(preamble)"
    for line in text.splitlines():
        if (m := _HEADING.match(line)):
            current = m.group(1).strip()
            out.setdefault(current, [])
        else:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def diff_text(before: str, after: str) -> TextDiff:
    old, new = _sections(before), _sections(after)
    return TextDiff(
        words_before=len(before.split()),
        words_after=len(after.split()),
        sections_added=sorted(set(new) - set(old)),
        sections_removed=sorted(set(old) - set(new)),
        sections_changed=sorted(k for k in set(old) & set(new)
                                if old[k] != new[k]),
        unchanged=before == after,
    )


def summarise(graph_diff: GraphDiff, text_diff: TextDiff) -> str:
    """One line a human can read in the run record."""
    if graph_diff.structural == 0 and text_diff.unchanged:
        return "nothing changed"
    bits = []
    if graph_diff.nodes_added:
        bits.append(f"+{len(graph_diff.nodes_added)} nodes")
    if graph_diff.nodes_removed:
        bits.append(f"-{len(graph_diff.nodes_removed)} nodes")
    if graph_diff.edges_added or graph_diff.edges_removed:
        bits.append(f"{len(graph_diff.edges_added):+d}/"
                    f"{-len(graph_diff.edges_removed):+d} edges")
    if graph_diff.requoted:
        bits.append(f"{len(graph_diff.requoted)} requoted")
    if graph_diff.appeals_flipped:
        bits.append(f"{len(graph_diff.appeals_flipped)} verdicts changed")
    if graph_diff.appeals_removed:
        bits.append(f"{len(graph_diff.appeals_removed)} verdicts withdrawn")
    if graph_diff.gaps_after != graph_diff.gaps_before:
        bits.append(f"gaps {graph_diff.gaps_before}->{graph_diff.gaps_after}")
    if text_diff.growth:
        bits.append(f"guide {text_diff.growth:+d} words")

    head = ", ".join(bits) or "prose edits only"
    if graph_diff.structural == 0:
        # Stated, not judged. A finding about what the reader is told is fixed in
        # the guide and nowhere else, and that is the common case; whether an
        # unchanged graph is a problem depends on what was claimed to be fixed,
        # which is a question for whoever is holding the dispositions.
        return f"{head}; the graph is unchanged"
    return head
