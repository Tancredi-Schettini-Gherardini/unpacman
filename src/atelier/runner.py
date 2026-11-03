"""Run execution: spawn, stream, steer, signal, finalize.

Three details here are load-bearing and easy to get wrong:

  * `start_new_session=True` puts the child in its own process group, so a
    signal reaches subagents and spawned tools too — not just the CLI.
  * The stdout reader needs a very large line limit. A single stream-json line
    carrying a big tool result will blow past asyncio's 64 KiB default and kill
    the run with LimitOverrunError.
  * stdin stays open for the whole turn so steering can be delivered, and is
    closed the moment the provider emits its terminal `result` event — that is
    what lets the process exit cleanly instead of hanging forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import events as ev
from . import handover, loop
from .compile import PassPlan, RunPlan, plan_next_pass, plan_writer_completion
from .compile import pass_sequence as compile_passes
from .config import Config
from .db import Database
from .delivery import (
    DIAGRAM_FILES, diagram_problems, diagram_required,
    reading_run_limit, reading_stage_deadline, REVIEW_EXTRA_MINUTES, REVISION_EXTRA_MINUTES,
    delivery_report, ensure_source_scope_notice, export_companion, export_error_path,
    export_fingerprint, export_problems, guide_problems, restore_source, stage_time_available,
)
from .events import Event, EventHub, EventLog, Stopwatch
from .labbook import NOTES_FILENAME
from .loop import LoopState
from .providers import get_adapter
from .time_remaining import WRAPUP_AT

GRACE_AFTER_STDIN_CLOSE = 90.0     # seconds to let the CLI exit on its own
SIGINT_TO_SIGTERM = 10.0
SIGTERM_TO_SIGKILL = 5.0
FREEZE_WARN_SECONDS = 60.0
WATCHDOG_INTERVAL = 2.0
# A later pass is not started once this much of the run's budget is gone. A pass
# launched with nothing left produces a half-written file and a contract
# violation, which is worse than not having run it. The harness's own wrap-up
# threshold binds first where it is lower, which it normally is.
LOOP_RESERVE = 0.95
# Providers whose CLI runs the Stop hook that holds a session to its file
# contract. A pass on any other is checked after the fact instead.
HOOKED = frozenset({"claude", "open"})      # "open" is Claude Code, another model


class RunState(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    FROZEN = "frozen"
    WRAPPING_UP = "wrapping_up"
    STOPPING = "stopping"
    DONE = "done"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (RunState.DONE, RunState.INCOMPLETE, RunState.FAILED, RunState.CANCELLED)


@dataclass(slots=True)
class Totals:
    tok_in: int = 0
    tok_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    turns: int = 0
    tool_calls: int = 0
    cost_computed: float = 0.0
    cost_reported: float | None = None
    # False when the model is absent from pricing.toml — which is the normal
    # case on a subscription, where tokens are not billed per token at all.
    # Without this a session reads as costing $0.00, i.e. as free.
    priced: bool = True
    tool_breakdown: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tok_in": self.tok_in, "tok_out": self.tok_out,
            "tok_cache_read": self.cache_read, "tok_cache_write": self.cache_write,
            "turns": self.turns, "tool_calls": self.tool_calls,
            "cost_computed": round(self.cost_computed, 6),
            "cost_reported": self.cost_reported,
            "priced": self.priced,
            "tool_breakdown": dict(self.tool_breakdown),
        }


def rate_limit_kind(status: str | None) -> str:
    """Classify a provider rate-limit report.

    `allowed_warning` means *allowed*: the provider is saying a utilisation
    threshold was crossed, not refusing the request. Recording it as a block put
    a false "Rate limited until …" chip on the account and made a healthy
    session look as though it had been cut off.
    """
    s = status or ""
    return "window" if not s or s.startswith("allowed") else "rate_limited"


class RunHandle:
    """One live provider process and everything attached to it."""

    def __init__(self, cfg: Config, plan: RunPlan, hub: EventHub, db: Database,
                 *, session_number: int = 1) -> None:
        self.cfg = cfg
        self.plan = plan
        self.hub = hub
        self.db = db
        self.session_number = session_number

        self.run_id = plan.spec.run_id
        self.topic = ev.topic_run(self.run_id)
        self.state = RunState.QUEUED
        self.ended_reason = ""
        self.error: str | None = None

        self.proc: asyncio.subprocess.Process | None = None
        self.pgid: int | None = None
        # A run is a sequence of passes. Almost always one; a harness with a
        # critique loop has a draft, then alternating critique and revision
        # passes, each its own process with its own context.
        self.current: PassPlan = plan.first
        self.loop_state: LoopState | None = None
        self._ending_run = False          # a stop that finalizes the run
        self._ending_pass = ""            # a stop that ends only this pass
        self._pass_base = Totals()        # totals as they stood when it started
        self._usage_base = Totals()       # totals before this provider invocation
        self._writer_completion_used = False
        self._completion_attempts: dict[int, int] = {}
        self._provider_session_id = ""
        self._delivery_transition = False
        self._export_digest = ""
        self._export_error = ""
        self._export_outputs: tuple[str, ...] = ()
        self._pass_started_at = 0.0
        self._stage_time_allocation: float | None = None
        self._pass_cost_reported = 0.0
        # Between a reading's steps the controller works on a private copy of
        # its files, never in the folder its AI works in (handover.py): where
        # that copy is while it is held, else None. Once the files could not
        # be taken, or were given back at the end of the run, the controller
        # has no working files at all. What the folder held besides, as seen
        # when the copy was last taken: handover.Collected.left_out.
        self._custody: Path | None = None
        self._left_out: frozenset[str] = frozenset()
        self._files_lost = False
        self._files_given_back = False
        self.log = EventLog(plan.run_dir / "events.jsonl")
        self.clock = Stopwatch()
        self.totals = Totals()
        self.started_at = datetime.now(UTC)
        self.ended_at: datetime | None = None

        self._adapter = get_adapter(plan.spec.provider)
        self._tasks: list[asyncio.Task] = []       # pumps, cancelled per pass
        self._watchdog_task: asyncio.Task | None = None
        self._stdin_lock = asyncio.Lock()
        self._stdin_closed = False
        self._result_seen = False
        self._initial_echo_pending = True
        self._wrapup_sent = False
        self._hard_stop_at: float | None = None
        self._frozen_since: float | None = None
        self._finalized = False
        self._counted_messages: set[str] = set()
        self._last_rate_limit: dict[str, Any] | None = None
        self._result_subtype: str = ""
        self.produced_output = False
        self._exit_future: asyncio.Future[int] = asyncio.get_event_loop().create_future()
        # Set only when _finalize has fully completed. Terminal *state* is
        # published early so the UI reacts immediately, so callers that need
        # the lab book and commit on disk must wait on this instead.
        self.finished: asyncio.Event = asyncio.Event()
        self._status_before = plan.problem.status() if plan.spec.mode != "oneshot" else "n/a"
        self._git_base: str | None = None

    # ------------------------------------------------------------------ util

    def emit(self, event: Event, *, topic: str | None = None) -> None:
        try:
            self.log.append(event)
        except Exception:                                     # noqa: BLE001
            pass          # logging must never be able to kill a run
        self.hub.publish(topic or self.topic, event)

    def notice(self, message: str, **payload: Any) -> None:
        self.emit(Event(type=ev.NOTICE, run_id=self.run_id,
                        payload={"message": message, **payload}))

    def set_state(self, state: RunState, reason: str = "") -> None:
        if self.state == state:
            return
        self.state = state
        self._write_time_remaining()
        self.emit(Event(type=ev.RUN_STATE, run_id=self.run_id,
                        payload={"state": str(state), "reason": reason}))
        self.hub.publish(ev.TOPIC_RUNS, Event(
            type=ev.RUN_STATE, run_id=self.run_id,
            payload={"state": str(state), "problem": self.plan.problem.slug,
                     "harness": self.plan.harness.id, "reason": reason},
        ))

    @property
    def elapsed(self) -> float:
        return self.clock.wall

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": str(self.state),
            "problem": self.plan.problem.slug,
            "problem_title": self.plan.problem.meta.title,
            "harness": self.plan.harness.id,
            "harness_version": self.plan.harness.version,
            "provider": self.plan.spec.provider,
            "account": self.plan.account.id if self.plan.account else None,
            "model": self.plan.model,
            "effort": self.plan.effort,
            "mode": self.plan.spec.mode,
            "pass": {
                "index": self.current.index,
                "label": self.current.label,
                "role": self.current.role,
                "round": self.current.round,
                "provider": self.current.provider or self.plan.spec.provider,
                "account": self._pass_account(),
                "model": self.current.model or self.plan.model,
                "effort": self.current.effort or self.plan.effort,
                "of": len(compile_passes(self.plan.harness)),
                "fraction": round(self.pass_fraction(), 4),
            },
            "started_at": self.started_at.isoformat(),
            "elapsed": round(self.elapsed, 1),
            "frozen_seconds": round(self.clock.frozen, 1),
            "budget": {
                "usd": self.plan.budget_usd,
                "minutes": self.plan.budget_minutes,
                "effective_minutes": self._run_time_limit(),
                "review_allowance_minutes": (max(0.0, REVIEW_EXTRA_MINUTES
                                                 - self.plan.extra.get("review_extra_minutes", 0.0))
                                             if self._is_reading_reviewer() else 0),
                "revision_allowance_minutes": (REVISION_EXTRA_MINUTES
                                               if self._is_reading_reviser() else 0),
                "review_extra_used_minutes": self.plan.extra.get("review_extra_minutes", 0.0),
                "tokens_out": self.plan.budget_tokens_out,
                "turns": self.plan.budget_turns,
                "fraction": round(self.budget_fraction(), 4),
            },
            "totals": self.totals.as_dict(),
            "ended_reason": self.ended_reason,
            "error": self.error,
        }

    # --------------------------------------------------------------- startup

    async def start(self) -> None:
        self.set_state(RunState.STARTING)
        self.emit(Event(type=ev.RUN_STARTED, run_id=self.run_id, payload={
            "problem": self.plan.problem.slug,
            "harness": self.plan.harness.id,
            "harness_version": self.plan.harness.version,
            "model": self.plan.model, "effort": self.plan.effort,
            "mode": self.plan.spec.mode,
            "account": self.plan.account.id if self.plan.account else None,
            "cwd": str(self.plan.cwd),
            "session_number": self.session_number,
        }))

        if (self.cfg.git.enabled and self.plan.spec.mode != "oneshot"
                and not self.plan.extra.get("isolated_reading")):
            from .git import baseline_commit

            self._git_base = baseline_commit(self.cfg, self.plan.problem, self.run_id)

        if self.plan.harness.loop.enabled and self.plan.spec.mode != "oneshot":
            self.loop_state = loop.start(
                self.plan.harness, self.plan.cwd, self.plan.run_dir,
                Path(self.plan.extra.get(
                    "document", self.plan.cwd / "progress" / "math.tex")))
            if self.plan.extra.get("continued_complete_draft"):
                loop.inherit_draft(self.loop_state, self.plan.spec.parent_run_id or "")
                await self._export_reading()
                loop.take_lane_snapshot(self.loop_state)

        self._persist_run_row()

        self._watchdog_task = asyncio.create_task(
            self._watchdog(), name=f"watchdog:{self.run_id}")
        await self._begin_pass(self.plan.first)

    async def _begin_pass(self, pass_plan: PassPlan, *, completion: bool = False) -> None:
        """Launch one provider process and attach the pumps to it."""
        # The step's files are its folder's again before it starts (they are
        # normally given back just before this is called).
        if self._custody is not None and not await self._files_back_to_the_ai():
            return
        self.current = pass_plan
        # A pass may run on another provider than the one before it
        # (RunSpec.role_agents): its events are read in its own CLI's terms.
        self._adapter = get_adapter(pass_plan.provider or self.plan.spec.provider)
        self.proc = None
        self.pgid = None
        self._stdin_closed = False
        self._result_seen = False
        self._result_subtype = ""
        self._initial_echo_pending = True
        self._hard_stop_at = None
        self._ending_pass = ""
        self._usage_base = replace(self.totals,
                                   tool_breakdown=Counter(self.totals.tool_breakdown))
        if not completion:
            self._wrapup_sent = False
            self._pass_base = self._usage_base
            self._pass_started_at = self.elapsed
            if self.plan.harness.kind == "read":
                minutes = stage_time_available(
                    self.plan.budget_minutes,
                    [role for role, _, _ in compile_passes(self.plan.harness)],
                    pass_plan.index - 1, self._pass_started_at / 60,
                    revision=self._is_reading_reviser(),
                    review=self._is_reading_reviewer(),
                    review_extra_used=self.plan.extra.get("review_extra_minutes", 0.0))
                self._stage_time_allocation = minutes * 60 if minutes is not None else None
            self._provider_session_id = ""
        self._write_time_remaining()
        self._pass_cost_reported = 0.0
        self._exit_future = asyncio.get_event_loop().create_future()

        if pass_plan.index > 1:
            self.emit(Event(type=ev.AGENT_STARTED, run_id=self.run_id, payload={
                "label": pass_plan.label, "role": pass_plan.role,
                "index": pass_plan.index, "round": pass_plan.round,
                "budget_usd": pass_plan.budget_usd,
            }))

        # A Claude Code reading pass gets a private scratch of its own as it
        # starts (access.attach_claude_scratch), removed when it ends.
        if (self.plan.harness.kind == "read"
                and (pass_plan.provider or self.plan.spec.provider) in ("claude", "open")):
            from .access import attach_claude_scratch
            attach_claude_scratch(pass_plan.dir / "settings.json", pass_plan.env)

        try:
            self.proc = await asyncio.create_subprocess_exec(
                *pass_plan.argv,
                cwd=str(self.plan.cwd),
                env=pass_plan.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,               # own process group
                limit=self.cfg.limits.max_event_line_bytes,
            )
        except (OSError, ValueError) as exc:
            # The pass never started, so nothing will end it: its scratch goes now.
            from .access import remove_claude_scratch
            remove_claude_scratch(pass_plan.env)
            self.error = f"failed to launch {pass_plan.argv[0]}: {exc}"
            self.emit(Event(type=ev.ERROR, run_id=self.run_id,
                            payload={"message": self.error}))
            await self._finalize(RunState.FAILED, "launch failed")
            return
        except BaseException:
            from .access import remove_claude_scratch
            remove_claude_scratch(pass_plan.env)
            raise

        try:
            self.pgid = os.getpgid(self.proc.pid)
        except (ProcessLookupError, OSError):
            self.pgid = self.proc.pid

        self.set_state(RunState.RUNNING)
        self.clock.mark_active()

        payload = self._adapter.initial_stdin(pass_plan.prompt)
        if payload:
            await self._write_stdin(payload)
        if self._adapter.capabilities.closes_stdin_after_prompt:
            # `codex exec` waits for end-of-input before it starts work; holding
            # stdin open for steering it cannot accept anyway hangs it forever.
            await self._close_stdin()

        self._tasks = [
            asyncio.create_task(self._pump_stdout(), name=f"stdout:{self.run_id}"),
            asyncio.create_task(self._pump_stderr(), name=f"stderr:{self.run_id}"),
        ]
        asyncio.create_task(self._await_exit(), name=f"wait:{self.run_id}")

    # ----------------------------------------------------------------- pumps

    async def _pump_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        reader = self.proc.stdout
        while True:
            try:
                line = await reader.readline()
            except (asyncio.LimitOverrunError, ValueError) as exc:
                # Should not happen with a 32 MiB limit, but if it does, say so
                # loudly rather than silently truncating the record.
                self.notice(f"oversized provider line skipped: {exc}", level="warn")
                with contextlib.suppress(Exception):
                    await reader.read(1 << 20)
                continue
            except (asyncio.IncompleteReadError, ConnectionResetError):
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                self.emit(Event(type=ev.PROVIDER_RAW, run_id=self.run_id,
                                payload={"unparsed": text[:4000]}))
                continue
            await self._handle_raw(raw)

    async def _pump_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        path = self.plan.run_dir / "stderr.log"
        with path.open("a", encoding="utf-8") as fh:
            while True:
                try:
                    line = await self.proc.stderr.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    continue
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                fh.write(text + "\n")
                fh.flush()
                lowered = text.lower()
                if any(k in lowered for k in ("error", "rate limit", "overloaded", "denied")):
                    self.emit(Event(type=ev.ERROR, run_id=self.run_id,
                                    payload={"message": text[:2000], "source": "stderr"}))
                    self._maybe_note_rate_limit(text)

    async def _handle_raw(self, raw: dict[str, Any]) -> None:
        for event in self._adapter.parse_event(raw, self.run_id):
            # Suppress the echo of our own opening prompt.
            if event.type == "steer.echo" and self._initial_echo_pending:
                self._initial_echo_pending = False
                continue

            if event.type == ev.USAGE:
                self._apply_usage(event.payload)
                if event.payload.get("final"):
                    self._result_seen = True
                    self._result_subtype = str(event.payload.get("subtype") or "")

            elif event.type == ev.ASSISTANT_TEXT:
                if str(event.payload.get("text", "")).strip():
                    self.produced_output = True

            elif event.type == ev.TOOL_USE:
                self.totals.tool_calls += 1
                name = str(event.payload.get("name") or "?")
                self.totals.tool_breakdown[name] += 1

            elif event.type == "steer.echo":
                self.emit(Event(type=ev.STEER_DELIVERED, run_id=self.run_id,
                                payload={"text": event.payload.get("text", "")}))
                continue

            elif event.type == ev.RATE_LIMIT:
                self._record_rate_limit(event.payload)

            elif event.type == ev.ERROR:
                self._maybe_note_rate_limit(str(event.payload.get("message", "")))

            if event.agent == "lead" and event.payload.get("session_id"):
                self._provider_session_id = str(event.payload["session_id"])

            if event.type in ev.EPHEMERAL:
                # High-frequency and only useful live: publish, never persist.
                event.seq = 0
                self.hub.publish(ev.topic_run_deltas(self.run_id), event)
            else:
                self.emit(event)

        if self._result_seen and not self._stdin_closed:
            # The turn is complete. Close stdin so the CLI exits instead of
            # waiting forever for more streamed input.
            await self._close_stdin()

    def _pass_account(self) -> str | None:
        """The account the pass now running uses."""
        if self.current.account:
            return self.current.account
        return self.plan.account.id if self.plan.account else None

    def _apply_usage(self, p: dict[str, Any]) -> None:
        # The model this pass was started on prices it; the one an event names
        # is the fallback (an alias such as `opus`, which the table cannot price,
        # comes back from the CLI as the model it stands for).
        planned = self.current.model or self.plan.model
        model = str(p.get("model") or planned)
        tin = int(p.get("tok_in") or 0)
        tout = int(p.get("tok_out") or 0)
        cr = int(p.get("cache_read") or 0)
        cw = int(p.get("cache_write") or 0)
        cw5 = int(p.get("cache_write_5m") or 0)
        cw1 = int(p.get("cache_write_1h") or 0)

        # The CLI repeats one API response's usage on every streamed block of
        # that response (a message with thinking + text reports it twice).
        # Counting per message id is what keeps the running total honest.
        if not p.get("final"):
            mid = p.get("message_id")
            if mid:
                if mid in self._counted_messages:
                    return
                self._counted_messages.add(mid)

        if p.get("final"):
            # The terminal result carries authoritative totals for *this pass*;
            # trust them over our running sum, but keep whichever is larger so a
            # provider that under-reports cannot erase measured usage. The
            # reconciliation is against the pass's own contribution: a run with
            # several passes would otherwise have pass two's smaller total
            # silently replace the sum of everything before it.
            base = getattr(self, "_usage_base", self._pass_base)

            def settle(current: int, reported: int, floor: int) -> int:
                return floor + max(current - floor, reported)

            self.totals.tok_in = settle(self.totals.tok_in, tin, base.tok_in)
            self.totals.tok_out = settle(self.totals.tok_out, tout, base.tok_out)
            self.totals.cache_read = settle(self.totals.cache_read, cr, base.cache_read)
            self.totals.cache_write = settle(self.totals.cache_write, cw, base.cache_write)
            if (turns := p.get("num_turns")) is not None:
                self.totals.turns = settle(self.totals.turns, int(turns), base.turns)
            self.totals.turns += int(p.get("turn_delta") or 0)
            if (cost := p.get("total_cost_usd")) is not None:
                # Assignment, not accumulation: a provider that emits its final
                # result twice in one pass would otherwise bill it twice. The
                # pass's own figure is added to what earlier passes reported.
                self._pass_cost_reported = float(cost)
                self.totals.cost_reported = ((base.cost_reported or 0.0)
                                             + self._pass_cost_reported)
        else:
            self.totals.tok_in += tin
            self.totals.tok_out += tout
            self.totals.cache_read += cr
            self.totals.cache_write += cw
            self.totals.cache_write_5m += cw5
            self.totals.cache_write_1h += cw1
            self.totals.turns += 1
            self.db.add_usage(
                run_id=self.run_id,
                # The first pass keeps the label every existing row has; later
                # passes carry their own, so per-pass spend is recoverable from
                # the usage table rather than only from the run's total.
                agent_label="lead" if self.current.index == 1 else self.current.label,
                ts=ev.now_iso(),
                account=self._pass_account(),
                model=model, tok_in=tin, tok_out=tout,
                tok_cache_read=cr, tok_cache_write=cw,
                cost=self.cfg.prices.cost(model, tok_in=tin, tok_out=tout,
                                          cache_read=cr, cache_write=cw,
                                          cache_write_5m=cw5, cache_write_1h=cw1) or 0.0,
            )

        # This pass's share, priced at its own model, added to what the passes
        # before it cost. With one pass — almost every run — the share is the
        # whole run and this is the run's total, exactly as it always was.
        base = self._pass_base
        share = {
            "tok_in": self.totals.tok_in - base.tok_in,
            "tok_out": self.totals.tok_out - base.tok_out,
            "cache_read": self.totals.cache_read - base.cache_read,
            "cache_write": self.totals.cache_write - base.cache_write,
            "cache_write_5m": self.totals.cache_write_5m - base.cache_write_5m,
            "cache_write_1h": self.totals.cache_write_1h - base.cache_write_1h,
        }
        recomputed = self.cfg.prices.cost(planned, **share)
        if recomputed is None and model != planned:
            recomputed = self.cfg.prices.cost(model, **share)
        if recomputed is not None:
            self.totals.cost_computed = base.cost_computed + recomputed
        elif p.get("final") and p.get("total_cost_usd") is not None:
            self.totals.cost_computed = base.cost_computed + self._pass_cost_reported
        else:
            self.totals.priced = False

    def _record_rate_limit(self, payload: dict[str, Any]) -> None:
        """Store the provider's own window state — the best credits signal there is.

        The CLI volunteers this unprompted (`status`, `rateLimitType`, `resetsAt`),
        which is far better than anything we could infer, so it is kept verbatim
        and surfaced in the accounts view alongside our own measured spend.
        """
        acct = self._pass_account() or "unknown"
        resets_at = payload.get("resets_at_epoch")
        iso = None
        if isinstance(resets_at, (int, float)) and resets_at > 0:
            iso = datetime.fromtimestamp(float(resets_at), tz=UTC).isoformat()
        record = {
            "status": payload.get("status"),
            "window": payload.get("window"),
            "resets_at": iso,
            "using_overage": payload.get("using_overage"),
        }
        if record == self._last_rate_limit:
            return                      # the CLI repeats this on every turn
        self._last_rate_limit = record
        kind = rate_limit_kind(payload.get("status"))
        self.db.add_account_event(acct, ev.now_iso(), kind, record)
        self.hub.publish(ev.TOPIC_ACCOUNTS, Event(
            type=ev.RATE_LIMIT, run_id=self.run_id,
            payload={"account": acct, **record},
        ))

    def _maybe_note_rate_limit(self, text: str) -> None:
        lowered = text.lower()
        if "rate limit" not in lowered and "usage limit" not in lowered:
            return
        acct = self._pass_account() or "unknown"
        self.db.add_account_event(acct, ev.now_iso(), "rate_limited", {"message": text[:500]})
        self.hub.publish(ev.TOPIC_ACCOUNTS, Event(
            type=ev.NOTICE, run_id=self.run_id,
            payload={"account": acct, "message": "rate limit reported", "detail": text[:500]},
        ))

    # ------------------------------------------------------------- budgeting

    def _write_time_remaining(self) -> None:
        if self.plan.harness.kind != "read":
            return
        from .time_remaining import state_path

        path = state_path(self.plan.run_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "stage": self.current.role,
            "state": str(self.state),
            "active": not self.state.terminal,
            "sampled_at": time.monotonic(),
            "elapsed_seconds": self.elapsed,
            "stage_started_at": self._pass_started_at,
            "stage_allocation_seconds": self._stage_time_allocation,
            "run_limit_seconds": (self._run_time_limit() * 60
                                  if self._run_time_limit() is not None else None),
            "stage_limit_kind": ("unlimited" if self._stage_time_allocation is None else
                                 "deadline"),
        }
        staged = path.with_suffix(".tmp")
        staged.write_text(json.dumps(data), encoding="utf-8")
        staged.replace(path)

    def _is_reading_reviser(self, role: str | None = None) -> bool:
        return (self.plan.harness.kind == "read" and self.plan.harness.loop.enabled
                and (role or self.current.role) == self.plan.harness.loop.reviser)

    def _is_reading_reviewer(self, role: str | None = None) -> bool:
        return (self.plan.harness.kind == "read" and self.plan.harness.loop.enabled
                and (role or self.current.role) == self.plan.harness.loop.critic)

    def _run_time_limit(self, role: str | None = None) -> float | None:
        return reading_run_limit(self.plan.budget_minutes,
                                 revision=self._is_reading_reviser(role),
                                 review=self._is_reading_reviewer(role),
                                 review_extra_used=self.plan.extra.get("review_extra_minutes", 0.0))

    def _budget_fraction_for(self, role: str) -> float:
        """How far along the *binding* cap we are, in [0, 1+)."""
        fractions: list[float] = []
        if self.plan.budget_usd:
            fractions.append(self.totals.cost_computed / self.plan.budget_usd)
        if limit := self._run_time_limit(role):
            fractions.append((self.elapsed / 60.0) / limit)
        if self.plan.budget_tokens_out:
            fractions.append(self.totals.tok_out / self.plan.budget_tokens_out)
        if self.plan.budget_turns:
            fractions.append(self.totals.turns / self.plan.budget_turns)
        return max(fractions) if fractions else 0.0

    def budget_fraction(self) -> float:
        return self._budget_fraction_for(self.current.role)

    def pass_fraction(self) -> float:
        """How far along the *current pass's* share of the budget we are.

        For a run with a single pass this is the run fraction, exactly. For a
        looped run it is what stops the draft consuming the whole session and
        leaving nothing for the reading that was the reason to enable the loop.
        """
        p = self.current
        fractions: list[float] = []
        if p.budget_usd:
            spent = self.totals.cost_computed - self._pass_base.cost_computed
            fractions.append(spent / p.budget_usd)
        if p.budget_minutes:
            minutes = (self.elapsed - self._pass_started_at) / 60.0
            fractions.append(minutes / p.budget_minutes)
        if p.budget_tokens_out:
            fractions.append((self.totals.tok_out - self._pass_base.tok_out)
                             / p.budget_tokens_out)
        if p.budget_turns:
            fractions.append((self.totals.turns - self._pass_base.turns)
                             / p.budget_turns)
        return max(fractions) if fractions else 0.0

    def _binding_cap_text(self) -> str:
        bits = []
        if self.plan.budget_usd:
            bits.append(f"${self.totals.cost_computed:.2f} of ${self.plan.budget_usd:.2f}")
        if limit := self._run_time_limit():
            bits.append(f"{self.elapsed / 60:.0f} of {limit:g} minutes")
        if self.plan.budget_tokens_out:
            bits.append(f"{self.totals.tok_out:,} of {self.plan.budget_tokens_out:,} output tokens")
        return "; ".join(bits) or "no cap"

    def _reading_deadline(self, *, include_review_grace: bool = True) -> float | None:
        if self.plan.harness.kind != "read" or self.loop_state is None:
            return None
        return reading_stage_deadline(
            self.plan.budget_minutes,
            [role for role, _, _ in compile_passes(self.plan.harness)],
            self.current.index - 1, self._pass_started_at / 60,
            review=include_review_grace and self._is_reading_reviewer(),
            review_extra_used=self.plan.extra.get("review_extra_minutes", 0.0))

    async def _watchdog(self) -> None:
        """Budget thresholds, freeze warnings, and the hard stop."""
        wrapup_at = self.plan.harness.budget.wrapup_at
        grace = self.plan.harness.budget.grace_seconds
        while not self.state.terminal:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            if self.state is RunState.FROZEN:
                if self._frozen_since and (time.monotonic() - self._frozen_since) > FREEZE_WARN_SECONDS:
                    self.notice(
                        "Frozen for over a minute — an in-flight request may time out. "
                        "Thaw, or use Checkpoint & stop for a longer pause.",
                        level="warn", once="freeze-warn",
                    )
                continue

            if self._ending_run or self._ending_pass or self._delivery_transition or self._finalized:
                continue            # already winding this pass down

            run_frac = self.budget_fraction()
            frac = max(run_frac, self.pass_fraction())
            if self.plan.harness.kind == "read":
                if run_frac >= 1.0:
                    await self.stop(reason="run budget exhausted")
                    return
                deadline = self._reading_deadline()
                if deadline is not None and self.elapsed / 60 >= deadline:
                    await self._stop_pass("reached its time allocation; "
                                          "reserving time for the remaining review/revision")
                    continue
                allocation = self._stage_time_allocation
                if (allocation is not None and not self._wrapup_sent
                        and self.elapsed - self._pass_started_at >= WRAPUP_AT * allocation):
                    await self.wrapup(auto=True)
                continue
            if frac and frac >= wrapup_at and not self._wrapup_sent:
                await self.wrapup(auto=True)
                self._hard_stop_at = time.monotonic() + grace
            if frac >= 1.0 and self._hard_stop_at is None:
                self._hard_stop_at = time.monotonic() + min(grace, 120)
            if self._hard_stop_at and time.monotonic() >= self._hard_stop_at:
                self.emit(Event(type=ev.BUDGET, run_id=self.run_id, payload={
                    "level": "hard_stop", "fraction": round(frac, 3),
                    "detail": self._binding_cap_text(),
                }))
                if self.loop_state is None or run_frac >= 1.0:
                    await self.stop(reason="budget exhausted")
                    return
                # Only this pass overran its own share. Ending it lets the
                # passes after it run, which is the whole reason the budget was
                # divided rather than left as one pool.
                await self._stop_pass("used its share of the budget")
                self._hard_stop_at = None

    # -------------------------------------------------------------- steering

    async def _write_stdin(self, payload: str) -> bool:
        async with self._stdin_lock:
            if self._stdin_closed or not self.proc or not self.proc.stdin:
                return False
            try:
                self.proc.stdin.write(payload.encode("utf-8"))
                await self.proc.stdin.drain()
                return True
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                self._stdin_closed = True
                return False

    async def _close_stdin(self) -> None:
        async with self._stdin_lock:
            if self._stdin_closed:
                return
            self._stdin_closed = True
            if self.proc and self.proc.stdin:
                with contextlib.suppress(Exception):
                    self.proc.stdin.close()

    async def steer(self, text: str, origin: str = "user") -> bool:
        text = text.strip()
        if not text:
            return False
        if (self.state.terminal or self._stdin_closed
                or self._adapter.capabilities.steering == "none"):
            # Nothing to emit: the log is closed and the transcript is final.
            return False
        sid = self.db.add_steering(self.run_id, ev.now_iso(), text, origin)
        self.emit(Event(type=ev.STEER_SENT, run_id=self.run_id,
                        payload={"text": text, "origin": origin, "id": sid}))
        ok = await self._write_stdin(self._adapter.encode_steering(text))
        if ok:
            self.db.mark_steering_delivered(sid, ev.now_iso())
        return ok

    def _required_files(self) -> str:
        """What the pass now running actually owes, named exactly.

        Telling a reading session to update `progress/math.tex` is not a
        harmless generic nudge: that session is forbidden to touch the document
        and is checked by hash on the way out, so the instruction asks it to end
        in violation.
        """
        if self.current.index > 1:
            # The compiled pass already owns an exact write lane. Reading that
            # declaration keeps wrap-up advice correct for combined roles such
            # as Balanced's auditor and for optional outputs such as its map.
            expect = ", ".join(self.current.writes)
            if not expect:
                expect = f"the output for the {self.current.role} pass"
            return f"write {expect} and progress/_session_notes.md"
        if self.plan.harness.kind == "read":
            return ("finish progress/reading/guide.md and write "
                    "progress/_session_notes.md. Leave the document untouched")
        return ("update progress/math.tex, update progress/state.json, and write "
                "progress/_session_notes.md")

    async def wrapup(self, auto: bool = False) -> bool:
        self._wrapup_sent = True
        self.set_state(RunState.WRAPPING_UP, "budget threshold" if auto else "requested")
        frac = max(self.budget_fraction(), self.pass_fraction())
        scope = "this pass's share of the budget" if self.current.index > 1 \
            else "this session's budget"
        detail = self._binding_cap_text()
        if self.plan.harness.kind == "read" and self._stage_time_allocation is not None:
            used = self.elapsed - self._pass_started_at
            frac = used / self._stage_time_allocation if self._stage_time_allocation else 1.0
            scope = "this stage's time allocation"
            detail = (f"{max(0.0, self._stage_time_allocation - used):.0f} seconds "
                      f"remain in this stage; {detail}")
        if auto:
            msg = (
                f"You have used {frac * 100:.0f}% of {scope} "
                f"({detail}). Stop opening new lines of attack now. "
                f"Finish what is in progress, then {self._required_files()}. "
                f"Be honest about what is unfinished."
            )
            self.emit(Event(type=ev.BUDGET, run_id=self.run_id, payload={
                "level": "wrapup", "fraction": round(frac, 3),
                "detail": detail,
            }))
        else:
            msg = ("Please wrap up now. Stop new work, finish what is in "
                   f"progress, then {self._required_files()}.")
        return await self.steer(msg, origin="budget" if auto else "user")

    # --------------------------------------------------------------- signals

    def _signal_group(self, sig: int) -> bool:
        if self.pgid is None:
            return False
        try:
            os.killpg(self.pgid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    async def freeze(self) -> bool:
        if self.state.terminal or self.state is RunState.FROZEN:
            return False
        if not self._signal_group(signal.SIGSTOP):
            return False
        self._frozen_since = time.monotonic()
        self.clock.freeze()
        self.set_state(RunState.FROZEN, "frozen by user")
        return True

    async def thaw(self) -> bool:
        if self.state is not RunState.FROZEN:
            return False
        if not self._signal_group(signal.SIGCONT):
            return False
        self._frozen_since = None
        self.clock.thaw()
        self.set_state(RunState.RUNNING, "thawed")
        return True

    async def _escalate(self, *, immediate: bool = False) -> None:
        """Close stdin, then SIGINT, SIGTERM, SIGKILL until the process is gone."""
        await self._close_stdin()
        if self.proc is None:
            return
        stages = [(signal.SIGINT, SIGINT_TO_SIGTERM),
                  (signal.SIGTERM, SIGTERM_TO_SIGKILL), (signal.SIGKILL, 5.0)]
        if not immediate:
            stages.insert(0, (None, 6.0))
        for sig, timeout in stages:
            if sig is not None:
                self._signal_group(sig)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._exit_future), timeout=timeout)
                return

    async def stop(self, reason: str = "stopped by user") -> None:
        """Graceful escalation: close stdin, SIGINT, SIGTERM, SIGKILL."""
        if self.state.terminal:
            return
        self._ending_run = True
        if self.state is RunState.FROZEN:
            await self.thaw()
        self.set_state(RunState.STOPPING, reason)
        self.ended_reason = reason

        if self.proc is None:
            await self._close_stdin()
            await self._finalize(RunState.CANCELLED, reason)
            return
        await self._escalate(immediate="budget" in reason)

    async def _stop_pass(self, reason: str) -> None:
        """End the pass now running, without ending the run.

        The loop goes on to the next pass. Used when a pass exhausts its own
        share of the budget while the run still has room.
        """
        if self._ending_pass or self.state.terminal:
            return
        self._ending_pass = reason
        self.notice(f"{self.current.label} {reason}; checking its saved output",
                    level="warn", kind="pass_stopped")
        await self._escalate(immediate=True)

    async def abandon(self) -> None:
        """Kill and revert everything this session wrote to the problem folder."""
        self.ended_reason = "abandoned"
        self._ending_run = True
        if self.state is RunState.FROZEN:
            await self.thaw()
        self._signal_group(signal.SIGKILL)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._exit_future), timeout=8.0)
        if (self.cfg.git.enabled and self.plan.spec.mode != "oneshot"
                and not self.plan.extra.get("isolated_reading")):
            from .git import revert_to

            if self._git_base:
                reverted = revert_to(self.cfg, self.plan.problem, self._git_base)
                self.notice(f"reverted problem folder to {self._git_base[:12]}"
                            if reverted else "revert failed; the working tree was left alone",
                            level="warn" if not reverted else "info")
        await self._finalize(RunState.CANCELLED, "abandoned")

    # ---------------------------------------------------------------- finish

    async def _await_exit(self) -> None:
        assert self.proc
        try:
            code = await self.proc.wait()
        except Exception:                                        # noqa: BLE001
            code = -1
        if not self._exit_future.done():
            self._exit_future.set_result(code)

        # Give the pumps a moment to drain anything still buffered.
        for task in self._tasks:
            if task.get_name().startswith(("stdout", "stderr")):
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
        for task in self._tasks:
            task.cancel()

        # A Claude Code pass's own scratch (access.claude_scratch) goes with it.
        if self.current is not None:
            from .access import remove_claude_scratch
            remove_claude_scratch(self.current.env)

        if self.state.terminal:
            return

        self._delivery_transition = True
        if self.plan.harness.kind == "read":
            # From here until its next step, the reading's files are a private
            # copy of the controller's: a program the step left running can
            # change the AI's folder, but nothing read or written from now on.
            # Entries that are not plain files or folders are refused while the
            # copy is taken — set aside, never followed — as the check here
            # always did.
            try:
                refused = self._take_files()
            except OSError as exc:
                self.error = f"The reading's working files could not be read: {exc}"
                await self._finalize(RunState.FAILED, "the working files could not be read")
                return
            if refused:
                self.error = handover.unsafe_message(refused[0])
                self.notice("Unsafe working files were quarantined; no linked "
                            "target was read or deleted.", level="warn", kind="access_policy")
                await self._finalize(RunState.FAILED, "unsafe file link in run output")
                return

        # A provider budget stop is a planned outcome, not a crash: the work it
        # produced is still on disk and the record should say what happened.
        known = {
            "error_max_budget_usd": ("provider budget cap reached", RunState.CANCELLED),
            "error_max_turns": ("provider turn limit reached", RunState.CANCELLED),
        }

        ok = code == 0 or self._result_subtype in known or bool(self._ending_pass)
        if await self._complete_writer_if_needed(ok=ok):
            return

        # A pass of a critique loop. The run ends only when the loop does.
        if self.loop_state is not None and not self._ending_run:
            if await self._advance(ok=ok):
                return
            await self._finalize(
                RunState.DONE if ok else RunState.FAILED,
                self.loop_state.finished or self.ended_reason or "completed normally")
            return

        if self._ending_run and self.plan.harness.kind == "read":
            await self._finalize(RunState.CANCELLED, self.ended_reason or "stopped")
            return

        if code == 0 or (self._ending_pass and self.plan.harness.kind == "read"):
            await self._finalize(RunState.DONE, self.ended_reason or "completed normally")
            return

        if self._result_subtype in known:
            reason, state = known[self._result_subtype]
            self.error = None
            await self._finalize(state, self.ended_reason or reason)
            return

        self.error = self.error or f"provider exited with code {code}"
        reason = self.ended_reason or f"exited with code {code}"
        state = RunState.CANCELLED if self.state is RunState.STOPPING else RunState.FAILED
        await self._finalize(state, reason)

    def _diagram_problems(self) -> list[str]:
        if not self.plan.harness.metadata.get("include_proof_map"):
            return []
        from deslopify.source import SourceError, read_source

        source = self.plan.run_dir / "source-excerpt.txt"
        try:
            text = (source.read_text() if source.is_file() else
                    read_source(self._document_now()))
        except (OSError, SourceError, TypeError) as exc:
            return [f"Could not check the diagram against its source: {exc}"]
        return diagram_problems(self._files / "progress/reading", True, text)

    async def _complete_writer_if_needed(self, *, ok: bool) -> bool:
        writer = self.current.role == "draft"
        reviser = self.current.role == self.plan.harness.loop.reviser
        if (not ok or self._ending_run or self.state.terminal
                or self.plan.harness.kind != "read" or not (writer or reviser)
                or (writer and self._writer_completion_used
                    and not (self._files / "progress/reading/guide.md").is_file())):
            return False
        deadline = self._reading_deadline()
        if deadline is not None and self.elapsed / 60 >= deadline:
            return False  # completion/resumption must not consume the reserved time
        problems = guide_problems(self._files / "progress" / "reading",
                                  self.plan.harness.contract)
        guide = self._files / "progress" / "reading" / "guide.md"
        missing_diagram = self._diagram_problems()
        has_content_gaps = bool(problems)
        problems.extend(missing_diagram)
        if reviser:
            await self._export_reading()
            problems.extend(export_problems(guide.parent, self.plan.harness.contract))
            dispositions = guide.parent / f"dispositions-{self.current.round}.json"
            if not dispositions.is_file() or not dispositions.read_text().strip():
                problems.append(f"Missing revision record: {dispositions.name}")
        if (writer and self.loop_state is not None and guide.is_file()
                and guide.read_text(encoding="utf-8", errors="replace").strip()
                and (has_content_gaps or not missing_diagram)):
            return False  # hand the partial draft and missing sections to review/revision
        if (not problems or self.budget_fraction() >= 1
                or not self._provider_session_id or not self._adapter.capabilities.resume):
            return False
        if writer:
            self._writer_completion_used = True
        attempt = self._completion_attempts.get(self.current.index, 0) + 1
        self._completion_attempts[self.current.index] = attempt
        try:
            if (document := self._document_now()) is not None and restore_source(
                    document, self.plan.run_dir):
                self.notice("Restored the original source before completion.", level="warn")
            completion = plan_writer_completion(
                self.cfg, self.plan, self._provider_session_id, problems,
                stage=self.current, attempt=attempt)
            limit = self._run_time_limit()
            remaining = max(0.0, limit * 60 - self.elapsed) if limit is not None else None
            if remaining is not None:
                completion.prompt += f"\nTime remaining in the whole run: {remaining:.0f} seconds.\n"
            (completion.dir / "prompt.md").write_text(completion.prompt, encoding="utf-8")
            (completion.dir / "attempt.json").write_text(json.dumps({
                "session_id": self._provider_session_id, "problems": problems,
                "remaining_seconds": remaining,
            }, indent=2), encoding="utf-8")
        except Exception as exc:
            self.notice(f"Could not prepare the completion: {exc}", level="warn")
            return False
        if self._ending_run or self.state.terminal:
            return False
        self.notice(f"Resuming the {'writer' if writer else 'reviser'} to finish required output "
                    "from existing evidence within the remaining shared budget.",
                    kind="writer_completion" if writer else "revision_completion")
        if not await self._files_back_to_the_ai():
            return True                   # the run has ended
        await self._begin_pass(completion, completion=True)
        self._delivery_transition = False
        return True

    async def _export_reading(self) -> None:
        if self.plan.harness.kind != "read" or (
                self.plan.harness.contract != "companion"
                and not (self.plan.harness.variant or "").startswith("multi-")):
            return
        from .access import check_job_tree
        check_job_tree(self._files)
        reading = self._files / "progress" / "reading"
        guide = reading / "guide.md"
        if not guide.is_file() or not guide.read_text(encoding="utf-8", errors="replace").strip():
            return
        ensure_source_scope_notice(reading, self.plan.run_dir / "source-scope.json")
        audience = "non-expert" if "nonexpert" in self.plan.harness.id else "expert"
        digest = export_fingerprint(reading, audience)
        import hashlib
        def outputs() -> tuple[str, ...]:
            return tuple(hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
                         for path in (reading / f"companion.{ext}" for ext in ("md", "tex", "pdf")))
        if digest == self._export_digest and outputs() == self._export_outputs:
            return
        self._export_digest = digest
        self._export_error = ""
        try:
            await self._typeset(reading, audience)
        except Exception as exc:
            self._export_error = f"Companion export failed: {exc}"
            self.notice(self._export_error, level="warn", kind="delivery")
        diagnostic = export_error_path(reading)
        if self._export_error:
            diagnostic.write_text(self._export_error, encoding="utf-8")
        else:
            diagnostic.unlink(missing_ok=True)
            # Export may stage a referenced source graphic into figures/.
            # Remember the resulting inputs so the next handoff need not build
            # the same PDF a second time.
            self._export_digest = export_fingerprint(reading, audience)
        self._export_outputs = outputs()

    def _reading_outcome(self, state: RunState, reason: str) -> tuple[RunState, str]:
        """A transcript or profile record is not a delivered companion."""
        reading = self._files / "progress" / "reading"
        if self.plan.harness.loop.critic == "auditor" and not diagram_required(
                reading, bool(self.plan.harness.metadata.get("include_proof_map"))):
            for name in DIAGRAM_FILES:
                (reading / name).unlink(missing_ok=True)
        guide = reading / "guide.md"
        has_guide = guide.is_file() and bool(guide.read_text(encoding="utf-8", errors="replace").strip())
        self.produced_output = has_guide
        if reason == "abandoned":
            return state, reason
        if not has_guide:
            return RunState.FAILED, f"No guide was saved. {reason}"
        checked = self.loop_state is None or (
            self.loop_state.completed and all(p.completed for p in self.loop_state.outcomes
                                             if p.role != "draft"))
        issues = [self._export_error] if self._export_error else []
        issues.extend(self._diagram_problems())
        if not checked:
            issues.append("The independent review and revision did not finish.")
        report = delivery_report(reading, self.plan.harness.contract,
                                 completed=checked and state == RunState.DONE, issues=issues)
        (self.plan.run_dir / "delivery.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if report["status"] != "completed":
            detail = " ".join([*report["missing"], *report["issues"]]) or reason
            return RunState.INCOMPLETE, f"{report['label']} saved. {detail}"
        return state, reason

    # ------------------------------------------------------------- handover

    def _custody_applies(self) -> bool:
        """Whether this run's files go through the handover (handover.py): a
        reading working in a snapshot of its own — every reading a launcher
        starts. A reading in the problem's own folder, which only tests and
        previews make, keeps the lab's way of finishing in that folder."""
        return self.plan.harness.kind == "read" and self.plan.cwd != self.plan.problem.root

    @property
    def _files(self) -> Path:
        """Where the controller finds the run's working files now: its private
        copy between a reading's steps; nowhere at all once they could not be
        taken, or have been given back at the end; otherwise the working folder
        itself — before a reading's first step, and for a lab session."""
        if self._custody is not None:
            return self._custody
        if self._files_lost or self._files_given_back:
            return self.plan.run_dir / handover.HELD          # not there by then
        return self.plan.cwd

    def _in_files(self, path: Path) -> Path:
        """`path`, a path in the working folder, where the controller finds it now."""
        if self._files == self.plan.cwd:
            return path
        try:
            return self._files / path.relative_to(self.plan.cwd)
        except ValueError:
            return path

    def _document_now(self) -> Path | None:
        raw = self.plan.extra.get("document")
        return self._in_files(Path(raw)) if raw else None

    def _loop_document(self) -> Path:
        """The document as the loop was started with it (`start`)."""
        return Path(self.plan.extra.get("document", self.plan.cwd / "progress" / "math.tex"))

    def _handover_parts(self) -> list[str]:
        """What the controller takes and gives back: `progress/` — the reading,
        the session notes and, for a paper or papers in unpacman, the document —
        and the document, wherever else it may be."""
        parts = ["progress"]
        if (raw := self.plan.extra.get("document")):
            try:
                rel = Path(raw).relative_to(self.plan.cwd).as_posix()
            except ValueError:
                rel = ""
            if rel and rel != "progress" and not rel.startswith("progress/"):
                parts.append(rel)
        return parts

    def _point_loop_at_files(self) -> None:
        if isinstance(self.loop_state, LoopState):
            self.loop_state.cwd = self._files
            self.loop_state.document = self._in_files(self._loop_document())

    def _take_files(self) -> list[str]:
        """Take the reading's files from its AI's folder into the controller's
        private copy (handover.collect) as a step ends, before anything in them
        is checked, read or written. Returns the entries refused, each set
        aside in rejected-files/ and never followed. Nothing to do for a lab
        session, or while the copy is held."""
        if (not self._custody_applies() or self._custody is not None
                or self._files_lost or self._files_given_back):
            return []
        try:
            took = handover.collect(self.plan.cwd, self.plan.run_dir / handover.HELD,
                                    self._handover_parts(),
                                    self.plan.run_dir / handover.QUARANTINE)
        except OSError:
            # Never fall back on the folder itself: without a copy the
            # controller has nothing to read.
            self._files_lost = True
            self._point_loop_at_files()
            raise
        self._custody = took.root
        self._left_out = took.left_out
        self._point_loop_at_files()
        return [str(self.plan.cwd / rel) for rel in took.unsafe]

    def _give_files_back(self, *, final: bool = False) -> list[str]:
        """Put the controller's copy back in the AI's folder (handover.deliver):
        just before the reading's next step, and at its end. Returns what had
        appeared there meanwhile that is not a plain file or folder — a link
        put there after the step had ended; what it was among is then kept in
        rejected-files/, and otherwise removed."""
        if self._custody is None:
            return []
        aside = self.plan.run_dir / handover.SET_ASIDE
        appeared = handover.deliver(self._custody, self.plan.cwd, self._handover_parts(), aside)
        self._custody = None
        self._files_given_back = final
        self._point_loop_at_files()
        if appeared:
            keep = self.plan.run_dir / handover.QUARANTINE
            keep.mkdir(exist_ok=True)
            aside.rename(keep / f"after-pass-{self.current.index}-{uuid.uuid4().hex[:8]}")
        else:
            shutil.rmtree(aside, ignore_errors=True)
        return [str(self.plan.cwd / rel) for rel in appeared]

    async def _files_back_to_the_ai(self) -> bool:
        """Give the files back before the next step. False when that could not
        be done safely, in which case the run has been ended."""
        try:
            appeared = self._give_files_back()
        except OSError as exc:
            self.error = f"The reading's working files could not be put back: {exc}"
            await self._finalize(RunState.FAILED, "the working files could not be put back")
            return False
        if appeared:
            self.error = handover.unsafe_message(appeared[0])
            self.notice("A link appeared in the reading's working files after its step had "
                        "ended. It was not followed; the files it was among are kept in "
                        "rejected-files/, and the reading has stopped.",
                        level="warn", kind="access_policy")
            await self._finalize(RunState.FAILED, "unsafe file link in run output")
            return False
        return True

    async def _typeset(self, reading: Path, audience: str) -> None:
        """The companion's exports, made in `reading`. From the controller's
        copy, a graphic the guide names by its absolute path in the AI's folder
        is read from the copy too (summary_pdf.private_copy_of); one the copy
        leaves out is refused in the words used when the export ran in that
        folder."""
        if self._custody is None:
            await asyncio.to_thread(export_companion, reading, audience)
            return
        from deslopify.summary_pdf import private_copy_of
        with private_copy_of(self.plan.cwd, self._custody, left_out=self._left_out):
            await asyncio.to_thread(export_companion, reading, audience)

    async def _advance(self, *, ok: bool) -> bool:
        """Close out the pass that just ended and start the next, if any.

        Returns True when another pass has been launched; False when the loop is
        over and the run should finalize.
        """
        state = self.loop_state
        assert state is not None
        done = self.current
        role = self.plan.harness.roles.get(done.role)
        if self._is_reading_reviewer():
            deadline = self._reading_deadline(include_review_grace=False)
            if deadline is not None:
                used = self.plan.extra.get("review_extra_minutes", 0.0)
                self.plan.extra["review_extra_minutes"] = min(
                    REVIEW_EXTRA_MINUTES, used + max(0.0, self.elapsed / 60 - deadline))

        try:
            if done.role != "draft":
                if note := loop.restore_document(state):
                    self.notice(note, level="warn", kind="loop")
                for note in loop.restore_outside_lane(state, done.writes, state.store / "lane"):
                    self.notice(note, level="warn", kind="loop")
                await self._export_reading()
            # Filesystem work — snapshots, restores, validation. Off the event
            # loop so a large reading directory cannot stall the UI.
            outcome = await asyncio.to_thread(
                lambda: loop.assess(state, done.index, done.role, done.round, role,
                                    ok=ok, writes=done.writes))
            if done.role == "draft":
                await self._export_reading()
                if state.pending():
                    loop.take_lane_snapshot(state)
        except Exception as exc:                                  # noqa: BLE001
            self.notice(f"the loop controller failed after {done.label}: "
                        f"{type(exc).__name__}: {exc}", level="warn", kind="loop")
            loop.finish(state, "the loop controller failed")
            return False

        self.emit(Event(type=ev.AGENT_ENDED, run_id=self.run_id, payload={
            "label": done.label, "role": done.role, "index": done.index,
            "round": done.round, "summary": outcome.summary,
            "findings": outcome.findings, "blocking": outcome.blocking,
            "diff": (outcome.diff or {}).get("summary", ""),
            "stop": outcome.stop,
        }))
        for note in outcome.notes:
            self.notice(f"{done.label}: {note}", level="warn", kind="loop")
        for problem in outcome.problems:
            self.notice(f"{done.label}: {problem}", level="warn", kind="loop_contract")
        if outcome.summary:
            self.notice(f"{done.label}: {outcome.summary}", kind="loop")

        # Stop can arrive while the controller is working. The process for the
        # pass that just ended is already gone, so the escalation in stop() has
        # nothing to wait on and returns at once — without this check the loop
        # would go on and launch the next pass over the top of it.
        if self._ending_run or self.state.terminal:
            loop.finish(state, self.ended_reason or "the run was stopped")
            return False

        pending = state.pending()
        if pending is None:
            loop.finish(state, "the loop ended")
            self.notice(f"review loop finished — {state.finished}", kind="loop")
            return False

        # Readings can use every remaining minute for the next stage. Other
        # harnesses retain their reserve before starting another pass.
        threshold = (1.0 if self.plan.harness.kind == "read" else
                     min(LOOP_RESERVE, self.plan.harness.budget.wrapup_at))
        if self._budget_fraction_for(pending[0]) >= threshold:
            loop.finish(state, "the run's budget was spent before the next pass "
                               "could begin")
            self.notice(f"review loop stopped early — {state.finished}",
                        level="warn", kind="loop")
            return False

        role_name, round_no = pending
        try:
            # Its prompt quotes what this pass left: read from the controller's
            # copy (handover.py), while the pass itself is set up, as always,
            # for the AI's own folder.
            nxt = plan_next_pass(self.cfg, self.plan, role_name=role_name,
                                 round_no=round_no, index=done.index + 1,
                                 remaining_minutes=(self.plan.budget_minutes - self.elapsed / 60
                                                    if self.plan.budget_minutes else None),
                                 files=self._custody)
        except Exception as exc:                                  # noqa: BLE001
            self.notice(f"could not compile the {role_name} pass: "
                        f"{type(exc).__name__}: {exc}", level="warn", kind="loop")
            loop.finish(state, "the next pass could not be compiled")
            return False
        if nxt is None:
            loop.finish(state, f"the harness defines no role {role_name!r}")
            self.notice(state.finished, level="warn", kind="loop")
            return False

        if not await self._files_back_to_the_ai():
            return True                   # the run has ended
        self.plan.passes.append(nxt)
        self._persist_run_row()
        await self._begin_pass(nxt)
        self._delivery_transition = False
        return True

    async def _finalize(self, state: RunState, reason: str) -> None:
        if self._finalized:
            return
        self._finalized = True
        await asyncio.shield(self._finalize_inner(state, reason))

    async def _finalize_inner(self, state: RunState, reason: str) -> None:
        if self.plan.harness.kind == "read":
            from .access import AccessPolicyError, check_job_tree, quarantine_job_links
            try:
                # Everything below works on the controller's copy (handover.py),
                # taken now if the run ended without its step's end being seen
                # (stopped, abandoned, failed to launch).
                if (refused := self._take_files()):
                    raise AccessPolicyError(handover.unsafe_message(refused[0]))
                if self._files_lost:
                    raise AccessPolicyError("the reading's working files could not be read")
                check_job_tree(self._files)
                if (document := self._document_now()) is not None and restore_source(
                        document, self.plan.run_dir):
                    self.notice("Restored the original source before delivery.", level="warn")
                if self.loop_state is not None:
                    loop.restore_document(self.loop_state)
                    if self.current.index > 1:
                        loop.restore_outside_lane(self.loop_state, self.current.writes,
                                                  self.loop_state.store / "lane")
                await self._export_reading()
                state, reason = self._reading_outcome(state, reason)
            except (AccessPolicyError, OSError) as exc:
                if self._custody is not None:
                    quarantine_job_links(self._custody, self.plan.run_dir / "rejected-files")
                state, reason = RunState.FAILED, f"Could not deliver the guide: {exc}"
        self.clock.mark_idle()
        self.ended_at = datetime.now(UTC)
        self.ended_reason = reason
        self.set_state(state, reason)

        files_changed: list[tuple[str, str]] = []
        commit: str | None = None
        status_after = self._status_before

        # Measured now, before anything below runs. Writing the lab book
        # *consumes* progress/_session_notes.md, so the contract check further
        # down — which fires for providers that cannot be stopped to fix
        # anything — was reporting every Codex session that wrote its notes as
        # having written none.
        # An isolated reading run writes to its private snapshot, not to the
        # shared problem. Contract checks must inspect the place the agent was
        # actually allowed to write.
        progress = self._files / "progress"
        delivered = {
            name for name in ("math.tex", "state.json", NOTES_FILENAME)
            if (progress / name).is_file()
            and (progress / name).read_text(encoding="utf-8", errors="replace").strip()
        }
        # A looped run's passes wrote their notes, and the loop moved each aside
        # as the pass ended (loop.stash_notes); they are put back together only
        # below. Looking only at the file here reported every Codex pass of a
        # loop as having written no notes when it had.
        if self.loop_state is not None and any(
                path.read_text(encoding="utf-8", errors="replace").strip()
                for path in self.loop_state.store.glob("notes-*.md")):
            delivered.add(NOTES_FILENAME)

        try:
            if self.loop_state is not None:
                # A pass killed mid-flight — stopped by the user, or out of
                # budget — never reached the controller, so nothing has checked
                # what it changed. This is the last chance, and for a pass that
                # ended normally it is a no-op.
                with contextlib.suppress(Exception):
                    if (note := loop.restore_document(self.loop_state)):
                        self.notice(f"{self.current.label}: {note}",
                                    level="warn", kind="loop")
                    if self.current.index > 1:
                        for undone in loop.restore_outside_lane(
                                self.loop_state, self.current.writes,
                                self.loop_state.store / "lane"):
                            self.notice(
                                f"{self.current.label}: restored outside this "
                                f"pass's lane: {undone}", level="warn", kind="loop")

                # Each pass wrote its own notes; the lab book takes one entry per
                # run, so they are put back together before it is composed. The
                # review log is written from the review and disposition files by
                # the app, for the same reason the lab book is: an account of
                # what was contested is worth having from something that was not
                # a participant.
                labels = {p.index: p.label for p in self.plan.passes}
                with contextlib.suppress(Exception):
                    loop.collect_notes(self.loop_state, labels)
                with contextlib.suppress(Exception):
                    if (log := loop.write_review_log(self.loop_state)):
                        self.notice(f"wrote {log.name}: what review found, and "
                                    f"what was done about it", kind="loop")
                loop.finish(self.loop_state, reason)

            if self.plan.extra.get("isolated_reading"):
                self._finalize_isolated_reading()
            elif self.plan.spec.mode != "oneshot":
                from .git import commit_session, describe_changes
                from .latex import compile_tex
                from .problems import read_state, validate_state

                # A tool-less harness has no filesystem, so it cannot fold its
                # own results into the problem's document however much it wants
                # to. In standard mode that left the document untouched — a
                # scaffold of empty section headings — while the mathematics sat
                # only in the session. So the app folds it in, exactly as it
                # writes the lab book on the session's behalf. This happens
                # before the compile below, so the problem's PDF picks it up.
                if self.plan.harness.tools.none and (text := self._answer_text()):
                    (self.plan.run_dir / "answer.md").write_text(
                        text + "\n", encoding="utf-8")
                    self.produced_output = True
                    # In standard mode the work belongs in the problem's own
                    # document, which is what the PDF tab shows. The session
                    # keeps a PDF of its own only when that proves impossible,
                    # so that a result is never lost — and so that a document
                    # under Sessions always means an independent one-shot.
                    if not self._append_answer_to_document(text):
                        await self._typeset_answer(text)

                # A final compile, in case the last edit landed after the hook ran.
                if (self.plan.problem.progress_dir / "math.tex").is_file():
                    result = await compile_tex(self.cfg, self.plan.problem.progress_dir)
                    self.emit(Event(type=ev.LATEX, run_id=self.run_id, payload={
                        "ok": result.ok, "errors": result.errors[:20],
                        "pdf": str(result.pdf) if result.pdf else None,
                    }))

                state_data = read_state(self.plan.problem.root)
                problems = validate_state(state_data)
                if problems:
                    self.notice("state.json did not validate at finalize: "
                                + "; ".join(problems), level="warn")
                status_after = state_data.get("status", self._status_before)
                # Provenance is measured, not self-reported: models routinely
                # invent a run id and a date here.
                from .problems import write_state

                state_data["updated"] = self.ended_at.isoformat() if self.ended_at else None
                state_data["updated_by_run"] = self.run_id
                write_state(self.plan.problem.root, state_data)

                files_changed = [
                    (path, change)
                    for path, change in describe_changes(
                        self.cfg, self.plan.problem, self._git_base)
                    if not path.endswith(NOTES_FILENAME)
                ]
                self._write_labbook(files_changed, status_after)

                if self.cfg.git.enabled and self.plan.spec.git_commit:
                    commit = commit_session(
                        self.cfg, self.plan.problem,
                        message=(f"{self.cfg.git.commit_prefix}({self.run_id}): "
                                 f"{self.plan.harness.id} on {self.plan.problem.slug}"),
                    )
            else:
                await self._finalize_oneshot()
        except Exception as exc:                                  # noqa: BLE001
            self.notice(f"finalize step failed: {type(exc).__name__}: {exc}", level="warn")

        # The reading's files, as the controller left them, are its folder's
        # again — as they always were at the end — and the controller keeps
        # nothing to work on (a late step finds no files rather than the
        # folder its AI's programs may still change).
        if self._custody is not None:
            try:
                appeared = self._give_files_back(final=True)
            except OSError as exc:
                appeared = []
                self.notice(f"the reading's files could not be put back in its working "
                            f"folder: {exc}", level="warn")
            if appeared:
                self.notice("A link appeared in the reading's working files after its last "
                            "step had ended. It was not followed; the files it was among are "
                            "kept in rejected-files/.", level="warn", kind="access_policy")
        self._files_given_back = self._custody_applies()

        # Solving sessions use a Stop hook on Claude. For other providers we
        # record any missing files here. Reading output is checked by the
        # controller after each pass.
        hookless = sorted({p.provider or self.plan.spec.provider for p in self.plan.passes}
                          - HOOKED)
        if (self.plan.spec.mode != "oneshot" and not self.plan.harness.tools.none
                and hookless):
            required = ("math.tex", "state.json", NOTES_FILENAME)
            if self.plan.harness.kind == "read":
                # A reading session is forbidden to touch the document and does
                # not own the state file. What it owes is its companion, and the
                # loop controller has already checked that.
                required = (NOTES_FILENAME,)
            missing = [name for name in required if name not in delivered]
            if missing:
                self.notice(
                    "the file contract was not met: missing or empty "
                    + ", ".join(missing)
                    + f" — {', '.join(hookless)} cannot be stopped mid-session "
                      "to fix this, so it is recorded rather than corrected",
                    level="warn", kind="contract_unmet")
                with contextlib.suppress(OSError):
                    (self.plan.run_dir / "contract_violations.json").write_text(
                        json.dumps({"missing": missing, "enforced": False}, indent=2),
                        encoding="utf-8")

        # A control condition that quietly stopped being one must say so: on
        # Codex a tool-free harness is asked, not forced, to use no tools.
        if self.plan.harness.tools.none and self.totals.tool_calls:
            self.notice(
                f"this run was configured as a tool-free control but used "
                f"{self.totals.tool_calls} tool call"
                f"{'' if self.totals.tool_calls == 1 else 's'} — "
                f"{self.plan.spec.provider} cannot enforce a tool-free session, so "
                f"treat this as a normal run, not as a baseline",
                level="warn", kind="control_broken")

        if (self.totals.cost_reported is not None
                and self.totals.cost_computed > 0
                and abs(self.totals.cost_reported - self.totals.cost_computed)
                / max(self.totals.cost_computed, 1e-9) > 0.10):
            self.notice(
                f"cost divergence: we computed ${self.totals.cost_computed:.4f}, "
                f"the CLI reported ${self.totals.cost_reported:.4f}. "
                f"Check pricing.toml against current rates.",
                level="warn", kind="cost_divergence",
            )

        stats = self.totals.as_dict() | {
            "wall_seconds": round(self.clock.wall, 2),
            "active_seconds": round(self.clock.active, 2),
            "frozen_seconds": round(self.clock.frozen, 2),
        }
        (self.plan.run_dir / "usage.json").write_text(
            json.dumps(stats, indent=2), encoding="utf-8")
        (self.plan.run_dir / "result.json").write_text(json.dumps({
            "run_id": self.run_id, "state": str(state), "ended_reason": reason,
            "produced_output": self.produced_output,
            "error": self.error, "status_before": self._status_before,
            "status_after": status_after, "git_commit": commit,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "stats": stats,
        }, indent=2), encoding="utf-8")

        # It ends on its own at the next tick, since the state is terminal; this
        # just stops it holding a reference for two more seconds.
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()

        self._persist_run_row(final=True, status_after=status_after, commit=commit)

        if (self.plan.spec.mode != "oneshot"
                and not self.plan.extra.get("isolated_reading")):
            from .problems import release_lock

            release_lock(self.plan.problem.root, self.run_id)

        self.emit(Event(type=ev.RUN_ENDED, run_id=self.run_id, payload={
            "state": str(state), "reason": reason, "stats": stats,
            "status_before": self._status_before, "status_after": status_after,
            "commit": commit, "error": self.error,
        }))
        self.hub.publish(ev.TOPIC_PROBLEMS, Event(
            type=ev.RUN_ENDED, run_id=self.run_id,
            payload={"problem": self.plan.problem.slug, "status": status_after},
        ))
        self.log.close()

        from .notify import notify

        notify(
            title=f"{self.plan.problem.meta.title} — {state}",
            message=f"{self.plan.harness.id} · {reason} · "
                    f"${self.totals.cost_computed:.2f} · {stats['wall_seconds'] / 60:.0f} min",
        )
        self.finished.set()

    def _finalize_isolated_reading(self) -> None:
        """Keep one reading run's products in its own permanent record.

        The workspace is a snapshot precisely so concurrent runs never touch
        the same files.  Copy the reader-facing products out beside the run
        transcript, where the web UI and later audits can find them after this
        process exits.  No problem state, lab book, or git history is changed.
        """
        # From the controller's copy of the reading's files (handover.py), which
        # nothing but the controller changes. (Read leniently: a stand-in
        # handle in a test has only the plan.)
        root = getattr(self, "_files", None) or self.plan.cwd
        source = root / "progress" / "reading"
        destination = self.plan.run_dir / "reading"
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
            guide = destination / "guide.md"
            self.produced_output = guide.is_file() and bool(
                guide.read_text(encoding="utf-8", errors="replace").strip())

        notes = root / "progress" / NOTES_FILENAME
        if notes.is_file():
            text = notes.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                (self.plan.run_dir / "notes.md").write_text(
                    text + "\n", encoding="utf-8")

        if text := self._answer_text():
            (self.plan.run_dir / "answer.md").write_text(
                text + "\n", encoding="utf-8")

    def _write_labbook(self, files_changed: list[tuple[str, str]], status_after: str) -> None:
        from .labbook import append_entry, compose_entry, consume_notes, has_entry

        labbook = self.plan.problem.progress_dir / "labbook.md"
        if has_entry(labbook, self.run_id):
            return
        notes = consume_notes(self.plan.problem.progress_dir)
        if notes:
            (self.plan.run_dir / "notes.md").write_text(notes, encoding="utf-8")

        task = ""
        spec = self.plan.spec
        if spec.task_kind == "text":
            task = spec.task_text
        elif spec.task_kind == "files":
            task = "Pointed at: " + ", ".join(spec.task_files)
        else:
            task = self.plan.harness.default_task or "(harness default)"

        # A run whose passes used different models says so, pass by pass.
        used = [(p.role, p.model or self.plan.model) for p in self.plan.passes]
        model = self.plan.model
        if len({m for _, m in used}) > 1:
            model = ", ".join(f"{m} ({role})" for role, m in used)

        entry = compose_entry(
            run_id=self.run_id, session_number=self.session_number,
            started_at=self.started_at, ended_at=self.ended_at,
            harness=self.plan.harness.id, harness_version=self.plan.harness.version,
            runner=self.plan.harness.runner, provider=spec.provider,
            model=model, effort=self.plan.effort,
            account=self.plan.account.id if self.plan.account else "—",
            mode=spec.mode,
            stats=self.totals.as_dict() | {
                "wall_seconds": self.clock.wall, "active_seconds": self.clock.active},
            task=task, notes=notes, files_changed=files_changed,
            status_before=self._status_before, status_after=status_after,
            ended_reason=self.ended_reason,
        )
        append_entry(labbook, entry)

    async def _finalize_oneshot(self) -> None:
        """Blind runs write to an isolated workspace, so their results have to be
        surfaced deliberately: the answer text, and a compiled PDF if the session
        had tools and produced a document."""
        text = self._answer_text()
        answer = self.plan.run_dir / "answer.md"
        if text:
            answer.write_text(text + "\n", encoding="utf-8")
            self.produced_output = True
        else:
            # Say so explicitly. A missing file looks like a bug in the app; an
            # empty answer with a reason is a finding about the session.
            answer.write_text(
                f"# No answer produced\n\n"
                f"This session ended without emitting any final text.\n\n"
                f"- ended: **{self.ended_reason}**\n"
                f"- output tokens: {self.totals.tok_out:,} "
                f"(spent without producing a reply — usually all reasoning)\n"
                f"- duration: {self.clock.wall / 60:.1f} min\n"
                f"- cost: ${self.totals.cost_computed:.2f}\n\n"
                f"If the model was reasoning at high effort on a hard problem, it may "
                f"have hit its output ceiling before writing a conclusion. Lower the "
                f"effort, raise the budget, or use a harness with tools so partial "
                f"work is written to disk as it goes.\n",
                encoding="utf-8")

        progress = self._files / "progress"
        document = False
        if (progress / "math.tex").is_file() and not self.plan.harness.tools.none:
            from .latex import compile_tex

            result = await compile_tex(self.cfg, progress)
            document = result.ok
            self.emit(Event(type=ev.LATEX, run_id=self.run_id, payload={
                "ok": result.ok, "errors": result.errors[:20],
                "pdf": str(result.pdf) if result.pdf else None,
            }))
            for name in ("math.pdf", "math.tex", "state.json", "_session_notes.md"):
                src = progress / name
                if src.is_file():
                    with contextlib.suppress(OSError):
                        shutil.copy2(src, self.plan.run_dir / name)

        if text and not document:
            await self._typeset_answer(text)

    def _answer_text(self) -> str:
        """The lead agent's final prose, in order."""
        texts = [
            str(event.payload.get("text", ""))
            for event in EventLog.read(self.plan.run_dir / "events.jsonl")
            if event.type == ev.ASSISTANT_TEXT and event.agent == "lead"
        ]
        return "\n\n".join(texts).strip()

    async def _typeset_answer(self, text: str) -> None:
        """Compile the session's answer to `answer.pdf`.

        A tool-less harness has no filesystem and so can never write `math.tex`,
        and a session that ran out of room may not have written one either — but
        in both cases there is mathematics on the screen, and it should be
        readable as a PDF like any other result. The conversion runs here, in
        the app, after the session has ended, so the model gains no capability
        and the `chat` control condition stays intact.
        """
        from .latex import compile_tex
        from .mdtex import answer_document, unmapped_characters
        from .problems import PREAMBLE_TEX

        if (lost := unmapped_characters(text)):
            self.notice(
                "the PDF replaces characters TeX cannot set: "
                + " ".join(lost[:12]) + " — answer.md has the original",
                level="warn")

        build = self.plan.run_dir / "tex"
        build.mkdir(exist_ok=True)
        supplied = self.plan.problem.progress_dir / "preamble.tex"
        (build / "preamble.tex").write_text(
            supplied.read_text(encoding="utf-8") if supplied.is_file() else PREAMBLE_TEX,
            encoding="utf-8")
        (build / "answer.tex").write_text(
            answer_document(
                text, title=self.plan.problem.meta.title,
                provenance=f"{self.plan.harness.id} · {self.plan.model} · "
                           f"{self.started_at:%d %B %Y}",
            ), encoding="utf-8")

        result = await compile_tex(self.cfg, build, "answer.tex")
        self.emit(Event(type=ev.LATEX, run_id=self.run_id, payload={
            "ok": result.ok, "errors": result.errors[:20], "document": "answer",
            "pdf": str(result.pdf) if result.pdf else None,
        }))
        if result.ok and result.pdf:
            with contextlib.suppress(OSError):
                shutil.copy2(result.pdf, self.plan.run_dir / "answer.pdf")
        elif result.errors or result.skipped:
            self.notice(
                "could not typeset the answer as a PDF: "
                + (result.skipped or "; ".join(result.errors[:3]))
                + " — the text is in answer.md",
                level="warn")

    def _append_answer_to_document(self, text: str) -> bool:
        """Fold a tool-less session's answer into `progress/math.tex`.

        Only ever appends, immediately before `\\end{document}`, and marks what
        it inserted with the session it came from. A curated document is the
        mathematical record of the problem, so nothing here rewrites or removes
        what is already in it: the worst this can do is add a section a reader
        can see the provenance of and delete.
        """
        from .mdtex import markdown_to_latex, tex_text

        doc = self.plan.problem.progress_dir / "math.tex"
        if not doc.is_file():
            return False
        try:
            current = doc.read_text(encoding="utf-8")
        except OSError as exc:
            self.notice(f"could not read math.tex to add this session's work: {exc}",
                        level="warn")
            return False

        marker = f"% atelier:session {self.run_id}"
        if marker in current:
            return True                  # already folded in; never do it twice

        # Local time, as the lab book and the whole interface use. started_at is
        # UTC, so without the conversion two sessions an hour apart can look
        # hours apart, or out of order, in the document itself.
        heading = (f"{self.plan.harness.id} · {self.plan.model} · "
                   f"{self.started_at.astimezone():%d %B %Y %H:%M}")
        block = (
            f"\n{marker}\n"
            f"\\section{{Session {self.session_number}: {tex_text(heading)}}}\n"
            f"\\begin{{quote}}\\small This section was added by atelier from a "
            f"session that had no file access, so the mathematics below is as the "
            f"model wrote it and has not been folded into the document by hand.\n"
            f"\\end{{quote}}\n\n"
            f"{markdown_to_latex(text)}\n"
        )

        end = current.rfind("\\end{document}")
        updated = (current[:end] + block + "\n" + current[end:]) if end != -1 \
            else current.rstrip() + "\n" + block
        try:
            doc.write_text(updated, encoding="utf-8")
        except OSError as exc:
            self.notice(f"could not add this session's work to math.tex: {exc}",
                        level="warn")
            return False
        self.notice(
            f"added this session's mathematics to progress/math.tex as "
            f"\"Session {self.session_number}\" — {self.plan.harness.id} has no file "
            f"access, so atelier folded it in",
            kind="answer_folded")
        return True

    def _persist_run_row(self, *, final: bool = False, status_after: str | None = None,
                         commit: str | None = None) -> None:
        row: dict[str, Any] = {
            "id": self.run_id,
            "problem": self.plan.problem.slug,
            "harness": self.plan.harness.id,
            "harness_version": self.plan.harness.version,
            "provider": self.plan.spec.provider,
            "account": self.plan.account.id if self.plan.account else None,
            "model": self.plan.model,
            "effort": self.plan.effort,
            "mode": self.plan.spec.mode,
            "provider_session_id": self.plan.spec.provider_session_id,
            "parent_run_id": self.plan.spec.parent_run_id,
            "bench_id": self.plan.spec.bench_id,
            "label": self.plan.spec.label,
            "started_at": self.started_at.isoformat(),
            "status": str(self.state),
            "status_before": self._status_before,
        }
        if final:
            row |= {
                "ended_at": self.ended_at.isoformat() if self.ended_at else None,
                "ended_reason": self.ended_reason,
                "wall_seconds": round(self.clock.wall, 2),
                "active_seconds": round(self.clock.active, 2),
                "turns": self.totals.turns,
                "tool_calls": self.totals.tool_calls,
                "tok_in": self.totals.tok_in,
                "tok_out": self.totals.tok_out,
                "tok_cache_read": self.totals.cache_read,
                "tok_cache_write": self.totals.cache_write,
                "cost_computed": round(self.totals.cost_computed, 6),
                "cost_reported": self.totals.cost_reported,
                "status_after": status_after,
                "git_commit": commit,
                "error": self.error,
            }
        self.db.upsert_run(row)


# --------------------------------------------------------------------------
# manager
# --------------------------------------------------------------------------


class RunManager:
    """Owns every live run in this process."""

    def __init__(self, cfg: Config, hub: EventHub, db: Database) -> None:
        self.cfg = cfg
        self.hub = hub
        self.db = db
        self.runs: dict[str, RunHandle] = {}

    def active(self) -> list[RunHandle]:
        return [r for r in self.runs.values() if not r.state.terminal]

    def get(self, run_id: str) -> RunHandle | None:
        return self.runs.get(run_id)

    def for_problem(self, slug: str) -> RunHandle | None:
        for r in self.active():
            if r.plan.problem.slug == slug:
                return r
        return None

    def concurrency_ok(self, account_id: str | None) -> tuple[bool, str]:
        active = self.active()
        if len(active) >= self.cfg.limits.max_concurrent_runs:
            return False, (f"already running {len(active)} sessions "
                           f"(limit {self.cfg.limits.max_concurrent_runs})")
        if account_id and (acct := self.cfg.account(account_id)):
            # A run counts against every account any of its passes uses.
            n = sum(1 for r in active if account_id in _accounts_of(r.plan))
            if n >= acct.max_concurrent:
                return False, (f"account {account_id} already has {n} sessions "
                               f"running (limit {acct.max_concurrent})")
        return True, ""

    async def launch(self, plan: RunPlan, session_number: int = 1) -> RunHandle:
        handle = RunHandle(self.cfg, plan, self.hub, self.db,
                           session_number=session_number)
        self.runs[handle.run_id] = handle
        await handle.start()
        return handle

    async def shutdown(self) -> None:
        handles = self.active()
        for handle in handles:
            with contextlib.suppress(Exception):
                await handle.stop(reason="server shutting down")
        for handle in handles:
            with contextlib.suppress(Exception, TimeoutError):
                await asyncio.wait_for(handle.finished.wait(), timeout=60)

    def reap_orphans(self) -> int:
        """Mark runs the database thinks are live but that this process does not own."""
        rows = self.db.query(
            "SELECT id, problem FROM runs WHERE status NOT IN "
            "('done','incomplete','failed','cancelled')"
        )
        n = 0
        for row in rows:
            if row["id"] in self.runs:
                continue
            self.db.execute(
                "UPDATE runs SET status = 'failed', ended_reason = ? WHERE id = ?",
                ("interrupted by server restart", row["id"]),
            )
            problem_root = self.cfg.problems_dir / row["problem"]
            if problem_root.is_dir():
                from .problems import read_lock, release_lock

                lock = read_lock(problem_root)
                if lock and not lock.alive():
                    release_lock(problem_root)
            n += 1
        return n


def _accounts_of(plan: RunPlan) -> set[str]:
    """Every account a run's passes use, planned or running. (Read leniently:
    a plan made before passes could differ names only its own account.)"""
    planned = (getattr(plan, "extra", None) or {}).get("agents") or {}
    out = {a.get("account") for a in planned.values() if isinstance(a, dict)}
    out |= {getattr(p, "account", None) for p in getattr(plan, "passes", None) or []}
    if getattr(plan, "account", None) is not None:
        out.add(plan.account.id)
    return {a for a in out if a}


def which_or_none(name: str) -> str | None:
    return shutil.which(name)
