"""Codex CLI adapter (OpenAI / ChatGPT).

Verified against codex-cli 0.147.0 by probing the real binary rather than
reading documentation. Four findings shaped this file, and each would have been
a silent failure if assumed instead of tested:

  * **stdin must be closed.** `codex exec` reads the prompt from stdin and then
    waits for end-of-input. Leave stdin open — as the Claude adapter does, so
    that steering can be delivered mid-run — and every session hangs forever
    after printing "Reading additional input from stdin...". Hence
    `closes_stdin_after_prompt`.
  * **`--ignore-user-config` is not enough for a hermetic run.** It ignores
    `config.toml` but *not* `$CODEX_HOME/AGENTS.md`, which is still injected into
    the prompt. A planted marker there came back in the model's reply. The fix is
    to run against a sanitised CODEX_HOME holding nothing but a link to the
    account's credentials; confirmed with `codex debug prompt-input`, which
    renders the model-visible prompt without spending anything.
  * **There is no system-prompt flag.** `experimental_instructions_file` is
    accepted and silently ignored. The harness method therefore travels in the
    prompt itself, which is a real difference from Claude and is recorded as
    such rather than papered over.
  * **`exec` is one turn.** There is no way to deliver a message mid-run, so
    steering is advertised as unavailable instead of appearing to work.

Unknown `-c` keys are accepted silently, so anything set here that a future
version renames will quietly stop taking effect. `--strict-config` guards the
keys we set.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..events import (
    ASSISTANT_TEXT,
    ERROR,
    NOTICE,
    PROVIDER_RAW,
    TOOL_RESULT,
    TOOL_USE,
    USAGE,
    Event,
)
from .base import Capabilities, ProviderInfo, register

if TYPE_CHECKING:
    from ..config import Account, Config
    from ..harnesses import Harness
    from ..spec import RunSpec

# atelier's effort vocabulary happens to match Codex's `model_reasoning_effort`
# exactly, but not every model supports every level; `max` falls back to `xhigh`.
_EFFORT = {"low": "low", "medium": "medium", "high": "high",
           "xhigh": "xhigh", "max": "max"}

# Codex's sandbox is coarser than Claude's permission modes. Mapped conservatively:
# when in doubt, the less powerful setting.
_SANDBOX = {
    "plan": "read-only",
    "default": "workspace-write",
    "acceptEdits": "workspace-write",
    "bypassPermissions": "danger-full-access",
}


class CodexAdapter:
    name = "codex"
    capabilities = Capabilities(
        # `codex exec` is a single turn with no input channel once it starts.
        steering="none",
        resume=True,
        freeze=True,
        native_budget_cap=False,
        subagents=False,
        partial_messages=False,
        effort_levels=("low", "medium", "high", "xhigh", "max"),
        closes_stdin_after_prompt=True,
        system_prompt=False,
        tool_free=False,
    )

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def executable() -> str | None:
        if found := shutil.which("codex"):
            return found
        for candidate in (Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")):
            if candidate.is_file():
                return str(candidate)
        # The ChatGPT VS Code extension bundles a working binary. Worth finding:
        # someone can have Codex installed and used daily without ever having a
        # `codex` on PATH, which is exactly the state this machine was in.
        bundles = sorted(
            (Path.home() / ".vscode/extensions").glob("openai.chatgpt-*/bin/*/codex"),
            reverse=True)
        return str(bundles[0]) if bundles else None

    async def probe(self, cfg: Config) -> ProviderInfo:
        exe = self.executable()
        if not exe:
            return ProviderInfo(
                name=self.name, available=False,
                error="`codex` not found. Install it with: brew install --cask codex",
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
            version = out.decode(errors="replace").strip()
        except (TimeoutError, OSError) as exc:
            return ProviderInfo(name=self.name, available=False, path=exe,
                                error=f"`codex --version` failed: {exc}")

        accounts = {a.id: self._account_status(a) for a in cfg.accounts_for(self.name)}
        return ProviderInfo(name=self.name, available=True, version=version,
                            path=exe, accounts=accounts)

    @staticmethod
    def _account_status(account: Account) -> str:
        """Read the credentials file directly.

        `codex login status` starts a process that may try to refresh tokens;
        the file says what is needed and cannot have side effects.
        """
        home = account.config_dir or Path.home() / ".codex"
        auth = Path(home).expanduser() / "auth.json"
        if not auth.is_file():
            return f"not logged in (no {auth})"
        try:
            data = json.loads(auth.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return f"unreadable auth.json: {exc}"
        mode = data.get("auth_mode") or ("api_key" if data.get("OPENAI_API_KEY") else "")
        return f"logged in ({mode})" if mode else "logged in"

    # -- invocation --------------------------------------------------------

    def _sanitised_home(self, cfg: Config, account: Account | None,
                        *, destination: Path | None = None) -> Path:
        """A CODEX_HOME holding credentials and nothing else.

        `--ignore-user-config` does not stop `$CODEX_HOME/AGENTS.md` reaching the
        model, and personal standing instructions there would apply to every
        session — which silently invalidates any comparison between harnesses.
        Credentials are linked rather than copied so that no second copy of the
        tokens is written to disk, and so a refreshed token is not stranded here.
        """
        source = Path(account.config_dir).expanduser() if account and account.config_dir \
            else Path.home() / ".codex"
        # Kept per account under var/ rather than per run: Codex fills this with
        # caches and databases (a couple of megabytes a session), which are
        # working state, not part of the run's record.
        home = destination or cfg.root / "var" / "codex" / (account.id if account else "default")
        home.mkdir(parents=True, exist_ok=True)

        # Scrubbed every time, not just on creation. Anything that puts standing
        # instructions here would otherwise silently apply to every session, and
        # that is precisely what makes comparisons meaningless.
        for leak in ("AGENTS.md", "config.toml", "instructions.md"):
            with contextlib.suppress(OSError):
                (home / leak).unlink()

        auth, target = home / "auth.json", source / "auth.json"
        if target.is_file() and not auth.exists():
            try:
                auth.symlink_to(target)
            except OSError:
                shutil.copy2(target, auth)          # e.g. a filesystem without links
        return home

    def build_invocation(
        self, *,
        cfg: Config, plan_dir: Path, cwd: Path, spec: RunSpec, harness: Harness,
        model: str, effort: str, account: Account | None,
        system_append_path: Path | None, settings_path: Path,
        agents_path: Path | None, plugin_dir: Path | None,
        problem_root: Path, budget_usd: float | None,
    ) -> tuple[list[str], dict[str, str]]:
        exe = self.executable() or "codex"

        sandbox = "read-only" if harness.tools.none else _SANDBOX.get(
            spec.permission_mode or harness.permissions.mode, "workspace-write")

        argv: list[str] = [exe]
        # Codex exposes live search only when the top-level --search flag is
        # present. Merely allowing network traffic in the sandbox does not add
        # the web_search tool, so a research-enabled harness would otherwise be
        # told to verify sources with a tool it cannot call.
        if harness.permissions.network:
            argv.append("--search")
        argv.append("exec")
        argv += [
            "--json",
            "--model", model,
            "--config", f"model_reasoning_effort={_EFFORT.get(effort, 'medium')}",
            # Ignores config.toml only. The sanitised CODEX_HOME below is what
            # actually makes the run hermetic.
            "--ignore-user-config",
            "--strict-config",
            "--cd", str(cwd),
            "--skip-git-repo-check",
            "--sandbox", sandbox,
            "--output-last-message", str(plan_dir / "last-message.txt"),
        ]

        if harness.kind == "read":
            from ..access import codex_permissions, codex_program_paths

            # workspace-write permits reads throughout the home directory.
            # Use a default-deny read policy, without the overriding legacy flag.
            i = argv.index("--sandbox")
            del argv[i:i + 2]
            run_root = cwd.parent if cwd.name == "workspace" else plan_dir
            # And Codex's own program: it starts itself again to apply each
            # file edit (access.codex_program_paths).
            argv += codex_permissions(
                cwd, readable=[run_root / "skill-references", run_root / "loop",
                               *codex_program_paths(exe)],
                network=harness.permissions.network)

        # One-shot runs are blind: cwd is an isolated copy and the real problem
        # folder must stay unreachable, exactly as for Claude.
        if (cwd != problem_root and spec.mode != "oneshot"
                and harness.kind != "read"):
            argv += ["--add-dir", str(problem_root)]

        # `-` means "the prompt is on stdin", which avoids argv length limits on
        # the long statements these problems carry.
        # Exec-only flags (notably --cd) belong before the resume subcommand.
        # Keep model/JSON/config options on the resume invocation itself.
        if spec.resume_session_id:
            parent_options = []
            for option in ("--cd", "--sandbox", "--add-dir"):
                while option in argv:
                    at = argv.index(option)
                    parent_options.extend(argv[at:at + 2])
                    del argv[at:at + 2]
            at = argv.index("exec") + 1
            argv[at:at] = [*parent_options, "resume", spec.resume_session_id]
        argv.append("-")

        if harness.kind == "read":
            from ..access import clean_environment
            env = clean_environment()
        else:
            env = dict(os.environ)
        env.pop("OPENAI_API_KEY", None)          # never leak the ambient key
        env.pop("CODEX_HOME", None)
        if account:
            env.update(account.env())
        env["CODEX_HOME"] = str(self._sanitised_home(
            cfg, account, destination=(plan_dir / "provider-state" / "codex")
            if harness.kind == "read" else None))
        env["ATELIER_RUN_ID"] = spec.run_id
        env["ATELIER_RUN_DIR"] = str(plan_dir)
        env["ATELIER_PROBLEM_ROOT"] = str(problem_root)
        return argv, env

    # -- stdin protocol ----------------------------------------------------

    def initial_stdin(self, prompt: str) -> str | None:
        # Plain text, not JSON: `codex exec -` takes the prompt verbatim. The
        # runner closes stdin immediately afterwards (see the capability above).
        return prompt if prompt.endswith("\n") else prompt + "\n"

    def encode_steering(self, text: str) -> str:
        raise NotImplementedError(
            "Codex runs one turn with no input channel, so a message cannot be "
            "delivered mid-session. Stop the session and resume it instead.")

    # -- event parsing -----------------------------------------------------

    def parse_event(self, raw: dict[str, Any], run_id: str) -> list[Event]:
        etype = raw.get("type")
        out: list[Event] = []

        if etype == "thread.started":
            out.append(Event(type=NOTICE, run_id=run_id, agent="lead", payload={
                "message": "session initialised",
                "session_id": raw.get("thread_id"),
                "kind": "init",
            }))
            return out

        if etype in ("turn.started", "turn.aborted"):
            out.append(Event(type=NOTICE, run_id=run_id, agent="lead",
                             payload={"message": etype.replace(".", " "), "raw": raw}))
            return out

        if etype in ("item.started", "item.updated", "item.completed"):
            return self._parse_item(raw, etype, run_id)

        if etype == "turn.completed":
            usage = raw.get("usage") or {}
            # `input_tokens` already includes the cached portion, so the cached
            # count is subtracted out to avoid charging those tokens twice.
            total_in = int(usage.get("input_tokens") or 0)
            cached = int(usage.get("cached_input_tokens") or 0)
            out.append(Event(type=USAGE, run_id=run_id, agent="lead", payload={
                "model": "",
                "tok_in": max(total_in - cached, 0),
                "tok_out": int(usage.get("output_tokens") or 0),
                "cache_read": cached,
                "cache_write": int(usage.get("cache_write_input_tokens") or 0),
                "cache_write_5m": 0, "cache_write_1h": 0,
                # Reasoning tokens are billed as output and are already counted
                # inside output_tokens; kept for the record, not added again.
                "reasoning_tokens": int(usage.get("reasoning_output_tokens") or 0),
                # Codex reports usage once, at the end of a turn, so the turn
                # count has to come from the event itself; the running-total path
                # that counts turns for Claude never fires here.
                "turn_delta": 1,
                "final": True,
            }))
            return out

        if etype == "turn.failed":
            out.append(Event(type=ERROR, run_id=run_id, agent="lead", payload={
                "message": _message(raw.get("error")) or "the turn failed",
            }))
            return out

        if etype == "error":
            out.append(Event(type=ERROR, run_id=run_id, agent="lead",
                             payload={"message": _message(raw) or "provider error"}))
            return out

        out.append(Event(type=PROVIDER_RAW, run_id=run_id, agent="lead", payload={"raw": raw}))
        return out

    @staticmethod
    def _parse_item(raw: dict[str, Any], etype: str, run_id: str) -> list[Event]:
        item = raw.get("item") or {}
        kind = item.get("type")
        item_id = item.get("id")
        out: list[Event] = []
        done = etype == "item.completed"

        if kind == "agent_message":
            if done and item.get("text"):
                out.append(Event(type=ASSISTANT_TEXT, run_id=run_id, agent="lead",
                                 payload={"text": item["text"]}))
            return out

        if kind == "reasoning":
            if done and (text := item.get("text") or item.get("summary")):
                out.append(Event(type="assistant.thinking", run_id=run_id, agent="lead",
                                 payload={"text": text if isinstance(text, str)
                                          else json.dumps(text)}))
            return out

        if kind == "command_execution":
            if not done:
                out.append(Event(type=TOOL_USE, run_id=run_id, agent="lead", payload={
                    "id": item_id, "name": "Bash",
                    "input": {"command": item.get("command", "")},
                }))
            else:
                code = item.get("exit_code")
                out.append(Event(type=TOOL_RESULT, run_id=run_id, agent="lead", payload={
                    "tool_use_id": item_id,
                    "is_error": bool(code),
                    "content": str(item.get("aggregated_output") or ""),
                }))
            return out

        if kind == "file_change":
            changes = item.get("changes") or []
            if not done:
                out.append(Event(type=TOOL_USE, run_id=run_id, agent="lead", payload={
                    "id": item_id, "name": "Edit",
                    "input": {"changes": changes},
                }))
            else:
                summary = ", ".join(
                    f"{c.get('kind', 'change')} {Path(str(c.get('path', ''))).name}"
                    for c in changes) or "no changes"
                out.append(Event(type=TOOL_RESULT, run_id=run_id, agent="lead", payload={
                    "tool_use_id": item_id,
                    "is_error": item.get("status") not in (None, "completed"),
                    "content": summary,
                }))
            return out

        if kind in ("web_search", "mcp_tool_call", "todo_list"):
            if not done:
                out.append(Event(type=TOOL_USE, run_id=run_id, agent="lead", payload={
                    "id": item_id, "name": kind, "input": item,
                }))
            else:
                out.append(Event(type=TOOL_RESULT, run_id=run_id, agent="lead", payload={
                    "tool_use_id": item_id, "is_error": False,
                    "content": json.dumps(item)[:2000],
                }))
            return out

        if kind == "error":
            # Codex reports non-fatal warnings this way too, so it is a notice
            # unless the turn itself fails.
            out.append(Event(type=NOTICE, run_id=run_id, agent="lead", payload={
                "message": _message(item) or "codex reported a problem",
                "level": "warn",
            }))
            return out

        if done:
            out.append(Event(type=PROVIDER_RAW, run_id=run_id, agent="lead",
                             payload={"raw": raw}))
        return out


def _message(obj: Any) -> str:
    """Codex nests its errors: the message is often a JSON document itself."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        text = obj
    elif isinstance(obj, dict):
        text = str(obj.get("message") or obj.get("error") or json.dumps(obj))
    else:
        text = str(obj)
    inner = text.strip()
    if inner.startswith("{"):
        try:
            data = json.loads(inner)
        except ValueError:
            return text
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if data.get("message"):
                return str(data["message"])
    return text


register(CodexAdapter())
