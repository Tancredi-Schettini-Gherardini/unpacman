"""Shared helpers for hook scripts.

Hooks run as separate short-lived processes. They receive a JSON payload on
stdin and communicate back through the exit code:

    0  allow / no comment
    2  blocking error — stderr is fed back to the model

Every hook fails OPEN. A hook that crashes must never wedge a session; git and
the finalize step are the real safety nets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make `atelier` importable even when the hook is launched by a bare interpreter.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--problem", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--cwd", type=Path, default=None)
    # Set only for a pass of a multi-pass run, which owes different files from
    # the session that drafted the thing it is reading.
    ap.add_argument("--role", default="")
    ap.add_argument("--round", type=int, default=0)
    ap.add_argument("--expect", default="")
    ap.add_argument("--validate", default="")
    # What a reading owes (atelier.harnesses.CONTRACTS): the full companion, or
    # only the guide its method asks for.
    ap.add_argument("--contract", default="companion")
    known, _ = ap.parse_known_args(argv)
    return known


def tool_file_path(payload: dict[str, Any]) -> str | None:
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return None
    for key in ("file_path", "path", "notebook_path", "filePath"):
        v = ti.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def block(message: str) -> None:
    """Refuse the action and explain why, in terms the model can act on."""
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


def counter(run_dir: Path, name: str, *, increment: bool = True) -> int:
    """A tiny persistent counter, used to stop hooks looping forever."""
    p = run_dir / f".{name}.count"
    try:
        n = int(p.read_text().strip() or "0")
    except (OSError, ValueError):
        n = 0
    if increment:
        n += 1
        try:
            p.write_text(str(n))
        except OSError:
            pass
    return n


def reset_counter(run_dir: Path, name: str) -> None:
    try:
        (run_dir / f".{name}.count").unlink(missing_ok=True)
    except OSError:
        pass
