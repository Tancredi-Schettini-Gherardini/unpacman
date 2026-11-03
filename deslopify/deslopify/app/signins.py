"""A sign-in of unpacman's own: another Claude or ChatGPT account for unpacman
alone, while Claude Code and Codex elsewhere on the Mac — in Terminal, say —
keep the sign-in they have (2026-10-17).

Each program keeps one sign-in per folder, and is told which folder by a
variable: Claude Code by CLAUDE_CONFIG_DIR, Codex by CODEX_HOME. Told a folder
of its own, it signs in and out there and nowhere else. Claude Code then keeps
the sign-in in a Keychain item named after that folder ("Claude
Code-credentials-" and the first eight hex digits of the folder's SHA-256)
and the account in the folder's `.claude.json`: seen on 2.1.296 with the
Keychain tool replaced by one that only logs, its status and its sign-out
asked for that item and no other, and wrote nothing outside the folder. Codex
keeps it in the folder's `auth.json` (0.162.1, signed in and out with a
made-up key). The Mac's own sign-ins were the same before and after.

So another account is a folder here — a new one for each sign-in — and the
reader's account for that AI (atelier.config.Account) is pointed at it by its
`config_dir`, which readings, explanations, the model menu and Getting ready
all follow already. Nothing changes until the new sign-in has finished; one
given up, and the account it replaces, are signed out and their folders
removed once no reading uses that AI (ready.Checker.sweep). Only folders of
this shape, here, are ever signed out — never the Mac's own sign-in.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import replace
from pathlib import Path

from atelier.config import Account, Config

from .places import var_dir

PROVIDERS = ("claude", "codex")
# What tells each program the folder its sign-in is in.
VARIABLE = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
# Never handed on with a folder of unpacman's own. Set, even empty, the first
# names the Keychain item Claude Code signs in and out of instead of the
# folder's — and set empty, it names the Mac's own (2.1.296); the others are a
# sign-in given some other way.
REDIRECTS = ("CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN",
             "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
_NAME = re.compile(r"(claude|codex)-[0-9a-f]{8}")
RECORD = "record.json"


def root(cfg: Config) -> Path:
    return var_dir(cfg) / "signins"


class Signins:
    """unpacman's own sign-ins, put in place in `cfg`'s accounts — the reader's
    own dict of them, shared by everything that runs an AI, which this changes
    in place when a sign-in is taken up or given up."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.root = root(cfg)
        self._lock = threading.Lock()
        # Each AI's account as configured: the Mac's own sign-in, as a rule.
        self._declared: dict[str, Account | None] = {}
        for provider in PROVIDERS:
            found = cfg.default_account(provider)
            if found is not None and found.config_dir and self.owns(Path(found.config_dir)):
                found = replace(found, config_dir=None)
            self._declared[provider] = found
        for provider, folder in self._recorded().items():
            if folder.is_dir():
                self._point(provider, folder)

    # -- what is in use ----------------------------------------------------

    def declared(self, provider: str) -> Account:
        """The account as configured: Claude Code's or Codex's usual sign-in."""
        return self._declared.get(provider) or Account(id=provider, provider=provider,
                                                       auth="subscription")

    def main(self, provider: str) -> Account:
        """The account readings on this AI use now."""
        return self.cfg.default_account(provider) or self.declared(provider)

    def folder(self, provider: str) -> Path | None:
        """The folder of unpacman's own sign-in to this AI, if it reads with one."""
        found = self.main(provider).config_dir
        return Path(found) if found and self.owns(Path(found), provider) else None

    def owns(self, folder: Path, provider: str | None = None) -> bool:
        """Whether this is a folder of unpacman's own sign-ins — here, of their
        shape, and (if said) for that AI. No other is ever signed out."""
        named = _NAME.fullmatch(folder.name)
        return (named is not None and folder.parent == self.root
                and (provider is None or named.group(1) == provider))

    def account(self, provider: str, folder: Path) -> Account:
        """The account, reading with the sign-in in `folder`."""
        return replace(self.declared(provider), config_dir=folder)

    def unused(self, provider: str, *, older_than: float = 0) -> list[Path]:
        """This AI's folders that nothing reads with — the account before
        another, a sign-in that did not finish — unchanged for `older_than`
        seconds or more."""
        if not self.root.is_dir():
            return []
        current, now, out = self.folder(provider), time.time(), []
        for folder in sorted(self.root.iterdir()):
            if (folder == current or folder.is_symlink() or not folder.is_dir()
                    or not self.owns(folder, provider)):
                continue
            try:
                if now - folder.stat().st_mtime < older_than:
                    continue
            except OSError:
                continue
            out.append(folder)
        return out

    # -- changing it -------------------------------------------------------

    def fresh(self, provider: str) -> Path:
        """A new, empty folder for a sign-in to begin in: its own, so the one
        in use stays as it is until this one has finished."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        while True:
            folder = self.root / f"{provider}-{secrets.token_hex(4)}"
            try:
                folder.mkdir(mode=0o700)
            except FileExistsError:
                continue
            return folder

    def adopt(self, provider: str, folder: Path) -> None:
        """Read with the sign-in in `folder` from now on."""
        if not self.owns(folder, provider):
            raise ValueError(f"not a sign-in of unpacman's own: {folder}")
        with self._lock:
            self._point(provider, folder)
            self._save()

    def drop(self, provider: str) -> None:
        """Read with the account as configured again."""
        with self._lock:
            base = self._declared.get(provider)
            if base is None:
                self.cfg.accounts.pop(provider, None)
            else:
                self.cfg.accounts[base.id] = base
            self._save()

    def _point(self, provider: str, folder: Path) -> None:
        account = self.account(provider, folder)
        self.cfg.accounts[account.id] = account

    def _recorded(self) -> dict[str, Path]:
        try:
            data = json.loads((self.root / RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out: dict[str, Path] = {}
        for provider in PROVIDERS:
            name = data.get(provider) if isinstance(data, dict) else None
            if isinstance(name, str) and self.owns(self.root / name, provider):
                out[provider] = self.root / name
        return out

    def _save(self) -> None:
        data = {p: found.name for p in PROVIDERS if (found := self.folder(p))}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.root / f"{RECORD}.tmp"
        tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.root / RECORD)
