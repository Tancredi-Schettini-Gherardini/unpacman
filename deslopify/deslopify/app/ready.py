"""Getting ready: what this computer needs before a reading can run, and the
buttons that put it in place.

A reading needs three things a new Mac does not have. An AI to read with —
Claude Code or Codex, installed *and signed in*. LaTeX, with every package the
companion's preamble loads, because a reading that cannot typeset its companion
stops after its first pass, which has already been paid for. And a way to read
the PDF's text, which is now a Python dependency (PDFium) and always present.

The first two are checked here, said in plain words, and installed from the
page into the person's own home folder, with no administrator password and no
Homebrew: Anthropic's and OpenAI's own downloads, and TinyTeX with the packages
the companion uses. Signing in runs each program's own sign-in, which opens the
browser.

Everything is measured rather than assumed: a program counts as installed when
it runs, an AI as usable when its own status says it is signed in, and LaTeX as
ready when `kpsewhich` finds every file the preamble loads — and, once per
start, when it has actually typeset a one-page companion.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atelier.config import Account, Config, resolve_secret

from .signins import REDIRECTS, VARIABLE, Signins

PROVIDER_NAMES = {"claude": "Claude", "codex": "ChatGPT", "open": "Open-weight"}
# The program each AI reads with here, and the site its sign-in page is on.
PROGRAMS = {"claude": "Claude Code", "codex": "Codex"}
SITES = {"claude": "claude.ai", "codex": "chatgpt.com"}
# The longest a sign-in may take; a folder of unpacman's own not changed for
# longer than this is no sign-in under way (Checker.sweep).
SIGN_IN_TIMEOUT = 15 * 60

# Where programs live that a process started from the Dock cannot see: it gets
# only /usr/bin:/bin:/usr/sbin:/sbin, not the folders a Terminal adds. Claude
# Code, Codex (when installed from the page) and uv go in ~/.local/bin; MacTeX
# in /Library/TeX/texbin; Homebrew in /opt/homebrew/bin or /usr/local/bin.
EXTRA_BIN = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin",
             "/Library/TeX/texbin")

# Every file the companion, the summary and the proof map load directly
# (summary_pdf._PREAMBLE, _CONTENTS_PREAMBLE, render.py). A package's own
# dependencies are not listed: a complete TeX installation has them, whatever
# its age, and the trial typeset below catches one that does not.
TEX_FILES = ("article.cls", "standalone.cls", "inputenc.sty", "fontenc.sty",
             "amsmath.sty", "amssymb.sty", "amsfonts.sty", "amsthm.sty",
             "graphicx.sty", "tcolorbox.sty", "geometry.sty", "hyperref.sty",
             "enumitem.sty", "tabularx.sty", "parskip.sty", "tocbibind.sty",
             "xcolor.sty", "tikz.sty")

# What TinyTeX's smallest bundle lacks for those, found by typesetting a real
# companion, summary and map with it (2026-10-06): tcolorbox's `most` library
# brings tikzfill, pdfcol and listings; cm-super gives outline fonts in the T1
# encoding, without which the text is set in blurry bitmap fonts. cleveref and
# mathtools are for the lab's own documents.
TINYTEX_PACKAGES = ("tcolorbox", "tikzfill", "pdfcol", "listings", "listingsutf8",
                    "enumitem", "tools", "parskip", "tocbibind", "grfext",
                    "standalone", "cm-super", "cleveref", "mathtools", "latexmk")
TINYTEX_URL = ("https://github.com/rstudio/tinytex-releases/releases/download/"
               "daily/TinyTeX-1-darwin.tar.xz")
CLAUDE_INSTALLER = "https://claude.ai/install.sh"
# Codex's own installer: it puts the whole of a release in place — the program,
# the codex-code-mode-host that runs every command a model asks for, ripgrep —
# checksummed, under ~/.codex/packages, linked into ~/.local/bin. The bare
# program alone (`codex-{arch}-apple-darwin.tar.gz`, what Getting ready
# installed until 2026-10-15) cannot run a single command.
CODEX_INSTALLER = "https://github.com/openai/codex/releases/latest/download/install.sh"
CODE_MODE_HOST = "codex-code-mode-host"
CODE_MODE_HOST_MISMATCH = "codex-code-mode-host-version"

CACHE_SECONDS = 60.0

# Where a Mac keeps a system-wide TeX: MacTeX's links, and TeX Live's own
# yearly folders. Constants, so a test can point them elsewhere.
MACTEX_BIN = Path("/Library/TeX/texbin")
TEXLIVE_ROOT = Path("/usr/local/texlive")


# --------------------------------------------------------------------------
# the environment
# --------------------------------------------------------------------------


def prepare_environment(env: dict[str, str] | None = None,
                        home: Path | None = None) -> None:
    """Make the programs a reading needs findable, however unpacman started.

    Started from its icon, unpacman inherits almost nothing: no PATH beyond the
    system's folders and no language setting. Known folders that exist are
    added after whatever PATH already holds, so a Terminal's own order still
    wins; and a UTF-8 language is set if none is, for the programs it runs.
    """
    env = os.environ if env is None else env
    home = home or Path.home()
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for raw in EXTRA_BIN:
        folder = str(Path(raw.replace("~", str(home), 1)))
        if folder not in parts and Path(folder).is_dir():
            parts.append(folder)
    # A Codex installed (or updated) from Getting ready is the one to use, even
    # where another — older — comes first on this PATH.
    mine = local_bin(home)
    if (mine / "codex").is_file() and str(mine) in parts:
        parts.remove(str(mine))
        parts.insert(0, str(mine))
    for system in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if system not in parts:
            parts.append(system)
    env["PATH"] = os.pathsep.join(parts)
    if not any(env.get(k) for k in ("LC_ALL", "LC_CTYPE", "LANG")):
        env["LANG"] = "en_US.UTF-8"


def local_bin(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local" / "bin"


def _prepend_path(folder: str, env: dict[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and p != folder]
    env["PATH"] = os.pathsep.join([folder, *parts])


def _run(argv: list[str], *, timeout: float = 20, env: dict[str, str] | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None


# --------------------------------------------------------------------------
# the AI
# --------------------------------------------------------------------------


PLAN_NAMES = {"pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise",
              "free": "Free", "plus": "Plus"}


@dataclass(slots=True)
class SignIn:
    """One account of one AI, as this computer finds it."""
    account: str
    provider: str
    installed: bool = False
    path: str = ""
    version: str = ""
    signed_in: bool = False
    who: str = ""                 # the email, when the program says
    plan: str = ""                # "Max", "Pro", "Team", "API key", …
    note: str = ""                # anything else worth saying, plainly
    label: str = ""               # an open-weight host's own name: "DeepSeek"
    # A program too old for the protections a reading runs under (access.py):
    # the first setting it does not know. Signed in or not, it cannot read.
    outdated: str = ""
    # A sign-in of unpacman's own (signins.py), not the one Claude Code or
    # Codex has elsewhere on the Mac — whose own is then said as well.
    own: bool = False
    mac_signed_in: bool = False
    mac_who: str = ""

    @property
    def name(self) -> str:
        return self.label or PROVIDER_NAMES.get(self.provider, self.provider)

    @property
    def usable(self) -> bool:
        return self.signed_in and not self.outdated

    def to_dict(self) -> dict[str, Any]:
        return {"account": self.account, "provider": self.provider, "name": self.name,
                "installed": self.installed, "path": self.path, "version": self.version,
                "signed_in": self.signed_in, "who": self.who, "plan": self.plan,
                "note": self.note, "outdated": self.outdated, "own": self.own,
                "mac_signed_in": self.mac_signed_in, "mac_who": self.mac_who}


def _executable(provider: str) -> str | None:
    from atelier.providers import all_adapters

    adapter = all_adapters().get(provider)
    return adapter.executable() if adapter else None


# The sign-in page each program names, to open again from Getting ready.
_CLAUDE_PAGE = re.compile(r"https://\S*oauth\S*")
_CODEX_PAGE = re.compile(r"https://auth\.openai\.com/\S+")


def _status(provider: str) -> Any:
    """How to ask whether an account of this AI is signed in."""
    return claude_status if provider == "claude" else codex_status


async def _quietly(argv: list[str], env: dict[str, str], *, timeout: float) -> int:
    """Run a program that need say nothing here; its exit code, or -1."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
    except OSError:
        return -1
    try:
        return await asyncio.wait_for(proc.wait(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 9)
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(proc.wait(), 5)
        return -1


def _version(exe: str, env: dict[str, str]) -> str:
    done = _run([exe, "--version"], timeout=25, env=env)
    return (done.stdout.strip().splitlines() or [""])[0][:80] if done else ""


def own_env(account: Account, env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a program runs in for this account: with a folder of its
    own (signins.py), that folder names the sign-in, and nothing else may."""
    env = dict(os.environ if env is None else env)
    if account.config_dir:
        for name in REDIRECTS:
            env.pop(name, None)
    env.update(account.env())
    return env


def claude_status(account: Account, exe: str | None) -> SignIn:
    out = SignIn(account=account.id, provider="claude")
    if not exe:
        return out
    env = own_env(account)
    out.installed, out.path, out.version = True, exe, _version(exe, env)
    done = _run([exe, "auth", "status", "--json"], timeout=25, env=env)
    if done is None:
        out.note = "Claude Code did not answer when asked whether it is signed in."
        return out
    try:
        data = json.loads(done.stdout or "{}")
    except ValueError:
        # Older versions print text; "Not logged in" is the phrase they use.
        data = {"loggedIn": "not logged in" not in (done.stdout + done.stderr).lower()
                and done.returncode == 0}
    out.signed_in = bool(data.get("loggedIn"))
    if out.signed_in:
        out.who = str(data.get("email") or "")
        method = str(data.get("authMethod") or "").lower()
        kind = str(data.get("subscriptionType") or "").lower()
        out.plan = ("API key" if "key" in method or "console" in method
                    else PLAN_NAMES.get(kind, kind.capitalize()))
        if kind == "free":
            out.note = ("A free Claude plan does not include Claude Code, which "
                        "unpacman reads with. It needs Pro, Max, Team or Enterprise.")
    return out


# What each Codex binary made of unpacman's settings: (path, size, mtime) →
# the first setting it did not know, or "".
_CODEX_FIT: dict[tuple[str, int, int], str] = {}


def codex_unfit(exe: str) -> str:
    """The first of the settings a reading gives Codex (access.codex_permissions)
    that this Codex does not know — "" when it knows them all. A Codex too old
    for them refuses to start, by design: a reading must not run unprotected
    (2026-10-13: Codex 0.147 did not know `features.skip_host_skill_discovery`,
    so every ChatGPT reading and explanation failed). Found without asking the
    model anything — a model provider that does not exist stops Codex right
    after it has read its settings — and once per binary."""
    try:
        stat = os.stat(exe)
    except OSError:
        return ""
    key = (exe, stat.st_size, stat.st_mtime_ns)
    if key in _CODEX_FIT:
        return _CODEX_FIT[key]
    from atelier.access import clean_environment, codex_permissions

    with tempfile.TemporaryDirectory(prefix="unpacman-codex-check-") as raw:
        home = Path(raw)
        done = _run([exe, "exec", "--ignore-user-config", "--strict-config",
                     "--skip-git-repo-check", *codex_permissions(home, network=True),
                     "--config", 'model_provider="unpacman-offline-check"', "offline check"],
                    timeout=30, env=clean_environment() | {"HOME": raw, "CODEX_HOME": raw},
                    cwd=home)
    said = f"{done.stdout}\n{done.stderr}" if done else ""
    found = re.search(r"unknown (?:configuration )?field `([^`]+)`", said)
    _CODEX_FIT[key] = found.group(1) if found else ""
    return _CODEX_FIT[key]


# What each Codex binary said of its code-mode host: (path, size, mtime) →
# CODE_MODE_HOST if it runs commands through one, else "".
_CODEX_HOST: dict[tuple[str, int, int], str] = {}


def codex_incomplete(exe: str) -> str:
    """The part this Codex lacks — "" when it lacks none. Codex runs every
    command a model asks for through a second program, codex-code-mode-host,
    which it looks for beside its own real file (through any link: checked
    with 0.162.1). Homebrew's and Codex's own installer put it there; the bare
    program alone, which Getting ready installed until 2026-10-15, has none,
    and then every command fails ("failed to spawn code-mode host") — a reading
    writes nothing, and its check and revision are "Not reached" (three of
    Tancredi's readings, 2026-10-14). Whether this Codex uses one is asked of
    it — `codex features list`, offline — once per binary."""
    try:
        real = Path(exe).resolve()
        stat = os.stat(real)
    except OSError:
        return ""
    host = real.with_name(CODE_MODE_HOST)
    if host.is_file() and os.access(host, os.X_OK):
        return ""
    key = (str(real), stat.st_size, stat.st_mtime_ns)
    if key not in _CODEX_HOST:
        from atelier.access import clean_environment

        with tempfile.TemporaryDirectory(prefix="unpacman-codex-check-") as raw:
            done = _run([exe, "features", "list"], timeout=30, cwd=Path(raw),
                        env=clean_environment() | {"HOME": raw, "CODEX_HOME": raw})
        uses = done is not None and re.search(r"^code_mode_host\s.*\btrue\s*$",
                                              done.stdout or "", re.MULTILINE)
        _CODEX_HOST[key] = CODE_MODE_HOST if uses else ""
    return _CODEX_HOST[key]


def codex_host_issue(exe: str, version: str) -> str:
    """Catch a CLI paired with a helper from another Codex release."""
    real = Path(exe).resolve()
    helper = real.with_name(CODE_MODE_HOST)
    if not helper.is_file():
        return ""  # codex_incomplete checks whether this CLI needs a host
    manifest = helper.resolve().parent.parent / "codex-package.json"
    try:
        host_version = str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""  # an unbundled helper has no release version to compare
    found = re.search(r"\d+\.\d+\.\d+", version)
    return CODE_MODE_HOST_MISMATCH if found and host_version != found.group() else ""


def codex_trouble(outdated: str) -> str:
    """Why a Codex that `outdated` names cannot read, as a clause: "Codex, which
    ChatGPT reads with here, …"."""
    if outdated == CODE_MODE_HOST:
        return (f"was installed without one of its parts ({CODE_MODE_HOST}), so it "
                "cannot run the commands a reading needs")
    if outdated == CODE_MODE_HOST_MISMATCH:
        return "has a tool helper from a different version, so it cannot run commands reliably"
    return "is too old for the protections a reading runs under"


def codex_status(account: Account, exe: str | None) -> SignIn:
    """Read Codex's credentials file, as the lab does: `codex login status` may
    try to refresh a token, and looking should change nothing."""
    out = SignIn(account=account.id, provider="codex")
    if not exe:
        return out
    env = own_env(account)
    out.installed, out.path, out.version = True, exe, _version(exe, env)
    out.outdated = (codex_unfit(exe) or codex_incomplete(exe)
                    or codex_host_issue(exe, out.version))
    if out.outdated == CODE_MODE_HOST:
        out.note = (f"This Codex ({out.version or 'this version'}) was installed without "
                    f"one of its parts, {CODE_MODE_HOST}, so it cannot run the commands a "
                    "reading needs: a reading would stop after its first step. Update it "
                    "here; it takes a minute.")
    elif out.outdated == CODE_MODE_HOST_MISMATCH:
        out.note = ("Codex's file and shell helper belongs to a different version. "
                    "Update Codex here before starting another reading.")
    elif out.outdated:
        out.note = (f"This Codex ({out.version or 'an older version'}) is too old for the "
                    "protections unpacman gives a reading. Update it here; it takes a minute.")
    home = Path(account.config_dir or Path.home() / ".codex").expanduser()
    auth = home / "auth.json"
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    mode = str(data.get("auth_mode") or ("apikey" if data.get("OPENAI_API_KEY") else ""))
    tokens = data.get("tokens") or {}
    out.signed_in = bool(data.get("OPENAI_API_KEY") or tokens.get("access_token")
                         or tokens.get("refresh_token"))
    if out.signed_in:
        out.plan = "API key" if "key" in mode.lower() else "ChatGPT"
        # Whose sign-in it is — the email its id_token carries, read here
        # only — so that Getting ready says which account readings use, and
        # that Change account changed it (2026-10-17; "Use another account"
        # until 2026-10-20).
        from .news import codex_email

        out.who = codex_email(home)
    return out


def open_status(account: Account, exe: str | None) -> SignIn:
    """An account reached through Claude Code at another host (openweight.py):
    ready when Claude Code, which runs it, is installed and the host can be
    used — Ollama running here with a model that can read, or a key saved for
    DeepSeek or OpenRouter."""
    from . import openweight

    from .openweight import DEEPSEEK, OPENROUTER

    # The two hosts that are AIs of their own on the page are called by name.
    named = account.id in (DEEPSEEK, OPENROUTER)
    out = SignIn(account=account.id, provider="open",
                 label=account.label if named else "")
    if account.auth == "local":
        status = openweight.ollama_status(account.base_url or None)
        out.installed = bool(status["installed"])
        out.version = f"Ollama {status['version']}" if status["version"] else ""
        if not status["installed"]:
            out.note = "Ollama is not installed on this Mac."
        elif not status["running"]:
            out.note = "Ollama is not running. Open it, then check again."
        elif not status["models"]:
            out.note = ("Ollama has no model that can read yet: download one that can "
                        "use tools (Advanced settings suggests some).")
        else:
            out.signed_in = True
            n = len(status["models"])
            out.plan = f"{n} model{'s' if n != 1 else ''}"
    else:
        out.installed = True
        if account.key_ref and resolve_secret(account.key_ref):
            out.signed_in, out.plan = True, "API key"
        elif named:
            out.note = f"{account.label} has no key yet. Add one under Getting ready."
        else:
            out.note = (f"{account.label or account.id} has no key yet. Add one in "
                        "Advanced settings, under Open-weight models.")
    if out.signed_in and not exe:
        # The model is there, but what runs it is not.
        out.signed_in = False
        out.note = (f"{f'{account.label} reads' if named else 'Open-weight models run'} "
                    "through Claude Code, which is not installed yet.")
    out.path = exe or ""
    return out


# --------------------------------------------------------------------------
# LaTeX
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Tex:
    bindir: str = ""
    label: str = ""               # "MacTeX", "TinyTeX", "TeX Live"
    missing: list[str] = field(default_factory=list)
    ours: bool = False            # TinyTeX in ~/Library/TinyTeX, where the page puts it
    trial: str = "pending"        # pending | ok | failed | skipped
    trial_error: str = ""

    @property
    def found(self) -> bool:
        return bool(self.bindir)

    @property
    def ready(self) -> bool:
        return self.found and not self.missing and self.trial != "failed"

    def to_dict(self) -> dict[str, Any]:
        return {"found": self.found, "ready": self.ready, "bindir": self.bindir,
                "label": self.label, "missing": self.missing, "ours": self.ours,
                "trial": self.trial, "trial_error": self.trial_error}


def tinytex_root(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "TinyTeX"


def tex_candidates(env: dict[str, str] | None = None,
                   home: Path | None = None) -> list[Path]:
    """Every TeX installation here that can run latexmk, best guesses first:
    the one on PATH, then MacTeX's, then TinyTeX, then a bare TeX Live."""
    env = os.environ if env is None else env
    home = home or Path.home()
    found: list[Path] = []
    on_path = shutil.which("latexmk", path=env.get("PATH", ""))
    pool = [Path(on_path).parent] if on_path else []
    pool.append(MACTEX_BIN)
    pool += sorted((tinytex_root(home) / "bin").glob("*"))
    pool += sorted(TEXLIVE_ROOT.glob("*/bin/*"), reverse=True)
    for folder in pool:
        if folder in found:
            continue
        if all((folder / tool).is_file() and os.access(folder / tool, os.X_OK)
               for tool in ("latexmk", "pdflatex", "kpsewhich")):
            found.append(folder)
    return found


def tex_missing(bindir: Path) -> list[str]:
    """The preamble's files this installation cannot find."""
    done = _run([str(bindir / "kpsewhich"), *TEX_FILES], timeout=30)
    if done is None:
        return list(TEX_FILES)
    have = {Path(line.strip()).name for line in done.stdout.splitlines() if line.strip()}
    return [f for f in TEX_FILES if f not in have]


def _tex_label(bindir: Path, home: Path | None = None) -> tuple[str, bool]:
    with contextlib.suppress(ValueError):
        bindir.resolve().relative_to(tinytex_root(home).resolve())
        return "TinyTeX", True
    if bindir == MACTEX_BIN or str(bindir).startswith("/Library/TeX"):
        return "MacTeX", False
    return "TeX Live", False


def find_tex(env: dict[str, str] | None = None, home: Path | None = None) -> Tex:
    """The installation readings will use: the first complete one, else the
    one with the least missing."""
    best: Tex | None = None
    for bindir in tex_candidates(env, home):
        label, ours = _tex_label(bindir, home)
        tex = Tex(bindir=str(bindir), label=label, missing=tex_missing(bindir), ours=ours)
        if not tex.missing:
            return tex
        if best is None or len(tex.missing) < len(best.missing):
            best = tex
    return best or Tex()


TRIAL_GUIDE = """# Reader's guide to a test page

## The idea

**Theorem 1.** If $n$ is even, then $n + 2$ is even, and $\\sum_{i=1}^{k} 2i = k(k+1)$.

A sentence with an accent, *é*, and a link: <https://example.org>.

| Claim | Evidence | Boundary |
|---|---|---|
| The numerical method converges | A refinement study | The model's own regime |

## References cited in this companion

- [Ab12] A. Author, *A paper*, 2012.
"""


def trial_typeset(bindir: str) -> str:
    """Typeset a one-page companion with this installation; "" if it worked,
    else the first error line LaTeX gave."""
    from deslopify.summary_pdf import SummaryPdfError, companion_from_guide

    with tempfile.TemporaryDirectory(prefix="unpacman-latex-test-") as raw:
        tmp = Path(raw)
        (tmp / "guide.md").write_text(TRIAL_GUIDE, encoding="utf-8")
        # latexmk is found on PATH by summary_pdf; this installation must be
        # the one found — and stays first, since it is the one readings use.
        _prepend_path(bindir)
        try:
            companion_from_guide(tmp / "guide.md", tmp / "out.pdf", audience="expert")
        except SummaryPdfError as exc:
            lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
            first = next((ln for ln in lines if ln.startswith("!")), lines[-1] if lines else "")
            return first[:300] or "LaTeX could not typeset a test page."
        except Exception as exc:                                  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"[:300]
        return "" if (tmp / "out.pdf").is_file() else "LaTeX produced no PDF."


# --------------------------------------------------------------------------
# PDF text
# --------------------------------------------------------------------------


def pdf_engine() -> str:
    if shutil.which("pdftotext"):
        return "pdftotext"
    if shutil.which("gs"):
        return "Ghostscript"
    return "PDFium"


# --------------------------------------------------------------------------
# readiness
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Readiness:
    ais: list[SignIn]
    tex: Tex
    pdf: str
    checked: float

    def signed_in(self, provider: str | None = None,
                  account: str | None = None) -> bool:
        """Whether an AI here can read: signed in, and not too old to."""
        return any(a.usable for a in self.ais
                   if (provider is None or a.provider == provider)
                   and (account is None or a.account == account))

    @property
    def ok(self) -> bool:
        return self.signed_in() and self.tex.ready

    def blocking(self, provider: str | None = None, account: str | None = None,
                 *, latex: bool = True) -> list[str]:
        """What stops a reading on this AI, each said plainly. `latex=False`
        for work that typesets nothing — a search, the questions before a
        reading — which needs only the AI."""
        out: list[str] = []
        if provider:
            pool = [a for a in self.ais if a.provider == provider
                    and (account is None or a.account == account)]
            if not any(a.usable for a in pool):
                name = PROVIDER_NAMES.get(provider, provider)
                outdated = next((a for a in pool if a.signed_in and a.outdated), None)
                if outdated is not None:
                    out.append(f"Codex, which {name} reads with here, "
                               f"{codex_trouble(outdated.outdated)}: update it.")
                elif provider == "open":
                    # Each says what it lacks: Ollama, a model, a key.
                    out.append(next((a.note for a in pool if a.note),
                                    "That open-weight model is not available here."))
                else:
                    out.append(f"{name} is not signed in on this computer."
                               if any(a.installed for a in pool)
                               else f"{name} is not installed on this computer.")
        elif not self.signed_in():
            out.append("No AI is signed in on this computer.")
        if not latex:
            return out
        if not self.tex.found:
            out.append("LaTeX is not installed, so the companion could not be typeset.")
        elif self.tex.missing:
            out.append(f"LaTeX is missing {len(self.tex.missing)} of the pieces the "
                       "companion needs (" + ", ".join(self.tex.missing[:4])
                       + ("…" if len(self.tex.missing) > 4 else "") + ").")
        elif self.tex.trial == "failed":
            out.append("LaTeX could not typeset a test page: " + self.tex.trial_error)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "ais": [a.to_dict() for a in self.ais],
                "tex": self.tex.to_dict(), "pdf": self.pdf, "checked": self.checked,
                "blocking": self.blocking(), "mac": platform.system() == "Darwin"}


class Checker:
    """Readiness, measured at most once a minute unless asked again, and the
    jobs that install and sign in."""

    def __init__(self, cfg: Config, *, home: Path | None = None,
                 signins: Signins | None = None) -> None:
        self.cfg = cfg
        self.home = home or Path.home()
        self._lock = threading.Lock()
        self._cached: Readiness | None = None
        self._trials: dict[str, tuple[str, str]] = {}    # bindir → (state, error)
        self._trial_running: set[str] = set()
        self.jobs: dict[str, Job] = {}
        # Told of every job that ends, however it ended (the server forgets
        # which models each AI offers when one is signed in anew: catalog.py).
        self.on_finish: list[Any] = []
        # Whether measuring also typesets a test page (in the background).
        # Off for `--check`, which typesets one itself and waits for it.
        self.auto_trial = True
        # unpacman's own sign-ins — another account for unpacman alone — put
        # in place in cfg's accounts (signins.py).
        self.signins = signins or Signins(cfg)
        # Whether a reading is using this AI ("claude", "codex") now: the
        # server, which knows the readings, says.
        self.in_use: Any = lambda provider: False

    # -- measuring --------------------------------------------------------

    def _accounts(self) -> list[Account]:
        # Every account the reader could offer; a provider with none still
        # gets its default line, so the page can offer to install it.
        out = list(self.cfg.accounts.values())
        for provider in ("claude", "codex"):
            if not any(a.provider == provider for a in out):
                out.append(Account(id=provider, provider=provider, auth="subscription"))
        return out

    def measure(self) -> Readiness:
        prepare_environment(home=self.home)
        ais: list[SignIn] = []
        exes = {p: _executable(p) for p in ("claude", "codex")}
        for account in self._accounts():
            if account.provider == "claude":
                ais.append(claude_status(account, exes["claude"]))
            elif account.provider == "codex":
                ais.append(codex_status(account, exes["codex"]))
            elif account.provider == "open":
                ais.append(open_status(account, exes["claude"]))
        for ai in ais:
            # A sign-in of unpacman's own says so, and whose the Mac's own is.
            if (ai.provider in VARIABLE and self.signins.folder(ai.provider) is not None
                    and ai.account == self.signins.main(ai.provider).id):
                status = claude_status if ai.provider == "claude" else codex_status
                mac = status(self.signins.declared(ai.provider), exes[ai.provider])
                ai.own, ai.mac_signed_in, ai.mac_who = True, mac.signed_in, mac.who
        tex = find_tex(home=self.home)
        if tex.found:
            # Readings must use the installation measured here, wherever it is.
            _prepend_path(tex.bindir)
            if not tex.missing:
                state, error = self._trials.get(tex.bindir, ("pending", ""))
                tex.trial, tex.trial_error = state, error
                if state == "pending" and self.auto_trial:
                    self._start_trial(tex.bindir)
        return Readiness(ais=ais, tex=tex, pdf=pdf_engine(), checked=time.time())

    def _start_trial(self, bindir: str) -> None:
        if bindir in self._trial_running:
            return
        self._trial_running.add(bindir)

        def work() -> None:
            try:
                error = trial_typeset(bindir)
                self._trials[bindir] = ("failed", error) if error else ("ok", "")
            finally:
                self._trial_running.discard(bindir)
                with self._lock:
                    if self._cached and self._cached.tex.bindir == bindir:
                        state, err = self._trials.get(bindir, ("pending", ""))
                        self._cached.tex.trial, self._cached.tex.trial_error = state, err

        threading.Thread(target=work, name="unpacman-latex-trial", daemon=True).start()

    def get(self, *, fresh: bool = False) -> Readiness:
        with self._lock:
            cached = self._cached
        if cached and not fresh and time.time() - cached.checked < CACHE_SECONDS:
            return cached
        now = self.measure()
        with self._lock:
            self._cached = now
        return now

    async def aget(self, *, fresh: bool = False) -> Readiness:
        return await asyncio.to_thread(self.get, fresh=fresh)

    def forget(self, *, trials: bool = False) -> None:
        with self._lock:
            self._cached = None
            if trials:
                self._trials.clear()

    # -- jobs -------------------------------------------------------------

    def job(self, ident: str) -> Job | None:
        return self.jobs.get(ident)

    def running(self, kind: str) -> Job | None:
        return next((j for j in self.jobs.values()
                     if j.kind == kind and j.state == "running"), None)

    def start(self, kind: str) -> Job:
        """Start installing or signing in; the job already running, if there is one."""
        if (current := self.running(kind)):
            return current
        work = {
            "install-claude": ("Installing Claude Code", self._install_claude),
            "install-codex": ("Installing Codex (ChatGPT)", self._install_codex),
            "install-latex": ("Installing LaTeX", self._install_latex),
            "signin-claude": ("Signing in to Claude", self._signin_claude),
            "signin-codex": ("Signing in to ChatGPT", self._signin_codex),
            "switch-claude": ("Signing in to Claude with another account, for unpacman only",
                              lambda job: self._switch(job, "claude")),
            "switch-codex": ("Signing in to ChatGPT with another account, for unpacman only",
                             lambda job: self._switch(job, "codex")),
            "revert-claude": ("Going back to Claude Code's sign-in on this Mac",
                              lambda job: self._revert(job, "claude")),
            "revert-codex": ("Going back to Codex's sign-in on this Mac",
                             lambda job: self._revert(job, "codex")),
        }.get(kind)
        if work is None:
            raise ValueError(f"unknown job {kind!r}")
        if kind.startswith(("switch-", "revert-")):
            # Not while that program is being installed or signed in already.
            provider = kind.split("-", 1)[1]
            if any(j.state == "running" and j.kind.endswith(f"-{provider}")
                   for j in self.jobs.values()):
                raise Busy(f"{PROVIDER_NAMES[provider]} is being installed or signed in "
                           "already. Wait for that to finish.")
        title, fn = work
        job = Job(id=secrets.token_hex(6), kind=kind, title=title)
        self.jobs[job.id] = job
        # Keep the record small: finished jobs beyond the last twenty go.
        done = [j for j in self.jobs.values() if j.state != "running"]
        for old in sorted(done, key=lambda j: j.started)[:-20]:
            self.jobs.pop(old.id, None)
        job.task = asyncio.get_running_loop().create_task(self._guard(job, fn))
        return job

    async def _guard(self, job: Job, fn: Any) -> None:
        try:
            await fn(job)
            if job.state == "running":
                job.finish("done")
        except asyncio.CancelledError:
            job.finish("cancelled", "Stopped.")
            await job.kill()
            raise
        except JobFailed as exc:
            job.finish("failed", str(exc))
            await job.kill()
        except Exception as exc:                                  # noqa: BLE001
            job.finish("failed", f"Something unexpected went wrong: {type(exc).__name__}: {exc}")
            await job.kill()
        finally:
            self.forget(trials=job.kind == "install-latex")
            for listener in self.on_finish:
                with contextlib.suppress(Exception):
                    listener(job)

    def _env(self, account: Account | None = None) -> dict[str, str]:
        """The environment a job's program runs in; for an account, with its
        sign-in's folder (own_env)."""
        env = dict(os.environ)
        prepare_environment(env, self.home)
        env.setdefault("HOME", str(self.home))
        return own_env(account, env) if account is not None else env

    async def _install_claude(self, job: Job) -> None:
        job.say("Downloading Claude Code from Anthropic…")
        code = await job.run(["/bin/bash", "-c",
                              f"set -o pipefail; curl -fsSL {CLAUDE_INSTALLER} | bash"],
                             env=self._env(), timeout=20 * 60)
        exe = local_bin(self.home) / "claude"
        if code != 0 or not exe.is_file():
            raise JobFailed("Claude Code could not be installed. " + job.tail_hint())
        job.say("Claude Code is installed. Next, sign in.")

    async def _install_codex(self, job: Job) -> None:
        bindir = local_bin(self.home)
        target = bindir / "codex"
        with tempfile.TemporaryDirectory(prefix="unpacman-codex-") as raw:
            installer = Path(raw) / "install.sh"
            await job.download(CODEX_INSTALLER, installer, "Codex's installer", approx_mb=1,
                               env=self._env())
            job.say("Installing Codex: about 135 MB to download, then a moment to set up…")
            # Codex's own installer, asking nothing. Shown only this folder of
            # ours and the system's, it finds no other Codex to warn about, and
            # leaves the person's shell settings alone: ~/.local/bin is already
            # on its PATH (unpacman puts it on its own, prepare_environment).
            env = {"HOME": str(self.home), "USER": os.environ.get("USER", ""),
                   "LANG": "en_US.UTF-8",
                   "PATH": os.pathsep.join([str(bindir), "/usr/bin", "/bin", "/usr/sbin",
                                            "/sbin"]),
                   "CODEX_INSTALL_DIR": str(bindir), "CODEX_NON_INTERACTIVE": "1"}
            env.update({k: v for k, v in os.environ.items()
                        if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")})
            bindir.mkdir(parents=True, exist_ok=True)
            if await job.run(["/bin/sh", str(installer)], env=env, timeout=20 * 60) != 0:
                raise JobFailed("Codex could not be installed. " + job.tail_hint())
        if await job.run([str(target), "--version"], env=self._env(), timeout=60) != 0:
            raise JobFailed("Codex was downloaded but does not run on this computer. "
                            + job.tail_hint())
        missing = codex_incomplete(str(target))
        if missing:
            raise JobFailed(f"Codex was installed without one of its parts ({missing}). "
                            + job.tail_hint())
        # This copy, not an older one elsewhere on PATH (Homebrew's, say), is
        # the one readings use from now on — here, and after a restart
        # (prepare_environment).
        _prepend_path(str(target.parent))
        # An update keeps the sign-in Codex already had; a first install needs one.
        account = self.cfg.default_account("codex")
        config = (Path(account.config_dir).expanduser() if account and account.config_dir
                  else self.home / ".codex")
        job.say("Codex is up to date: ChatGPT can read again." if (config / "auth.json").is_file()
                else "Codex is installed. Next, sign in.")

    async def _install_latex(self, job: Job) -> None:
        root = tinytex_root(self.home)
        bins = sorted((root / "bin").glob("*/tlmgr"))
        env = self._env()
        if not bins:
            if root.exists():
                # Something is there that is not a working TinyTeX. It is put
                # aside, not deleted: it is the person's.
                aside = root.with_name(f"TinyTeX.set-aside-{int(time.time())}")
                root.rename(aside)
                job.note(f"An incomplete TinyTeX was moved to {aside}.")
            with tempfile.TemporaryDirectory(prefix="unpacman-tinytex-") as raw:
                archive = Path(raw) / "TinyTeX.tar.xz"
                await job.download(TINYTEX_URL, archive, "LaTeX (TinyTeX)",
                                   approx_mb=70, env=env)
                job.say("Unpacking LaTeX…")
                root.parent.mkdir(parents=True, exist_ok=True)
                if await job.run(["/usr/bin/tar", "-xJf", str(archive), "-C", str(root.parent)],
                                 env=env, timeout=20 * 60) != 0:
                    raise JobFailed("The LaTeX download could not be unpacked. " + job.tail_hint())
            bins = sorted((root / "bin").glob("*/tlmgr"))
            if not bins:
                raise JobFailed("LaTeX was unpacked, but its tools are not where expected.")
        tlmgr = bins[0]
        job.say("Bringing LaTeX's package manager up to date…")
        await job.run([str(tlmgr), "update", "--self"], env=env, timeout=20 * 60)
        job.say("Adding the pieces the companion needs (about 100 MB; a few minutes)…")
        code = await job.run([str(tlmgr), "install", *TINYTEX_PACKAGES], env=env,
                             timeout=40 * 60)
        if code != 0:
            raise JobFailed("Some LaTeX packages could not be installed. " + job.tail_hint())
        job.say("Checking that LaTeX can typeset a test page…")
        bindir = str(tlmgr.parent)
        error = await asyncio.to_thread(trial_typeset, bindir)
        if error:
            raise JobFailed(f"LaTeX is installed but could not typeset a test page: {error}")
        self._trials[bindir] = ("ok", "")
        job.say("LaTeX is ready.")

    async def _signin_claude(self, job: Job) -> None:
        await self._signin(job, "claude")

    async def _signin_codex(self, job: Job) -> None:
        await self._signin(job, "codex")

    async def _signin(self, job: Job, provider: str) -> None:
        """Sign in the account readings on this AI use: the Mac's Claude Code
        or Codex, as a rule; unpacman's own, if its sign-in has ended."""
        name = PROVIDER_NAMES[provider]
        exe = _executable(provider)
        if not exe:
            raise JobFailed(f"Install {PROGRAMS[provider]} first.")
        account = self.signins.main(provider)
        job.say(f"A {name} sign-in page is opening in your browser. Sign in there; "
                "this page notices when you are done.")
        code = await self._login(job, provider, exe, account)
        status = await asyncio.to_thread(_status(provider), account, exe)
        if code != 0 or not status.signed_in:
            raise JobFailed(f"Signing in to {name} did not finish. " + job.tail_hint())
        job.say(f"Signed in to {name}{f' as {status.who}' if status.who else ''}.")

    async def _login(self, job: Job, provider: str, exe: str, account: Account) -> int:
        """The program's own sign-in, for this account's folder; its exit code."""
        if provider == "claude":
            return await job.run([exe, "auth", "login", "--claudeai"], env=self._env(account),
                                 timeout=SIGN_IN_TIMEOUT, stdin=True, url=_CLAUDE_PAGE)
        return await job.run([exe, "login"], env=self._env(account), timeout=SIGN_IN_TIMEOUT,
                             url=_CODEX_PAGE)

    # Another account for the same AI, for unpacman alone (2026-10-17;
    # signins.py). It signs in in a folder of its own, so Claude Code and Codex
    # elsewhere on the Mac — in Terminal, say — are never signed in or out,
    # and the account unpacman reads with stays as it was until the new one
    # has finished. A browser still signed in to that account may simply offer
    # it again, which is said when it happens; and going back to the Mac's
    # sign-in signs unpacman's own out.

    async def _switch(self, job: Job, provider: str) -> None:
        name, program = PROVIDER_NAMES[provider], PROGRAMS[provider]
        exe = _executable(provider)
        if not exe:
            raise JobFailed(f"Install {program} first.")
        status = _status(provider)
        now = await asyncio.to_thread(status, self.signins.main(provider), exe)
        folder = self.signins.fresh(provider)
        account = self.signins.account(provider, folder)
        try:
            job.say(f"A {name} sign-in page is opening in your browser. Sign in there with "
                    "the other account; this page notices when you are done.")
            code = await self._login(job, provider, exe, account)
            new = await asyncio.to_thread(status, account, exe)
            if code != 0 or not new.signed_in:
                raise JobFailed(f"Signing in to {name} did not finish, so unpacman reads as "
                                "before. " + job.tail_hint())
        except BaseException:
            # Given up, failed or stopped: nothing has changed, and the new
            # folder goes, signed out first in case its sign-in got that far
            # (or stays for Checker.sweep, if it cannot be now) — without
            # hiding why the sign-in stopped.
            await job.kill()
            with contextlib.suppress(Exception):
                await self._sign_out(provider, folder)
            raise
        if now.signed_in and new.who and new.who == now.who:
            await self._sign_out(provider, folder)
            job.say(f"That is {new.who} again — the account unpacman reads with already — so "
                    f"nothing has changed. To read with another, sign out of {SITES[provider]} "
                    "in your browser first, then try again.")
            return
        self.signins.adopt(provider, folder)
        job.say(f"unpacman reads with {new.who or 'the other account'} from now on. {program} "
                "elsewhere on this Mac keeps the sign-in it has.")
        await self.sweep(provider, job=job)

    async def _revert(self, job: Job, provider: str) -> None:
        name, program = PROVIDER_NAMES[provider], PROGRAMS[provider]
        exe = _executable(provider)
        status = _status(provider)
        if self.signins.folder(provider) is None:
            job.say(f"unpacman reads with {program}'s sign-in on this Mac already.")
            return
        own = await asyncio.to_thread(status, self.signins.main(provider), exe)
        self.signins.drop(provider)
        job.say(f"Signing unpacman's own account{f', {own.who},' if own.who else ''} out…")
        await self.sweep(provider, job=job)
        mac = await asyncio.to_thread(status, self.signins.main(provider), exe)
        if mac.signed_in:
            job.say(f"unpacman reads with {program}'s sign-in on this Mac again"
                    f"{f', {mac.who}' if mac.who else ''}.")
        else:
            job.say(f"unpacman no longer reads with an account of its own. {program} is not "
                    f"signed in on this Mac: sign in to read with {name}.")

    async def sweep(self, provider: str | None = None, *, job: Job | None = None,
                    older_than: float = 0) -> None:
        """Sign out, and remove, the folders of unpacman's own sign-ins that
        nothing reads with — an account given up for another, a sign-in that
        did not finish (signins.py) — unchanged for `older_than` seconds. Not
        while a reading uses that AI, which may be running on one, nor while
        another job signs in to it, whose folder may be one."""
        for p in [provider] if provider else list(VARIABLE):
            if self.in_use(p) or any(other is not job and other.state == "running"
                                     and other.kind.endswith(f"-{p}")
                                     for other in self.jobs.values()):
                continue
            for folder in self.signins.unused(p, older_than=older_than):
                with contextlib.suppress(OSError):
                    await self._sign_out(p, folder)

    async def _sign_out(self, provider: str, folder: Path) -> bool:
        """Sign one folder of unpacman's own out, then remove it; whether it
        went. The program is told that folder and no other, and a folder that
        is not one of unpacman's is refused: the Mac's own sign-in is never
        signed out. One the program cannot sign out — it is not installed, or
        could not say so — is kept, to be signed out later."""
        if not self.signins.owns(folder, provider):
            raise ValueError(f"not a sign-in of unpacman's own: {folder}")
        if folder.is_dir():
            exe = _executable(provider)
            if not exe:
                return False
            env = self._env(self.signins.account(provider, folder))
            if env.get(VARIABLE[provider]) != str(folder):
                raise ValueError(f"not told the folder it signs out: {folder}")
            argv = [exe, "auth", "logout"] if provider == "claude" else [exe, "logout"]
            code = await _quietly(argv, env, timeout=60)
            if provider == "claude":
                gone = code == 0
            else:
                # Codex's sign-in is the folder's auth.json, and gone when it is.
                gone = not (folder / "auth.json").exists()
            if not gone:
                return False
        shutil.rmtree(folder, ignore_errors=True)
        return True


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


class JobFailed(RuntimeError):
    """A job's own plain account of why it stopped."""


class Busy(RuntimeError):
    """A job that cannot start now, and why, said plainly."""


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    title: str
    state: str = "running"         # running | done | failed | cancelled
    step: str = ""                 # what is happening now, in a sentence
    notes: list[str] = field(default_factory=list)
    error: str = ""
    url: str = ""                  # a sign-in page, to open again
    started: float = field(default_factory=time.time)
    ended: float | None = None
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=40))
    proc: asyncio.subprocess.Process | None = None
    task: asyncio.Task[None] | None = None

    def say(self, step: str) -> None:
        self.step = step

    def note(self, text: str) -> None:
        self.notes.append(text)

    def finish(self, state: str, error: str = "") -> None:
        if self.state == "running":
            self.state, self.error, self.ended = state, error, time.time()

    def tail_hint(self) -> str:
        useful = [ln for ln in self.lines if ln.strip()][-3:]
        return ("What it said: " + " / ".join(useful)) if useful else ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "state": self.state, "step": self.step, "notes": self.notes,
                "error": self.error, "url": self.url,
                "elapsed": round((self.ended or time.time()) - self.started, 1),
                "output": list(self.lines)[-12:],
                # Claude's sign-in page may show a code to paste back, for
                # another account as for the first.
                "waiting_for_code": (self.kind in ("signin-claude", "switch-claude")
                                     and self.state == "running")}

    async def run(self, argv: list[str], *, env: dict[str, str], timeout: float,
                  stdin: bool = False, url: re.Pattern[str] | None = None) -> int:
        """Run one program, keeping the tail of what it says; its exit code."""
        self.proc = await asyncio.create_subprocess_exec(
            *argv, env=env,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)

        async def read() -> None:
            assert self.proc and self.proc.stdout
            while True:
                chunk = await self.proc.stdout.readline()
                if not chunk:
                    return
                text = _ANSI.sub("", chunk.decode("utf-8", errors="replace")).rstrip()
                for line in text.split("\r"):
                    if line.strip():
                        self.lines.append(line.strip()[:400])
                        if url and not self.url and (m := url.search(line)):
                            self.url = m.group(0)

        try:
            await asyncio.wait_for(asyncio.gather(read(), self.proc.wait()), timeout)
        except TimeoutError:
            await self.kill()
            raise JobFailed("This took far longer than it should, and was stopped. "
                            "Check the internet connection and try again.") from None
        return self.proc.returncode if self.proc.returncode is not None else -1

    async def send(self, text: str) -> bool:
        """Type a line into the program — the code Claude's sign-in page shows."""
        if not self.proc or not self.proc.stdin or self.proc.returncode is not None:
            return False
        self.proc.stdin.write(text.strip().encode() + b"\n")
        with contextlib.suppress(ConnectionError):
            await self.proc.stdin.drain()
        return True

    async def kill(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 15)
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(proc.wait(), 5)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, 9)

    async def download(self, url: str, dest: Path, what: str, *, approx_mb: int,
                       env: dict[str, str]) -> None:
        """Fetch a file with curl — the Mac's own, which trusts what the Mac
        trusts — saying how much has arrived as it goes."""
        self.say(f"Downloading {what}…")

        async def watch() -> None:
            while True:
                await asyncio.sleep(0.5)
                with contextlib.suppress(OSError):
                    mb = dest.stat().st_size / 1_000_000
                    self.say(f"Downloading {what}… {mb:.0f} of about {approx_mb} MB")

        watcher = asyncio.create_task(watch())
        try:
            code = await self.run(["/usr/bin/curl", "-fL", "--retry", "3", "--silent",
                                   "--show-error", "-o", str(dest), url],
                                  env=env, timeout=40 * 60)
        finally:
            watcher.cancel()
        if code != 0 or not dest.is_file():
            raise JobFailed(f"{what} could not be downloaded. Check the internet "
                            "connection and try again. " + self.tail_hint())


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


# --------------------------------------------------------------------------
# failures, said plainly
# --------------------------------------------------------------------------


_PLAIN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"not logged in|please run /login|invalid api key|oauth token has expired|"
                r"authentication_error|token[^\n]{0,40}(expired|revoked)|401 unauthori[sz]ed|"
                r"could not be refreshed", re.I),
     "The AI is not signed in on this computer, or its sign-in has expired. Sign in "
     "again under Getting ready (in Advanced settings), then try again."),
    (re.compile(r"usage limit|limit reached|rate.?limit|too many requests|"
                r"hit your usage", re.I),
     "Your AI plan's usage limit was reached. It resets after a few hours; try again "
     "then, or with a smaller model."),
    (re.compile(r"unknown (configuration )?field `[a-z_.]+` in -c/--config", re.I),
     "Codex, which ChatGPT reads with here, is too old for the protections unpacman "
     "puts around it. Update it under Getting ready (in Advanced settings), then try again."),
    (re.compile(r"failed to spawn code-mode host|code mode is unavailable", re.I),
     "Codex, which ChatGPT reads with here, was installed without one of its parts "
     "(codex-code-mode-host), so it could not run a single command and wrote nothing. "
     "Update it under Getting ready (in Advanced settings) — it takes a minute — then "
     "try again."),
    (re.compile(r"missing field code_mode_host_duration_ns", re.I),
     "Codex's file and shell helper is incompatible. Update Codex under Getting ready "
     "(in Advanced settings), then continue the draft."),
    (re.compile(r"insufficient balance|insufficient_balance|insufficient credits|"
                r"402 payment required|api error: 402", re.I),
     "The AI account has no credit left, or the key has reached the limit set on it. "
     "DeepSeek and OpenRouter are paid in advance: top up at platform.deepseek.com or "
     "openrouter.ai/settings/credits, then try again."),
    (re.compile(r"does not have access to claude code|subscription.*(required|does not)|"
                r"upgrade your plan|credit balance is too low", re.I),
     "This AI account cannot run readings: Claude Code needs a paid Claude plan "
     "(Pro, Max, Team or Enterprise), and an API account needs credit."),
    (re.compile(r"overloaded|api error: 5\d\d|internal server error|service unavailable",
                re.I),
     "The AI service was overloaded or unavailable for a moment. Try again shortly."),
    (re.compile(r"enotfound|econnrefused|econnreset|etimedout|getaddrinfo|"
                r"connection (error|refused|reset)|unable to connect|network error|"
                r"could not resolve host|timed out while connecting", re.I),
     "unpacman could not reach the AI over the internet. Check the connection and "
     "try again."),
    (re.compile(r"no valid map\.pdf", re.I),
     "The proof map could not be drawn as a PDF, and the reading stopped there. "
     "Try again without the proof map."),
    (re.compile(r"latexmk[^\n]{0,40}not found|latex could not render|"
                r"no valid (companion|summary)\.pdf", re.I),
     "The companion could not be typeset as a PDF. If LaTeX is missing or incomplete, "
     "install it under Getting ready (in Advanced settings)."),
]


def plain_failure(*texts: str | None) -> str:
    """What went wrong, in a sentence a reader can act on; "" if nothing
    recognisable was said."""
    joined = "\n".join(t for t in texts if t)
    for pattern, sentence in _PLAIN:
        if pattern.search(joined):
            return sentence
    return ""


def failure_texts(run_dir: Path, limit_bytes: int = 400_000) -> list[str]:
    """The error messages a run recorded, newest last — from the tail of its
    event log, which is where a provider's own words end up."""
    path = run_dir / "events.jsonl"
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit_bytes))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: list[str] = []
    for line in raw.splitlines():
        if '"error"' not in line:
            continue
        with contextlib.suppress(ValueError):
            event = json.loads(line)
            if event.get("type") == "error":
                message = (event.get("payload") or {}).get("message")
                if message:
                    out.append(str(message)[:1000])
    return out[-10:]


# --------------------------------------------------------------------------
# for the Terminal
# --------------------------------------------------------------------------


def describe(r: Readiness) -> str:
    """The same account the page gives, as text."""
    lines = ["", "  unpacman — getting ready", ""]
    for a in r.ais:
        if not a.installed:
            state = "not installed"
        elif a.signed_in:
            state = "signed in" + (f" as {a.who}" if a.who else "") + (f" ({a.plan})" if a.plan else "")
        else:
            state = "installed, not signed in"
        lines.append(f"  [{'ok' if a.usable else '--'}] {a.name} ({a.account}): {state}"
                     + (f" — {a.version}" if a.version else ""))
        if a.own:
            lines.append("       unpacman's own sign-in; elsewhere on this Mac, "
                         + (f"signed in as {a.mac_who}" if a.mac_who
                            else "signed in" if a.mac_signed_in else "not signed in") + ".")
        if a.note:
            lines.append(f"       {a.note}")
    t = r.tex
    if not t.found:
        lines.append("  [--] LaTeX: not installed")
    elif t.missing:
        lines.append(f"  [--] LaTeX ({t.label}, {t.bindir}): missing {', '.join(t.missing)}")
    else:
        trial = {"ok": "typeset a test page", "pending": "test page not yet typeset",
                 "failed": f"could not typeset a test page: {t.trial_error}"}.get(t.trial, t.trial)
        lines.append(f"  [{'ok' if t.ready else '--'}] LaTeX ({t.label}, {t.bindir}): {trial}")
    lines.append(f"  [ok] Reading PDFs: {r.pdf}")
    lines.append("")
    lines.append("  Ready to read." if r.ok else
                 "  Not ready yet: " + " ".join(r.blocking())
                 + " Open unpacman and use Getting ready, which installs what is missing.")
    lines.append("")
    return "\n".join(lines)
