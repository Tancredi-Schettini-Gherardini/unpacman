"""Keep source remarks before the mathematics in every companion edition."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

_LABEL = re.compile(r"^\*\*Remark\s*\(source\s+and\s+scope\)\.?\*\*\.?", re.I)
_PAPER = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?\s+)?Paper\s+\d+:", re.I)
_MAIN = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?\s+)?Main (?:results|findings)$", re.I)


def _place(text: str, headings: list[tuple[int, int, int, str]],
           remarks: list[tuple[int, int]]) -> str:
    papers = [h for h in headings if _PAPER.match(h[3])]
    containers = [(h[1], papers[i + 1][0] if i + 1 < len(papers) else len(text), h[2])
                  for i, h in enumerate(papers)] if papers else [(0, len(text), 0)]
    edits = []
    for start, end, level in containers:
        target = next((h[0] for h in headings if start <= h[0] < end and
                       (h[2] > level if papers else _MAIN.fullmatch(h[3]))), None)
        blocks = [(a, b) for a, b in remarks if start <= a < b <= end]
        if target is None or not blocks:
            continue
        # Leave an already correctly placed remark byte-for-byte unchanged.
        if blocks[-1][1] <= target and not text[blocks[-1][1]:target].strip() and all(
                not text[a[1]:b[0]].strip() for a, b in zip(blocks, blocks[1:])):
            continue
        edits.extend((a, b, "") for a, b in blocks)
        edits.append((target, target, "\n\n".join(text[a:b].strip() for a, b in blocks) + "\n\n"))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def place_source_remarks(markdown: str) -> str:
    """Move only labelled, standalone paragraphs; keep each paper's own text."""
    if not re.search(r"source\s+and\s+scope", markdown, re.I):
        return markdown
    lines = markdown.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    tokens = MarkdownIt("commonmark").use(dollarmath_plugin).parse(markdown)
    headings, remarks = [], []
    for i, token in enumerate(tokens[:-1]):
        if token.level != 0 or token.map is None:
            continue
        start, end = (offsets[n] for n in token.map)
        if token.type == "heading_open":
            headings.append((start, end, int(token.tag[1:]), tokens[i + 1].content.strip()))
        elif token.type == "paragraph_open" and _LABEL.match(tokens[i + 1].content):
            remarks.append((start, end))
    return _place(markdown, headings, remarks)


_TEX_LABEL = r"\\textbf\{Remark\s*\(source\s+and\s+scope\)\.?\}\.?"
_TEX_HEADING = re.compile(
    r"^\\(?P<kind>(?:sub){0,2}section|paragraph)\*?\{(?P<title>[^\n]+)\}[ \t]*$", re.M)
_TEX_BOX = re.compile(
    r"^\\begin\{(?P<env>statement\w+)\}\s*" + _TEX_LABEL +
    r"[\s\S]*?\\end\{(?P=env)\}[ \t]*\n?", re.M | re.I)
_TEX_PARAGRAPH = re.compile(
    "^" + _TEX_LABEL + r"[^\n]*(?:\n(?!\s*\n|\\(?:sub)*section|\\end\{)[^\n]+)*\n?", re.M | re.I)


def place_source_remarks_tex(document: str) -> str:
    """Upgrade stored LaTeX without regenerating its mathematics or figures."""
    if not re.search(r"source\s+and\s+scope", document, re.I):
        return document
    _, begin, body = document.partition(r"\begin{document}")
    if not begin:
        return document
    offset = len(document) - len(body)
    ignored = [(m.start(), m.end()) for m in re.finditer(
        r"\\begin\{(?P<env>verbatim\*?|lstlisting|minted|quote)\}[\s\S]*?\\end\{(?P=env)\}", document)]
    def visible(start: int) -> bool:
        return start >= offset and not any(a <= start < b for a, b in ignored)
    headings = [(m.start(), m.end(), ("section", "subsection", "subsubsection", "paragraph").index(m["kind"]) + 1,
                 m["title"].strip()) for m in _TEX_HEADING.finditer(document) if visible(m.start())]
    remarks = [(m.start(), m.end()) for m in _TEX_BOX.finditer(document) if visible(m.start())]
    remarks += [(m.start(), m.end()) for m in _TEX_PARAGRAPH.finditer(document)
                if visible(m.start()) and not any(a <= m.start() < b for a, b in remarks)]
    return _place(document, headings, sorted(remarks))
