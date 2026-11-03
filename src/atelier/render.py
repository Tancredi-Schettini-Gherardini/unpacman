"""Server-side rendering: markdown with mathematics, and syntax highlighting.

Rendering on the server keeps the browser free of a markdown pipeline entirely.
Only KaTeX runs client-side, and only over the math spans this module emits —
which matters, because the agent writes mathematics and reading raw TeX for an
hour is miserable. Both Markdown dollar delimiters and LaTeX bracket delimiters
are accepted because the companion skills explicitly permit both.
"""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer_for_filename
from pygments.util import ClassNotFound

_TEXT_EXT = {
    "md", "markdown", "txt", "tex", "bib", "sty", "cls", "py", "sage", "jl",
    "json", "toml", "yaml", "yml", "csv", "tsv", "sh", "bash", "zsh", "c", "h",
    "cpp", "hpp", "rs", "go", "js", "ts", "html", "css", "ipynb", "log", "cfg",
    "ini", "gp", "m", "nb", "lean", "v", "thy",
}
_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg", "avif"}


def is_text(ext: str) -> bool:
    return ext.lower().lstrip(".") in _TEXT_EXT


def is_image(ext: str) -> bool:
    return ext.lower().lstrip(".") in _IMAGE_EXT


def is_pdf(ext: str) -> bool:
    return ext.lower().lstrip(".") == "pdf"


def _highlight_code(code: str, lang: str, _attrs: str) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    formatter = HtmlFormatter(nowrap=False, cssclass="hl", wrapcode=True)
    return highlight(code, lexer, formatter)


def _bracket_math_plugin(md: MarkdownIt) -> None:
    """Parse ``\\(...\\)`` and standalone ``\\[...\\]`` as KaTeX math.

    CommonMark otherwise treats the opening backslash as an escape, leaving
    commands such as ``\\uparrow`` visible as plain text. Distinct token names
    keep these rules from replacing the dollar-math plugin's renderers.
    """

    def inline_rule(state, silent: bool) -> bool:
        start = state.pos
        if not state.src.startswith(r"\(", start):
            return False
        end = state.src.find(r"\)", start + 2)
        if end < 0 or "\n" in state.src[start + 2:end]:
            return False
        content = state.src[start + 2:end]
        if not content:
            return False
        if not silent:
            token = state.push("math_inline_bracket", "math", 0)
            token.content = content
            token.markup = r"\("
        state.pos = end + 2
        return True

    def block_rule(state, start_line: int, end_line: int, silent: bool) -> bool:
        if state.sCount[start_line] - state.blkIndent >= 4:
            return False
        start = state.bMarks[start_line] + state.tShift[start_line]
        first_end = state.eMarks[start_line]
        first = state.src[start:first_end]
        if not first.startswith(r"\["):
            return False

        body = first[2:]
        closing_line = start_line
        while True:
            close = body.find(r"\]")
            if close >= 0:
                if body[close + 2:].strip():
                    return False
                content = body[:close]
                break
            closing_line += 1
            if closing_line >= end_line:
                return False
            line_start = state.bMarks[closing_line] + state.tShift[closing_line]
            line_end = state.eMarks[closing_line]
            body += "\n" + state.src[line_start:line_end]

        if not content.strip():
            return False
        if silent:
            return True
        state.line = closing_line + 1
        token = state.push("math_block_bracket", "math", 0)
        token.block = True
        token.content = content
        token.markup = r"\["
        token.map = [start_line, state.line]
        return True

    def render_inline(_renderer, tokens, idx, _options, _env) -> str:
        content = html.escape(str(tokens[idx].content).strip(), quote=False)
        return f'<span class="math inline">{content}</span>'

    def render_block(_renderer, tokens, idx, _options, _env) -> str:
        content = html.escape(str(tokens[idx].content).strip(), quote=False)
        return f'<div class="math block">\n{content}\n</div>\n'

    md.inline.ruler.before("escape", "math_inline_bracket", inline_rule)
    md.block.ruler.before("fence", "math_block_bracket", block_rule)
    md.add_render_rule("math_inline_bracket", render_inline)
    md.add_render_rule("math_block_bracket", render_block)


# An arXiv category — `math.GT`, `cs.LO`, `q-bio.NC` — which linkify takes for
# a web address (http://math.GT: GT is Guatemala's domain, AT Austria's, CA
# Canada's). A domain is not written in capitals; a category always is.
_CATEGORY = re.compile(r"^[a-z][a-z-]*\.[A-Z]{2}$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _separate_display_math(text: str) -> str:
    """Let standalone displays end a preceding Markdown paragraph.

    Without a blank line, MarkdownIt parses ``$$...$$`` as inline math inside
    that paragraph. The resulting ``div`` is invalid inside ``p`` and a
    following statement equation cannot be grouped with its statement box.
    Leave fenced and indented code alone.
    """
    lines = text.split("\n")
    out: list[str] = []
    fence: tuple[str, int] | None = None
    math_close: str | None = None
    for line in lines:
        marker = _FENCE.match(line)
        if fence:
            if (marker and marker.group(1)[0] == fence[0]
                    and len(marker.group(1)) >= fence[1]
                    and not marker.group(2).strip()):
                fence = None
        elif math_close:
            if line.strip() == math_close:
                math_close = None
        elif marker:
            fence = (marker.group(1)[0], len(marker.group(1)))
        elif (not line.startswith("\t") and len(line) - len(line.lstrip(" ")) < 4
              and ((display := line.strip()) in ("$$", r"\[") or
                   (display.startswith("$$") and display.endswith("$$")
                    and len(display) > 4))):
            if out and out[-1].strip():
                out.append("")
            if display == "$$":
                math_close = "$$"
            elif display == r"\[":
                math_close = r"\]"
        out.append(line)
    return "\n".join(out)


def _unlink_categories(state: Any) -> None:
    for block in state.tokens:
        if block.type != "inline" or not block.children:
            continue
        kids, out, i = block.children, [], 0
        while i < len(kids):
            if (kids[i].type == "link_open" and kids[i].markup == "linkify"
                    and i + 2 < len(kids) and kids[i + 1].type == "text"
                    and kids[i + 2].type == "link_close"
                    and _CATEGORY.match(kids[i + 1].content)):
                out.append(kids[i + 1])
                i += 3
                continue
            out.append(kids[i])
            i += 1
        block.children = out


@lru_cache(maxsize=1)
def _md() -> MarkdownIt:
    md = (
        MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True,
                                  "highlight": _highlight_code})
        .enable(["table", "strikethrough", "linkify"])
        .use(dollarmath_plugin, allow_space=True, allow_digits=True,
             double_inline=True)
        .use(_bracket_math_plugin)
        .use(footnote_plugin)
        .use(deflist_plugin)
        .use(tasklists_plugin, enabled=True)
    )
    md.core.ruler.after("linkify", "unlink_categories", _unlink_categories)
    return md


def render_markdown(text: str) -> str:
    """Markdown → HTML. Math is emitted as `<span/div class="math">` for KaTeX."""
    return _md().render(_separate_display_math(text))


def render_code(text: str, filename: str = "", lang: str = "") -> str:
    try:
        if lang:
            lexer = get_lexer_by_name(lang)
        elif filename:
            lexer = guess_lexer_for_filename(filename, text)
        else:
            lexer = TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    formatter = HtmlFormatter(cssclass="hl", linenos="table", wrapcode=True)
    return highlight(text, lexer, formatter)


def render_file(path: Path, *, max_bytes: int = 4_000_000) -> dict[str, str]:
    """Render a file for display. Returns {kind, html|url, title}."""
    ext = path.suffix.lower().lstrip(".")
    if is_pdf(ext):
        return {"kind": "pdf", "html": ""}
    if is_image(ext):
        return {"kind": "image", "html": ""}
    if not is_text(ext):
        return {"kind": "binary",
                "html": f"<p class='muted'>Binary file ({_human(path)}). "
                        f"Use “Open externally” to view it.</p>"}

    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"kind": "error", "html": f"<p class='error'>{html.escape(str(exc))}</p>"}
    if size > max_bytes:
        return {"kind": "toobig",
                "html": f"<p class='muted'>File is {_human(path)} — too large to render "
                        f"inline. Open it externally.</p>"}

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"kind": "error", "html": f"<p class='error'>{html.escape(str(exc))}</p>"}

    if ext in ("md", "markdown"):
        return {"kind": "markdown", "html": render_markdown(text)}
    return {"kind": "code", "html": render_code(text, filename=path.name)}


def _human(path: Path) -> str:
    try:
        n = float(path.stat().st_size)
    except OSError:
        return "unknown size"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_size(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} GB"


@lru_cache(maxsize=1)
def pygments_css() -> str:
    """Dark-first Pygments theme matching the app's palette."""
    return HtmlFormatter(cssclass="hl", style="stata-dark").get_style_defs(".hl")
