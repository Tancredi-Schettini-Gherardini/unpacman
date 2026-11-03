#!/usr/bin/env python3
"""PostToolUse(Bash): append every shell command to the run's audit trail."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from _util import allow, parse_args, read_payload  # noqa: E402


def main() -> None:
    args = parse_args()
    payload = read_payload()
    ti = payload.get("tool_input") or {}
    command = ti.get("command") if isinstance(ti, dict) else None
    if not command:
        allow()

    response = payload.get("tool_response")
    exit_code = None
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "returncode"):
            if key in response:
                exit_code = response[key]
                break

    line = json.dumps({
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        "description": ti.get("description"),
        "exit_code": exit_code,
    }, ensure_ascii=False)

    try:
        args.run.mkdir(parents=True, exist_ok=True)
        with (args.run / "commands.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    allow()


if __name__ == "__main__":
    sys.exit(main())
