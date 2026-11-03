"""Updates: whether a newer unpacman is waiting, and taking it in one click.

unpacman is a copy of the public repository, on the branch it was installed
from (`mac`, or `windows`), whether it was installed from a package or from
GitHub; it fetches that branch only. An update moves that copy forward to what
the branch holds upstream
(`git merge --ff-only`), installs the Python packages that version needs
(`uv sync --frozen`), and restarts. It is offered only when that is safe:

- the copy has no changes of its own — no edited files the repository tracks
  and no commits of its own. A developer's copy is updated by its developer,
  with git; this says so rather than touching it;
- no reading is running, since a restart stops it.

A copy installed from a package before unpacman was public followed the
bundle that installer kept in `var/package/`, not GitHub: it has nothing to
look for online until a newer package's installer moves it to the public
repository — which this says, instead of reporting GitHub unreachable.

It never touches what belongs to the person: papers, readings, settings and an
edited method all live in files the repository ignores, which a fast-forward
does not see. And the new version is started once, on the side
(`--self-check`), before the running one gives way: if it cannot even start,
the copy goes back to where it was and says why.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHECK_EVERY = 6 * 3600.0       # seconds between automatic checks
FIRST_CHECK_AFTER = 30.0       # after start-up, so a check never slows it


def _git_env() -> dict[str, str]:
    """git that never stops to ask: no password prompt, no SSH passphrase."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15"}


def git_usable() -> bool:
    """Whether git can run here without asking anything of anyone.

    On a Mac without Apple's command-line tools, /usr/bin/git exists but only
    to offer installing them — a dialog, every time it is run.
    """
    git = shutil.which("git")
    if git is None:
        return False
    if sys.platform == "darwin" and git == "/usr/bin/git":
        done = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True,
                              check=False)
        return done.returncode == 0
    return True


def _git(root: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          env=_git_env(), stdin=subprocess.DEVNULL, check=False)


@dataclass(slots=True)
class Version:
    sha: str
    date: str                       # the commit's date, YYYY-MM-DD
    branch: str
    upstream: str                   # "origin/unpackman", or ""

    @property
    def short(self) -> str:
        return self.sha[:7]

    def to_dict(self) -> dict[str, Any]:
        return {"sha": self.sha, "short": self.short, "date": self.date,
                "branch": self.branch, "upstream": self.upstream}


def current(root: Path) -> Version | None:
    """The version of this copy, or None when it is not a git checkout."""
    if not (root / ".git").exists() or not git_usable():
        return None
    try:
        head = _git(root, "log", "-1", "--format=%H %cs")
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except (OSError, subprocess.SubprocessError):
        return None
    if head.returncode != 0 or not head.stdout.strip():
        return None
    sha, _, date = head.stdout.strip().partition(" ")
    return Version(sha=sha, date=date, branch=branch.stdout.strip(),
                   upstream=upstream.stdout.strip() if upstream.returncode == 0 else "")


@dataclass(slots=True)
class Status:
    version: Version | None = None
    checked: float | None = None    # when upstream was last fetched
    behind: int = 0
    ahead: int = 0
    dirty: list[str] = field(default_factory=list)
    news: list[dict[str, str]] = field(default_factory=list)
    error: str = ""                 # why the last check could not be made
    # Set for a copy installed from a package: what var/unpacman-package.json
    # says of it (when it was made, from which commit), or {} if nothing.
    package: dict[str, Any] | None = None

    @property
    def why_not(self) -> str:
        """Why this copy cannot update itself, said plainly; "" if it can."""
        if self.version is None:
            return ("This copy of unpacman is not one it can update itself: it is not "
                    "connected to the shared repository.")
        if self.package is not None:
            made = str(self.package.get("made") or "")
            with contextlib.suppress(ValueError):
                made = _dt.date.fromisoformat(made).strftime("%-d %B %Y")
            return ("This copy was installed from a package"
                    + (f" made on {made}" if made else "") + ". To update it, run the "
                    "installer of a newer package, as you did the first time.")
        if not self.version.upstream:
            return ("This copy is not following a branch of the shared repository, so "
                    "it has nothing to update from.")
        if self.ahead:
            return ("This copy has changes of its own that are not in the shared "
                    "repository. Update it with git.")
        if self.dirty:
            n = len(self.dirty)
            return (f"This copy has {n} edited file{'s' if n != 1 else ''} of its own "
                    f"({', '.join(self.dirty[:3])}{'…' if n > 3 else ''}). Update it with git.")
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version.to_dict() if self.version else None,
                "checked": self.checked, "behind": self.behind, "ahead": self.ahead,
                "dirty": self.dirty, "news": self.news, "error": self.error,
                "why_not": self.why_not, "package": self.package,
                "available": self.behind > 0 and not self.why_not}


def package_info(root: Path) -> dict[str, Any] | None:
    """For a copy installed from a package — its origin is the bundle the
    installer keeps — what the package said of itself; None otherwise."""
    try:
        origin = _git(root, "remote", "get-url", "origin", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if origin.returncode != 0 or not origin.stdout.strip().endswith(".bundle"):
        return None
    try:
        data = json.loads((root / "var" / "unpacman-package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def measure(root: Path) -> Status:
    """Where this copy stands against upstream, from what was last fetched."""
    status = Status(version=current(root))
    v = status.version
    if v is None:
        return status
    status.package = package_info(root)
    if not v.upstream:
        return status
    counts = _git(root, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if counts.returncode == 0:
        ahead, _, behind = counts.stdout.strip().partition("\t")
        status.ahead, status.behind = int(ahead or 0), int(behind or 0)
    changed = _git(root, "status", "--porcelain", "--untracked-files=no")
    status.dirty = [line[3:].strip() for line in changed.stdout.splitlines() if line.strip()]
    if status.behind:
        log = _git(root, "log", "--no-merges", "--format=%cs%x09%an%x09%s", "-n", "20",
                   "HEAD..@{u}")
        for line in log.stdout.splitlines():
            date, _, rest = line.partition("\t")
            author, _, subject = rest.partition("\t")
            status.news.append({"date": date, "author": author, "subject": subject})
    return status


def fetch(root: Path) -> str:
    """Bring this copy's record of upstream up to date; "" or why not."""
    v = current(root)
    if v is None or not v.upstream or package_info(root) is not None:
        return ""                     # a package's copy has nothing to fetch online
    remote, _, branch = v.upstream.partition("/")
    try:
        done = _git(root, "fetch", "--quiet", remote, branch, timeout=90)
    except subprocess.TimeoutExpired:
        return "Checking for a new version took too long. Check the internet connection."
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        text = detail[-1] if detail else ""
        if any(k in text.lower() for k in ("authentication", "permission denied",
                                           "could not read username", "repository not found",
                                           "terminal prompts disabled")):
            return ("unpacman could not sign in to GitHub to look for a new version. "
                    "Run the installer again to sign in.")
        return ("unpacman could not reach GitHub to look for a new version"
                + (f" ({text[:160]})" if text else "") + ".")
    return ""


def find_uv() -> str | None:
    """uv, wherever its installer or Homebrew put it."""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv",
                      Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv")):
        if candidate.is_file():
            return str(candidate)
    return None


class Updater:
    """Checks for a new version (automatically, for a copy started from its
    icon), and applies one when asked."""

    def __init__(self, root: Path, *, auto: bool = False) -> None:
        self.root = root
        self.auto = auto
        self.state = "idle"           # idle | checking | updating | restarting | failed
        self.step = ""
        self.error = ""
        self.notes: list[str] = []
        self.restart = False          # set when the new version should take over
        self._status = Status()
        self._task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return self._status.to_dict() | {"state": self.state, "step": self.step,
                                         "error": self.error or self._status.error,
                                         "notes": self.notes, "auto": self.auto}

    async def refresh(self, *, fetch_first: bool) -> dict[str, Any]:
        if self.state in ("updating", "restarting"):
            return self.status()
        self.state, self.error = "checking", ""
        try:
            error = await asyncio.to_thread(fetch, self.root) if fetch_first else ""
            status = await asyncio.to_thread(measure, self.root)
            status.error = error
            status.checked = time.time() if fetch_first and not error else self._status.checked
            self._status = status
        finally:
            if self.state == "checking":
                self.state = "idle"
        return self.status()

    async def watch(self) -> None:
        """Look for a new version now and then — only for a copy whose icon was
        made (`--make-app`): a developer's copy is not fetched behind their back."""
        await self.refresh(fetch_first=False)
        if not self.auto or self._status.package is not None:
            return
        await asyncio.sleep(FIRST_CHECK_AFTER)
        while True:
            with contextlib.suppress(Exception):
                await self.refresh(fetch_first=True)
            await asyncio.sleep(CHECK_EVERY)

    def start(self, *, readings_running: int) -> None:
        """Begin updating, or raise UpdateRefused with the reason it cannot."""
        if self.state in ("updating", "restarting"):
            return
        if readings_running:
            raise UpdateRefused(
                f"{readings_running} reading{'s are' if readings_running != 1 else ' is'} "
                "running. Update when it has finished: restarting would stop it.")
        status = measure(self.root)
        if status.why_not:
            raise UpdateRefused(status.why_not)
        if not status.behind:
            raise UpdateRefused("This copy is already up to date.")
        self.state, self.error, self.notes = "updating", "", []
        self._task = asyncio.get_running_loop().create_task(self._apply())

    async def _apply(self) -> None:
        try:
            await asyncio.to_thread(self._apply_sync)
            self.state = "restarting"
            self.restart = True
        except UpdateFailed as exc:
            self.state, self.error, self.step = "failed", str(exc), ""
        except Exception as exc:                                   # noqa: BLE001
            self.state, self.error, self.step = "failed", f"{type(exc).__name__}: {exc}", ""

    def _apply_sync(self) -> None:
        root = self.root
        before = current(root)
        if before is None:
            raise UpdateFailed("This copy is not a git checkout.")
        self.step = "Fetching the new version…"
        if (error := fetch(root)):
            raise UpdateFailed(error)
        self.step = "Moving to the new version…"
        merged = _git(root, "merge", "--ff-only", "--quiet", "@{u}", timeout=120)
        if merged.returncode != 0:
            raise UpdateFailed("The new version could not be taken in: "
                               + (merged.stderr.strip().splitlines() or ["git refused"])[-1])
        try:
            self.step = "Installing what the new version needs…"
            self._sync()
            self.step = "Checking that the new version starts…"
            self._self_check()
        except UpdateFailed as exc:
            self.step = "Going back to the version you had…"
            _git(root, "reset", "--hard", "--quiet", before.sha, timeout=120)
            with contextlib.suppress(UpdateFailed):
                self._sync()
            said = str(exc).rstrip()
            raise UpdateFailed(f"{said}{'' if said.endswith('.') else '.'} Nothing was "
                               "changed: this is still the version you had.") from exc
        self.step = "Bringing Claude Code up to date…"
        self.notes.append(_update_claude())
        self.step = "Restarting…"

    def _sync(self) -> None:
        uv = find_uv()
        if uv is None:
            raise UpdateFailed("uv, which installs unpacman's Python packages, was not found.")
        try:
            done = subprocess.run([uv, "sync", "--frozen", "--quiet"], cwd=self.root,
                                  capture_output=True, text=True, timeout=900,
                                  stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired as exc:
            raise UpdateFailed("Installing the new version's packages took too long.") from exc
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()
            raise UpdateFailed("The new version's packages could not be installed"
                               + (f": {tail[-1][:200]}" if tail else "."))

    def _self_check(self) -> None:
        python = self.root / ".venv" / "bin" / "python"
        try:
            done = subprocess.run([str(python), "-m", "deslopify.app", "--self-check"],
                                  cwd=self.root, capture_output=True, text=True,
                                  timeout=180, stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateFailed(f"The new version did not start ({exc}).") from exc
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()
            raise UpdateFailed("The new version did not start"
                               + (f": {tail[-1][:200]}" if tail else "."))


def _update_claude() -> str:
    """Claude Code updates itself only when someone uses it directly — which an
    unpacman user never does. So an update brings it along; best effort."""
    from atelier.providers import all_adapters

    adapter = all_adapters().get("claude")
    exe = adapter.executable() if adapter else None
    if not exe:
        return ""
    try:
        done = subprocess.run([exe, "update"], capture_output=True, text=True,
                              timeout=300, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "Claude Code could not be updated this time; it still works as it was."
    lines = [ln.strip() for ln in (done.stdout + done.stderr).splitlines() if ln.strip()]
    if done.returncode != 0:
        return "Claude Code could not be updated this time; it still works as it was."
    return lines[-1][:200] if lines else "Claude Code is up to date."


class UpdateRefused(RuntimeError):
    """An update that cannot start, with the reason in plain words."""


class UpdateFailed(RuntimeError):
    """An update that started and could not finish, said plainly."""
