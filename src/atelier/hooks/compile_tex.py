#!/usr/bin/env python3
"""PostToolUse: recompile math.tex and feed LaTeX errors straight back.

Catching a broken document at the moment it breaks is far cheaper than finding
out at the end of the session. After three consecutive failures the hook goes
quiet, so a document the agent genuinely cannot fix does not trap it in a loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from _util import allow, block, counter, parse_args, read_payload, reset_counter, tool_file_path  # noqa: E402

MAX_CONSECUTIVE_REPORTS = 3


def main() -> None:
    args = parse_args()
    payload = read_payload()
    raw_path = tool_file_path(payload)
    if not raw_path or not raw_path.endswith((".tex", ".bib")):
        allow()

    try:
        from atelier.config import load_config
        from atelier.latex import compile_tex_sync
    except Exception:                                        # noqa: BLE001 - fail open
        allow()
        return

    problem_root = args.problem.resolve()
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = (args.cwd or problem_root) / target
    try:
        rel = target.resolve().relative_to(problem_root)
    except (ValueError, OSError):
        allow()
        return
    if rel.parts[:1] != ("progress",):
        allow()

    try:
        cfg = load_config()
        result = compile_tex_sync(cfg, problem_root / "progress")
    except Exception as exc:                                 # noqa: BLE001
        print(f"[atelier] latex hook error: {exc}", file=sys.stderr)
        allow()
        return

    if result.skipped:
        allow()
    if result.ok:
        reset_counter(args.run, "texfail")
        allow()

    n = counter(args.run, "texfail")
    if n > MAX_CONSECUTIVE_REPORTS:
        print("[atelier] LaTeX still failing; suppressing further reports this session.",
              file=sys.stderr)
        allow()

    errors = "\n".join(f"  {e}" for e in result.errors[:10]) or "  (no parseable error lines)"
    block(
        f"`progress/math.tex` no longer compiles ({n}/{MAX_CONSECUTIVE_REPORTS}).\n\n"
        f"latexmk reported:\n{errors}\n\n"
        f"Fix the document now — a math.tex that does not build produces no PDF, "
        f"and the next session will inherit the breakage."
    )


if __name__ == "__main__":
    sys.exit(main())
