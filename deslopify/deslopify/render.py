"""Turning the graph into something a reader — or a model — can actually read.

An agent does not see a graph, it sees a string, and the string matters. Across
six encodings and three frontier models the Syrup thesis found compact text best
on the accuracy-token frontier and JSON dominated: more expensive *and* less
accurate. So the agent-facing view here is compact text with centrality folded
in, and JSON stays on disk as the record rather than as the view.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from .analysis import Analysis, SUPPORT, _adjacency
from .graph import Graph, NODE_TYPES

_ARROW = {"SUPPORTS": "->", "MOTIVATES": "~>", "CONTRADICTS": "!>"}


def layers(graph: Graph) -> list[list[str]]:
    """Longest-path layering, so support always flows downward on the page."""
    incoming = _adjacency(graph, kinds=(SUPPORT,), reverse=True)
    depth: dict[str, int] = {}

    def settle(node: str, trail: frozenset[str] = frozenset()) -> int:
        if node in depth:
            return depth[node]
        if node in trail:                       # defensive: validate() rejects cycles
            return 0
        parents = incoming.get(node, set())
        depth[node] = 0 if not parents else 1 + max(
            settle(p, trail | {node}) for p in parents)
        return depth[node]

    for n in graph.nodes:
        settle(n.id)
    out: list[list[str]] = []
    for node_id, d in sorted(depth.items(), key=lambda kv: (kv[1], kv[0])):
        while len(out) <= d:
            out.append([])
        out[d].append(node_id)
    return out


def to_compact_text(graph: Graph, analysis: Analysis | None = None,
                    quotes: bool = False) -> str:
    """The agent-facing view: every node on one line, then every edge."""
    lines: list[str] = [f"DOCUMENT: {graph.document}",
                        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges"]
    cent = (analysis.centrality if analysis else {})
    crux = set(analysis.crux) if analysis else set()

    lines.append("")
    lines.append("NODES  (id  role  centrality  label)")
    for n in sorted(graph.nodes, key=lambda x: (x.id[0], int(x.id[1:] or 0))):
        marks = []
        if n.id in crux:
            marks.append("CRUX")
        if n.uncertain:
            marks.append("uncertain")
        if n.parent:
            marks.append(f"in {n.parent}")
        suffix = ("  [" + ", ".join(marks) + "]") if marks else ""
        lines.append(f"  {n.id:<4} {n.role:<12} {cent.get(n.id, 0):.2f}  "
                     f"{n.label}{suffix}")
        if quotes and n.quote:
            lines.append(f"       “{n.quote.strip()}”")

    lines.append("")
    lines.append("EDGES  (-> supports, ~> motivates, !> contradicts)")
    for e in graph.edges:
        mark = " [inferred]" if e.uncertain else ""
        lines.append(f"  {e.source} {_ARROW.get(e.type, '->')} {e.target}"
                     f"   {e.justification}{mark}")

    if analysis:
        lines.append("")
        lines.append("STRUCTURE")
        if analysis.total:
            lines.append(f"  focus        {analysis.on_topic}/{analysis.total} nodes "
                         f"({analysis.focus:.0%}) reach the goal or a conclusion")
        if analysis.crux:
            lines.append(f"  crux         {', '.join(analysis.crux)}")
        lines.extend(_findings(analysis))
    return "\n".join(lines) + "\n"


def _findings(analysis: Analysis, threshold: int = 4) -> list[str]:
    """One line each, until a kind fires so often that listing it is noise.

    A finding repeated twenty-three times stops being twenty-three findings and
    becomes one fact about the document. Reported as such, it is the most
    useful line in the output; reported one per node, it buries everything else.
    """
    out: list[str] = []
    grouped: dict[str, list] = {}
    for f in analysis.findings:
        grouped.setdefault(f.kind, []).append(f)
    for kind, group in grouped.items():
        if len(group) < threshold:
            out.extend(f"  {kind:<12} {f.message}" for f in group)
            continue
        ids = ", ".join(f.node_ids[0] for f in group)
        out.append(f"  {kind:<12} {len(group)} nodes: {ids}")
        out.append(f"  {'':<12} {_summary(kind, len(group), analysis)}")
    return out


def _summary(kind: str, count: int, analysis: Analysis) -> str:
    if kind == "adrift":
        return (f"-> {count} of {analysis.total} nodes are not part of the argument "
                f"this document claims to make")
    if kind == "unsupported":
        return f"-> {count} assertions rest on nothing stated in the document"
    if kind == "unused":
        return f"-> {count} things are introduced and never used"
    if kind == "uncertain":
        return f"-> {count} readings were inferred rather than read"
    return f"-> {count} occurrences"


def to_ascii_map(graph: Graph, analysis: Analysis | None = None) -> str:
    """A layered map that survives anywhere — a terminal, a PDF, a plain file."""
    crux = set(analysis.crux) if analysis else set()
    by_id = graph.by_id()
    out: list[str] = []
    for depth, row in enumerate(layers(graph)):
        if not [i for i in row if not i.startswith("!")]:
            continue
        out.append(f"  level {depth}")
        for node_id in row:
            n = by_id.get(node_id)
            if n is None or n.id[0] == "!":
                continue          # gaps annotate paths; they get their own list
            mark = " *CRUX*" if node_id in crux else ""
            label = n.label if len(n.label) <= 62 else n.label[:59] + "..."
            out.append(f"    {node_id:<4} {label}{mark}")
        out.append("")
    if gaps := graph.of_type("!"):
        out.append("  gaps")
        for g in gaps:
            out.append(f"    {g.id:<4} {g.label}")
        out.append("")
    return "\n".join(out)


def to_mermaid(graph: Graph, analysis: Analysis | None = None) -> str:
    """Mermaid source, for anywhere that renders it."""
    crux = set(analysis.crux) if analysis else set()
    shape = {"H": ("([", "])"), "G": ("{{", "}}"), "X": ("[", "]"),
             "M": ("[/", "/]"), "C": ("[", "]"), "Q": ("((", "))"),
             "!": (">", "]")}
    style = {"H": "hyp", "G": "goal", "X": "con", "M": "mov",
             "C": "claim", "Q": "concl", "!": "gap"}

    out = ["graph TD"]
    for n in graph.nodes:
        open_b, close_b = shape.get(n.id[0], ("[", "]"))
        label = n.label.replace('"', "'").replace("\n", " ")
        if len(label) > 58:
            label = label[:55] + "..."
        out.append(f'  {_safe(n.id)}{open_b}"{n.id}: {label}"{close_b}')
    for e in graph.edges:
        arrow = {"SUPPORTS": "-->", "MOTIVATES": "-.->", "CONTRADICTS": "==>"}.get(
            e.type, "-->")
        out.append(f"  {_safe(e.source)} {arrow} {_safe(e.target)}")
    out.append("")
    for n in graph.nodes:
        out.append(f"  class {_safe(n.id)} {style.get(n.id[0], 'claim')};")
        if n.id in crux:
            out.append(f"  class {_safe(n.id)} crux;")
    out += [
        "  classDef hyp fill:#eef4ff,stroke:#5b7fbe;",
        "  classDef goal fill:#f3eeff,stroke:#7a5bbe;",
        "  classDef con fill:#fff8e8,stroke:#b8922f;",
        "  classDef mov fill:#f2f7ee,stroke:#6f9a4e;",
        "  classDef claim fill:#ffffff,stroke:#888888;",
        "  classDef concl fill:#eafaf0,stroke:#3d9970;",
        "  classDef gap fill:#fdecec,stroke:#c0392b,stroke-dasharray:4 3;",
        "  classDef crux stroke-width:3px;",
    ]
    return "\n".join(out) + "\n"


class PdfRenderError(RuntimeError):
    """The graph could not be rendered to PDF."""


def to_tikz(graph: Graph, analysis: Analysis | None = None) -> str:
    """A self-contained TikZ document for the argument graph.

    The layout uses the same support layers as the text and Mermaid views.  It
    deliberately uses only TeX Live's standard TikZ package: PDF production is
    therefore local and offline, with no Mermaid/Node installation involved.
    """
    crux = set(analysis.crux) if analysis else set()
    gap_ids = [node.id for node in graph.of_type("!")]
    rows = [[node_id for node_id in row if not node_id.startswith("!")]
            for row in layers(graph)]
    rows = [row for row in rows if row]
    if gap_ids:
        rows.append(gap_ids)
    positions: dict[str, tuple[float, float]] = {}
    widest = max((len(row) for row in rows), default=1)
    x_step, y_step = 5.3, 3.0
    for depth, row in enumerate(rows):
        offset = (widest - len(row)) * x_step / 2
        for column, node_id in enumerate(row):
            positions[node_id] = (offset + column * x_step, -depth * y_step)

    node_colours = {
        "H": "hyp", "G": "goal", "X": "construction", "M": "move",
        "C": "claim", "Q": "conclusion", "!": "gap",
    }
    out = [
        r"\documentclass[tikz,border=8pt]{standalone}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{tikz}",
        r"\usetikzlibrary{arrows.meta}",
        r"\begin{document}",
        r"\begin{tikzpicture}[>=Stealth, every node/.style={font=\sffamily}]",
        r"\tikzset{base/.style={draw, rounded corners=2pt, text width=4.35cm, "
        r"align=left, inner sep=6pt, minimum height=1.1cm}}",
        r"\tikzset{hyp/.style={base,fill=blue!7,draw=blue!55!black}}",
        r"\tikzset{goal/.style={base,fill=violet!7,draw=violet!65!black}}",
        r"\tikzset{construction/.style={base,fill=orange!9,draw=orange!60!black}}",
        r"\tikzset{move/.style={base,fill=green!7,draw=green!45!black}}",
        r"\tikzset{claim/.style={base,fill=white,draw=black!55}}",
        r"\tikzset{conclusion/.style={base,fill=green!12,draw=green!55!black}}",
        r"\tikzset{gap/.style={base,fill=red!8,draw=red!70!black,dashed}}",
    ]
    for index, node in enumerate(graph.nodes):
        x, y = positions.get(node.id, (0.0, 0.0))
        style = node_colours.get(node.id[0], "claim")
        if node.id in crux:
            style += ",line width=1.2pt"
        label = node.label.replace("\n", " ")
        if len(label) > 92:
            label = label[:89] + "..."
        text = rf"\textbf{{{_tex_text(node.id)}}} {_tex_text(label)}"
        out.append(rf"\node[{style}] (n{index}) at ({x:.2f},{y:.2f}) {{{text}}};")

    indices = {node.id: index for index, node in enumerate(graph.nodes)}
    for edge in graph.edges:
        if edge.source not in indices or edge.target not in indices:
            continue
        style = {
            "SUPPORTS": "->,black!65",
            "MOTIVATES": "->,dashed,green!45!black",
            "CONTRADICTS": "->,very thick,red!70!black",
        }.get(edge.type, "->,black!65")
        out.append(rf"\draw[{style}] (n{indices[edge.source]}) -- "
                   rf"(n{indices[edge.target]});")
    out.extend([r"\end{tikzpicture}", r"\end{document}", ""])
    return "\n".join(out)


def write_pdf(graph: Graph, output: Path | str,
              analysis: Analysis | None = None) -> Path:
    """Render *graph* as a one-page vector PDF at *output*."""
    engine = shutil.which("latexmk")
    if engine is None:
        raise PdfRenderError(
            "latexmk is required for --pdf but was not found. Install a TeX "
            "distribution, or continue to use --mermaid.")
    target = Path(output).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deslopify-map-") as raw_tmp:
        tmp = Path(raw_tmp)
        source = tmp / "map.tex"
        source.write_text(to_tikz(graph, analysis), encoding="ascii")
        try:
            result = subprocess.run(
                [engine, "-pdf", "-interaction=nonstopmode", "-halt-on-error",
                 f"-outdir={tmp}", str(source)],
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfRenderError("LaTeX timed out while rendering the graph") from exc
        built = tmp / "map.pdf"
        if result.returncode != 0 or not built.is_file():
            detail = "\n".join((result.stdout + result.stderr).splitlines()[-20:])
            raise PdfRenderError(f"LaTeX could not render the graph:\n{detail}")
        shutil.copy2(built, target)
    return target


def _tex_text(text: str) -> str:
    """Plain graph labels as safe, pdfLaTeX-compatible text."""
    replacements = {
        "–": "-", "—": "---", "‘": "'", "’": "'",
        "“": '"', "”": '"', "→": "->", "≤": "<=", "≥": ">=",
        "−": "-", "×": "x", "…": "...", " ": " ",
    }
    plain = "".join(replacements.get(ch, ch) for ch in text)
    plain = unicodedata.normalize("NFKD", plain).encode(
        "ascii", errors="backslashreplace").decode("ascii")
    escaped = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%",
        "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
        "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(escaped.get(ch, ch) for ch in plain)


def _safe(node_id: str) -> str:
    """`!` is not a Mermaid identifier; everything else already is."""
    return node_id.replace("!", "GAP")


def legend() -> str:
    return "  ".join(f"{k}={v}" for k, v in NODE_TYPES.items())
