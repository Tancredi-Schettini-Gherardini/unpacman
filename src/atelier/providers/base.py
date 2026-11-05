"""Provider adapter interface.

Everything above this layer — the UI, accounting, budgets, steering, logging —
is provider-agnostic. Adding a provider is one file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..config import Account, Config
    from ..harnesses import Harness
    from ..spec import RunSpec


@dataclass(slots=True)
class Capabilities:
    """What a provider can actually do, so the UI never offers a lie."""
    steering: str = "none"          # "live" | "next_turn" | "none"
    resume: bool = False
    freeze: bool = True             # SIGSTOP works for any local child process
    native_budget_cap: bool = False
    subagents: bool = False
    partial_messages: bool = False
    effort_levels: tuple[str, ...] = ()
    # Some CLIs read the prompt from stdin and then block until end-of-input.
    # Leaving stdin open for steering makes those hang forever.
    closes_stdin_after_prompt: bool = False
    # Whether the CLI accepts a system prompt at all. Without one the harness
    # method has to be folded into the user prompt.
    system_prompt: bool = True
    # Whether a genuinely tool-free run can be enforced. Claude accepts
    # `--tools ""`; Codex's least-powerful setting still permits reading files
    # and running commands, so its `chat` control is asked for, not enforced.
    tool_free: bool = True


@dataclass(slots=True)
class ProviderInfo:
    name: str
    available: bool
    version: str = ""
    path: str = ""
    error: str = ""
    accounts: dict[str, str] = field(default_factory=dict)   # account id -> status text


@dataclass(slots=True)
class ParsedUsage:
    model: str = ""
    tok_in: int = 0
    tok_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cost_reported: float | None = None


class ProviderAdapter(Protocol):
    name: str
    capabilities: Capabilities

    async def probe(self, cfg: Config) -> ProviderInfo: ...

    def build_invocation(
        self, *,
        cfg: Config, plan_dir: Path, cwd: Path, spec: RunSpec, harness: Harness,
        model: str, effort: str, account: Account | None,
        system_append_path: Path | None, settings_path: Path,
        agents_path: Path | None, plugin_dir: Path | None,
        problem_root: Path, budget_usd: float | None,
    ) -> tuple[list[str], dict[str, str]]: ...

    def initial_stdin(self, prompt: str) -> str | None:
        """First payload written to the child's stdin, if it takes input there."""
        ...

    def encode_steering(self, text: str) -> str:
        """Encode a mid-run user message for the child's stdin."""
        ...

    def parse_event(self, raw: dict[str, Any], run_id: str) -> list[Any]:
        """Normalise one provider event into zero or more `Event`s."""
        ...


_REGISTRY: dict[str, ProviderAdapter] = {}


def register(adapter: ProviderAdapter) -> None:
    _REGISTRY[adapter.name] = adapter


def get_adapter(name: str) -> ProviderAdapter:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY))
        alias = {"openai": "codex", "chatgpt": "codex", "anthropic": "claude"}.get(name)
        extra = f"  Did you mean {alias!r}?" if alias and alias in _REGISTRY else ""
        raise KeyError(
            f"Atelier does not know how to use {name!r}. It can use: {known}.{extra}")
    return _REGISTRY[name]


def all_adapters() -> dict[str, ProviderAdapter]:
    return dict(_REGISTRY)
