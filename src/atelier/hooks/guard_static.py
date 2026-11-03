#!/usr/bin/env python3
"""PreToolUse: refuse edits to the human's specification files.

problem.md, strategy/ and refs/ are the statement of the problem, not part of
the working state. If the agent believes the statement is wrong, the right move
is to say so in the session notes, not to rewrite it silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

from _util import allow, block, parse_args, read_payload, tool_file_path  # noqa: E402


def main() -> None:
    args = parse_args()
    payload = read_payload()
    raw_path = tool_file_path(payload)
    if not raw_path:
        allow()

    try:
        from atelier.problems import ProblemMeta, classify
    except Exception:                                        # noqa: BLE001 - fail open
        allow()
        return

    problem_root = args.problem.resolve()
    try:
        target = Path(raw_path).expanduser()
        if not target.is_absolute():
            target = (args.cwd or problem_root) / target
        target = target.resolve()
        rel = target.relative_to(problem_root)
    except (ValueError, OSError):
        allow()
        return

    try:
        meta = ProblemMeta.load(problem_root)
    except Exception:                                        # noqa: BLE001
        meta = None

    if classify(str(rel), meta) != "static":
        allow()

    block(
        f"Refused: `{rel}` is part of the problem specification and is read-only "
        f"for this session.\n\n"
        f"You may create and edit files under `progress/` and `work/` only.\n"
        f"If you believe the statement itself is mistaken or ambiguous, do not "
        f"edit it — record the issue in `progress/_session_notes.md` and carry on "
        f"under a clearly stated assumption."
    )


if __name__ == "__main__":
    sys.exit(main())
