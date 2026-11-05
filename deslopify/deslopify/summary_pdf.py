"""Compile reading artefacts through explicit, inspectable LaTeX sources."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import warnings
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from atelier.mdtex import answer_document, markdown_to_latex, tex_text
from .companion_layout import place_source_remarks, place_source_remarks_tex


class SummaryPdfError(RuntimeError):
    """A reading artefact could not be rendered to PDF."""


# Between a reading's passes the controller typesets its companion from a
# private copy of the working folder (atelier/handover.py), never from the
# folder the AI works in, which a program it started may still be changing.
# A guide may name a graphic by its absolute path in that folder, as the AI saw
# it; while this is set, such a path is read from the copy instead.
_PRIVATE_COPY: contextvars.ContextVar[
    tuple[tuple[Path, ...], Path, frozenset[str]] | None] = \
    contextvars.ContextVar("deslopify_private_copy", default=None)


@contextlib.contextmanager
def private_copy_of(workspace: Path, copy: Path, *,
                    left_out: Iterable[str] = ()) -> Iterator[None]:
    """Read paths under `workspace` from `copy`, laid out as it is, while this
    lasts (in this context, and in threads started from it). `left_out`: the
    folder's other entries (relative paths), which the copy does not hold."""
    workspace = Path(workspace)
    names = tuple(dict.fromkeys((Path(os.path.abspath(workspace)), workspace.resolve())))
    token = _PRIVATE_COPY.set((names, Path(copy), frozenset(left_out)))
    try:
        yield
    finally:
        _PRIVATE_COPY.reset(token)


def _from_private_copy(path: Path) -> Path:
    """`path`, read from the private copy when one is in force and the path is
    in the working folder it copies (compared without following anything)."""
    held = _PRIVATE_COPY.get()
    if held is None:
        return path
    names, copy, _ = held
    plain = Path(os.path.normpath(path))
    for name in names:
        if plain.is_relative_to(name):
            return copy / plain.relative_to(name)
    return path


def _left_out_of_private_copy(path: Path) -> bool:
    """Whether `path` is an entry of the working folder that the private copy
    leaves out: one outside the reading's files, there when the copy was
    taken. Decided from what was seen then, never by looking in the folder
    again; a graphic there is refused as it was when the export ran in the
    folder itself — as outside this paper's working files, not as missing."""
    held = _PRIVATE_COPY.get()
    if held is None:
        return False
    names, _, left_out = held
    plain = Path(os.path.normpath(path))
    for name in names:
        if plain.is_relative_to(name):
            return plain.relative_to(name).as_posix() in left_out
    return False


_PREAMBLE = r"""\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb,amsfonts,amsthm}
% Tate--Shafarevich symbol, from the AMS Cyrillic font (not a Latin W).
\DeclareFontFamily{U}{wncy}{}
\DeclareFontShape{U}{wncy}{m}{n}{<->wncyr10}{}
\DeclareSymbolFont{companioncyrillic}{U}{wncy}{m}{n}
\DeclareMathSymbol{\Sha}{\mathord}{companioncyrillic}{"58}
\usepackage{graphicx}
\usepackage[most]{tcolorbox}
\usepackage[margin=1in]{geometry}
\usepackage{hyperref}
\usepackage{enumitem}
\usepackage{tabularx}
\setlength{\emergencystretch}{3em}
\Urlmuskip=0mu plus 1mu
\newtcolorbox{statementblue}{enhanced,breakable,colback=blue!5!white,
  colframe=blue!32!black,boxrule=.55pt,arc=2mm,left=3mm,right=3mm,
  top=2mm,bottom=2mm,before skip=10pt,after skip=10pt}
\newtcolorbox{statementviolet}{enhanced,breakable,colback=violet!6!white,
  colframe=violet!32!black,boxrule=.55pt,arc=2mm,left=3mm,right=3mm,
  top=2mm,bottom=2mm,before skip=10pt,after skip=10pt}
\newtcolorbox{statementgreen}{enhanced,breakable,colback=green!6!white,
  colframe=green!28!black,boxrule=.55pt,arc=2mm,left=3mm,right=3mm,
  top=2mm,bottom=2mm,before skip=10pt,after skip=10pt}
\newtcolorbox{statementamber}{enhanced,breakable,colback=yellow!9!white,
  colframe=orange!36!black,boxrule=.55pt,arc=2mm,left=3mm,right=3mm,
  top=2mm,bottom=2mm,before skip=10pt,after skip=10pt}
"""

_REFERENCE_HEADING = re.compile(
    r"^#{2,6}\s+References cited in this companion\s*$", re.MULTILINE)
_REFERENCE_ENTRY = re.compile(
    r"^\s*[-*]\s+\[([A-Za-z][A-Za-z0-9+.-]*\d{2}[a-z]?)\]\s+(.+?)"
    r"(?=^\s*[-*]\s+\[[A-Za-z][A-Za-z0-9+.-]*\d{2}[a-z]?\]\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)
# Old collection guides occasionally used empty HTML anchors to make manually
# written contents links work. Reader-facing Markdown is deliberately safe and
# renders those tags literally, so remove this obsolete navigation-only form
# before producing the persistent Markdown and PDF artefacts.
_RAW_EMPTY_ANCHOR_LINE = re.compile(
    r"(?im)^[ \t]*<a\b(?=[^>\n]*\b(?:id|name)\s*=)[^>\n]*>\s*</a\s*>\s*(?:\n|$)")
_EDITION_SUFFIX = re.compile(r"\s+[—–-]\s+(?:non-)?expert edition\s*$", re.IGNORECASE)
_REPEATED_EDITION = re.compile(
    r"(\s+[—–-]\s+(?:non-)?expert edition)"
    r"(?:\s+[—–-]\s+(?:non-)?expert edition)+\s*$", re.IGNORECASE)
_MODE_TITLE = re.compile(
    r"Reader[’']s companion to (?:``(?P<tex>.+)''|'(?P<single>.+)'|"
    r'"(?P<double>.+)"|“(?P<curly>.+)”) -- (?P<mode>(?:non-)?expert mode)')
_ABSOLUTE_GRAPHIC = re.compile(
    r"(?P<command>\\includegraphics(?:\[[^\]\r\n]*\])?\{)"
    r"(?:\\detokenize\{(?P<wrapped>/[^{}\r\n]+)\}\}|(?P<plain>/[^{}\r\n]+)\})")
_GRAPHIC_TYPES = frozenset({".pdf", ".png", ".jpg", ".jpeg"})

_STATEMENT_TEX = re.compile(
    r"(?ms)(?P<lead>^|\n\n)(?P<body>\\textbf\{(?P<kind>"
    r"Theorem|Proposition|Lemma|Corollary|Claim|Fact|"
    r"Conjecture|Question|Problem|"
    r"Algorithm|Definition|Assumption|Hypothesis|"
    r"Remark|Example|Observation"
    # The skill's statement convention always puts a period inside the bold
    # label: `**Claim.**` or `**Theorem [Abc00, Theorem 1].**`. This prevents a
    # table header such as `| **Claim** | **Evidence** |` from being wrapped
    # around an entire tabular environment.
    r")\b(?=(?:[ \t]*(?:\[[^\]\n]+\]|"
    r"\\cite(?:\[[^\]\n]*\])?\{[^{}\n]+\}))*"
    r"(?:\s+\([^\n]*?\))?\.).*?)"
    r"(?=\n\n(?!\\\[)|(?<=\\\])\n(?!\n*\\\[)|"
    r"\n(?=\\textbf\{Source correspondence\.\})|\Z)")


def _box_statements(latex: str) -> str:
    """Wrap a statement and its immediately following display math in one box.

    When a theorem introduces a formula rather than referring to an earlier
    companion equation, the formula follows the labelled statement without
    intervening prose. Consecutive displays stay together, but a line after
    the final display or a source-correspondence note starts outside the box
    — unless it goes on with the statement itself (`_statement_end`).
    """
    blue = {"Theorem", "Proposition", "Lemma", "Corollary", "Claim", "Fact"}
    violet = {"Conjecture", "Question", "Problem"}
    green = {"Algorithm", "Definition", "Assumption", "Hypothesis"}

    out: list[str] = []
    at = 0
    while (match := _STATEMENT_TEX.search(latex, at)) is not None:
        kind = match.group("kind")
        tone = ("blue" if kind in blue else "violet" if kind in violet
                else "green" if kind in green else "amber")
        end = _statement_end(latex, match.group("body"), match.end())
        out.append(latex[at:match.start()])
        out.append(f'{match.group("lead")}\\begin{{statement{tone}}}\n'
                   f'{latex[match.start("body"):end]}\n\\end{{statement{tone}}}')
        at = end
    out.append(latex[at:])
    return "".join(out)


# What may follow a statement's paragraph and displays and still be part of
# the statement (a tester's report, 2026-10-23: boxes that stopped short of
# the whole statement). A sentence a display interrupted goes on in lower
# case — "for every prime $p$", "where $a_k$ is …" — and the method writes a
# statement in italics, so a paragraph wholly in italics goes on with it too;
# a list belongs to it when it is introduced by a colon or an unfinished
# sentence, and so does a display. Prose in upright type that begins a new
# sentence, a source note, a heading or another statement begins after the box.
_BLOCK_END = re.compile(
    r"\n\n|\n(?=\\\[|\\begin\{(?:enumerate|itemize)\}|\\textbf\{Source correspondence\.\})")
_LIST_START = re.compile(r"\\begin\{(enumerate|itemize)\}")
_OPENING = re.compile(r"\\(?:emph|textit)\{|\{")
_DISPLAY_TAIL = re.compile(r"(?:\\(?:tag\*?|label)\{[^{}]*\}|\\(?:notag|nonumber)|\s)+$")


def _statement_end(latex: str, body: str, end: int) -> int:
    """Where the statement whose box would end at `end` really ends."""
    open_ = (_display_open(body[body.rfind(r"\["):]) if body.rstrip().endswith(r"\]")
             else _sentence_open(body))
    while True:
        start = end
        while start < len(latex) and latex[start] in " \t\n":
            start += 1
        if start == len(latex):
            return end
        if latex.startswith(r"\[", start):
            close = latex.find(r"\]", start)
            if not open_ or close < 0:
                return end
            stop = close + 2
            open_ = _display_open(latex[start:stop])
        elif (opened := _LIST_START.match(latex, start)) is not None:
            if not open_ or (stop := _list_end(latex, start, opened.group(1))) < 0:
                return end
            open_ = False
        else:
            block = _BLOCK_END.search(latex, start)
            stop = block.start() if block else len(latex)
            text = latex[start:stop]
            if not (_first_letter(text).islower() or _wholly_italic(text)):
                return end
            open_ = _sentence_open(text)
        end = stop


def _first_letter(text: str) -> str:
    """The first character a reader sees, past the braces opening emphasis."""
    while (opening := _OPENING.match(text)) is not None:
        text = text[opening.end():]
    return text[:1]


def _wholly_italic(text: str) -> bool:
    """A paragraph that is one run of emphasis, with at most a mark after it."""
    opening = re.match(r"\\(?:emph|textit)\{", text)
    if opening is None:
        return False
    depth, i = 1, opening.end()
    while i < len(text):
        if text[i] == "\\":
            i += 2                      # an escaped brace, \{ or \}, is not one
            continue
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[i + 1:].strip() in ("", ".", ",", ";", ":")
        i += 1
    return False


def _sentence_open(text: str) -> bool:
    """Whether a paragraph stops mid-sentence: no full stop, question or
    exclamation mark at its end (a colon leaves it open) — past the braces
    closing emphasis and the dollar closing a formula, as a reader sees it."""
    tail = text.rstrip()
    while tail.endswith(("}", "$")):
        tail = tail[:-1].rstrip()
    return not tail.endswith((".", "?", "!"))


def _display_open(display: str) -> bool:
    """Whether a display leaves its sentence open, as its own last mark says."""
    inner = display.strip()
    inner = inner[2:] if inner.startswith(r"\[") else inner
    inner = inner[:-2] if inner.endswith(r"\]") else inner
    return _sentence_open(_DISPLAY_TAIL.sub("", inner))


def _list_end(latex: str, start: int, env: str) -> int:
    """The end of the list environment opened at `start`, or -1."""
    depth = 0
    for mark in re.finditer(rf"\\(begin|end)\{{{env}\}}", latex[start:]):
        depth += 1 if mark.group(1) == "begin" else -1
        if depth == 0:
            return start + mark.end()
    return -1


# The generic Markdown renderer emits starred headings. Companions promote
# their shallowest body heading to a numbered academic section.
_STARRED_HEADING = re.compile(
    r"^\\(?P<kind>(?:sub){0,2}section|paragraph)\*\{(?P<title>.*)\}[ \t]*$", re.MULTILINE)
_HEADING_KINDS = ("section", "subsection", "subsubsection", "paragraph")
HEADING_FORMAT = "% unpacman companion headings v1"
_HEADING_PREAMBLE = r"""\setcounter{secnumdepth}{3}
\makeatletter
\renewcommand\section{\@startsection{section}{1}{\z@}
  {-3.5ex \@plus -1ex \@minus -.2ex}{1.8ex \@plus .2ex}
  {\normalfont\LARGE\bfseries}}
\renewcommand\subsection{\@startsection{subsection}{2}{\z@}
  {-3.25ex \@plus -1ex \@minus -.2ex}{1.2ex \@plus .2ex}
  {\normalfont\large\bfseries}}
\renewcommand\subsubsection{\@startsection{subsubsection}{3}{\z@}
  {-3.25ex \@plus -1ex \@minus -.2ex}{1ex \@plus .2ex}
  {\normalfont\normalsize\bfseries}}
\makeatother"""

# The bibliography goes into the contents as well (tocbibind), and the
# contents go two levels deep: the guide's sections and what is inside them.
# Links are coloured text rather than hyperref's default red boxes, which turn
# a table of contents into a column of frames: the contents in the text's own
# colour, links that leave the document in a dark blue.
_CONTENTS_PREAMBLE = (
    "\\usepackage[nottoc]{tocbibind}\n"
    "\\setcounter{tocdepth}{2}\n"
    "\\usepackage{xcolor}\n"
    "\\definecolor{companionlink}{rgb}{0.16,0.25,0.42}\n"
    "\\hypersetup{colorlinks=true,linkcolor=black,citecolor=black,"
    "urlcolor=companionlink}")


def _list_headings(body: str) -> str:
    """Number section levels; LaTeX supplies their contents entries and links."""
    # Remove the contents entries added by older versions before allowing
    # LaTeX to create numbered ones. Keep unrelated document anchors intact.
    body = re.sub(
        r"(?:\\phantomsection\n)?(^\\(?:sub){0,2}section\*\{.*\}[ \t]*)\n"
        r"\\addcontentsline\{toc\}\{(?:sub)?section\}\{.*\}[ \t]*",
        r"\1", body, flags=re.MULTILINE)
    depths = sorted({_HEADING_KINDS.index(m.group("kind"))
                     for m in _STARRED_HEADING.finditer(body)})
    kinds = dict(zip(depths, _HEADING_KINDS))

    def listed(match: re.Match[str]) -> str:
        kind = kinds[_HEADING_KINDS.index(match.group("kind"))]
        title = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", match.group("title"))
        if title.lower() in ("references", "references cited in this companion", "bibliography"):
            return (f"\\phantomsection\n\\section*{{{title}}}\n"
                    f"\\addcontentsline{{toc}}{{section}}{{{title}}}")
        # Collection paper headings separate independent mini-companions.
        # Flush pending figures as well as text before starting each paper.
        page_break = ("\\clearpage\n" if kind == "section"
                      and re.match(r"Paper [1-9]\d*:\s+", title) else "")
        return f"{page_break}\\{kind}{{{title}}}"

    return _STARRED_HEADING.sub(listed, body)


def generation_date(guide: Path) -> str:
    """The guide's saved date, stable when its exports are rebuilt later."""
    return datetime.fromtimestamp(guide.stat().st_mtime, UTC).date().isoformat()


def dated_guide(guide: Path) -> str:
    """The reader-facing Markdown, dated without changing the saved guide."""
    text = place_source_remarks(_RAW_EMPTY_ANCHOR_LINE.sub(
        "", guide.read_text(encoding="utf-8", errors="replace")).strip())
    if not text:
        return text
    notice = f"Generated on {generation_date(guide)} (UTC)."
    heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if heading:
        clean_heading = normalise_companion_title(
            _REPEATED_EDITION.sub(r"\1", heading.group(1)))
        text = text[:heading.start(1)] + clean_heading + text[heading.end(1):]
        heading = re.search(r"^#\s+.+$", text, re.MULTILINE)
    at = heading.end() if heading else 0
    return text[:at] + ("\n\n" if at else "") + notice + "\n\n" + text[at:].lstrip("\n")


def with_contents(document: str, *, generated_on: str | None = None) -> str:
    """A complete companion with numbered headings and a table of contents.

    Used for every new companion, and to rebuild the PDF of one written before
    companions had contents, from its own LaTeX source. A document that already
    uses this heading format comes back unchanged.
    """
    document = place_source_remarks_tex(document)
    if generated_on and r"\date{Generated on " not in document:
        document = document.replace(
            r"\begin{document}",
            rf"\date{{Generated on {generated_on} (UTC).}}" + "\n" + r"\begin{document}", 1)
    if HEADING_FORMAT in document:
        return document
    head, begin, rest = document.partition("\\begin{document}")
    if not begin:
        return document
    rest = _list_headings(rest)
    if "\\tableofcontents" not in rest:
        if "\\maketitle" in rest:
            rest = rest.replace("\\maketitle", "\\maketitle\n\\tableofcontents\n\\bigskip", 1)
        else:
            rest = "\n\\tableofcontents\n\\bigskip" + rest
    contents = "" if "{tocbibind}" in head else _CONTENTS_PREAMBLE + "\n"
    return f"{head.rstrip()}\n{contents}{HEADING_FORMAT}\n{_HEADING_PREAMBLE}\n\n{begin}{rest}"


def rebuild_with_contents(tex: Path | str, output: Path | str) -> Path:
    """Compile a stored companion source again, with a table of contents.

    The source is not changed: it is copied, with the figures it includes, into
    a scratch folder, given its contents there, and compiled. The PDF is
    written to `output` in one step, so a reader never opens half a file.
    """
    source = Path(tex).expanduser()
    text = source.read_text(encoding="utf-8", errors="replace")
    guide = source.parent / "guide.md"
    return _compile_beside(with_contents(
        text, generated_on=generation_date(guide if guide.is_file() else source)),
        source.parent, Path(output).expanduser())


def companion_from_guide(guide: Path | str, output: Path | str, *, audience: str,
                         paper_title: str | None = None) -> Path:
    """Typeset a companion PDF, with contents, from a reading's guide alone.

    For readings made before the method kept a LaTeX source (version 11 and
    earlier): their PDF was typeset from the same guide by the older summary
    layout, which had no contents. This is that guide in the current layout.
    """
    source = Path(guide).expanduser()
    text = source.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise SummaryPdfError("the guide must be non-empty")
    _, latex = _companion_document(
        text, audience="non-expert" if audience == "non-expert" else "expert",
        paper_title=paper_title)
    latex = with_contents(latex, generated_on=generation_date(source))
    return _compile_beside(latex, source.parent, Path(output).expanduser())


def _compile_beside(latex: str, reading: Path, target: Path) -> Path:
    """Compile a document that includes a reading's figures, in a scratch
    folder, and put the PDF at `target` in one step."""
    from atelier.access import check_job_tree
    check_job_tree(reading)
    with tempfile.TemporaryDirectory(prefix="deslopify-contents-") as raw_tmp:
        tmp = Path(raw_tmp)
        figures = reading / "figures"
        if figures.is_dir():
            shutil.copytree(figures, tmp / "figures")
        copy = tmp / "companion.tex"
        copy.write_text(_materialize_local_graphics(latex, reading, tmp),
                        encoding="utf-8")
        built = _compile_tex(copy, tmp / "out" / "companion.pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
        shutil.copy2(built, staged)
        staged.replace(target)
    return target


def _materialize_local_graphics(latex: str, reading: Path, destination: Path) -> str:
    """Make absolute references to this paper's graphics portable for TeX.

    Restricted pdfTeX cannot open absolute paths, even when the source is the
    uploaded paper. Only files inside this paper's progress directory may be
    copied; the compiler never receives broader filesystem access.
    """
    matches = list(_ABSOLUTE_GRAPHIC.finditer(latex))
    if not matches:
        return latex

    from atelier.access import check_job_tree
    # Only the known progress/reading layout may reach its parent, where the
    # uploaded source lives. A standalone guide cannot widen this to /tmp or /.
    allowed = (reading.parent if reading.name == "reading"
               and reading.parent.name == "progress" else reading)
    root = allowed.resolve()
    sources: dict[str, Path] = {}
    for match in matches:
        name = match.group("wrapped") or match.group("plain")
        source = _from_private_copy(Path(name))
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            if _left_out_of_private_copy(Path(name)):
                raise SummaryPdfError(
                    f"Referenced graphic is outside this paper's working files: {name}") from exc
            raise SummaryPdfError(f"Referenced graphic does not exist: {name}") from exc
        if not resolved.is_relative_to(root):
            raise SummaryPdfError(
                f"Referenced graphic is outside this paper's working files: {name}")
        if not resolved.is_file() or resolved.suffix.lower() not in _GRAPHIC_TYPES:
            raise SummaryPdfError(f"Unsupported local graphic: {name}")
        sources[name] = resolved
    check_job_tree(allowed)

    names: dict[str, str] = {}
    figures = destination / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        relative = f"figures/source-{digest}{source.suffix.lower()}"
        target = destination / relative
        if not target.exists():
            shutil.copy2(source, target)
        names[name] = relative

    def replace(match: re.Match[str]) -> str:
        name = match.group("wrapped") or match.group("plain")
        return f"{match.group('command')}{names[name]}}}"

    return _ABSOLUTE_GRAPHIC.sub(replace, latex)


def _latex_engine() -> str:
    engine = shutil.which("latexmk")
    if engine is None:
        raise SummaryPdfError(
            "latexmk is required for companion PDFs but was not found. Install "
            "a TeX distribution; the Markdown and LaTeX sources remain available.")
    return engine


def _compile_tex(tex: Path, output: Path) -> Path:
    """Compile *tex* itself, keeping auxiliary files out of its directory."""
    engine = _latex_engine()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deslopify-latex-") as raw_tmp:
        tmp = Path(raw_tmp)
        from atelier.access import clean_environment, sandbox_command
        # TeX is executable input, even without shell escape (e.g. \input).
        # Never compile a model-authored file with the server's full read access.
        argv = sandbox_command(
            [engine, "-norc", "-pdf", "-no-shell-escape",
             "-interaction=nonstopmode", "-halt-on-error",
             f"-outdir={tmp}", tex.name],
            read=[tex.parent.resolve()], write=[tmp], inherit_job_sandbox=True)
        env = clean_environment() | {
            "TMPDIR": str(tmp), "HOME": str(tmp),
            "openin_any": "p", "openout_any": "p", "shell_escape": "f",
            "TEXMFVAR": str(tmp / "texmf-var"), "TEXMFCONFIG": str(tmp / "texmf-config")}
        try:
            result = subprocess.run(
                argv, env=env,
                cwd=tex.parent, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise SummaryPdfError("LaTeX timed out while rendering the companion") from exc

        built = tmp / f"{tex.stem}.pdf"
        if result.returncode != 0 or not built.is_file():
            log = result.stdout + result.stderr
            if "! Undefined control sequence." in log:
                # Keep the offending command and source line, even when
                # latexmk's final summary pushes them out of the log tail.
                context = log.split("! Undefined control sequence.", 1)[1]
                detail = "! Undefined control sequence.\n" + "\n".join(context.splitlines()[:8])
                raise SummaryPdfError(
                    f"LaTeX could not render {tex.name}:\n{detail}\n"
                    "Repair the undefined command in guide.md using its correct "
                    "standard LaTeX expansion; preserve the mathematical meaning. "
                    "Do not invent a definition or delete the formula. Changes to "
                    "companion.tex alone will be overwritten on export.")
            detail = "\n".join(log.splitlines()[-30:])
            raise SummaryPdfError(f"LaTeX could not render {tex.name}:\n{detail}")
        shutil.copy2(built, output)
    return output


def normalise_companion_title(title: str, *, tex_quotes: bool = True) -> str:
    """Use double quotes around a reader mode title, preserving its paper and mode."""
    match = _MODE_TITLE.fullmatch(title)
    if match is None:
        return title
    paper = next(match.group(kind) for kind in ("tex", "single", "double", "curly")
                 if match.group(kind) is not None)
    opening, closing = ("``", "''") if tex_quotes else ("“", "”")
    return f"Reader's companion to {opening}{paper}{closing} -- {match.group('mode')}"


def _first_heading(markdown: str) -> tuple[str, str]:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    if not match:
        return "the paper", markdown
    heading = match.group(1).strip().rstrip("#").strip()
    paper = re.sub(r"(?i)^reader(?:'|’)s guide to\s+", "", heading).strip()
    body = markdown[:match.start()] + markdown[match.end():]
    return paper or "the paper", body.lstrip("\n")


def _companion_document(markdown: str, *, audience: str,
                        paper_title: str | None = None,
                        box_statements: bool = True) -> tuple[str, str]:
    """Return the deterministic PDF title and a self-contained LaTeX document."""
    inferred_title, body = _first_heading(place_source_remarks(markdown))
    paper = (paper_title or inferred_title).strip()
    level = "non-expert" if audience == "non-expert" else "expert"
    if _MODE_TITLE.fullmatch(inferred_title):
        title = normalise_companion_title(inferred_title)
    else:
        title = (inferred_title if re.match(r"(?i)^reader[’']s companion to ", inferred_title)
                 else f"Reader's guide to {paper}")
        title = f"{_EDITION_SUFFIX.sub('', _REPEATED_EDITION.sub(r'\1', title))} — {level} edition"
    # Turn the source's TeX quotation markers into prose quotes before Markdown
    # parsing, so backticks in the title cannot turn its opening quote into code.
    title_tex = markdown_to_latex(
        normalise_companion_title(title, tex_quotes=False)).strip()

    bibliography = ""
    citation_keys: frozenset[str] = frozenset()
    if match := _REFERENCE_HEADING.search(body):
        reference_markdown = body[match.end():].strip()
        body = body[:match.start()].rstrip()
        entries = _REFERENCE_ENTRY.findall(reference_markdown)
        if entries:
            citation_keys = frozenset(key for key, _ in entries)
            rendered = []
            for key, entry in entries:
                entry_tex = markdown_to_latex(entry.strip()).strip()
                rendered.append(f"\\bibitem[{tex_text(key)}]{{{tex_text(key)}}}\n{entry_tex}")
            bibliography = (
                "\n\\begingroup\n\\sloppy\n"
                "\\renewcommand{\\refname}{References cited in this companion}\n"
                "\\begin{thebibliography}{ABC+99}\n"
                + "\n\n".join(rendered)
                + "\n\\end{thebibliography}\n\\endgroup\n"
            )
        else:
            body += "\n\n" + match.group(0) + "\n\n" + reference_markdown

    latex_body = markdown_to_latex(body, citation_keys=citation_keys).rstrip()
    if box_statements:
        latex_body = _box_statements(latex_body)
    else:
        latex_body = ("% Statement boxes disabled after styled compilation failure.\n"
                      + latex_body)

    document = "\n".join([
        r"\documentclass[11pt]{article}",
        _PREAMBLE.rstrip(),
        r"\usepackage{parskip}",
        "",
        rf"\title{{{title_tex}}}",
        r"\author{}",
        r"\date{}",
        "",
        r"\begin{document}",
        r"\maketitle",
        "",
        latex_body,
        bibliography.rstrip(),
        "",
        r"\end{document}",
        "",
    ])
    return title, with_contents(document)


def write_summary_pdf(source: Path | str, output: Path | str,
                      *, title: str = "Paper summary") -> Path:
    """Compile a legacy Markdown summary through a temporary LaTeX source."""
    markdown = Path(source).expanduser()
    if not markdown.is_file():
        raise SummaryPdfError(f"summary source does not exist: {markdown}")
    target = Path(output).expanduser()
    text = markdown.read_text(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory(prefix="deslopify-summary-") as raw_tmp:
        tmp = Path(raw_tmp)
        (tmp / "preamble.tex").write_text(_PREAMBLE, encoding="ascii")
        tex = tmp / "summary.tex"
        tex.write_text(answer_document(text, title=title), encoding="utf-8")
        return _compile_tex(tex, target)


def write_companion(guide: Path | str, markdown_output: Path | str,
                    tex_output: Path | str, pdf_output: Path | str, *,
                    audience: str,
                    paper_title: str | None = None) -> tuple[Path, Path, Path]:
    """Write Markdown and LaTeX reader files, then compile that exact LaTeX file."""
    guide_path = Path(guide).expanduser()
    if not guide_path.is_file():
        raise SummaryPdfError(f"guide source does not exist: {guide_path}")
    guide_text = _RAW_EMPTY_ANCHOR_LINE.sub(
        "", guide_path.read_text(encoding="utf-8", errors="replace")).strip()
    if not guide_text:
        raise SummaryPdfError("the guide must be non-empty")
    if audience not in {"expert", "non-expert"}:
        raise SummaryPdfError("audience must be 'expert' or 'non-expert'")

    markdown_target = Path(markdown_output).expanduser()
    tex_target = Path(tex_output).expanduser()
    markdown_target.parent.mkdir(parents=True, exist_ok=True)
    tex_target.parent.mkdir(parents=True, exist_ok=True)
    markdown_target.write_text(dated_guide(guide_path) + "\n", encoding="utf-8")

    _, latex = _companion_document(
        guide_text, audience=audience, paper_title=paper_title)
    latex = with_contents(latex, generated_on=generation_date(guide_path))
    pdf_output_path = Path(pdf_output).expanduser()
    pdf_output_path.unlink(missing_ok=True)
    latex = _materialize_local_graphics(latex, guide_path.parent, tex_target.parent)
    tex_target.write_text(latex, encoding="utf-8")
    try:
        pdf_target = _compile_tex(tex_target, pdf_output_path)
    except SummaryPdfError as styled_error:
        # Pastel boxes are an optional presentation layer.  If that layer is
        # what made an otherwise complete companion fail, preserve every word,
        # formula, table, figure and citation and retry from a persistent,
        # unboxed LaTeX source.  Other failures still go to the reviser.
        if r"\begin{statement" not in latex:
            raise
        _, fallback = _companion_document(
            guide_text, audience=audience, paper_title=paper_title,
            box_statements=False)
        fallback = with_contents(fallback, generated_on=generation_date(guide_path))
        fallback = _materialize_local_graphics(
            fallback, guide_path.parent, tex_target.parent)
        tex_target.write_text(fallback, encoding="utf-8")
        warnings.warn(
            "styled companion compilation failed; retrying without optional "
            "statement boxes",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            pdf_target = _compile_tex(tex_target, pdf_output_path)
        except SummaryPdfError as fallback_error:
            raise SummaryPdfError(
                f"styled companion compilation failed:\n{styled_error}\n\n"
                f"unboxed fallback also failed:\n{fallback_error}") from fallback_error
    return markdown_target, tex_target, pdf_target
