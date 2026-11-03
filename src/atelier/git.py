"""Git integration for the problems repository.

Every session gets a baseline commit before it starts and a result commit when
it ends, so "what did this session actually change" is always answerable and
"undo that session" is always possible. Failures here are never fatal: a broken
git state must not stop mathematics from happening.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config
from .problems import Problem

_TIMEOUT = 60


def _run(cfg: Config, args: list[str], *, cwd: Path | None = None,
         check: bool = False) -> subprocess.CompletedProcess[str]:
    env_args = [
        "-c", f"user.name={cfg.git.author_name}",
        "-c", f"user.email={cfg.git.author_email}",
        "-c", "commit.gpgsign=false",
    ]
    return subprocess.run(
        ["git", *env_args, *args],
        cwd=str(cwd or cfg.problems_dir),
        capture_output=True, text=True, timeout=_TIMEOUT, check=check,
    )


def is_repo(cfg: Config) -> bool:
    """True only if the problems directory is itself the root of a repository.

    `--git-dir` alone answers "is this inside *any* repository", which is a
    different question with a damaging answer: when problems live inside the
    atelier checkout — the default for a fresh copy — it reports the atelier
    repository, `ensure_repo` then declines to create one, and every session
    commit silently does nothing because the path is ignored there. Comparing
    the top level makes the nested case initialise its own repository, which is
    what the design assumes and what keeps research out of the tool's history.
    """
    r = _run(cfg, ["rev-parse", "--show-toplevel"])
    if r.returncode != 0 or not r.stdout.strip():
        return False
    try:
        return Path(r.stdout.strip()).resolve() == cfg.problems_dir.resolve()
    except OSError:
        return False


def ensure_repo(cfg: Config) -> bool:
    """Initialise the problems directory as a git repo if it is not one."""
    if not cfg.problems_dir.is_dir():
        return False
    if is_repo(cfg):
        _ensure_gitignore(cfg)
        return True
    r = _run(cfg, ["init", "-q"])
    if r.returncode != 0:
        return False
    _ensure_gitignore(cfg)
    _run(cfg, ["add", "-A"])
    _run(cfg, ["commit", "-q", "-m", "atelier: initial import of problems"])
    return True


GITIGNORE = """\
# managed by atelier
.atelier/
*.aux
*.log
*.fls
*.fdb_latexmk
*.out
*.toc
*.synctex.gz
*.bbl
*.blg
__pycache__/
.ipynb_checkpoints/
.DS_Store
"""


def _ensure_gitignore(cfg: Config) -> None:
    p = cfg.problems_dir / ".gitignore"
    if not p.is_file():
        p.write_text(GITIGNORE, encoding="utf-8")
        return
    existing = p.read_text(encoding="utf-8")
    if ".atelier/" not in existing:
        p.write_text(existing.rstrip() + "\n\n" + GITIGNORE, encoding="utf-8")


def head(cfg: Config) -> str | None:
    r = _run(cfg, ["rev-parse", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else None


def baseline_commit(cfg: Config, problem: Problem, run_id: str) -> str | None:
    """Commit any uncommitted state before a session starts, and return the SHA.

    Without this the session diff would be contaminated by whatever was already
    dirty in the working tree.
    """
    if not cfg.git.enabled or not ensure_repo(cfg):
        return None
    rel = problem.root.relative_to(cfg.problems_dir)
    _run(cfg, ["add", "-A", "--", str(rel)])
    status = _run(cfg, ["status", "--porcelain", "--", str(rel)])
    if status.stdout.strip():
        _run(cfg, ["commit", "-q", "-m",
                   f"atelier: baseline before {run_id} ({problem.slug})"])
    return head(cfg)


def commit_session(cfg: Config, problem: Problem, message: str) -> str | None:
    if not cfg.git.enabled or not is_repo(cfg):
        return None
    rel = problem.root.relative_to(cfg.problems_dir)
    _run(cfg, ["add", "-A", "--", str(rel)])
    status = _run(cfg, ["status", "--porcelain", "--", str(rel)])
    if not status.stdout.strip():
        return None
    r = _run(cfg, ["commit", "-q", "-m", message])
    return head(cfg) if r.returncode == 0 else None


def describe_changes(cfg: Config, problem: Problem,
                     base: str | None) -> list[tuple[str, str]]:
    """List (path, "+a −b" | "new" | "deleted") for the problem folder since `base`."""
    if not is_repo(cfg):
        return []
    rel = str(problem.root.relative_to(cfg.problems_dir))
    args = ["diff", "--numstat"]
    if base:
        args.append(base)
    args += ["--", rel]
    r = _run(cfg, args)
    out: list[tuple[str, str]] = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        short = path[len(rel) + 1:] if path.startswith(rel + "/") else path
        if added == "-" and removed == "-":
            out.append((short, "binary changed"))
        else:
            out.append((short, f"+{added} −{removed}"))

    # Untracked files never appear in `git diff`; list them explicitly.
    r2 = _run(cfg, ["ls-files", "--others", "--exclude-standard", "--", rel])
    for path in r2.stdout.splitlines():
        short = path[len(rel) + 1:] if path.startswith(rel + "/") else path
        out.append((short, "new"))
    return out


def diff_text(cfg: Config, problem: Problem, base: str | None,
              head_ref: str | None = None) -> str:
    if not is_repo(cfg):
        return ""
    rel = str(problem.root.relative_to(cfg.problems_dir))
    args = ["diff", "--patch", "--no-color"]
    if base:
        args.append(f"{base}..{head_ref}" if head_ref else base)
    args += ["--", rel]
    return _run(cfg, args).stdout


def revert_to(cfg: Config, problem: Problem, sha: str) -> bool:
    """Restore the problem folder to `sha`. Used by Abandon."""
    if not is_repo(cfg):
        return False
    rel = str(problem.root.relative_to(cfg.problems_dir))
    r = _run(cfg, ["checkout", sha, "--", rel])
    if r.returncode != 0:
        return False
    _run(cfg, ["clean", "-fd", "--", rel])
    return True


def log_for_problem(cfg: Config, problem: Problem, limit: int = 50) -> list[dict[str, str]]:
    if not is_repo(cfg):
        return []
    rel = str(problem.root.relative_to(cfg.problems_dir))
    r = _run(cfg, ["log", f"-{limit}", "--format=%H%x1f%an%x1f%aI%x1f%s", "--", rel])
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            out.append({"sha": parts[0], "author": parts[1],
                        "date": parts[2], "subject": parts[3]})
    return out
