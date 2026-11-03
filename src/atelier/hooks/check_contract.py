#!/usr/bin/env python3
"""Stop: refuse to end the session until the file contract is satisfied.

This is the hook that makes the contract real. Without it, roughly one session
in four decides the bookkeeping was optional — and a session whose results were
never written down may as well not have happened.

It blocks at most twice, then relents: a session that genuinely cannot finish
the paperwork should still be allowed to end, with the gap recorded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _util import allow, block, counter, parse_args, read_payload  # noqa: E402

MAX_BLOCKS = 2


def main() -> None:
    args = parse_args()
    payload = read_payload()
    problem_root = args.problem.resolve()
    progress = problem_root / "progress"

    # The provider sets this once a stop hook has already fired; respect it as a
    # backstop against looping even if our own counter file is lost.
    already_active = bool(payload.get("stop_hook_active"))

    missing: list[str] = []

    notes = progress / "_session_notes.md"
    if not notes.is_file() or not notes.read_text(encoding="utf-8", errors="replace").strip():
        missing.append(
            "`progress/_session_notes.md` — your account of this session: what you "
            "tried, what worked, what did not, and what the next session should do "
            "first. Write it now."
        )

    state_file = progress / "state.json"
    if not state_file.is_file():
        missing.append("`progress/state.json` — does not exist. Create it.")
    else:
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            missing.append(f"`progress/state.json` — is not valid JSON ({exc}). Fix it.")
        else:
            try:
                from atelier.problems import validate_state

                for problem in validate_state(data):
                    missing.append(f"`progress/state.json` — {problem}")
            except Exception:                                # noqa: BLE001 - fail open
                pass

    mathtex = progress / "math.tex"
    if not mathtex.is_file():
        missing.append("`progress/math.tex` — does not exist. Create it.")

    if not missing:
        allow()

    n = counter(args.run, "stopblock", increment=not already_active)
    if already_active or n > MAX_BLOCKS:
        print(
            "[atelier] The session contract is still incomplete; ending anyway and "
            "recording the gap:\n  - " + "\n  - ".join(missing),
            file=sys.stderr,
        )
        try:
            (args.run / "contract_violations.json").write_text(
                json.dumps({"missing": missing}, indent=2), encoding="utf-8")
        except OSError:
            pass
        allow()

    block(
        "You cannot end this session yet — the required record is incomplete:\n\n  - "
        + "\n  - ".join(missing)
        + "\n\nComplete these now, then finish. Do not claim results you did not "
          "establish; an honest 'no progress on X' is a useful record."
    )


if __name__ == "__main__":
    sys.exit(main())
