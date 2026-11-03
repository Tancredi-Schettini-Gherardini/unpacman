"""Where a gap is likely to be hiding.

The quotation check catches invented evidence. It cannot catch the subtler
failure, which is *repair by explanation*: a companion explains a step so
plausibly that a hole closes, and the reader comes away believing the document
argued something it only asserted. That was observed on the first live run —
the document said "a direct search ... yields no other solutions" and never
showed one; the guide replied that trial division is exhaustive and reliable,
which is true of trial division and irrelevant to this document.

No checker can decide whether a step is genuinely justified. What it can do is
point at every place where mathematical English signals that work has been
skipped, and insist that the question was at least asked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that stand in for an argument. Each is perfectly legitimate when the
# omitted work really is routine; each is also exactly where a gap hides.
APPEALS: dict[str, str] = {
    r"exhaustive (?:computation|search|enumeration)": "a computation that is asserted",
    r"direct (?:search|computation|calculation|verification)": "a computation that is asserted",
    r"verified by (?:computation|computer|exhaustive)": "a computation that is asserted",
    r"(?:it is |it's )?(?:clear|obvious|evident)(?:ly)?\b": "asserted as self-evident",
    r"easy to (?:see|check|verify|show)": "asserted as self-evident",
    r"straightforward(?:ly)?": "asserted as routine",
    r"one can (?:check|verify|see|show)": "delegated to the reader",
    r"the reader (?:can|may|will) (?:check|verify)": "delegated to the reader",
    r"left (?:as an exercise|to the reader)": "delegated to the reader",
    r"\bsimilarly\b|\banalogously\b": "an argument reused without being repeated",
    r"by symmetry": "an argument reused without being repeated",
    r"\bwell[- ]known\b|\bstandard (?:result|argument|fact)\b": "an uncited appeal to authority",
    r"\bit follows (?:immediately|at once|directly)\b": "an inference with no visible step",
    # "trivial" is overwhelmingly a technical term in mathematics — a trivial
    # bundle, group, or line is not a hedge — so only its adverbial and
    # predicative uses are appeals. Flagging "the extra trivial line" as skipped
    # work, which the first version did, is noise that trains a reader to ignore
    # the report.
    r"\btrivially\b": "asserted as trivial",
    r"\b(?:is|are|was|were|it\s+is)\s+trivial\b(?!\s*(?:line|bundle|group|case))":
        "asserted as trivial",
    r"\b(?:we )?omit(?:ted)?\b": "an argument explicitly not given",
    r"\broutine\b": "asserted as routine",
    r"\\href\{[^}]*\}|https?://": "a source cited by link, with no statement given",
}
_PATTERNS = [(re.compile(p, re.I), why) for p, why in APPEALS.items()]


@dataclass(slots=True)
class Appeal:
    line: int
    text: str
    phrase: str
    why: str


def find_appeals(document: str, context: int = 90) -> list[Appeal]:
    """Every place the document stands in a phrase for an argument."""
    out: list[Appeal] = []
    for number, line in enumerate(document.splitlines(), start=1):
        if line.lstrip().startswith("%"):
            continue                        # a LaTeX comment is not the document
        for pattern, why in _PATTERNS:
            if (m := pattern.search(line)):
                snippet = line.strip()
                if len(snippet) > context:
                    start = max(0, m.start() - context // 3)
                    snippet = ("..." if start else "") + snippet[start:start + context] + "..."
                out.append(Appeal(number, snippet, m.group(0).strip(), why))
                break                       # one flag per line is enough
    return out


def gap_warning(appeals: list[Appeal], gap_nodes: int) -> str | None:
    """Ask whether the appeals were considered — not whether they are faults.

    A document may legitimately contain twenty routine appeals and no real gap.
    A graph that records none of them, though, has either judged each one or
    looked at none, and only one of those is a reading.
    """
    if not appeals or gap_nodes:
        return None
    return (f"the document contains {len(appeals)} place(s) where a phrase stands "
            f"in for an argument, and the graph records no gaps at all — run "
            f"`deslopify appeals` and decide, for each, whether the omitted work "
            f"is genuinely routine")
