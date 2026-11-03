"""Environment checks.

Runs at startup and on demand. The point is to surface a broken setup in the
header of the UI rather than at the moment a session fails three minutes in.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import Config
from .db import Database

Level = Literal["ok", "warn", "fail"]


@dataclass(slots=True)
class Check:
    name: str
    level: Level
    detail: str = ""
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "level": self.level,
                "detail": self.detail, "fix": self.fix}


@dataclass(slots=True)
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, level: Level, detail: str = "", fix: str = "") -> None:
        self.checks.append(Check(name, level, detail, fix))

    @property
    def worst(self) -> Level:
        if any(c.level == "fail" for c in self.checks):
            return "fail"
        if any(c.level == "warn" for c in self.checks):
            return "warn"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {"worst": self.worst, "checks": [c.to_dict() for c in self.checks]}


async def run_checks(cfg: Config, db: Database, *, live: bool = False) -> dict[str, Any]:
    rep = Report()

    # --- python ---------------------------------------------------------
    v = sys.version_info
    if v < (3, 13):
        rep.add("python", "fail", f"{v.major}.{v.minor} — atelier requires 3.13+")
    else:
        rep.add("python", "ok", f"{v.major}.{v.minor}.{v.micro}")

    # --- paths ----------------------------------------------------------
    if cfg.problems_dir.is_dir():
        n = sum(1 for p in cfg.problems_dir.iterdir()
                if p.is_dir() and not p.name.startswith("."))
        rep.add("problems directory", "ok", f"{cfg.problems_dir} — {n} problems")
    else:
        rep.add("problems directory", "fail", f"{cfg.problems_dir} does not exist",
                fix=f"mkdir -p {cfg.problems_dir}")

    for label, path in (("harnesses", cfg.harnesses_dir), ("shared", cfg.shared_dir)):
        rep.add(f"{label} directory", "ok" if path.is_dir() else "warn", str(path))

    # --- harnesses ------------------------------------------------------
    from .harnesses import discover as discover_harnesses

    harnesses, errors = discover_harnesses(cfg.harnesses_dir)
    if errors:
        rep.add("harness manifests", "warn",
                "; ".join(f"{k}: {v}" for k, v in errors.items()))
    if harnesses:
        rep.add("harnesses", "ok", f"{len(harnesses)} loaded: "
                + ", ".join(h.id for h in harnesses[:10]))
    else:
        rep.add("harnesses", "fail", "no harnesses found",
                fix=f"create one under {cfg.harnesses_dir}")

    # --- providers ------------------------------------------------------
    # Said out loud, because "no provider" is the one failure that stops
    # everything and the reason is not guessable from an empty screen.
    from .providers import all_adapters

    provider_available = False
    for name, adapter in all_adapters().items():
        try:
            info = await adapter.probe(cfg) if live else _quick_probe(adapter)
        except Exception as exc:                              # noqa: BLE001
            rep.add(f"provider: {name}", "warn", f"probe failed: {exc}")
            continue
        if info.available:
            provider_available = True
            rep.add(f"provider: {name}", "ok", f"{info.version or 'present'} at {info.path}")
            for acct, status in info.accounts.items():
                lowered = status.lower()
                level: Level = "ok" if ("logged in" in lowered or "ok" in lowered
                                        or "authenticated" in lowered) else "warn"
                rep.add(f"account: {acct}", level, status)
        else:
            rep.add(f"provider: {name}", "warn", info.error,
                    fix="install the CLI and authenticate")
    if not provider_available:
        rep.add("providers", "fail", "no provider CLI is available",
                fix="install Claude Code or Codex and authenticate")

    # --- accounts -------------------------------------------------------
    if not cfg.accounts:
        rep.add("accounts", "warn", "no accounts configured",
                fix="add an [[account]] block to accounts.toml")
    else:
        for acct in cfg.accounts.values():
            if acct.config_dir and not acct.config_dir.is_dir():
                rep.add(f"account: {acct.id}", "warn",
                        f"config_dir {acct.config_dir} does not exist",
                        fix=f"mkdir -p {acct.config_dir} && "
                            f"CLAUDE_CONFIG_DIR={acct.config_dir} claude auth login")
            if acct.auth == "api_key" and acct.key_ref:
                from .config import resolve_secret

                if not resolve_secret(acct.key_ref):
                    rep.add(f"account: {acct.id}", "warn",
                            f"key_ref {acct.key_ref} resolves to nothing")

    # --- latex ----------------------------------------------------------
    engine = shutil.which(cfg.latex.engine)
    if engine:
        rep.add("latex", "ok", f"{cfg.latex.engine} at {engine}")
    else:
        rep.add("latex", "warn", f"{cfg.latex.engine} not found — PDFs will not build",
                fix="install MacTeX or TeX Live")

    # --- git ------------------------------------------------------------
    if not shutil.which("git"):
        rep.add("git", "warn", "git not found — session commits disabled")
    elif cfg.git.enabled:
        from .git import is_repo

        if is_repo(cfg):
            rep.add("git", "ok", f"{cfg.problems_dir} is a repository")
        else:
            rep.add("git", "warn", f"{cfg.problems_dir} is not a git repository",
                    fix="atelier init-git")
    else:
        rep.add("git", "ok", "disabled in config")

    # --- pricing --------------------------------------------------------
    if cfg.prices.models:
        rep.add("pricing table", "ok", f"{len(cfg.prices.models)} models priced")
    else:
        rep.add("pricing table", "warn", "pricing.toml is empty — costs will read $0")

    # --- database -------------------------------------------------------
    try:
        row = db.one("SELECT COUNT(*) AS n FROM runs")
        rep.add("database", "ok", f"{cfg.db_path} — {row['n'] if row else 0} runs recorded")
    except Exception as exc:                                  # noqa: BLE001
        rep.add("database", "fail", str(exc))

    return rep.to_dict()


def _quick_probe(adapter: Any) -> Any:
    """Cheap synchronous availability check used at startup (no subprocesses)."""
    from .providers.base import ProviderInfo

    exe = getattr(adapter, "executable", lambda: None)()
    if exe:
        return ProviderInfo(name=adapter.name, available=True, path=str(exe))
    return ProviderInfo(name=adapter.name, available=False,
                        error=f"`{adapter.name}` CLI not found on PATH")


def format_report(report: dict[str, Any]) -> str:
    glyph = {"ok": "\033[32m✓\033[0m", "warn": "\033[33m!\033[0m", "fail": "\033[31m✗\033[0m"}
    lines = []
    for c in report["checks"]:
        line = f"  {glyph.get(c['level'], '?')} {c['name']:<26} {c['detail']}"
        lines.append(line.rstrip())
        if c.get("fix") and c["level"] != "ok":
            lines.append(f"      → {c['fix']}")
    verdict = {"ok": "\033[32mall good\033[0m",
               "warn": "\033[33musable, with warnings\033[0m",
               "fail": "\033[31mnot ready\033[0m"}[report["worst"]]
    lines.append("")
    lines.append(f"  {verdict}")
    return "\n".join(lines)
