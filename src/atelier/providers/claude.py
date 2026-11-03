"""Claude Code CLI adapter.

Verified against Claude Code 2.1.156. The invocation is deliberately hermetic
(`--setting-sources ''`), so a run never inherits personal settings, CLAUDE.md,
or project config — without that, comparing two harnesses proves nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..events import (
    ASSISTANT_TEXT,
    ASSISTANT_THINKING,
    ERROR,
    NOTICE,
    PROGRESS,
    PROVIDER_RAW,
    RATE_LIMIT,
    TOOL_RESULT,
    TOOL_USE,
    USAGE,
    Event,
)
from .base import Capabilities, ParsedUsage, ProviderInfo, register

# Emitted continuously while the model reasons. Useful as a liveness signal,
# useless as a record — kept out of the persisted log.
_PROGRESS_SUBTYPES = frozenset({"thinking_tokens", "token_count", "heartbeat"})

if TYPE_CHECKING:
    from ..config import Account, Config
    from ..harnesses import Harness
    from ..spec import RunSpec


class ClaudeAdapter:
    name = "claude"
    capabilities = Capabilities(
        steering="live",
        resume=True,
        freeze=True,
        native_budget_cap=True,
        subagents=True,
        partial_messages=True,
        effort_levels=("low", "medium", "high", "xhigh", "max"),
    )

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def executable() -> str | None:
        return shutil.which("claude") or (
            str(Path.home() / ".local/bin/claude")
            if (Path.home() / ".local/bin/claude").is_file() else None
        )

    async def probe(self, cfg: Config) -> ProviderInfo:
        exe = self.executable()
        if not exe:
            return ProviderInfo(
                name=self.name, available=False,
                error="`claude` not found on PATH. Install Claude Code, then re-run.",
            )
        version = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
            version = out.decode().strip()
        except (TimeoutError, OSError) as exc:
            return ProviderInfo(name=self.name, available=False, path=exe,
                                error=f"`claude --version` failed: {exc}")

        accounts: dict[str, str] = {}
        for acct in cfg.accounts_for(self.name):
            accounts[acct.id] = await self._probe_account(exe, acct)

        return ProviderInfo(name=self.name, available=True, version=version,
                            path=exe, accounts=accounts)

    async def _probe_account(self, exe: str, account: Account) -> str:
        env = {**os.environ, **account.env()}
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "auth", "status", env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            text = out.decode(errors="replace").strip()
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            return first[:200] or ("ok" if proc.returncode == 0 else "unknown")
        except TimeoutError:
            return "timed out"
        except OSError as exc:
            return f"error: {exc}"

    # -- invocation --------------------------------------------------------

    def build_invocation(
        self, *,
        cfg: Config, plan_dir: Path, cwd: Path, spec: RunSpec, harness: Harness,
        model: str, effort: str, account: Account | None,
        system_append_path: Path | None, settings_path: Path,
        agents_path: Path | None, plugin_dir: Path | None,
        problem_root: Path, budget_usd: float | None,
    ) -> tuple[list[str], dict[str, str]]:
        exe = self.executable() or "claude"
        argv: list[str] = [
            exe, "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--replay-user-messages",
            "--model", model,
            "--effort", effort,
            # Hermetic: ignore ~/.claude settings, project settings, and CLAUDE.md.
            "--setting-sources", "",
            "--settings", str(settings_path),
        ]

        if spec.resume_session_id:
            argv += ["--resume", spec.resume_session_id]
        else:
            argv += ["--session-id", spec.provider_session_id]

        if system_append_path and system_append_path.is_file():
            argv += ["--append-system-prompt",
                     system_append_path.read_text(encoding="utf-8")]

        if agents_path and agents_path.is_file():
            argv += ["--agents", agents_path.read_text(encoding="utf-8")]

        if plugin_dir and plugin_dir.is_dir() and harness.kind != "read":
            argv += ["--plugin-dir", str(plugin_dir)]

        argv += ["--permission-mode", "dontAsk" if harness.kind == "read"
                 else spec.permission_mode or harness.permissions.mode]

        # Tools: `--tools ""` disables everything (the `chat` baseline).
        if harness.tools.none:
            argv += ["--tools", ""]
        elif harness.kind == "read":
            # Native file tools run outside Claude's command sandbox. Confine
            # paper work to Bash, whose children get the credential-free policy.
            allowed = ["Bash", "TodoWrite"]
            if harness.permissions.network:
                allowed += ["WebSearch", "WebFetch"]
            argv += ["--tools", ",".join(allowed), "--allowedTools", *allowed,
                     "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                     "--disable-slash-commands"]
        else:
            if harness.tools.allow:
                argv += ["--allowedTools", *harness.tools.allow]
            if harness.tools.deny:
                argv += ["--disallowedTools", *harness.tools.deny]

        # In one-shot mode cwd is an isolated copy, so the real problem folder is
        # deliberately NOT added — the session must be blind to progress.
        if (cwd != problem_root and spec.mode != "oneshot"
                and harness.kind != "read"):
            argv += ["--add-dir", str(problem_root)]

        if budget_usd:
            # A backstop for the case where our own accounting is wrong, set far
            # enough above our soft cap that our graceful wrap-up always fires
            # first and has room to finish the file contract. Too tight a margin
            # and the CLI kills the session mid-wrap-up, losing the session's work.
            argv += ["--max-budget-usd", f"{max(budget_usd * 2.0, budget_usd + 1.0):.2f}"]

        if harness.kind == "read":
            from ..access import clean_environment
            env = clean_environment()
        else:
            env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)      # never leak the ambient key
        env.pop("CLAUDE_CONFIG_DIR", None)
        if account:
            env.update(account.env())
        # Mark child processes so nested tooling can tell it is not interactive.
        env["CLAUDE_CODE_ENTRYPOINT"] = "atelier"
        env["ATELIER_RUN_ID"] = spec.run_id
        env["ATELIER_RUN_DIR"] = str(plan_dir)
        env["ATELIER_PROBLEM_ROOT"] = str(problem_root)
        return argv, env

    # -- stdin protocol ----------------------------------------------------

    def initial_stdin(self, prompt: str) -> str | None:
        return self.encode_steering(prompt)

    def encode_steering(self, text: str) -> str:
        return json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        }, ensure_ascii=False) + "\n"

    # -- event parsing -----------------------------------------------------

    def parse_event(self, raw: dict[str, Any], run_id: str) -> list[Event]:
        etype = raw.get("type")
        agent = "lead" if not raw.get("parent_tool_use_id") else f"sub:{raw['parent_tool_use_id'][-8:]}"
        out: list[Event] = []

        if etype == "system":
            sub = raw.get("subtype")
            if sub == "init":
                out.append(Event(type=NOTICE, run_id=run_id, agent=agent, payload={
                    "message": "session initialised",
                    "session_id": raw.get("session_id"),
                    "model": raw.get("model"),
                    "tools": raw.get("tools", []),
                    "permission_mode": raw.get("permissionMode"),
                    "cwd": raw.get("cwd"),
                    "kind": "init",
                }))
            elif sub in _PROGRESS_SUBTYPES:
                out.append(Event(type=PROGRESS, run_id=run_id, agent=agent,
                                 payload={"kind": sub, "raw": raw}))
            else:
                out.append(Event(type=NOTICE, run_id=run_id, agent=agent,
                                 payload={"message": sub or "system", "raw": raw}))
            return out

        if etype == "assistant":
            msg = raw.get("message") or {}
            for block in msg.get("content") or []:
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    out.append(Event(type=ASSISTANT_TEXT, run_id=run_id, agent=agent,
                                     payload={"text": block["text"]}))
                elif btype == "thinking" and block.get("thinking"):
                    out.append(Event(type=ASSISTANT_THINKING, run_id=run_id, agent=agent,
                                     payload={"text": block["thinking"]}))
                elif btype == "tool_use":
                    out.append(Event(type=TOOL_USE, run_id=run_id, agent=agent, payload={
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    }))
            if usage := self._usage(msg):
                out.append(Event(type=USAGE, run_id=run_id, agent=agent, payload={
                    "model": usage.model or msg.get("model", ""),
                    "tok_in": usage.tok_in, "tok_out": usage.tok_out,
                    "cache_read": usage.cache_read, "cache_write": usage.cache_write,
                    "cache_write_5m": usage.cache_write_5m,
                    "cache_write_1h": usage.cache_write_1h,
                    # The CLI repeats one API response's usage on every streamed
                    # block of that response. Without this id the accumulator
                    # double-counts every message that has both thinking and text.
                    "message_id": msg.get("id"),
                    "incremental": True,
                }))
            return out

        if etype == "user":
            msg = raw.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        out.append(Event(type=TOOL_RESULT, run_id=run_id, agent=agent, payload={
                            "tool_use_id": block.get("tool_use_id"),
                            "is_error": bool(block.get("is_error")),
                            "content": _flatten(block.get("content")),
                        }))
                    elif block.get("type") == "text":
                        # Echo of a steering message we sent (--replay-user-messages).
                        out.append(Event(type="steer.echo", run_id=run_id, agent=agent,
                                         payload={"text": block.get("text", "")}))
            elif isinstance(content, str):
                out.append(Event(type="steer.echo", run_id=run_id, agent=agent,
                                 payload={"text": content}))
            return out

        if etype == "result":
            usage = raw.get("usage") or {}
            payload = {
                "subtype": raw.get("subtype"),
                "is_error": bool(raw.get("is_error")),
                "duration_ms": raw.get("duration_ms"),
                "duration_api_ms": raw.get("duration_api_ms"),
                "num_turns": raw.get("num_turns"),
                "session_id": raw.get("session_id"),
                "total_cost_usd": raw.get("total_cost_usd"),
                "result": raw.get("result"),
                "tok_in": int(usage.get("input_tokens") or 0),
                "tok_out": int(usage.get("output_tokens") or 0),
                "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
                "model_usage": raw.get("modelUsage") or {},
                "final": True,
            }
            out.append(Event(type=USAGE, run_id=run_id, agent="lead", payload=payload))
            if raw.get("is_error"):
                out.append(Event(type=ERROR, run_id=run_id, agent="lead", payload={
                    "message": _flatten(raw.get("result")) or "provider reported an error",
                    "subtype": raw.get("subtype"),
                }))
            return out

        if etype == "stream_event":
            ev = raw.get("event") or {}
            if ev.get("type") == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    out.append(Event(type="assistant.delta", run_id=run_id, agent=agent,
                                     payload={"text": delta["text"]}))
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    out.append(Event(type="assistant.delta", run_id=run_id, agent=agent,
                                     payload={"text": delta["thinking"], "thinking": True}))
            return out

        if etype == "rate_limit_event":
            # The provider volunteering its own window state — the closest thing
            # to a real "credits remaining" signal that exists for subscriptions.
            info = raw.get("rate_limit_info") or {}
            out.append(Event(type=RATE_LIMIT, run_id=run_id, agent="lead", payload={
                "status": info.get("status"),
                "window": info.get("rateLimitType"),
                "resets_at_epoch": info.get("resetsAt"),
                "overage_status": info.get("overageStatus"),
                "using_overage": info.get("isUsingOverage"),
                "raw": info,
            }))
            return out

        if etype in ("error", "control_response"):
            out.append(Event(type=ERROR if etype == "error" else NOTICE, run_id=run_id,
                             agent=agent, payload={"raw": raw}))
            return out

        # Never drop anything: an unrecognised event is still part of the record.
        out.append(Event(type=PROVIDER_RAW, run_id=run_id, agent=agent, payload={"raw": raw}))
        return out

    @staticmethod
    def _usage(msg: dict[str, Any]) -> ParsedUsage | None:
        u = msg.get("usage")
        if not isinstance(u, dict):
            return None
        creation = u.get("cache_creation") or {}
        parsed = ParsedUsage(
            model=msg.get("model", ""),
            tok_in=int(u.get("input_tokens") or 0),
            tok_out=int(u.get("output_tokens") or 0),
            cache_read=int(u.get("cache_read_input_tokens") or 0),
            cache_write=int(u.get("cache_creation_input_tokens") or 0),
            # 5-minute and 1-hour cache writes bill at different multipliers
            # (1.25x vs 2x input), so they must be costed separately.
            cache_write_5m=int(creation.get("ephemeral_5m_input_tokens") or 0),
            cache_write_1h=int(creation.get("ephemeral_1h_input_tokens") or 0),
        )
        if not any((parsed.tok_in, parsed.tok_out, parsed.cache_read, parsed.cache_write)):
            return None
        return parsed


def _flatten(content: Any) -> str:
    """Tool results arrive as a string or a list of blocks; normalise to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(block)[:500])
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


register(ClaudeAdapter())
