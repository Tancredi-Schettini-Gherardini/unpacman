"""Which models Claude and ChatGPT offer here: as their own programs say, not
as a list written by hand says.

The AI menu's models came from the lab's `atelier.toml` alone, and a list kept
by hand is out of date the day a model appears: on 2026-10-15 it still offered
Opus 5 and GPT-5.6 Terra as the newest, when Claude Code offered Opus 5.5 and
Fable 5.1, and Codex GPT-6.1 Sol and GPT-6 Astra. So the reader asks:

- **Claude Code** names the newest model of each kind — Opus, Fable, Sonnet,
  Haiku — when asked the way Anthropic's Agent SDK asks it (an `initialize`
  request on its stream-json input). No message is sent, so no model is asked
  anything and nothing is spent; with its non-essential traffic off it answers
  from what it already knows, in under a second (measured 2026-10-15: 0.2–0.5 s,
  nothing written). Older models still come from the lab's list.
- **Codex** keeps the list its service gave the signed-in account
  (`models_cache.json` beside the sign-in, or in the folder unpacman runs Codex
  in), brought up to date whenever Codex runs: the models that account can
  use, in the service's order — a model the account cannot use is not on it
  (GPT-5.5 answers "not found" on a ChatGPT plan). When no such list is a week
  old or younger, the list built into Codex itself (`codex debug models
  --bundled`, offline).

Whatever cannot be asked — a program missing, too old, or answering in a form
not understood — leaves the lab's own list, exactly as before. Each answer is
kept for some hours, and asked again at once when the program itself changes
(an update).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from atelier.config import Account, Config

# How long an answer is used before the program is asked again.
TTL = 6 * 3600
# A Codex list older than this gives way to the one built into Codex: a
# person who has not run Codex for a month has a list a month old.
FRESH = 7 * 86400
TIMEOUT = 20.0

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")

# Claude Code, asked as quietly as it can be: no update, no telemetry, nothing
# kept of the session, no settings read, no tools.
_QUIET = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
          "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"}
_HANDSHAKE = json.dumps({"type": "control_request", "request_id": "unpacman-models",
                         "request": {"subtype": "initialize"}}) + "\n"


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------


def claude_models(stdout: str) -> list[str]:
    """The models in Claude Code's answer to `initialize`: each one's own id
    (an alias such as "opus" resolved to `claude-opus-5-5`), once, in its order."""
    out: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "control_response":
            continue
        response = event.get("response")
        inner = response.get("response") if isinstance(response, dict) else None
        models = inner.get("models") if isinstance(inner, dict) else None
        for m in models if isinstance(models, list) else []:
            if not isinstance(m, dict):
                continue
            ident = str(m.get("resolvedModel") or m.get("value") or "")
            if ident.startswith("claude-") and _ID.match(ident) and ident not in out:
                out.append(ident)
    return out


def ask_claude(exe: str, account: Account | None) -> list[str]:
    """What this Claude Code offers the account; [] if it cannot say."""
    from atelier.access import clean_environment

    env = clean_environment() | (account.env() if account else {}) | _QUIET
    with tempfile.TemporaryDirectory(prefix="unpacman-models-") as raw:
        try:
            done = subprocess.run(
                [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                 "--verbose", "--tools", "", "--setting-sources", "",
                 "--no-session-persistence"],
                input=_HANDSHAKE, capture_output=True, text=True, timeout=TIMEOUT,
                cwd=raw, env=env, encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return []
    return claude_models(done.stdout or "")


# --------------------------------------------------------------------------
# Codex
# --------------------------------------------------------------------------


def codex_models(data: Any) -> list[str]:
    """The models a Codex catalogue lists for choosing (not those it keeps
    hidden), in its order of priority."""
    models = data.get("models") if isinstance(data, dict) else None
    ranked: list[tuple[float, int, str]] = []
    for m in models if isinstance(models, list) else []:
        if not isinstance(m, dict):
            continue
        slug = str(m.get("slug") or "")
        if not _ID.match(slug) or m.get("visibility", "list") != "list":
            continue
        try:
            priority = float(m.get("priority"))
        except (TypeError, ValueError):
            priority = float("inf")
        ranked.append((priority, len(ranked), slug))
    out: list[str] = []
    for _, _, slug in sorted(ranked):
        if slug not in out:
            out.append(slug)
    return out


def _fetched(data: Any) -> float | None:
    """When Codex's service gave this list, as a timestamp."""
    raw = data.get("fetched_at") if isinstance(data, dict) else None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def codex_homes(cfg: Config, account: Account | None) -> list[Path]:
    """Where Codex keeps the account's list: beside its sign-in, and in the
    folder the lab runs Codex in for that account (CodexAdapter._sanitised_home)
    — though not for a sign-in of unpacman's own (signins.py): that folder is
    the account's id's, whose list may be another account's."""
    from .signins import root

    own = (Path(account.config_dir).expanduser() if account and account.config_dir
           else Path.home() / ".codex")
    if own.parent == root(cfg):
        return [own]
    return [own, cfg.root / "var" / "codex" / (account.id if account else "default")]


def ask_codex(exe: str, account: Account | None, cfg: Config,
              now: float | None = None) -> list[str]:
    """What this Codex offers the account; [] if it cannot say."""
    now = time.time() if now is None else now
    best: tuple[float, list[str]] | None = None
    for home in codex_homes(cfg, account):
        try:
            data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        at = _fetched(data)
        if at is None or now - at > FRESH:
            continue
        found = codex_models(data)
        if found and (best is None or at > best[0]):
            best = (at, found)
    if best is not None:
        return best[1]
    from atelier.access import clean_environment

    # Built into the program: offline, and needing no sign-in.
    with tempfile.TemporaryDirectory(prefix="unpacman-models-") as raw:
        try:
            done = subprocess.run(
                [exe, "debug", "models", "--bundled"], capture_output=True, text=True,
                timeout=TIMEOUT, cwd=raw, stdin=subprocess.DEVNULL,
                env=clean_environment() | {"HOME": raw, "CODEX_HOME": raw},
                encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return []
    if done.returncode != 0:
        return []
    try:
        return codex_models(json.loads(done.stdout or ""))
    except ValueError:
        return []


# --------------------------------------------------------------------------
# kept for the server
# --------------------------------------------------------------------------


def ask_program(provider: str, exe: str, account: Account | None,
                cfg: Config) -> list[str]:
    """The models one account's program offers; [] if it cannot say."""
    if provider == "claude":
        return ask_claude(exe, account)
    if provider == "codex":
        return ask_codex(exe, account, cfg)
    return []


def _executable(provider: str) -> str | None:
    from atelier.providers import all_adapters

    adapter = all_adapters().get(provider)
    return adapter.executable() if adapter else None


def _identity(exe: str | None) -> tuple[str, int, int] | None:
    """The program as it is now: a new version is asked again at once."""
    if not exe:
        return None
    try:
        real = Path(exe).resolve()
        stat = os.stat(real)
    except OSError:
        return None
    return (str(real), stat.st_size, stat.st_mtime_ns)


@dataclass
class Catalog:
    """What Claude Code and Codex offer each account, asked at most every
    few hours (or when the program changes) and kept for every request that
    draws a menu."""
    cfg: Config
    # (provider, program, account, cfg) → models; `ask_program` unless a test
    # gives its own.
    ask: Callable[[str, str, Account | None, Config], list[str]] | None = None
    clock: Callable[[], float] = time.time
    _found: dict[str, tuple[float, Any, list[str]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _asking: threading.Lock = field(default_factory=threading.Lock)

    def refresh(self) -> dict[str, list[str]]:
        """Ask each Claude and Codex account's program what it offers, where the
        answer kept is old or the program has changed. Blocking: run it in a
        thread. One at a time; a second caller waits for the first's answer."""
        ask = self.ask or ask_program
        with self._asking:
            for account in list(self.cfg.accounts.values()):
                if account.provider not in ("claude", "codex"):
                    continue
                exe = _executable(account.provider)
                ident = _identity(exe)
                with self._lock:
                    kept = self._found.get(account.id)
                if kept and kept[1] == ident and self.clock() - kept[0] < TTL:
                    continue
                try:
                    found = ask(account.provider, exe, account, self.cfg) if exe else []
                except Exception:                                   # noqa: BLE001
                    found = []
                with self._lock:
                    self._found[account.id] = (self.clock(), ident, list(found or []))
        return self.snapshot()

    def forget(self) -> None:
        """Ask again next time: an account signed in, or another signed in
        instead, may be offered other models than the one before it."""
        with self._lock:
            self._found.clear()

    def snapshot(self) -> dict[str, list[str]]:
        """Each account's models as last asked, without asking: an account
        whose program could not say is left out (the lab's list stands)."""
        with self._lock:
            return {acct: list(found) for acct, (_, _, found) in self._found.items() if found}
