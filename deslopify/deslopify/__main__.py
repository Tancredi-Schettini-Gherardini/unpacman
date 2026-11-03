"""deslopify — check and query an argument graph.

    python -m deslopify check    GRAPH [--document DOC]
    python -m deslopify summary  MARKDOWN --pdf OUTPUT
    python -m deslopify companion GUIDE --markdown OUTPUT --tex OUTPUT --pdf OUTPUT --audience LEVEL
    python -m deslopify view     GRAPH [--quotes]
    python -m deslopify map      GRAPH [--mermaid | --ascii | --pdf OUTPUT]
    python -m deslopify why      GRAPH NODE     what was this introduced for?
    python -m deslopify uses     GRAPH NODE     where is this used?
    python -m deslopify without  GRAPH NODE     what fails without this?
    python -m deslopify between  GRAPH A B      how do these two relate?

Working out loud, for the critique loop:

    python -m deslopify coverage GRAPH GUIDE        what the guide owes the graph
    python -m deslopify review   REVIEW  --graph G --guide GD
    python -m deslopify dispose  REVIEW  DISPOSITIONS --graph G --guide GD
    python -m deslopify diff     BEFORE AFTER       what a round changed

`check`, `review` and `dispose` exit non-zero when their file is invalid, so
each can gate a session. `coverage` and `diff` only report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import analyse, downstream, paths, upstream, without
from .compare import diff_graphs, diff_text, summarise
from .coverage import coverage
from .graph import Graph, GraphError, validate
from .lint import find_appeals, gap_warning
from .render import PdfRenderError, to_ascii_map, to_compact_text, to_mermaid, write_pdf
from .review import (Dispositions, Review, validate_dispositions,
                     validate_review)
from .summary_pdf import SummaryPdfError, write_companion, write_summary_pdf
from .source import SourceError, read_source


def _load(path: str) -> tuple[Graph, Path]:
    p = Path(path).expanduser()
    return Graph.load(p), p


def _document_text(graph: Graph, graph_path: Path, override: str | None) -> str | None:
    """The document the graph claims to describe, found relative to the graph."""
    for candidate in ([override] if override else []) + [graph.document]:
        if not candidate:
            continue
        p = Path(candidate).expanduser()
        for resolved in (p, graph_path.parent / p, graph_path.parent.parent / p):
            if resolved.is_file():
                try:
                    return read_source(resolved)
                except SourceError:
                    # A stale or unsupported override should not hide the
                    # graph's own usable document path. Try every candidate
                    # before reporting that the document could not be read.
                    continue
    return None


def _describe(graph: Graph, ids) -> list[str]:
    by_id = graph.by_id()
    return [f"  {i:<4} {by_id[i].label}" for i in sorted(ids) if i in by_id]


def _read(path: str | None) -> str | None:
    if not path:
        return None
    f = Path(path).expanduser()
    if not f.is_file():
        return None
    try:
        return read_source(f)
    except SourceError:
        return None


def _report(problems, *, ok: str) -> int:
    """Print a list of Problems the way `check` does, and exit accordingly."""
    for prob in problems:
        print(f"  {prob}")
    errors = [p for p in problems if p.severity == "error"]
    if errors:
        print(f"\n{len(errors)} error(s), {len(problems) - len(errors)} warning(s)")
        return 1
    if problems:
        print(f"\n  {ok} ({len(problems)} warning(s))")
    else:
        print(f"  {ok}")
    return 0


def _loop_command(args) -> int:
    """The four commands the critique loop runs between passes."""
    if args.cmd == "coverage":
        graph, graph_path = _load(args.graph)
        guide = _read(args.guide)
        if guide is None:
            print(f"error: no guide at {args.guide}", file=sys.stderr)
            return 2
        result = coverage(graph, guide)
        if result.observations:
            print(f"  {len(result.observations)} observation(s):\n")
            for obs in result.observations:
                print(f"  {obs}")
            print()
        else:
            print("  the guide accounts for everything the graph found\n")
        print(f"  {result.words:,} words, {len(result.sections)} sections, "
              f"{result.questions} question(s), "
              f"{len(result.referenced)} node(s) referenced of {len(graph.nodes)}")
        # Advisory by design: an unusual heading is not a reason to wedge a
        # session, and these are handed to a reviewer as a starting list rather
        # than enforced as a contract.
        return 0

    if args.cmd == "diff":
        before, _ = _load(args.before)
        after, _ = _load(args.after)
        gd = diff_graphs(before, after)
        guide_before = _read(args.guide_before) or ""
        guide_after = _read(args.guide_after) or ""
        td = diff_text(guide_before, guide_after)
        print(f"  {summarise(gd, td)}\n")
        for label, items in (("added", gd.nodes_added), ("removed", gd.nodes_removed),
                             ("requoted", gd.requoted), ("relabelled", gd.relabelled)):
            if items:
                print(f"  nodes {label}: {', '.join(items)}")
        for label, items in (("added", gd.edges_added), ("removed", gd.edges_removed)):
            if items:
                print(f"  edges {label}: {', '.join(items)}")
        for line, was, now in gd.appeals_flipped:
            print(f"  appeal at line {line}: {was} -> {now}")
        if td.sections_changed:
            print(f"  guide sections changed: {', '.join(td.sections_changed)}")
        return 0

    # review / dispose --------------------------------------------------
    review = Review.load(Path(args.review).expanduser())
    graph = document = None
    if args.graph:
        graph, graph_path = _load(args.graph)
        document = _document_text(graph, graph_path, args.document)
    elif args.document:
        document = _read(args.document)
    guide = _read(args.guide)

    if document is None:
        print("warning: the document could not be found, so quotations in this "
              "critique were NOT verified", file=sys.stderr)

    if args.cmd == "review":
        problems = validate_review(review, graph=graph, guide=guide, document=document)
        blocking = len(review.at_least("blocking"))
        summary = (f"{len(review.findings)} finding(s), {blocking} blocking"
                   if review.findings else
                   "no findings — the critique says the companion holds up")
        return _report(problems, ok=f"ok — {summary}")

    dispositions = Dispositions.load(Path(args.dispositions).expanduser())
    before = None
    if args.before_graph and Path(args.before_graph).expanduser().is_file():
        before, _ = _load(args.before_graph)
    problems = validate_dispositions(review, dispositions, graph=graph,
                                     guide=guide, document=document,
                                     before=before,
                                     before_guide=_read(args.before_guide))
    fixed = sum(1 for d in dispositions.items if d.disposition == "fixed")
    rejected = sum(1 for d in dispositions.items if d.disposition == "rejected")
    return _report(problems,
                   ok=f"ok — {fixed} fixed, {rejected} rejected, "
                      f"{len(review.findings)} answered of {len(review.findings)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deslopify", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="validate the graph, including its quotations")
    p.add_argument("graph")
    p.add_argument("--document", default=None,
                   help="the document it describes (default: read from the graph)")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.add_argument("--allow-missing-document", action="store_true",
                   help="check the structure alone, without verifying quotations")

    p = sub.add_parser("appeals", help="where the document stands a phrase in for an argument")
    p.add_argument("document")

    p = sub.add_parser("summary", help="compile a Markdown paper summary to PDF")
    p.add_argument("markdown")
    p.add_argument("--pdf", metavar="OUTPUT", required=True)
    p.add_argument("--title", default="Paper summary")

    p = sub.add_parser("companion", help="compile the guide under its reader-facing name")
    p.add_argument("guide")
    p.add_argument("--markdown", metavar="OUTPUT", required=True)
    p.add_argument("--tex", metavar="OUTPUT", required=True,
                   help="persistent LaTeX source from which the PDF is compiled")
    p.add_argument("--pdf", metavar="OUTPUT", required=True)
    p.add_argument("--audience", choices=("expert", "non-expert"), required=True)
    p.add_argument("--paper-title", default=None,
                   help="paper title (default: infer it from the guide's first H1)")

    p = sub.add_parser("view", help="compact text view, as an agent should see it")
    p.add_argument("graph")
    p.add_argument("--quotes", action="store_true")

    p = sub.add_parser("map", help="a drawing of the argument")
    p.add_argument("graph")
    output = p.add_mutually_exclusive_group()
    output.add_argument("--mermaid", action="store_true")
    output.add_argument("--ascii", action="store_true")
    output.add_argument("--pdf", metavar="OUTPUT",
                        help="render a standalone PDF directly to this path")

    for name, help_text in (("why", "what was this introduced for?"),
                            ("uses", "where is this used?"),
                            ("without", "what fails without this?")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("graph")
        p.add_argument("node")

    p = sub.add_parser("between", help="how do these two relate?")
    p.add_argument("graph")
    p.add_argument("source")
    p.add_argument("target")

    p = sub.add_parser("coverage", help="what the guide owes the graph")
    p.add_argument("graph")
    p.add_argument("guide")

    p = sub.add_parser("review", help="validate a critique")
    p.add_argument("review")
    p.add_argument("--graph", default=None)
    p.add_argument("--guide", default=None)
    p.add_argument("--document", default=None)

    p = sub.add_parser("dispose", help="check that every finding was answered")
    p.add_argument("review")
    p.add_argument("dispositions")
    p.add_argument("--graph", default=None)
    p.add_argument("--guide", default=None)
    p.add_argument("--document", default=None)
    p.add_argument("--before-graph", default=None,
                   help="the graph as the critique found it, so that a fix may "
                        "name the node or edge it removed")
    p.add_argument("--before-guide", default=None)

    p = sub.add_parser("diff", help="what a round of revision changed")
    p.add_argument("before", help="the graph as it was")
    p.add_argument("after", help="the graph as it is")
    p.add_argument("--guide-before", default=None)
    p.add_argument("--guide-after", default=None)

    args = ap.parse_args(argv)

    if args.cmd == "summary":
        try:
            path = write_summary_pdf(args.markdown, args.pdf, title=args.title)
        except SummaryPdfError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"  wrote {path}")
        return 0

    if args.cmd == "companion":
        try:
            markdown, tex, pdf = write_companion(
                args.guide, args.markdown, args.tex, args.pdf,
                audience=args.audience, paper_title=args.paper_title)
        except SummaryPdfError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"  wrote {markdown}\n  wrote {tex}\n  wrote {pdf}")
        return 0

    if args.cmd == "appeals":
        try:
            text = read_source(Path(args.document))
        except SourceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        found = find_appeals(text)
        if not found:
            print("  none — every step is argued on the page")
            return 0
        print(f"  {len(found)} place(s) where a phrase stands in for an argument:\n")
        for a in found:
            print(f"  line {a.line:<5} {a.why}")
            print(f"    \u201c{a.text}\u201d\n")
        print("  For each, the question is NOT 'would that method work?' but:")
        print()
        print("    Could a competent reader reconstruct the omitted work in their")
        print("    head, right now, without real labour?")
        print()
        print("  Yes -> routine.  No -> gap, however sound the named method is.")
        print("  Checking 100 integers by trial division is a sound method and is")
        print("  not something anyone reconstructs in their head; that is a gap,")
        print("  and saying so is not a criticism of trial division.")
        return 0

    if args.cmd in ("coverage", "review", "dispose", "diff"):
        try:
            return _loop_command(args)
        except GraphError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        graph, graph_path = _load(args.graph)
    except GraphError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "check":
        document = _document_text(graph, graph_path, args.document)
        if document is None and not args.allow_missing_document:
            # Not a warning. The `document` field is written by whoever wrote
            # the graph, so a graph that names a file which does not exist had
            # every quotation in it accepted unread — and the check exited 0,
            # which is what a Stop hook reads as "grounded". Naming the wrong
            # file is the cheapest possible way to defeat the one guarantee this
            # package makes, and it must not be a warning on stderr.
            print(f"  [error] graph: the document it claims to describe "
                  f"({graph.document or 'unnamed'}) cannot be found, so not one "
                  f"quotation was verified. Point `document` at the real file, "
                  f"or pass --document. Grounding is the check that matters "
                  f"most; without it this graph asserts nothing.")
            print("\n1 error(s), 0 warning(s)")
            return 1
        problems = validate(graph, document)
        if document is not None:
            warning = gap_warning(find_appeals(document),
                                  len(graph.of_type("!")))
            if warning:
                from .graph import Problem as _P
                problems.append(_P("warning", "grounding", warning))
        errors = [p for p in problems if p.severity == "error"]
        for prob in problems:
            print(f"  {prob}")
        kept = errors if not args.strict else problems
        if kept:
            print(f"\n{len(errors)} error(s), "
                  f"{len(problems) - len(errors)} warning(s)")
            return 1
        print(f"  ok — {len(graph.nodes)} nodes, {len(graph.edges)} edges, "
              f"every quotation found in the document"
              if document is not None else
              f"  ok — {len(graph.nodes)} nodes, {len(graph.edges)} edges "
              f"(quotations unverified)")
        return 0

    result = analyse(graph)
    by_id = graph.by_id()

    if args.cmd == "view":
        print(to_compact_text(graph, result, quotes=args.quotes), end="")
        return 0

    if args.cmd == "map":
        if args.pdf:
            try:
                path = write_pdf(graph, args.pdf, result)
            except PdfRenderError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"  wrote {path}")
            return 0
        print(to_mermaid(graph, result) if args.mermaid
              else to_ascii_map(graph, result), end="")
        return 0

    if args.cmd in ("why", "uses", "without") and args.node not in by_id:
        print(f"error: {args.node} is not a node. Known: "
              f"{', '.join(sorted(by_id))}", file=sys.stderr)
        return 2

    if args.cmd == "why":
        node = by_id[args.node]
        print(f"{node.id}  {node.label}")
        served = [e for e in graph.out_edges(node.id)]
        if not served:
            print("\n  Nothing. It is introduced and then used for nothing —")
            print("  which is a finding about the document, not about the graph.")
            return 0
        print("\n  It serves:")
        for e in served:
            arrow = {"SUPPORTS": "supports", "MOTIVATES": "motivates",
                     "CONTRADICTS": "contradicts"}.get(e.type, e.type.lower())
            print(f"    {arrow} {e.target}  {by_id[e.target].label}")
            print(f"      because {e.justification}")
        return 0

    if args.cmd == "uses":
        cone = downstream(graph, args.node)
        print(f"{args.node}  {by_id[args.node].label}")
        print(f"\n  Used by {len(cone)} step(s):" if cone else
              "\n  Used by nothing.")
        print("\n".join(_describe(graph, cone)))
        if rests := upstream(graph, args.node):
            print(f"\n  It rests on {len(rests)}:")
            print("\n".join(_describe(graph, rests)))
        return 0

    if args.cmd == "without":
        lost = without(graph, args.node)
        print(f"Removing {args.node}  ({by_id[args.node].label})")
        if not lost:
            print("\n  Nothing loses support. The argument does not need it,")
            print("  at least not along any chain the document makes explicit.")
            return 0
        print(f"\n  {len(lost)} step(s) lose their support:")
        print("\n".join(_describe(graph, lost)))
        return 0

    if args.cmd == "between":
        for end in (args.source, args.target):
            if end not in by_id:
                print(f"error: {end} is not a node", file=sys.stderr)
                return 2
        forward = paths(graph, args.source, args.target)
        backward = paths(graph, args.target, args.source)
        if not forward and not backward:
            print(f"{args.source} and {args.target} are independent: no chain of "
                  f"reasoning in the document runs from either to the other.")
            return 0
        for route in forward or backward:
            print("  " + " -> ".join(route))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
