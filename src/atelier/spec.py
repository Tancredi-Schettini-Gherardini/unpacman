"""The session specification: everything the user chooses when launching a run."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Mode = Literal["oneshot", "standard", "continue"]
TaskKind = Literal["text", "files", "default"]


def new_run_id() -> str:
    """Sortable, unique, filesystem-safe: 2026-08-09T14-02-11Z-a3f9."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


@dataclass(slots=True)
class RunSpec:
    problem: str
    harness: str
    mode: Mode = "standard"
    # Empty means "decide from the account, then the harness, then the config".
    # Defaulting to a provider name here silently overrode the chosen account.
    provider: str = ""
    account: str | None = None
    model: str | None = None
    effort: str | None = None

    task_kind: TaskKind = "default"
    task_text: str = ""
    task_files: list[str] = field(default_factory=list)

    # budget overrides (None = inherit from the harness)
    max_usd: float | None = None
    max_minutes: float | None = None
    max_tokens_out: int | None = None
    max_turns: int | None = None
    # Rounds of critique for a harness that has a loop. None inherits the
    # harness default; 0 turns the loop off for this run.
    loop_rounds: int | None = None

    permission_mode: str | None = None
    allow_static_edits: bool = False
    include_work_in_oneshot: bool = False

    resume_session_id: str | None = None
    parent_run_id: str | None = None
    bench_id: str | None = None
    label: str = ""
    notes: str = ""
    git_commit: bool = True
    # Who the work is for, in their own words: for a reading, what its reader
    # said they already know. Shown to every pass of the run, so that the
    # companion is pitched at them and the check measures it against them.
    reader_profile: str = ""
    # A different AI for some passes of a looped run: role name ("draft", or a
    # role of the harness's loop) -> any of provider, account, model, effort.
    # What a role leaves out is the run's own choice above. Empty for almost
    # every run, which then takes exactly the path it always did.
    role_agents: dict[str, dict[str, str]] = field(default_factory=dict)

    run_id: str = field(default_factory=new_run_id)
    provider_session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunSpec:
        known = set(cls.__slots__)
        return cls(**{k: v for k, v in d.items() if k in known})
