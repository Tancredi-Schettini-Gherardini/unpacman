"""Open-weight models, run through Claude Code pointed at another endpoint.

Claude Code speaks Anthropic's Messages API, and two kinds of host serve
open-weight models through that same API: Ollama on this computer
(`http://127.0.0.1:11434`) and OpenRouter (`https://openrouter.ai/api`). So the
session is the Claude session in every respect that matters to a harness —
the same tools, the same hooks holding it to its file contract, the same event
stream — and only the model behind it differs. Verified on 2026-10-08 with
Claude Code 2.1.156 against Ollama 0.40.1 (`qwen3:1.7b` wrote a file with the
Write tool and stopped) and against OpenRouter's endpoint (which answers in
Anthropic's format).

What does not carry over, and is said wherever it matters:

  * **web search** is a tool Anthropic's own servers run; elsewhere it answers
    "Web search error: unavailable" and the session goes on without it — so a
    model asked for references gives them from memory (seen: a small model
    then invented one). Fetching a known address (WebFetch) still works.
  * **cost**: Claude Code prices every model as one of Anthropic's, so its
    `total_cost_usd` is dropped here. A hosted model is priced from its host's
    published rates (`PriceTable.add`); a local one costs nothing and is held
    to time.
  * **the CLI's own budget cap** relies on that same wrong figure, and is left
    out for the same reason; the run's own caps still bind.

The account (`Account.base_url`, `key_ref`) says where the model is. Claude
Code gets a private config folder per account under `var/open/`, so nothing of
the user's own Claude sign-in or settings is read or changed, and its
background traffic to Anthropic is switched off.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import resolve_secret
from ..events import USAGE
from .base import Capabilities, ProviderInfo, register
from .claude import ClaudeAdapter

if TYPE_CHECKING:
    from ..config import Account, Config
    from ..harnesses import Harness
    from ..spec import RunSpec

# What Claude Code is told, so that nothing it does on its own — a title, a
# summary, a subagent — asks Anthropic, or the host, for a Claude model.
_MODEL_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
               "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
               "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
_QUIET = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
          "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
          "DISABLE_BUG_COMMAND": "1"}
# Stands in for a key where the host needs none (Ollama ignores it), so that
# Claude Code never falls back to the user's own Claude sign-in.
LOCAL_TOKEN = "local"
# A key kept in a file is fetched by Claude Code itself (key_helper) once per
# start, as a key in its environment was given once: asked again only after a
# day — longer than any step — or when the host refuses it.
HELPER_TTL_MS = str(24 * 3600 * 1000)


def key_helper(account: Account | None) -> tuple[str, Path] | None:
    """Claude Code's `apiKeyHelper` for `account` — a command printing its key
    from the file it is kept in (the reader keeps OpenRouter's and DeepSeek's
    so, 0600) — and that file; None for a key kept anywhere else.

    A key Claude Code is GIVEN in its environment (ANTHROPIC_AUTH_TOKEN) is in
    that program's environment as it was started, for as long as it runs, and
    macOS lets any program of the same user read that (sysctl KERN_PROCARGS2)
    — from inside Claude Code's and Codex's command sandboxes too: a command of
    any reading running meanwhile could read the key (the other reviewer's
    finding 1, 2026-11-02). Fetched by Claude Code itself, by a command it runs
    outside its command sandbox, the key is in no program's environment or
    command line; only in Claude Code's memory. Claude Code then sends it as
    both `Authorization: Bearer` and `x-api-key` (2.1.296, seen against a
    stand-in host); OpenRouter and DeepSeek read either (each refused a made-up
    key in the same words, whichever header carried it, and both together).
    A key named by an environment variable is in an environment already; one in
    the Keychain (the lab's accounts may say so) is given as before."""
    if account is None or not account.key_ref:
        return None
    scheme, _, name = account.key_ref.partition(":")
    if scheme != "file":
        return None
    path = Path(name).expanduser()
    return f"/bin/cat {shlex.quote(str(path))}", path


def open_env(cfg: Config, account: Account | None, model: str,
             base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment that points Claude Code at `account`'s endpoint — with
    its key in it, unless the key is kept in a file: then the key is left out
    for Claude Code to fetch itself, and whoever starts it must give it
    `key_helper(account)` as its `apiKeyHelper` (helper_needed says so)."""
    env = dict(os.environ if base is None else base)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CONFIG_DIR", *_MODEL_VARS):
        env.pop(var, None)
    if account is None or not account.base_url:
        raise ValueError("an open-weight model needs an account naming its endpoint")
    secret = resolve_secret(account.key_ref) if account.key_ref else None
    home = cfg.root / "var" / "open" / account.id
    home.mkdir(parents=True, exist_ok=True)
    env.update({
        "ANTHROPIC_BASE_URL": account.base_url.rstrip("/"),
        "ANTHROPIC_AUTH_TOKEN": secret or LOCAL_TOKEN,
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CONFIG_DIR": str(home),
        **{var: model for var in _MODEL_VARS},
        **_QUIET,
    })
    if secret and key_helper(account):
        del env["ANTHROPIC_AUTH_TOKEN"]
        env["CLAUDE_CODE_API_KEY_HELPER_TTL_MS"] = HELPER_TTL_MS
    return env


def helper_needed(env: dict[str, str], account: Account | None) -> tuple[str, Path] | None:
    """What Claude Code must be given with `env` (open_env's) to have its key:
    key_helper(account) when open_env left the key out, else None."""
    return None if "ANTHROPIC_AUTH_TOKEN" in env else key_helper(account)


def give_key_helper(settings_path: Path, command: str) -> None:
    """Add Claude Code's `apiKeyHelper` to the settings file it is started with
    (compile._compile_pass writes it just before)."""
    settings = (json.loads(settings_path.read_text(encoding="utf-8"))
                if settings_path.exists() else {})
    settings["apiKeyHelper"] = command
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _drop_budget_flag(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item == "--max-budget-usd":
            skip = True
            continue
        out.append(item)
    return out


def reachable(base_url: str, timeout: float = 2.0) -> bool:
    """Whether anything answers at the endpoint (for the lab's doctor)."""
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                base_url.rstrip("/") + "/", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True                   # something answered, if only to refuse
    except (OSError, ValueError):
        return False


class OpenWeightAdapter(ClaudeAdapter):
    name = "open"
    capabilities = Capabilities(
        steering="live",
        # Claude Code keeps the conversation in this account's local directory;
        # resuming uses that history with the same endpoint and model.
        resume=True,
        freeze=True,
        native_budget_cap=False,      # its figure is Anthropic's prices, not the host's
        subagents=True,
        partial_messages=True,
        effort_levels=("low", "medium", "high", "xhigh", "max"),
    )

    async def probe(self, cfg: Config) -> ProviderInfo:
        exe = self.executable()
        if not exe:
            return ProviderInfo(
                name=self.name, available=False,
                error="Open-weight models run through Claude Code, which is not installed.")
        accounts = {}
        for account in cfg.accounts_for(self.name):
            up = await asyncio.to_thread(reachable, account.base_url)
            key = bool(resolve_secret(account.key_ref)) if account.key_ref else True
            accounts[account.id] = ("ready" if up and key
                                    else "no key" if not key else "not reachable")
        return ProviderInfo(name=self.name, available=True, path=exe, accounts=accounts)

    def build_invocation(
        self, *,
        cfg: Config, plan_dir: Path, cwd: Path, spec: RunSpec, harness: Harness,
        model: str, effort: str, account: Account | None,
        system_append_path: Path | None, settings_path: Path,
        agents_path: Path | None, plugin_dir: Path | None,
        problem_root: Path, budget_usd: float | None,
    ) -> tuple[list[str], dict[str, str]]:
        argv, env = ClaudeAdapter.build_invocation(
            self, cfg=cfg, plan_dir=plan_dir, cwd=cwd, spec=spec, harness=harness,
            model=model, effort=effort, account=None,
            system_append_path=system_append_path, settings_path=settings_path,
            agents_path=agents_path, plugin_dir=plugin_dir,
            problem_root=problem_root, budget_usd=None)
        env = open_env(cfg, account, model, env)
        if helper := helper_needed(env, account):
            give_key_helper(settings_path, helper[0])
        return _drop_budget_flag(argv), env

    def parse_event(self, raw: dict[str, Any], run_id: str) -> list[Any]:
        events = ClaudeAdapter.parse_event(self, raw, run_id)
        for event in events:
            if event.type == USAGE and event.payload.get("final"):
                # Anthropic's prices applied to someone else's model.
                event.payload["total_cost_usd"] = None
        return events


register(OpenWeightAdapter())
