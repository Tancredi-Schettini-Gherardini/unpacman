"""Markdown → LaTeX, for typesetting a session's answer.

A tool-less harness (`chat`) has no filesystem, so it can never write
`progress/math.tex` — but it still produces mathematics, and that mathematics
deserves to be read as a PDF like any other result. The app therefore typesets
the answer itself, after the session has ended, from text the model already
emitted. This grants the model no new capability and so leaves the `chat`
control condition intact.

Math is passed through untouched. That is the one thing this module must not get
wrong, so math and code are lifted out into a vault before any other rewriting
happens and put back only at the very end. Everything else is a best-effort
rendering of the Markdown that models actually write: headings, lists, emphasis,
inline code, fenced blocks, block quotes, rules and pipe tables.
"""

from __future__ import annotations

import re
import unicodedata

# A single escaping pass over these, so nothing is ever escaped twice.
# `_` is deliberately absent: it is a Markdown emphasis marker as well as a
# LaTeX special, so it is escaped after emphasis has been resolved.
_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "$": r"\$",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_SPECIAL_RE = re.compile("[" + re.escape("".join(_SPECIALS)) + "]")

# NUL cannot occur in model output, so it makes a safe placeholder delimiter.
_MARK = "\x00"
_MARK_RE = re.compile(rf"{_MARK}(\d+){_MARK}")

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_IMAGE = re.compile(
    r'^\s*!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)\s*$')
_CITATION = re.compile(r"(?<![!A-Za-z0-9_/#])\[([A-Za-z][A-Za-z0-9+.-]*\d{2}[a-z]?)\](?!\()")

_SECTIONS = [r"\section*", r"\subsection*", r"\subsubsection*", r"\paragraph*"]

# pdflatex with utf8 inputenc dies on any character outside Latin-1, and models
# emit ✓, →, ≤ and Greek letters constantly — a single ✗ in a summary table is
# enough to lose the whole PDF. `\ensuremath` is used throughout so one table
# serves both prose and formulae.
def _e(macro: str) -> str:
    return rf"\ensuremath{{{macro}}}"


_UNICODE_TEX: dict[str, str] = {
    # marks and bullets
    "✓": _e(r"\checkmark"), "✔": _e(r"\checkmark"), "☑": _e(r"\checkmark"),
    "✗": _e(r"\times"), "✘": _e(r"\times"), "✕": _e(r"\times"), "☒": _e(r"\times"),
    "✦": _e(r"\star"), "★": _e(r"\bigstar"), "☆": _e(r"\star"),
    "•": r"\textbullet{}", "◦": _e(r"\circ"), "·": _e(r"\cdot"),
    "∎": _e(r"\blacksquare"), "■": _e(r"\blacksquare"), "□": _e(r"\square"),
    "†": r"\dagger{}", "‡": r"\ddagger{}",
    # arrows
    "→": _e(r"\to"), "←": _e(r"\leftarrow"), "↔": _e(r"\leftrightarrow"),
    "⇒": _e(r"\Rightarrow"), "⇐": _e(r"\Leftarrow"), "⇔": _e(r"\Leftrightarrow"),
    "↦": _e(r"\mapsto"), "↪": _e(r"\hookrightarrow"), "↩": _e(r"\hookleftarrow"),
    "↑": _e(r"\uparrow"), "↓": _e(r"\downarrow"), "⟶": _e(r"\longrightarrow"),
    "⟵": _e(r"\longleftarrow"), "⟹": _e(r"\Longrightarrow"),
    # relations
    "≤": _e(r"\leq"), "≥": _e(r"\geq"), "≠": _e(r"\neq"), "≈": _e(r"\approx"),
    "≡": _e(r"\equiv"), "∼": _e(r"\sim"), "≅": _e(r"\cong"), "≃": _e(r"\simeq"),
    "∝": _e(r"\propto"), "≪": _e(r"\ll"), "≫": _e(r"\gg"), "≺": _e(r"\prec"),
    "≻": _e(r"\succ"), "⊥": _e(r"\perp"), "∥": _e(r"\parallel"),
    # operators
    "±": _e(r"\pm"), "∓": _e(r"\mp"), "×": _e(r"\times"), "÷": _e(r"\div"),
    "⋅": _e(r"\cdot"), "∘": _e(r"\circ"), "⊕": _e(r"\oplus"), "⊗": _e(r"\otimes"),
    "⊙": _e(r"\odot"), "∗": _e(r"\ast"), "√": _e(r"\sqrt{\;}"), "∂": _e(r"\partial"),
    "∇": _e(r"\nabla"), "∞": _e(r"\infty"), "∑": _e(r"\sum"), "∏": _e(r"\prod"),
    "∫": _e(r"\int"), "∮": _e(r"\oint"), "⨁": _e(r"\bigoplus"), "⨂": _e(r"\bigotimes"),
    # sets and logic
    "∈": _e(r"\in"), "∉": _e(r"\notin"), "∋": _e(r"\ni"), "⊂": _e(r"\subset"),
    "⊆": _e(r"\subseteq"), "⊃": _e(r"\supset"), "⊇": _e(r"\supseteq"),
    "∪": _e(r"\cup"), "∩": _e(r"\cap"), "∅": _e(r"\emptyset"), "∖": _e(r"\setminus"),
    "∀": _e(r"\forall"), "∃": _e(r"\exists"), "∄": _e(r"\nexists"), "¬": _e(r"\neg"),
    "∧": _e(r"\wedge"), "∨": _e(r"\vee"), "⊢": _e(r"\vdash"), "⊨": _e(r"\models"),
    "∴": _e(r"\therefore"), "∵": _e(r"\because"),
    # blackboard and script
    "ℝ": _e(r"\mathbb{R}"), "ℤ": _e(r"\mathbb{Z}"), "ℚ": _e(r"\mathbb{Q}"),
    "ℕ": _e(r"\mathbb{N}"), "ℂ": _e(r"\mathbb{C}"), "ℙ": _e(r"\mathbb{P}"),
    "ℍ": _e(r"\mathbb{H}"), "𝔽": _e(r"\mathbb{F}"), "ℓ": _e(r"\ell"),
    # delimiters, ellipses, primes
    "⟨": _e(r"\langle"), "⟩": _e(r"\rangle"), "⌊": _e(r"\lfloor"), "⌋": _e(r"\rfloor"),
    "⌈": _e(r"\lceil"), "⌉": _e(r"\rceil"), "…": r"\ldots{}", "⋯": _e(r"\cdots"),
    "⋮": _e(r"\vdots"), "⋱": _e(r"\ddots"), "′": _e(r"\prime"), "″": _e(r"\prime\prime"),
    "°": _e(r"^\circ"),
    # punctuation and spacing
    "–": "--", "—": "---", "‘": "`", "’": "'", "“": "``", "”": "''",
    " ": "~", " ": r"\,", "​": "", "﻿": "",
    # Dashes that are not hyphens. U+2212 is what models use for negative
    # numbers, and mapping it to `?` silently corrupted "-2" into "?2".
    "\u2212": _e("-"), "\u2011": "-", "\u2012": "--", "\u2044": _e("/"), "\u2215": _e("/"),
}
_UNICODE_TEX.update({
    c: _e("\\" + name) for c, name in {
        "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "varepsilon",
        "ϵ": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta", "ϑ": "vartheta",
        "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi",
        "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "varsigma", "τ": "tau",
        "υ": "upsilon", "φ": "varphi", "ϕ": "phi", "χ": "chi", "ψ": "psi",
        "ω": "omega", "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda",
        "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma", "Υ": "Upsilon", "Φ": "Phi",
        "Ψ": "Psi", "Ω": "Omega",
    }.items()
})


def _script(c: str) -> str | None:
    """Sub- and superscripts, from Unicode's own decomposition data.

    A model writing `k\u2081 + k\u2082` must not come out as `k? + k?`. Enumerating
    these by hand misses most of them, so ask the character database instead.
    """
    tag, _, rest = unicodedata.decomposition(c).partition(" ")
    if tag not in ("<sub>", "<super>") or not rest:
        return None
    try:
        base = "".join(chr(int(x, 16)) for x in rest.split())
    except ValueError:
        return None
    if not base.isascii() or _SPECIAL_RE.search(base):
        return None
    return _e(("_" if tag == "<sub>" else "^") + "{" + base + "}")


def _compat(c: str) -> str | None:
    """Ligatures and similar, where the ASCII expansion is simply correct."""
    tag, _, rest = unicodedata.decomposition(c).partition(" ")
    if tag not in ("<compat>", "<noBreak>", "<font>", "") or not rest:
        return None
    try:
        base = "".join(chr(int(x, 16)) for x in rest.split())
    except ValueError:
        return None
    return base if base.isascii() and not _SPECIAL_RE.search(base) else None


def _unicode(s: str) -> str:
    """Rewrite what pdflatex cannot encode.

    Anything still unrecognised becomes a visible `[U+XXXX]` marker rather than
    a `?`. In mathematics a stray `?` reads as a legitimate symbol, so a silent
    substitution is worse than an obviously broken one: `k\u2081` arriving as `k?` is
    a wrong document that looks right.
    """
    out = []
    for c in s:
        if c in _UNICODE_TEX:
            out.append(_UNICODE_TEX[c])
        elif (rep := _script(c) or _compat(c)) is not None:
            # Ahead of the Latin-1 test: ¹ ² ³ live down there but are still
            # superscripts, and T1 has no dependable glyph for them.
            out.append(rep)
        elif ord(c) < 0x100:
            out.append(c)
        else:
            out.append(rf"\texttt{{[U+{ord(c):04X}]}}")
    return "".join(out)


def unmapped_characters(text: str) -> list[str]:
    """Characters that will show up as `[U+XXXX]`, so a caller can say so."""
    return sorted({
        c for c in text
        if ord(c) >= 0x100 and c not in _UNICODE_TEX
        and _script(c) is None and _compat(c) is None
    })


class _Vault:
    """Holds spans that must reach the output byte-for-byte."""

    def __init__(self) -> None:
        self._items: list[str] = []

    def stash(self, latex: str) -> str:
        self._items.append(latex)
        return f"{_MARK}{len(self._items) - 1}{_MARK}"

    def restore(self, text: str) -> str:
        return _MARK_RE.sub(lambda m: self._items[int(m.group(1))], text)


def _escape(s: str) -> str:
    return _SPECIAL_RE.sub(lambda m: _SPECIALS[m.group()], s)


def _verbatim(body: str) -> str:
    """Verbatim admits no macros, so anything unencodable becomes a marker."""
    return "".join(c if ord(c) < 0x100 else f"[U+{ord(c):04X}]" for c in body)


def _texttt(code: str) -> str:
    # Underscores are escaped before _unicode runs: subscript macros contain `_`
    # and a later blanket escape would turn \ensuremath{_{1}} into nonsense.
    return r"\texttt{" + _unicode(_escape(code).replace("_", r"\_")) + "}"


def _protect(text: str, vault: _Vault) -> str:
    """Lift code and math out of harm's way, innermost construct last."""
    # Fenced blocks first: their contents may contain anything at all.
    text = re.sub(
        r"^[ \t]*```[^\n]*\n(?P<body>.*?)^[ \t]*```[ \t]*$",
        lambda m: vault.stash("\\begin{verbatim}\n"
                              + _verbatim(m.group("body").rstrip("\n"))
                              + "\n\\end{verbatim}"),
        text, flags=re.S | re.M)
    # Inline code before math, so that `$x$` reads as code rather than as math.
    text = re.sub(r"`([^`\n]+)`", lambda m: vault.stash(_texttt(m.group(1))), text)
    # Display math, both spellings.
    text = re.sub(r"\$\$(.+?)\$\$",
                  lambda m: vault.stash("\\[\n" + _unicode(m.group(1).strip("\n")) + "\n\\]"),
                  text, flags=re.S)
    text = re.sub(r"\\\[(.+?)\\\]",
                  lambda m: vault.stash("\\[\n" + _unicode(m.group(1).strip("\n")) + "\n\\]"),
                  text, flags=re.S)
    # Inline math, under the same rules the web renderer's dollarmath applies:
    # the opening `$` is not followed by a space, the closing `$` is not preceded
    # by one, and the closing `$` is not followed by a digit. Without them
    # "costs $5, but $x$ is math" reads its first two dollars as a formula.
    text = re.sub(r"(?<!\\)\$(?!\s)((?:[^$\\\n]|\\.)+?)(?<!\s)(?<!\\)\$(?!\d)",
                  lambda m: vault.stash("$" + _unicode(m.group(1)) + "$"), text)
    text = re.sub(r"\\\((.+?)\\\)",
                  lambda m: vault.stash("\\(" + _unicode(m.group(1)) + "\\)"), text,
                  flags=re.S)
    return text


def _inline(text: str) -> str:
    """Escape, resolve emphasis, mop up underscores — and only then bring in the
    Unicode macros, several of which legitimately contain `_`."""
    # Protect link destinations before emphasis: a URL such as
    # AST_2009__324__R1_0 must not acquire a \textbf command inside \href.
    # Use a different marker from the outer math/code vault; link labels may
    # already contain its placeholders.
    links: list[str] = []

    def link(match: re.Match[str]) -> str:
        from urllib.parse import quote
        url = quote(match.group(2), safe=":/?#[]@!$&'()*+,;=%._~-")
        # URL characters have hyperref's URL semantics, not prose semantics
        # (in particular, '~' must not become \textasciitilde{}).
        url_tex = re.sub(r"[%#&_$]", lambda m: "\\" + m.group(), url)
        links.append(r"\href{" + url_tex + "}{" + _inline(match.group(1)) + "}")
        return f"\x01{len(links) - 1}\x01"

    text = re.sub(r"!?\[([^\]\n]*)\]\(([^)\s]+)\)", link, text)
    text = _escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"\\textbf{\1}", text, flags=re.S)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\\emph{\1}", text)
    text = re.sub(r"(?<![A-Za-z0-9\\])_([^_\n]+?)_(?![A-Za-z0-9])", r"\\emph{\1}", text)
    # Straight quotes both render as closing quotes in LaTeX; pair them properly.
    text = re.sub(r'"([^"\n]*)"', r"``\1''", text)
    text = _unicode(text.replace("_", r"\_"))
    return re.sub(r"\x01(\d+)\x01", lambda m: links[int(m.group(1))], text)


def _is_table_row(line: str) -> bool:
    return "|" in line and line.strip() != ""


def _table_alignment(line: str) -> list[str] | None:
    """Return column alignments if `line` is a pipe-table separator."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if not cells or not all(re.fullmatch(r":?-{1,}:?", c) for c in cells):
        return None
    out = []
    for c in cells:
        left, right = c.startswith(":"), c.endswith(":")
        out.append("c" if left and right else "r" if right else "l")
    return out


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _render_blocks(text: str) -> str:  # noqa: C901 — a flat block dispatcher
    lines = text.split("\n")
    out: list[str] = []
    stack: list[tuple[int, str]] = []   # (indent, environment)
    i = 0

    def close_lists(to_indent: int = -1) -> None:
        while stack and stack[-1][0] > to_indent:
            out.append(f"\\end{{{stack.pop()[1]}}}")

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            close_lists()
            out.append("")
            i += 1
            continue

        if (m := _HEADING.match(line)):
            close_lists()
            level = min(len(m.group(1)), len(_SECTIONS)) - 1
            out.append(f"{_SECTIONS[level]}{{{_inline(m.group(2))}}}")
            i += 1
            continue

        if (m := _IMAGE.match(line)):
            close_lists()
            alt, path = m.group(1).strip(), m.group(2)
            parts = path.replace("\\", "/").split("/")
            local = (bool(re.fullmatch(r"[A-Za-z0-9._/-]+", path))
                     and not path.startswith("/") and ".." not in parts)
            if local:
                out.append(r"\begin{center}")
                out.append(
                    r"\includegraphics[width=\linewidth,height=.72\textheight,"
                    r"keepaspectratio]{\detokenize{" + path + "}}")
                if alt:
                    out.append(r"\par\small\emph{" + _inline(alt) + "}")
                out.append(r"\end{center}")
            else:
                label = alt or "Illustration"
                out.append(_inline(f"[{label}]({path})"))
            i += 1
            continue

        # A rule, but not a table separator (which also consists of dashes).
        if _RULE.match(line) and "|" not in line:
            close_lists()
            out.append(r"\begin{center}\rule{0.5\linewidth}{0.4pt}\end{center}")
            i += 1
            continue

        if _QUOTE.match(line):
            close_lists()
            body = []
            while i < len(lines) and (q := _QUOTE.match(lines[i])):
                body.append(q.group(1))
                i += 1
            out.append(r"\begin{quote}")
            out.append(_render_blocks("\n".join(body)))
            out.append(r"\end{quote}")
            continue

        if (_is_table_row(line) and i + 1 < len(lines)
                and (align := _table_alignment(lines[i + 1]))):
            close_lists()
            header = _split_row(line)
            i += 2
            rows = []
            while i < len(lines) and _is_table_row(lines[i]):
                rows.append(_split_row(lines[i]))
                i += 1
            width = len(align)
            # A plain ``tabular`` gives every cell its natural width.  One
            # long claim or URL can therefore make a companion table extend
            # into the margin (or off the PDF altogether).  ``tabularx``
            # keeps the table at the text measure and lets prose wrap inside
            # its cells.  The alignment markers from Markdown still control
            # the text alignment within each equal-width column.
            column = {
                "l": r">{\raggedright\arraybackslash}X",
                "c": r">{\centering\arraybackslash}X",
                "r": r">{\raggedleft\arraybackslash}X",
            }
            columns = "".join(column[side] for side in align)
            out.append(r"\begingroup\small\sloppy"
                       r"\setlength{\tabcolsep}{4pt}"
                       r"\renewcommand{\arraystretch}{1.15}"
                       r"\begin{tabularx}{\linewidth}{@{}" + columns + r"@{}}")
            out.append(r"\hline")
            out.append(" & ".join(
                r"\textbf{" + _inline(c) + "}" for c in header[:width]) + r" \\")
            out.append(r"\hline")
            for row in rows:
                cells = (row + [""] * width)[:width]
                rendered = [_inline(c) for c in cells]
                # After \\, a leading [ is read as an optional row-spacing
                # argument. Citation keys and intervals may begin a cell;
                # \relax ends that scan without changing the printed text.
                if rendered and rendered[0].startswith("["):
                    rendered[0] = r"\relax " + rendered[0]
                out.append(" & ".join(rendered) + r" \\")
            out.append(r"\hline")
            out.append(r"\end{tabularx}\endgroup")
            continue

        bullet, ordered = _BULLET.match(line), _ORDERED.match(line)
        if bullet or ordered:
            m = bullet or ordered
            indent = len(m.group(1))
            env = "itemize" if bullet else "enumerate"
            while stack and stack[-1][0] > indent:
                out.append(f"\\end{{{stack.pop()[1]}}}")
            if not stack or stack[-1][0] < indent:
                out.append(f"\\begin{{{env}}}")
                stack.append((indent, env))
            elif stack[-1][1] != env:
                out.append(f"\\end{{{stack.pop()[1]}}}")
                out.append(f"\\begin{{{env}}}")
                stack.append((indent, env))
            out.append(r"\item " + _inline(m.group(2)))
            i += 1
            continue

        out.append(_inline(stripped))
        i += 1

    close_lists()
    return "\n".join(out)


def tex_text(s: str) -> str:
    """A plain string as safe LaTeX: escaped, and with symbols TeX can set."""
    return _unicode(_escape(s))


def markdown_to_latex(text: str, *, citation_keys: frozenset[str] = frozenset()) -> str:
    """Convert Markdown to LaTeX, linking known bibliography keys when given."""
    vault = _Vault()
    protected = _protect(text.replace("\r\n", "\n"), vault)
    if citation_keys:
        protected = _CITATION.sub(
            lambda m: vault.stash(r"\cite{" + m.group(1) + "}")
            if m.group(1) in citation_keys else m.group(), protected)
    body = _render_blocks(protected)
    return vault.restore(body).strip() + "\n"


def answer_document(text: str, *, title: str, provenance: str = "") -> str:
    """A complete, compilable document wrapping the converted answer."""
    head = [
        r"\documentclass[11pt]{article}",
        r"\input{preamble}",
        r"\usepackage{graphicx}",
        r"\usepackage{tabularx}",
        r"\usepackage{parskip}",
        "",
        rf"\title{{{_unicode(_escape(title))}}}",
        r"\author{}",
        rf"\date{{{_unicode(_escape(provenance))}}}" if provenance else r"\date{\today}",
        "",
        r"\begin{document}",
        r"\maketitle",
        "",
    ]
    return "\n".join(head) + markdown_to_latex(text) + "\n\n" + r"\end{document}" + "\n"
