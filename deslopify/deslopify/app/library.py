"""Papers: what the reader uploads, and what comes back.

The interface deals in *papers*, not in problems, harnesses and runs. Underneath
each paper is an ordinary Atelier problem — that is how the pipeline is driven,
and keeping it that way means a paper read here is identical to one read from
the lab, with the same run records and the same artefacts on disk.

Nothing in this module decides anything about the method. It finds the files a
finished reading leaves behind and puts them in an order a person can use.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atelier.config import Config
from atelier.problems import Problem, ProblemError, create_problem, load_problem

MARKER = "deslopify.json"
MAX_SOURCE_PAGES = 10_000
# A paper is read at one of two levels. Two other kinds of thing are read too:
# several papers together (a collection), and a search of the literature. Each
# is read by its own variant of the method (readings.VARIANTS), named here by
# the "level" it is read at, so that every list of readings keeps one shape.
LEVELS = ("expert", "general")
# A collection has the same two reader audiences as one paper. These names
# remain distinct because its harness and document shape are different.
COLLECTION_LEVELS = ("collection-expert", "collection-nonexpert")
KIND_LEVELS = {"paper": LEVELS, "collection": COLLECTION_LEVELS, "search": ("search",)}
ALL_LEVELS = (*LEVELS, *COLLECTION_LEVELS, "search")

# The pure-mathematics sections the method asks a guide to have, in the order a
# reader meets them. Worked examples may be omitted. Applied guides use Main
# findings, omit Main ideas, and call their final method-and-validation section differently. Matching
# is by normalised heading
# text, so a guide may number them or not, and may use any heading level,
# without the rail losing track.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("main-results", "main results", "Main results"),
    ("main-ideas", "main ideas", "The ideas behind it"),
    ("proofs", "about the proofs", "About the proofs"),
    ("qa", "q&a", "Questions and answers"),
    ("examples", "worked examples", "Worked examples"),
    ("history", "historical context", "Historical context"),
    ("thin", "where the document is thin", "Where it is thin"),
)
APPLIED_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("main-results", "main findings", "Main findings"),
    ("qa", "q&a", "Questions and answers"),
    ("examples", "worked examples", "Worked examples"),
    ("history", "historical context", "Historical context"),
    ("proofs", "about the method and experimental validation",
     "About the method and experimental validation"),
    ("thin", "where the document is thin", "Where it is thin"),
)
# The bibliography every section cites, which the method (since version 12)
# puts last, after Where the document is thin. Earlier guides kept it inside
# Historical context; either way it is lifted out into a section of its own,
# rather than read as part of whatever it happens to follow.
REFERENCES = ("references", "references cited in this companion", "References")
_BY_HEADING = {heading: (key, label) for key, heading, label in (*SECTIONS, REFERENCES)}
# Keep completed companions from the former eight-section contract readable.
# If both legacy headings occur, split_guide folds the second into the already
# opened unified proof section instead of exposing two navigation items.
_BY_HEADING.update({
    "main findings": ("main-results", "Main findings"),
    "about the method and experimental validation": (
        "proofs", "About the method and experimental validation"),
    "about the proof": ("proofs", "About the proof"),
    "structure of the proofs": ("proofs", "About the proofs"),
    "supporting results": ("proofs", "About the proofs"),
    "about validation, experiments, and proofs": (
        "proofs", "About the validation, experiments, and proofs"),
    "about the validation, experiments, and proofs": (
        "proofs", "About the validation, experiments, and proofs"),
})
_ORDER = {key: i for i, (key, _, _) in enumerate(SECTIONS)}
# Keep `main-ideas` in this ordering only for completed applied companions from
# the previous method. New applied guides do not contain that section.
_APPLIED_KEYS = (
    "main-results", "main-ideas", "qa", "examples", "history", "proofs", "thin",
)
_APPLIED_ORDER = {key: i for i, key in enumerate(_APPLIED_KEYS)}

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_SLUG_OK = re.compile(r"[^a-z0-9]+")


def normalise_heading(value: str) -> str:
    value = re.sub(r"^\s*\d+[.)]?\s*", "", value)
    value = re.sub(r"[*_`]", "", value).strip().lower()
    return re.sub(r"\s+", " ", value)


def _ascii(text: str) -> str:
    """Spell a title in ASCII without losing its mathematics.

    Accents decompose (`é` → `e`); Greek letters have no decomposition and would
    simply vanish, so they are spelled out — `Γ₇ and exotic spheres` becomes
    `gamma7 and exotic spheres`, not `and exotic spheres`.
    """
    import unicodedata

    out: list[str] = []
    for ch in text:
        if ch.isascii():
            out.append(ch)
            continue
        plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
        if plain:
            out.append(plain)
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("GREEK") and "LETTER" in name:
            out.append(name.split()[-1].lower())
        else:
            out.append(" ")
    return "".join(out)


def slugify(name: str, taken: set[str]) -> str:
    """A folder name for a paper, unique against what already exists.

    Given a title, not a filename: `Path(...).stem` would cut "A note on S^7.
    Part II" at the full stop.
    """
    stem = _ascii(name).lower()
    slug = _SLUG_OK.sub("-", stem).strip("-")[:48].strip("-")
    if not slug or not slug[0].isalnum():
        slug = f"paper-{slug}".strip("-")
    base, n = slug, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


# --------------------------------------------------------------------------
# the paper
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Paper:
    """Something the reader reads: a paper — or several papers together, or a
    search of the literature, each an ordinary problem folder like a paper's."""
    slug: str
    title: str
    original_name: str
    level: str
    created: str
    problem: Problem
    kind: str = "paper"                  # "paper" | "collection" | "search"
    # A collection's papers, in order: {"slug", "title", "file"} each.
    members: list[dict[str, str]] = field(default_factory=list)
    query: str = ""                      # a search's question

    @property
    def root(self) -> Path:
        return self.problem.root

    @property
    def reading(self) -> Path:
        return self.problem.progress_dir / "reading"

    @property
    def source(self) -> Path:
        """What every pass reads: the PDF, or for several papers or a search
        the text made for them (`source.md`)."""
        name = "source.pdf" if self.kind == "paper" else "source.md"
        return self.problem.progress_dir / name

    @property
    def levels(self) -> tuple[str, ...]:
        return KIND_LEVELS.get(self.kind, LEVELS)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "slug": self.slug, "title": self.title,
            "original_name": self.original_name,
            "level": self.level, "created": self.created, "kind": self.kind,
        }
        if self.kind == "collection":
            out["members"] = [{"slug": m.get("slug", ""), "title": m.get("title", "")}
                              for m in self.members]
        if self.kind == "search":
            out["query"] = self.query
        return out


def _marker(root: Path) -> dict[str, Any]:
    path = root / MARKER
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _paper_from(problem: Problem) -> Paper | None:
    data = _marker(problem.root)
    if not data:
        return None
    kind = str(data.get("kind") or "paper")
    members = data.get("members") if isinstance(data.get("members"), list) else []
    level = str(data.get("level") or "")
    # Collections created by the initial placeholder had one "together" level.
    # Keep them readable and give an unread one a sensible new default.
    if kind == "collection" and level == "together":
        level = "collection-expert"
    return Paper(
        slug=problem.slug,
        title=str(data.get("title") or problem.meta.title),
        original_name=str(data.get("original_name") or ""),
        level=level,
        created=str(data.get("created") or problem.meta.created),
        problem=problem,
        kind=kind if kind in KIND_LEVELS else "paper",
        members=[{k: str(v) for k, v in m.items()} for m in members if isinstance(m, dict)],
        query=str(data.get("query") or ""),
    )


def list_papers(cfg: Config) -> list[Paper]:
    """Every paper this interface created, newest first."""
    out: list[Paper] = []
    if not cfg.problems_dir.is_dir():
        return out
    for entry in cfg.problems_dir.iterdir():
        if not entry.is_dir() or not (entry / MARKER).is_file():
            continue
        try:
            problem = load_problem(cfg.problems_dir, entry.name)
        except ProblemError:
            continue
        if (paper := _paper_from(problem)) is not None:
            out.append(paper)
    return sorted(out, key=lambda p: p.created, reverse=True)


def load_paper(cfg: Config, slug: str) -> Paper:
    problem = load_problem(cfg.problems_dir, slug)
    paper = _paper_from(problem)
    if paper is None:
        raise ProblemError(f"{slug} is not a paper uploaded here")
    return paper


def create_paper(cfg: Config, filename: str, data: bytes, *,
                 title: str = "") -> Paper:
    """Save an uploaded PDF as a new paper."""
    taken = {p.name for p in cfg.problems_dir.iterdir()} \
        if cfg.problems_dir.is_dir() else set()
    shown = " ".join((title or Path(filename).stem.replace("_", " ")
                      .replace("-", " ")).split())[:160]
    slug = slugify(shown or Path(filename).stem, taken)

    problem = create_problem(
        cfg.problems_dir, slug, title=shown or slug,
        statement=(f"# {shown or slug}\n\n"
                   f"Uploaded for reading. The source is "
                   f"`progress/source.pdf`; the companion is built beside it "
                   f"in `progress/reading/`.\n"))
    # `deslopify.source.select_source` prefers progress/source.* over the
    # scaffold's math.tex, so the PDF is what every pass reads.
    (problem.progress_dir / "source.pdf").write_bytes(data)

    created = datetime.now(UTC).isoformat(timespec="seconds")
    (problem.root / MARKER).write_text(json.dumps({
        "title": shown or slug, "original_name": filename,
        "level": "", "created": created, "sha256": fingerprint(data),
    }, indent=2), encoding="utf-8")
    return Paper(slug=slug, title=shown or slug, original_name=filename,
                 level="", created=created, problem=problem)


def page_texts(pdf: Path) -> list[str]:
    """A PDF's text, one string per page, in reading order (not `-layout`).

    pdftotext when there is one, else PDFium, which still knows where each
    page ends; a file neither can split is one page.
    """
    import shutil
    import subprocess

    pages: list[str] = []
    if engine := shutil.which("pdftotext"):
        try:
            out = subprocess.run([engine, str(pdf), "-"], capture_output=True,
                                 timeout=120).stdout.decode("utf-8", errors="replace")
            pages = [re.sub(r"[ \t]+", " ", p).strip() for p in out.split("\f")]
        except (OSError, subprocess.SubprocessError):
            pages = []
    if not any(pages):
        from deslopify.source import SourceError, pdf_pages, read_source

        try:
            pages = [re.sub(r"[ \t]+", " ", p).strip() for p in pdf_pages(pdf)]
        except SourceError:
            pages = []
        if not any(pages):
            pages = [read_source(pdf)]
    while pages and not pages[-1]:
        pages.pop()
    return pages


# --------------------------------------------------------------------------
# several papers, read together
# --------------------------------------------------------------------------

# Where each paper of a collection begins in its `source.md`: what the passes
# read, and what explanations split it back into, one "page" per paper.
_MEMBER_HEADING = re.compile(r"^## Paper (\d+) of (\d+): ", re.MULTILINE)


def paper_fingerprint(paper: Paper) -> str:
    """What a paper was made from: its PDF's hash."""
    return str(_marker(paper.root).get("sha256") or paper.slug)


def collection_fingerprint(members: list[Paper]) -> str:
    """The same papers dropped together again are the same collection, in any
    order."""
    import hashlib

    digests = sorted(paper_fingerprint(m) for m in members)
    return hashlib.sha256(("collection:" + "\n".join(digests)).encode()).hexdigest()


def source_page_count(paper: Paper) -> int:
    """Count the source PDFs for a paper or an existing collection."""
    from deslopify.source import pdf_page_count

    if paper.kind == "paper":
        return pdf_page_count(paper.source)
    if paper.kind == "collection":
        return sum(pdf_page_count(paper.root / member["file"])
                   for member in paper.members)
    return 0


def check_source_page_limit(pages: int) -> None:
    if pages > MAX_SOURCE_PAGES:
        raise ProblemError(
            f"The source PDFs have {pages:,} pages in total; "
            f"the limit is {MAX_SOURCE_PAGES:,} pages.")


def create_collection(cfg: Config, members: list[Paper]) -> Paper:
    """Several papers to be read together, as a problem folder of its own.

    Each paper stays a paper, readable on its own. The collection holds a copy
    of each PDF in `refs/` — read-only to a session, like any reference — and
    `progress/source.md`, their text one after another, which is what the
    passes read and what the hook checks was left unchanged.
    """
    papers = list({m.slug: m for m in members if m.kind == "paper"}.values())
    if len(papers) < 2:
        raise ProblemError("Reading papers together needs at least two different papers.")
    check_source_page_limit(sum(source_page_count(paper) for paper in papers))
    digest = collection_fingerprint(papers)
    if (known := find_by_fingerprint(cfg, digest)) is not None:
        return known

    taken = {p.name for p in cfg.problems_dir.iterdir()} \
        if cfg.problems_dir.is_dir() else set()
    n = len(papers)
    shown = " · ".join(m.title for m in papers)
    if len(shown) > 160:
        shown = shown[:157].rstrip(" ·") + "…"
    slug = slugify(f"{n} papers {papers[0].title}", taken)
    listing = "\n".join(f"{i}. {m.title}" for i, m in enumerate(papers, start=1))
    problem = create_problem(
        cfg.problems_dir, slug, title=shown,
        statement=(f"# {n} papers, read together\n\n{listing}\n\n"
                   "Their PDFs are in `refs/`; their text, one after another, is "
                   "`progress/source.md`. The reading is built beside it in "
                   "`progress/reading/`.\n"))
    refs = problem.root / "refs"
    refs.mkdir(exist_ok=True)
    listed: list[dict[str, str]] = []
    parts = [f"# {n} papers, read together\n",
             f"The text of {n} papers, extracted from their PDFs, one after another. "
             "Each begins with a heading `## Paper i of n: <title>`, and each of its "
             "pages with `[page k]` — the paper's own page numbering of the PDF in "
             "`refs/`.\n"]
    for i, member in enumerate(papers, start=1):
        name = f"{i:02d}-{member.slug}.pdf"
        shutil.copy2(member.source, refs / name)
        listed.append({"slug": member.slug, "title": member.title, "file": f"refs/{name}"})
        pages = page_texts(member.source)
        body = "\n\n".join(f"[page {k}]\n{text}" for k, text in enumerate(pages, start=1))
        parts.append(f"## Paper {i} of {n}: {member.title}\n\n"
                     f"File: `refs/{name}` · {len(pages)} pages\n\n{body}\n")
    (problem.progress_dir / "source.md").write_text("\n".join(parts), encoding="utf-8")

    created = datetime.now(UTC).isoformat(timespec="seconds")
    (problem.root / MARKER).write_text(json.dumps({
        "kind": "collection", "title": shown, "original_name": "",
        "level": "collection-expert", "created": created, "sha256": digest,
        "members": listed,
    }, indent=2), encoding="utf-8")
    return Paper(slug=slug, title=shown, original_name="", level="collection-expert",
                 created=created, problem=problem, kind="collection", members=listed)


def member_pages(source: Path) -> list[str]:
    """A collection's text, one "page" per paper, as explanations are given it."""
    return split_members(source.read_text(encoding="utf-8", errors="replace"))


def split_members(text: str) -> list[str]:
    """A collection's text cut where each paper's heading begins: one string per
    paper, heading included (and the whole text when it has no such heading)."""
    starts = [m.start() for m in _MEMBER_HEADING.finditer(text)]
    if not starts:
        return [text.strip()] if text.strip() else []
    return [text[a:b].strip() for a, b in zip(starts, [*starts[1:], len(text)])]


# --------------------------------------------------------------------------
# a search of the literature
# --------------------------------------------------------------------------

MAX_QUERY = 1000


def normalise_query(query: str) -> str:
    return " ".join(str(query or "").split())[:MAX_QUERY]


def search_fingerprint(query: str) -> str:
    """A search is found again by its question, however it was spaced or cased."""
    import hashlib

    return hashlib.sha256(("search:" + normalise_query(query).lower()).encode()).hexdigest()


def create_search(cfg: Config, query: str) -> Paper:
    """A search for papers on a subject, as a problem folder of its own.

    Its `progress/source.md` is the request, rewritten with the day's date at
    each search (`set_query`) — "the latest preprints" means something else
    next month. The same question asked again is the same search, where a new
    run can be started.
    """
    asked = normalise_query(query)
    if len(asked) < 3:
        raise ProblemError("Say what you are looking for, in a few words at least.")
    digest = search_fingerprint(asked)
    if (known := find_by_fingerprint(cfg, digest)) is not None:
        return known
    taken = {p.name for p in cfg.problems_dir.iterdir()} \
        if cfg.problems_dir.is_dir() else set()
    shown = asked if len(asked) <= 160 else asked[:157].rstrip() + "…"
    slug = slugify(f"search {asked}", taken)
    problem = create_problem(
        cfg.problems_dir, slug, title=shown,
        statement=f"# A search of the literature\n\n{asked}\n")
    created = datetime.now(UTC).isoformat(timespec="seconds")
    (problem.root / MARKER).write_text(json.dumps({
        "kind": "search", "title": shown, "original_name": "", "level": "search",
        "created": created, "sha256": digest, "query": asked,
    }, indent=2), encoding="utf-8")
    paper = Paper(slug=slug, title=shown, original_name="", level="search",
                  created=created, problem=problem, kind="search", query=asked)
    set_query(paper, asked)
    return paper


def set_query(paper: Paper, query: str, *, today: str | None = None) -> None:
    """The search's request as its next run will read it: the question, and
    the date it is asked on."""
    asked = normalise_query(query)
    if len(asked) < 3:
        raise ProblemError("Say what you are looking for, in a few words at least.")
    day = today or datetime.now(UTC).date().isoformat()
    paper.source.parent.mkdir(parents=True, exist_ok=True)
    paper.source.write_text(
        f"# A search of the literature\n\nAsked on {day} (UTC).\n\n"
        f"## What the reader is looking for\n\n{asked}\n", encoding="utf-8")
    if asked != paper.query:
        data = _marker(paper.root) | {"query": asked,
                                      "title": asked if len(asked) <= 160
                                      else asked[:157].rstrip() + "…"}
        (paper.root / MARKER).write_text(json.dumps(data, indent=2), encoding="utf-8")
        paper.query = asked
        paper.title = data["title"]


_NOT_A_TITLE = re.compile(
    r"^(abstract|contents|introduction|preprint|draft|arxiv:\S+.*|"
    r"page \d+.*|\d+|[ivxlc]+|microsoft word.*|untitled.*|.*\.(?:tex|dvi|pdf|docx?))$",
    re.I)
_BODY_STARTS = re.compile(
    r"^(abstract|summary|contents|table of contents|(?:\d+\.?\s*|§\s*)?introduction)\b",
    re.I)
_DATE = re.compile(
    r"^(\w+ \d{1,2}, \d{4}|\d{1,2} \w+ \d{4}|\d{4}-\d{2}-\d{2})$")


def _metadata_title(pdf: Path) -> str:
    import shutil
    import subprocess

    engine = shutil.which("pdfinfo")
    if engine is None:
        from deslopify.source import pdf_title

        return pdf_title(pdf)
    try:
        out = subprocess.run([engine, str(pdf)], capture_output=True,
                             text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in out.splitlines():
        if line.startswith("Title:"):
            return line.partition(":")[2].strip()
    return ""


def _plausible(line: str) -> bool:
    words = line.split()
    return (2 <= len(words) <= 24 and any(c.isalpha() for c in line)
            and not _NOT_A_TITLE.match(line) and not _DATE.match(line))


def guess_title(pdf: Path, text: str, filename: str) -> str:
    """What a person would call this paper.

    Filenames are the worst source — papers arrive as `main.pdf` or
    `2301.04567v2.pdf` — so they come last. The PDF's own Title field comes
    first when it says something, and is rejected when it is plainly the name
    of a build artefact. Otherwise the first line of the text layer that reads
    like a title, which on almost every typeset paper is the title.
    """
    meta = " ".join(_metadata_title(pdf).split())
    if meta and _plausible(meta) and meta.lower() != Path(filename).stem.lower():
        return meta[:160]
    for raw in text.splitlines()[:30]:
        line = " ".join(raw.split())
        # A title precedes the abstract. Past it, the first plausible line is
        # the opening of the body — "We prove" is not what anyone calls a paper.
        if _BODY_STARTS.match(line):
            break
        if line and _plausible(line):
            return line[:160]
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip() or "Untitled"


def guess_title_from(data: bytes, text: str, filename: str) -> str:
    """`guess_title` for bytes not yet written anywhere permanent."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="deslopify-title-") as tmp:
        scratch = Path(tmp) / "paper.pdf"
        scratch.write_bytes(data)
        return guess_title(scratch, text, filename)


def fingerprint(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def find_by_fingerprint(cfg: Config, digest: str) -> Paper | None:
    """The paper already made from these exact bytes, if there is one.

    Dropping the same PDF twice should take you to the reading you have, not
    start a second one that costs the same and says the same.
    """
    for paper in list_papers(cfg):
        if _marker(paper.root).get("sha256") == digest:
            return paper
    return None


def set_level(paper: Paper, level: str) -> None:
    data = _marker(paper.root) | {"level": level}
    (paper.root / MARKER).write_text(json.dumps(data, indent=2), encoding="utf-8")
    paper.level = level


def find_by_download_url(cfg: Config, url: str) -> Paper | None:
    """Reuse an explicitly requested previous import, never a title match.

    Exact URLs only: arXiv versions must not be conflated. A deleted paper or
    missing PDF must be fetched again. Ordinary individual imports still fetch
    afresh, so an unversioned address can be checked for a newer PDF.
    """
    for paper in list_papers(cfg):
        urls = _marker(paper.root).get("download_urls", [])
        if paper.kind == "paper" and paper.source.is_file() \
                and isinstance(urls, list) and url in urls:
            return paper
    return None


def remember_download(paper: Paper, url: str) -> None:
    data = _marker(paper.root)
    urls = data.get("download_urls", [])
    urls = [u for u in urls if isinstance(u, str)] if isinstance(urls, list) else []
    data["download_urls"] = list(dict.fromkeys([*urls, url]))
    (paper.root / MARKER).write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# what a finished reading leaves behind
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Section:
    key: str
    label: str
    heading: str
    markdown: str
    tone: str = ""


@dataclass(slots=True)
class Companion:
    title: str
    preamble: str = ""                       # title block and scope warning
    sections: list[Section] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    # A search's papers, from its `results.json` (read_results); None for
    # anything else.
    results: list[dict[str, Any]] | None = None


def _plain_title(heading: str) -> str:
    """A heading as a reader would name it: no number, no Markdown emphasis."""
    title = re.sub(r"^\s*\d+[a-z]?[.)]?\s*", "", heading)
    title = re.sub(r"^(Paper [1-9]\d*:\s+)``(.+)''$", r"\1“\2”", title)
    return re.sub(r"[*_`]", "", title).strip()


def split_free(text: str) -> tuple[str, list[Section]]:
    """Cut a guide that is not a companion to one paper — several papers read
    together, a search — at its own headings, in its own order.

    Its sections are the headings one level below its title (an only `#`
    heading at the top), or the shallowest level it uses when it has no title.
    Deeper headings stay inside the section they are under.
    """
    lines = text.splitlines()
    found = [(i, len(m.group(1)), m.group(2).strip())
             for i, line in enumerate(lines) if (m := _HEADING.match(line))]
    if not found:
        return text.strip(), []
    titled = found[0][1] == 1 and sum(1 for _, lv, _ in found if lv == 1) == 1
    rest = [lv for _, lv, _ in found if not (titled and lv == 1)]
    if not rest:
        return text.strip(), []
    top = min(rest)
    preamble: list[str] = []
    sections: list[Section] = []
    taken: set[str] = set()
    current: Section | None = None
    body: list[str] = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m and len(m.group(1)) == top:
            if current is not None:
                current.markdown = "\n".join(body).strip()
                sections.append(current)
            heading = m.group(2).strip()
            title = _plain_title(heading) or heading
            key = "part-" + (_SLUG_OK.sub("-", _ascii(title).lower()).strip("-")[:40]
                             or "section")
            while key in taken:
                key += "-2"
            taken.add(key)
            current, body = Section(key=key, label=title, heading=heading, markdown=""), []
            continue
        (body if current is not None else preamble).append(line)
    if current is not None:
        current.markdown = "\n".join(body).strip()
        sections.append(current)
    return "\n".join(preamble).strip(), sections


def split_guide(text: str) -> tuple[str, list[Section]]:
    """Cut the guide at its sections, and put them in the method's order.

    The known sections are found by name, at any heading level, and appear in
    the method's order whatever order the guide used. One more top-level
    section is allowed by the method — a paper-specific aid such as
    "Dictionary between the two formalisms", placed "where a reader naturally
    needs it" — so a heading at the level of the known ones becomes a section
    under its own title, kept directly after the section it followed. The
    bibliography comes last, where the method puts it. Anything deeper — a
    sub-heading inside Historical context, say — stays with the section it is
    under, which is what a reader expects and what keeps the rail from filling
    up with a guide's internal structure.
    """
    lines = text.splitlines()
    top: int | None = None
    for line in lines:
        m = _HEADING.match(line)
        if m and normalise_heading(m.group(2)) in _BY_HEADING \
                and normalise_heading(m.group(2)) != REFERENCES[1]:
            top = len(m.group(1))
            break

    preamble: list[str] = []
    sections: list[Section] = []
    current: Section | None = None
    body: list[str] = []
    taken: set[str] = set()

    def close() -> None:
        nonlocal current, body
        if current is not None:
            current.markdown = "\n".join(body).strip()
            sections.append(current)
        current, body = None, []

    for line in lines:
        m = _HEADING.match(line)
        if m:
            heading = m.group(2).strip()
            hit = _BY_HEADING.get(normalise_heading(heading))
            if hit and hit[0] not in taken:
                close()
                # A canonical key gives the UI its order and behavior, but the
                # companion itself names the section.  Never substitute a
                # registry label in the reader's sidebar: authors may choose a
                # more informative title, and it should agree with the page.
                key, fallback_label = hit
                label = _plain_title(heading) or fallback_label
                taken.add(key)
                current = Section(key=key, label=label, heading=heading,
                                  markdown="")
                continue
            if (current is not None and top is not None and 1 < len(m.group(1)) <= top
                    and not hit):
                close()
                title = _plain_title(heading) or heading
                key = "extra-" + (_SLUG_OK.sub("-", _ascii(title).lower()).strip("-")[:40]
                                  or "section")
                while key in taken:
                    key += "-2"
                taken.add(key)
                current = Section(key=key, label=title, heading=heading, markdown="")
                continue
        (body if current is not None else preamble).append(line)
    close()

    # The applied profile deliberately puts its validation-and-proof section
    # near the end.
    # Detect that explicit source order; older and pure guides retain
    # the established proof-forward order even if their headings were shuffled.
    source_keys = [sec.key for sec in sections if sec.key in _ORDER]
    order = (_APPLIED_ORDER
             if "history" in source_keys and "proofs" in source_keys
             and source_keys.index("history") < source_keys.index("proofs")
             else _ORDER)

    # Each paper-specific section rides with the known section before it.
    known = [sec for sec in sections if sec.key in _ORDER]
    riders: dict[str, list[Section]] = {}
    anchor = ""
    for sec in sections:
        if sec.key in _ORDER:
            anchor = sec.key
        elif sec.key.startswith("extra-"):
            riders.setdefault(anchor, []).append(sec)
    ordered = list(riders.get("", []))
    for sec in sorted(known, key=lambda x: order[x.key]):
        ordered.append(sec)
        ordered.extend(riders.get(sec.key, []))
    ordered.extend(sec for sec in sections if sec.key == REFERENCES[0])
    return "\n".join(preamble).strip(), ordered


COMPANION_FILES = ("companion.pdf", "companion.tex", "map.pdf", "map.mmd", "graph.json")


def read_companion(paper: Paper, reading: Path) -> Companion | None:
    """The companion in one reading's folder, cut into sections."""
    from deslopify.summary_pdf import dated_guide, normalise_companion_title

    guide = reading / "guide.md"
    if not guide.is_file():
        return None
    text = dated_guide(guide)
    if paper.kind != "paper":
        # Not a companion to one paper: its method decides its sections.
        preamble, sections = split_free(text)
        files = {name: name for name in COMPANION_FILES if (reading / name).is_file()}
        return Companion(title=paper.title, preamble=preamble, sections=sections,
                         files=files, missing=[],
                         results=read_results(reading) if paper.kind == "search" else None)
    preamble, sections = split_guide(text)

    files = {}
    for name in COMPANION_FILES:
        if (reading / name).is_file():
            files[name] = name
    if paper.source.is_file():
        files["source.pdf"] = "source.pdf"

    present = {s.key for s in sections}
    applied = (any(normalise_heading(s.heading) == "main findings" for s in sections)
               or ("history" in present and "proofs" in present
                   and next(s.key for s in sections if s.key in {"history", "proofs"})
                   == "history"))
    required = APPLIED_SECTIONS if applied else SECTIONS
    missing = [label for key, _, label in required
               if key not in present and key != "examples"]
    heading = re.search(r"^#\s+(Reader[’']s companion to .+)$", text, re.MULTILINE)
    title = (_plain_title(normalise_companion_title(heading.group(1), tex_quotes=False))
             if heading else paper.title)
    return Companion(title=title, preamble=preamble, sections=sections,
                     files=files, missing=missing)


# What a search may say about each paper it found, and how long each may be.
_RESULT_TEXT = {"title": 400, "venue": 300, "summary": 2000, "why": 1200,
                "group": 120, "found_by": 300, "status": 200}
_ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


def _web(url: Any) -> str:
    text = str(url or "").strip()
    return text[:600] if re.match(r"^https?://[^\s<>\"']+$", text) else ""


def read_results(reading: Path) -> list[dict[str, Any]]:
    """The papers a search found, from its `results.json`, kept to what the
    page shows — whatever else the file holds, and however it is malformed.

    Each has a title; the rest is what it gives: authors, year, venue, arXiv
    id, DOI, a link, a PDF link, a summary, why it answers the question, and a
    group heading. A link is only ever http(s); an arXiv id or DOI only one of
    the right shape. Nothing here is fetched.
    """
    path = reading / "results.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("results") if isinstance(raw, dict) else raw
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {k: " ".join(str(item.get(k) or "").split())[:n]
                                 for k, n in _RESULT_TEXT.items()}
        if not entry["title"]:
            continue
        authors = item.get("authors")
        if isinstance(authors, str):
            authors = [a.strip() for a in re.split(r",| and ", authors) if a.strip()]
        entry["authors"] = [" ".join(str(a).split())[:120]
                            for a in (authors if isinstance(authors, list) else [])][:30]
        year = item.get("year")
        entry["year"] = int(year) if str(year or "").strip().isdigit() \
            and 1600 <= int(year) <= 2100 else None
        arxiv = str(item.get("arxiv") or "").strip().removeprefix("arXiv:")
        entry["arxiv"] = arxiv if _ARXIV_ID.match(arxiv) else ""
        doi = str(item.get("doi") or "").strip().removeprefix("https://doi.org/")
        entry["doi"] = doi if _DOI.match(doi) else ""
        entry["url"] = _web(item.get("url")) or (
            f"https://arxiv.org/abs/{entry['arxiv']}" if entry["arxiv"] else
            f"https://doi.org/{entry['doi']}" if entry["doi"] else "")
        entry["pdf"] = _web(item.get("pdf")) or (
            f"https://arxiv.org/pdf/{entry['arxiv']}" if entry["arxiv"] else "")
        out.append(entry)
        if len(out) >= 200:
            break
    return out


def artefact_path(paper: Paper, reading: Path, name: str) -> Path | None:
    """Resolve a file the interface is allowed to serve."""
    if name == "source.pdf":
        return paper.source if paper.kind == "paper" and paper.source.is_file() else None
    allowed = {*COMPANION_FILES, "guide.md", "review-log.md"}
    if name not in allowed:
        return None
    path = reading / name
    if path.is_symlink() or not path.resolve().is_relative_to(reading.resolve()):
        return None
    return path if path.is_file() else None


# --------------------------------------------------------------------------
# the map, for drawing in the page
# --------------------------------------------------------------------------

ROLE_NAMES = {
    "H": "Hypothesis", "G": "Goal", "X": "Construction", "M": "Method",
    "C": "Claim", "Q": "Conclusion", "!": "Gap",
}


def _order_rows(rows: list[list[str]], parents: dict[str, set[str]]) -> list[list[str]]:
    """Reorder each row by where its parents sit — the barycentre heuristic.

    `layers` breaks ties alphabetically, which says nothing about the drawing
    and crosses arrows that need not cross. Two downward sweeps put each node
    under the average position of what it rests on, which untangles almost
    every graph of the size the method asks for.
    """
    ordered = [list(r) for r in rows]
    for _ in range(2):
        for i in range(1, len(ordered)):
            above = {node: pos for pos, node in enumerate(ordered[i - 1])}

            def centre(node: str, row: list[str] = ordered[i]) -> float:
                ps = [above[p] for p in parents.get(node, ()) if p in above]
                return sum(ps) / len(ps) if ps else row.index(node)

            ordered[i].sort(key=centre)
    return ordered


def read_map(reading: Path) -> dict[str, Any] | None:
    """The argument graph as something a page can draw and a reader can explore."""
    path = reading / "graph.json"
    if not path.is_file():
        return None
    from deslopify.analysis import analyse
    from deslopify.graph import Graph, GraphError
    from deslopify.render import layers

    try:
        graph = Graph.load(path)
    except GraphError:
        return None
    result = analyse(graph)
    crux = set(result.crux)

    parents: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.type == "SUPPORTS":
            parents.setdefault(e.target, set()).add(e.source)

    # Gaps carry no edges, so layering would put them all on the top row as if
    # they were hypotheses. They annotate the route; they go beneath it.
    gap_ids = [n.id for n in graph.nodes if n.id.startswith("!")]
    rows = [[i for i in row if not i.startswith("!")] for row in layers(graph)]
    rows = _order_rows([r for r in rows if r], parents)

    return {
        "rows": rows,
        "gaps": gap_ids,
        "nodes": {
            n.id: {"id": n.id, "role": ROLE_NAMES.get(n.id[0], "Step"),
                   "kind": n.id[0], "label": n.label,
                   "description": n.description, "quote": n.quote,
                   "crux": n.id in crux, "uncertain": n.uncertain}
            for n in graph.nodes
        },
        "edges": [{"source": e.source, "target": e.target, "type": e.type,
                   "justification": e.justification, "uncertain": e.uncertain}
                  for e in graph.edges],
    }


# --------------------------------------------------------------------------
# what the check found, and what became of it
# --------------------------------------------------------------------------

SEVERITY_NAMES = {"blocking": "Serious", "worth-fixing": "Worth fixing",
                  "minor": "Minor"}
_SEVERITY_ORDER = {"blocking": 0, "worth-fixing": 1, "minor": 2}

# The review's vocabulary, said the way a reader would say it.
KIND_NAMES = {
    "unfaithful": "Says something the paper does not",
    "ungrounded": "Not supported by the passage it cites",
    "missing": "Leaves out something the paper does",
    "misjudged": "Misjudges a step the paper skips",
    "unanswered": "Leaves a natural question unanswered",
    "flattened": "Makes a hard step look routine",
    "unclear": "Hard to follow",
}


def _place(where: str, extra: dict[str, tuple[str, str]] | None = None) -> dict[str, Any]:
    """Where a finding points, as a section a reader can jump to."""
    kind, _, target = where.partition(":")
    if kind == "guide":
        heading = normalise_heading(target)
        known_headings = {**_BY_HEADING, **(extra or {})}
        hit = known_headings.get(heading)
        if hit is None:
            for known, value in known_headings.items():
                if len(heading) >= 4 and (heading in known or known in heading):
                    hit = value
                    break
        if hit:
            key, label = hit
            return {"section": key, "label": label}
        return {"section": None, "label": target.strip() or "the explanation"}
    if kind == "graph":
        return {"section": "map", "label": f"the map ({target.strip()})",
                "node": target.split("->")[0].strip()}
    if kind == "appeal":
        return {"section": "thin", "label": "a step the paper skips"}
    return {"section": None, "label": "a passage in the paper"}


def read_review(reading: Path) -> dict[str, Any] | None:
    """The grounded verifier's findings, each with its answer if there is one.

    Returned whether or not the reviser ran. If it did not, a reader needs this
    more, not less: it is the only record of where the explanation they are
    holding is known to be unreliable.
    """
    reviews = sorted(reading.glob("review-*.json"))
    if not reviews:
        return None
    path = reviews[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    round_no = path.stem.split("-")[-1]
    answers: dict[str, dict[str, Any]] = {}
    disp = reading / f"dispositions-{round_no}.json"
    revised = disp.is_file()
    if revised:
        try:
            for d in json.loads(disp.read_text(encoding="utf-8")).get("dispositions", []):
                answers[str(d.get("id"))] = {
                    "disposition": d.get("disposition", ""),
                    "note": d.get("note", ""),
                }
        except (OSError, ValueError):
            revised = False

    # A finding may point at the guide's own paper-specific section.
    extra: dict[str, tuple[str, str]] = {}
    guide = reading / "guide.md"
    if guide.is_file():
        for sec in split_guide(guide.read_text(encoding="utf-8", errors="replace"))[1]:
            if sec.key.startswith("extra-"):
                extra[normalise_heading(sec.heading)] = (sec.key, sec.label)

    findings = []
    for f in data.get("findings", []) or []:
        severity = str(f.get("severity", "minor"))
        findings.append({
            "id": f.get("id", ""),
            "severity": severity,
            "severity_label": SEVERITY_NAMES.get(severity, severity.title()),
            "kind": f.get("kind", ""),
            "kind_label": KIND_NAMES.get(str(f.get("kind")), str(f.get("kind", ""))),
            "place": _place(str(f.get("where", "")), extra),
            "problem": f.get("problem", ""),
            "fix": f.get("fix", ""),
            # Lenient in what it displays, where the lab is strict in what it
            # accepts: a small model has been seen writing "quotation" for
            # "quote", which fails validation — but the passage is still the most
            # useful thing a reader can be shown about the finding.
            "quote": f.get("quote") or f.get("quotation") or "",
            "answer": answers.get(str(f.get("id"))),
        })
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 3), f["id"]))
    return {
        "summary": data.get("summary", ""),
        "revised": revised,
        "findings": findings,
        "counts": {s: sum(1 for f in findings if f["severity"] == s)
                   for s in ("blocking", "worth-fixing", "minor")},
    }
