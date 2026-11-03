"""Problem discovery, metadata, file classification, locking, and state.json.

A problem is a directory under `paths.problems`. It carries static files the
agent may read but not modify, and writable files it maintains across sessions.
The static/writable split is enforced at runtime by a PreToolUse hook; this
module is the single place that decides which is which.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

STATUSES = ("open", "partial", "solved", "disproved", "stalled")
Status = Literal["open", "partial", "solved", "disproved", "stalled"]

# Directories that are never problems.
_SKIP_DIRS = {".git", ".idea", ".vscode", "__pycache__", "node_modules", ".atelier"}

# Paths the agent may write, relative to the problem root.
WRITABLE_GLOBS = ("progress/**", "work/**", "progress/*", "work/*")
# Paths that are the human's specification and must not be rewritten.
STATIC_GLOBS = ("problem.md", "problem.toml", "strategy/**", "strategy/*", "refs/**", "refs/*")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ProblemError(Exception):
    pass


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ProblemMeta:
    title: str
    slug: str
    tags: list[str] = field(default_factory=list)
    difficulty: str = "research"
    statement: str = "problem.md"
    strategy: list[str] = field(default_factory=lambda: ["strategy/*.md"])
    benchmark: bool = False
    notes: str = ""
    answer: str | None = None
    answer_check: str | None = None
    created: str = ""

    @classmethod
    def load(cls, root: Path) -> ProblemMeta:
        path = root / "problem.toml"
        slug = root.name
        if not path.is_file():
            return cls(title=_prettify(slug), slug=slug)
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        known = set(cls.__slots__)
        data = {k: v for k, v in raw.items() if k in known}
        data.setdefault("title", _prettify(slug))
        data["slug"] = slug  # folder name always wins
        if isinstance(data.get("created"), (datetime,)):
            data["created"] = data["created"].isoformat()
        elif data.get("created") is not None:
            data["created"] = str(data["created"])
        return cls(**data)

    def dump(self, root: Path) -> None:
        lines = [
            f'title      = {json.dumps(self.title)}',
            f'slug       = {json.dumps(self.slug)}',
            f'tags       = {json.dumps(self.tags)}',
            f'difficulty = {json.dumps(self.difficulty)}',
            f'statement  = {json.dumps(self.statement)}',
            f'strategy   = {json.dumps(self.strategy)}',
            f'benchmark  = {"true" if self.benchmark else "false"}',
        ]
        if self.created:
            lines.append(f'created    = {json.dumps(self.created)}')
        if self.notes:
            lines.append(f'notes      = {json.dumps(self.notes)}')
        if self.answer:
            lines.append(f'answer     = {json.dumps(self.answer)}')
        if self.answer_check:
            lines.append(f'answer_check = {json.dumps(self.answer_check)}')
        (root / "problem.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prettify(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


# --------------------------------------------------------------------------
# state.json
# --------------------------------------------------------------------------

STATE_TEMPLATE: dict[str, Any] = {
    "schema": 1,
    "status": "open",
    "confidence": 0.0,
    "one_line": "",
    "established": [],
    "conjectures": [],
    "blockers": [],
    "next_steps": [],
    "dead_ends": [],
    "updated": None,
    "updated_by_run": None,
}


def validate_state(data: Any) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["state.json must contain a JSON object"]
    status = data.get("status")
    if status not in STATUSES:
        errs.append(f"`status` must be one of {', '.join(STATUSES)} (got {status!r})")
    conf = data.get("confidence")
    if conf is not None and not (isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0):
        errs.append("`confidence` must be a number in [0, 1]")
    for key in ("established", "conjectures", "blockers", "next_steps", "dead_ends"):
        if key in data and not isinstance(data[key], list):
            errs.append(f"`{key}` must be a list")
    if not str(data.get("one_line") or "").strip():
        errs.append("`one_line` must be a non-empty one-sentence summary of the current state")
    return errs


def read_state(root: Path) -> dict[str, Any]:
    path = root / "progress" / "state.json"
    if not path.is_file():
        return dict(STATE_TEMPLATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(STATE_TEMPLATE) | {"one_line": "(state.json is unreadable)"}
    return data if isinstance(data, dict) else dict(STATE_TEMPLATE)


def write_state(root: Path, data: dict[str, Any]) -> None:
    path = root / "progress" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# locking
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Lock:
    run_id: str
    pid: int
    host: str
    harness: str
    started_at: str

    def alive(self) -> bool:
        if self.pid <= 0:
            return False
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


class LockHeld(ProblemError):
    def __init__(self, lock: Lock) -> None:
        super().__init__(
            f"problem is locked by run {lock.run_id} (harness {lock.harness}, "
            f"pid {lock.pid}, since {lock.started_at})"
        )
        self.lock = lock


def lock_path(root: Path) -> Path:
    return root / ".atelier" / "lock.json"


def read_lock(root: Path) -> Lock | None:
    p = lock_path(root)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return Lock(**{k: d[k] for k in Lock.__slots__})
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def acquire_lock(root: Path, run_id: str, harness: str) -> Lock:
    """Atomically acquire the per-problem exclusive lock.

    A lock whose owning process is gone is reclaimed automatically — a crashed
    session must never wedge a problem permanently.
    """
    import socket

    p = lock_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = Lock(
        run_id=run_id, pid=os.getpid(), host=socket.gethostname(),
        harness=harness, started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    payload = json.dumps({k: getattr(lock, k) for k in Lock.__slots__}, indent=2)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        existing = read_lock(root)
        if existing and existing.alive():
            raise LockHeld(existing) from None
        p.unlink(missing_ok=True)
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return lock


def release_lock(root: Path, run_id: str | None = None) -> bool:
    existing = read_lock(root)
    if existing is None:
        return False
    if run_id is not None and existing.run_id != run_id:
        return False
    lock_path(root).unlink(missing_ok=True)
    return True


# --------------------------------------------------------------------------
# file classification
# --------------------------------------------------------------------------


def _matches(rel: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel, g) or rel.startswith(g.rstrip("*").rstrip("/") + "/")
               for g in globs)


def classify(rel_path: str, meta: ProblemMeta | None = None) -> Literal["static", "writable", "other"]:
    """Decide whether a problem-relative path is static, writable, or neither."""
    rel = rel_path.lstrip("./")
    if meta and rel == meta.statement:
        return "static"
    if _matches(rel, STATIC_GLOBS):
        return "static"
    if _matches(rel, WRITABLE_GLOBS):
        return "writable"
    if rel.startswith(".atelier/"):
        return "other"
    # Loose files at the problem root default to static: they are almost always
    # something the human put there.
    return "static" if "/" not in rel else "other"


@dataclass(slots=True)
class FileEntry:
    path: str            # problem-relative
    name: str
    is_dir: bool
    size: int
    modified: float
    kind: str            # static | writable | other
    ext: str


def list_files(root: Path, meta: ProblemMeta | None = None,
               *, subdir: str = "") -> list[FileEntry]:
    base = (root / subdir).resolve() if subdir else root
    if not str(base).startswith(str(root.resolve())):
        raise ProblemError("path escapes the problem directory")
    if not base.is_dir():
        return []
    out: list[FileEntry] = []
    for p in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if p.name.startswith(".") and p.name != ".atelier":
            continue
        if p.name in _SKIP_DIRS and p.is_dir():
            continue
        rel = str(p.relative_to(root))
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(FileEntry(
            path=rel, name=p.name, is_dir=p.is_dir(),
            size=0 if p.is_dir() else st.st_size, modified=st.st_mtime,
            kind=classify(rel, meta), ext=p.suffix.lower().lstrip("."),
        ))
    return out


def walk_files(root: Path, meta: ProblemMeta | None = None) -> list[FileEntry]:
    """Recursive listing, used for the file picker and prompt assembly."""
    out: list[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            try:
                st = p.stat()
            except OSError:
                continue
            out.append(FileEntry(
                path=rel, name=name, is_dir=False, size=st.st_size,
                modified=st.st_mtime, kind=classify(rel, meta),
                ext=p.suffix.lower().lstrip("."),
            ))
    return out


def safe_join(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing traversal outside it."""
    target = (root / rel).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise ProblemError(f"path {rel!r} escapes the problem directory")
    return target


# --------------------------------------------------------------------------
# the Problem object
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Problem:
    root: Path
    meta: ProblemMeta

    @property
    def slug(self) -> str:
        return self.meta.slug

    @property
    def progress_dir(self) -> Path:
        return self.root / "progress"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    def state(self) -> dict[str, Any]:
        return read_state(self.root)

    def status(self) -> str:
        s = self.state().get("status")
        return s if s in STATUSES else "open"

    def lock(self) -> Lock | None:
        lk = read_lock(self.root)
        if lk and not lk.alive():
            return None
        return lk

    def statement_path(self) -> Path | None:
        p = self.root / self.meta.statement
        return p if p.is_file() else None

    def strategy_paths(self) -> list[Path]:
        out: list[Path] = []
        for pattern in self.meta.strategy:
            out.extend(sorted(self.root.glob(pattern)))
        return [p for p in out if p.is_file()]

    def has_pdf(self) -> bool:
        return (self.progress_dir / "math.pdf").is_file()

    def ensure_scaffold(self) -> None:
        """Create the writable directories and seed files if missing."""
        (self.root / "progress").mkdir(parents=True, exist_ok=True)
        (self.root / "work").mkdir(parents=True, exist_ok=True)
        (self.root / ".atelier").mkdir(parents=True, exist_ok=True)
        state_file = self.progress_dir / "state.json"
        if not state_file.is_file():
            seed = dict(STATE_TEMPLATE)
            seed["one_line"] = "No sessions yet."
            write_state(self.root, seed)
        labbook = self.progress_dir / "labbook.md"
        if not labbook.is_file():
            labbook.write_text(
                f"# Lab book — {self.meta.title}\n\n"
                "Session records are appended by atelier. Text you write between\n"
                "entries is preserved.\n",
                encoding="utf-8",
            )
        preamble = self.progress_dir / "preamble.tex"
        if not preamble.is_file():
            preamble.write_text(PREAMBLE_TEX, encoding="utf-8")
        mathtex = self.progress_dir / "math.tex"
        if not mathtex.is_file():
            mathtex.write_text(
                MATH_TEX_TEMPLATE.replace("__TITLE__", _tex_escape(self.meta.title)),
                encoding="utf-8",
            )


def _tex_escape(s: str) -> str:
    for a, b in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
                 ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
                 ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")):
        s = s.replace(a, b)
    return s


PREAMBLE_TEX = r"""% Provided by atelier. Extend freely; do not remove the theorem environments,
% which the lab-book tooling and the write-up skill both rely on.
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath, amssymb, amsthm, mathtools}
\usepackage[margin=1.1in]{geometry}
\usepackage{hyperref}
\usepackage{cleveref}
\usepackage{enumitem}
\usepackage{tabularx}

\theoremstyle{plain}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{proposition}[theorem]{Proposition}
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{corollary}[theorem]{Corollary}
\newtheorem{conjecture}[theorem]{Conjecture}

\theoremstyle{definition}
\newtheorem{definition}[theorem]{Definition}
\newtheorem{example}[theorem]{Example}

\theoremstyle{remark}
\newtheorem{remark}[theorem]{Remark}

% \status{...} records the confidence level of a claim inline.
\newcommand{\status}[1]{\medskip\noindent\textbf{Status:} #1\medskip}
% Mark anything not yet verified.
\newcommand{\conjectural}{\textsuperscript{[CONJECTURE]}}
"""

MATH_TEX_TEMPLATE = r"""\documentclass[11pt]{article}
\input{preamble}

\title{__TITLE__}
\author{}
\date{\today}

\begin{document}
\maketitle

\section{Problem}
% Restate the problem precisely, in the notation you will actually use.

\section{Established results}
% Only material that has survived verification belongs here.

\section{Conjectures and partial results}
% Anything believed but not proved. Mark each with \conjectural.

\section{Open questions and next steps}

\section{Failed approaches}
% What was tried and why it did not work. This section is as valuable as the
% others: it stops future sessions from re-walking dead ends.

\end{document}
"""


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


def discover(problems_dir: Path) -> list[Problem]:
    if not problems_dir.is_dir():
        return []
    out: list[Problem] = []
    for entry in sorted(problems_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.name in _SKIP_DIRS or entry.name.startswith("."):
            continue
        out.append(Problem(root=entry, meta=ProblemMeta.load(entry)))
    return out


def load_problem(problems_dir: Path, slug: str) -> Problem:
    root = problems_dir / slug
    if not root.is_dir():
        raise ProblemError(f"no such problem: {slug}")
    return Problem(root=root, meta=ProblemMeta.load(root))


def create_problem(problems_dir: Path, slug: str, title: str = "",
                   statement: str = "") -> Problem:
    if not SLUG_RE.match(slug):
        raise ProblemError(
            "slug must be lowercase letters, digits, dot, dash or underscore, "
            "starting with a letter or digit"
        )
    root = problems_dir / slug
    if root.exists():
        raise ProblemError(f"{slug} already exists")
    root.mkdir(parents=True)
    (root / "strategy").mkdir()
    (root / "refs").mkdir()
    meta = ProblemMeta(
        title=title or _prettify(slug), slug=slug,
        created=datetime.now(UTC).date().isoformat(),
    )
    meta.dump(root)
    (root / "problem.md").write_text(
        statement or f"# {meta.title}\n\n_State the problem here._\n", encoding="utf-8"
    )
    problem = Problem(root=root, meta=meta)
    problem.ensure_scaffold()
    return problem


def snapshot_static(problem: Problem, dest: Path, *, include_work: bool = False) -> None:
    """Copy only the static inputs into `dest` — the one-shot workspace.

    Progress is deliberately excluded: a one-shot session must be blind to it,
    otherwise the comparison against a standard session is meaningless.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for name in (problem.meta.statement, "problem.toml"):
        src = problem.root / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    for sub in ("strategy", "refs"):
        src = problem.root / sub
        if src.is_dir():
            shutil.copytree(src, dest / sub, dirs_exist_ok=True)
    if include_work and (problem.root / "work").is_dir():
        shutil.copytree(problem.root / "work", dest / "work", dirs_exist_ok=True)
    # Seed the writable scaffold. Without preamble.tex and a math.tex skeleton
    # the session is told to maintain a document it has no way to compile.
    progress = dest / "progress"
    progress.mkdir(exist_ok=True)
    (dest / "work").mkdir(exist_ok=True)
    if not (progress / "preamble.tex").is_file():
        (progress / "preamble.tex").write_text(PREAMBLE_TEX, encoding="utf-8")
    if not (progress / "math.tex").is_file():
        (progress / "math.tex").write_text(
            MATH_TEX_TEMPLATE.replace("__TITLE__", _tex_escape(problem.meta.title)),
            encoding="utf-8")
    if not (progress / "state.json").is_file():
        seed = dict(STATE_TEMPLATE)
        seed["one_line"] = "Blind one-shot session; no prior state available."
        (progress / "state.json").write_text(
            json.dumps(seed, indent=2) + "\n", encoding="utf-8")


def snapshot_problem(problem: Problem, dest: Path) -> None:
    """Copy the current problem into an isolated working directory.

    Reading harnesses produce substantial derived files, but they do not own
    the mathematical state of the problem. Giving each such run a snapshot
    lets several readers work on the same source at once without interleaving
    writes to ``progress/reading`` or consuming one another's session notes.

    The lock directory is process state, not an input. Common generated caches
    are omitted as well; everything else is copied so a selected source under
    ``progress/`` (notably an uploaded PDF) remains available.
    """
    problem.ensure_scaffold()
    shutil.copytree(
        problem.root,
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".atelier", "__pycache__", ".DS_Store", "*.pyc",
            "*.aux", "*.log", "*.fls", "*.fdb_latexmk", "*.synctex.gz",
        ),
    )
    # These are products of a particular reading session, not inputs to the
    # next one. Starting them empty also prevents a failed run from appearing
    # to have delivered files merely copied from an earlier run.
    reading = dest / "progress" / "reading"
    shutil.rmtree(reading, ignore_errors=True)
    reading.mkdir(parents=True, exist_ok=True)
    (dest / "progress" / "_session_notes.md").unlink(missing_ok=True)
