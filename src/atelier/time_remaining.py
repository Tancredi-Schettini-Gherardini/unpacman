"""Read the runner's clock from an agent's shell, without contacting the app."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

WRAPUP_AT = 0.9


def state_path(run_dir: Path) -> Path:
    # This directory is already readable, but not writable, in agent sandboxes.
    return run_dir / "loop" / "time-remaining.json"


def time_remaining(state: dict, *, now: float | None = None) -> dict:
    """Advance the saved monotonic clock between controller updates."""
    now = time.monotonic() if now is None else now
    elapsed = state["elapsed_seconds"]
    if state["active"]:
        elapsed += max(0.0, now - state["sampled_at"])
    stage_elapsed = max(0.0, elapsed - state["stage_started_at"])
    allocation = state["stage_allocation_seconds"]
    run_limit = state["run_limit_seconds"]
    stage_left = max(0.0, allocation - stage_elapsed) if allocation is not None else None
    run_left = max(0.0, run_limit - elapsed) if run_limit is not None else None
    limits = [v for v in (stage_left, run_left) if v is not None]
    wrap_up = (stage_elapsed >= WRAPUP_AT * allocation if allocation is not None else
               run_limit is not None and elapsed >= WRAPUP_AT * run_limit)
    wrap_up = wrap_up or (run_limit is not None and run_left == 0)
    return {
        "available": True,
        "stage": state["stage"],
        "state": state["state"],
        "stage_limit_kind": state["stage_limit_kind"],
        "stage_allocated_seconds": allocation,
        "stage_elapsed_seconds": round(stage_elapsed, 1),
        "seconds_remaining": round(min(limits), 1) if limits else None,
        "run_seconds_remaining": round(run_left, 1) if run_left is not None else None,
        "wrap_up": wrap_up,
        "stage_time_reached": allocation is not None and stage_left == 0,
        "run_deadline_reached": run_limit is not None and run_left == 0,
        "instruction": ("Stop new research and expansion. Finish saving your stage's "
                        "required files and session notes; record unresolved gaps honestly."
                        if wrap_up else "Save progressively and check time again after "
                        "substantial tool calls and before starting new research."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True,
                        help="the timing file named in this stage's instructions")
    args = parser.parse_args(argv)
    try:
        report = time_remaining(json.loads(args.state.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"available": False, "error": str(exc)}))
        return 2
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
