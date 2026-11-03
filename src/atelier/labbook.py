"""Lab-book composition and parsing.

The agent never writes the lab book. It writes a narrative to
`progress/_session_notes.md`; this module wraps that narrative in a header and
a statistics block computed from measurement, then appends the result.

That split is deliberate: models are unreliable narrators about their own
resource use, and a lab book with invented token counts is worse than none.
Anything you write by hand between entries is preserved verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

OPEN_RE = re.compile(r"<!--\s*atelier:entry\s+id=(?P<id>[^\s>]+)\s*-->")
CLOSE = "<!-- /atelier:entry -->"

NOTES_FILENAME = "_session_notes.md"


@dataclass(slots=True)
class Entry:
    run_id: str
    text: str
    start: int
    end: int


def read_entries(labbook: Path) -> list[Entry]:
    """Parse fenced entries, oldest first. Free text between them is ignored here."""
    if not labbook.is_file():
        return []
    content = labbook.read_text(encoding="utf-8", errors="replace")
    out: list[Entry] = []
    for m in OPEN_RE.finditer(content):
        close_at = content.find(CLOSE, m.end())
        if close_at == -1:
            continue
        out.append(Entry(
            run_id=m.group("id"),
            text=content[m.end():close_at].strip(),
            start=m.start(),
            end=close_at + len(CLOSE),
        ))
    return out


def has_entry(labbook: Path, run_id: str) -> bool:
    return any(e.run_id == run_id for e in read_entries(labbook))


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} m {sec:02d} s"
    if m:
        return f"{m} m {sec:02d} s"
    return f"{sec} s"


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_usd(v: Any) -> str:
    try:
        return f"${float(v):.4f}" if float(v) < 0.01 else f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def compose_entry(
    *,
    run_id: str,
    session_number: int,
    started_at: datetime,
    ended_at: datetime | None,
    harness: str,
    harness_version: int,
    runner: str,
    provider: str,
    model: str,
    effort: str,
    account: str,
    mode: str,
    stats: dict[str, Any],
    task: str,
    notes: str,
    files_changed: list[tuple[str, str]],
    status_before: str,
    status_after: str,
    ended_reason: str,
    git_commit: str | None = None,
    agent_calls: int = 1,
) -> str:
    """Build one lab-book entry. `stats` comes from measurement, never the model."""
    when = started_at.astimezone()
    end_txt = ended_at.astimezone().strftime("%H:%M") if ended_at else "—"

    tok_in = stats.get("tok_in", 0)
    cache_r = stats.get("tok_cache_read", 0)
    cache_w = stats.get("tok_cache_write", 0)
    tok_out = stats.get("tok_out", 0)
    cost_c = stats.get("cost_computed")
    cost_r = stats.get("cost_reported")
    if stats.get("priced", True):
        cost_txt = _fmt_usd(cost_c) + " (computed)"
        if cost_r is not None:
            cost_txt += f" · {_fmt_usd(cost_r)} (reported by CLI)"
    else:
        # Saying "$0.00" for a subscription model would read as free. It is not
        # free; it is simply not billed per token, so no honest figure exists.
        cost_txt = "not billed per token (subscription); no price for this model"

    tools = stats.get("tool_breakdown") or {}
    tool_txt = ", ".join(f"{k} {v}" for k, v in sorted(tools.items(), key=lambda kv: -kv[1])[:8])

    status_txt = (f"`{status_before}` → `{status_after}`"
                  if status_before != status_after else f"`{status_after}` (unchanged)")

    rows = [
        ("Harness", f"`{harness}` v{harness_version} ({runner}"
                    + (f", {agent_calls} agent calls" if agent_calls > 1 else "") + ")"),
        ("Model / effort", f"`{model}` · {effort}"),
        ("Provider / account", f"{provider} · `{account}`"),
        ("Mode", mode),
        ("Duration", f"{_fmt_duration(stats.get('wall_seconds'))} wall"
                     + (f" · {_fmt_duration(stats.get('active_seconds'))} model-active"
                        if stats.get("active_seconds") else "")),
        ("Tokens", f"in {_fmt_int(tok_in)} "
                   f"({_fmt_int(cache_r)} cache-read · {_fmt_int(cache_w)} cache-write) "
                   f"· out {_fmt_int(tok_out)}"),
        ("Cost", cost_txt),
        ("Turns / tool calls", f"{_fmt_int(stats.get('turns'))} · {_fmt_int(stats.get('tool_calls'))}"
                               + (f" ({tool_txt})" if tool_txt else "")),
        ("Ended", ended_reason),
        ("Status", status_txt),
    ]

    lines = [
        f"<!-- atelier:entry id={run_id} -->",
        f"## Session {session_number} · {when.strftime('%Y-%m-%d %H:%M')}–{end_txt} "
        f"{when.strftime('%Z')}",
        "",
        "| | |",
        "|---|---|",
    ]
    lines += [f"| **{k}** | {v} |" for k, v in rows]

    if task.strip():
        lines += ["", "### Task given", ""]
        lines += [f"> {ln}" if ln.strip() else ">" for ln in task.strip().splitlines()[:40]]

    lines += ["", "### Agent's account", ""]
    lines.append(notes.strip() if notes.strip()
                 else "_(the agent left no session notes)_")

    if files_changed:
        lines += ["", "### Files changed", ""]
        for path, change in files_changed[:60]:
            lines.append(f"- `{path}` — {change}")
        if len(files_changed) > 60:
            lines.append(f"- _… and {len(files_changed) - 60} more_")

    if git_commit:
        lines += ["", f"Commit `{git_commit[:12]}` · "
                      f"`git show {git_commit[:12]}` for the full diff."]

    lines += ["", CLOSE, ""]
    return "\n".join(lines)


def append_entry(labbook: Path, entry_text: str) -> None:
    labbook.parent.mkdir(parents=True, exist_ok=True)
    existing = labbook.read_text(encoding="utf-8") if labbook.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    sep = "\n" if existing else ""
    labbook.write_text(existing + sep + entry_text, encoding="utf-8")


def consume_notes(progress_dir: Path) -> str:
    """Read and remove the agent's transient session-notes file."""
    p = progress_dir / NOTES_FILENAME
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    try:
        p.unlink()
    except OSError:
        pass
    return text
