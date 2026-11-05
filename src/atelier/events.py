"""Normalised event model, JSONL persistence, and the SSE fan-out hub.

Every provider event is normalised into an `Event`. Anything we do not
recognise is still recorded verbatim as `provider.raw` — the log is never
lossy, because a run record you cannot fully reconstruct is worthless for
comparing harnesses months later.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# event types
# --------------------------------------------------------------------------

# Lifecycle
RUN_STARTED = "run.started"
RUN_ENDED = "run.ended"
RUN_STATE = "run.state"            # queued/running/frozen/wrapping_up/stopping
AGENT_STARTED = "agent.started"    # a sub-invocation (script harness or subagent)
AGENT_ENDED = "agent.ended"
PHASE = "phase"                    # script-harness phase marker

# Content
ASSISTANT_TEXT = "assistant.text"
ASSISTANT_THINKING = "assistant.thinking"
ASSISTANT_DELTA = "assistant.delta"   # partial-message streaming
TOOL_USE = "tool.use"
TOOL_RESULT = "tool.result"

# Bookkeeping
USAGE = "usage"
STEER_SENT = "steer.sent"
STEER_DELIVERED = "steer.delivered"
BUDGET = "budget"                  # threshold crossings
RATE_LIMIT = "rate_limit"          # provider-reported window state
PROGRESS = "progress"              # liveness ping; never persisted

# Event types that exist only to keep the UI alive. They are published to the
# hub but kept out of events.jsonl: one long reasoning turn emitted 928 of these
# in a 949-event run, which buries the actual transcript.
EPHEMERAL = frozenset({"assistant.delta", PROGRESS})
FILE_CHANGED = "file.changed"
LATEX = "latex"                    # compile ok/failed
NOTICE = "notice"                  # human-readable app message
ERROR = "error"
PROVIDER_RAW = "provider.raw"      # unrecognised provider payload, kept verbatim


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class Event:
    type: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    agent: str = "lead"
    ts: str = field(default_factory=now_iso)
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        return cls(
            type=d.get("type", PROVIDER_RAW),
            run_id=d.get("run_id", ""),
            payload=d.get("payload", {}),
            agent=d.get("agent", "lead"),
            ts=d.get("ts", now_iso()),
            seq=int(d.get("seq", 0)),
        )


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


class EventLog:
    """Append-only JSONL log for one run. Sequence numbers start at 1."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._closed = False
        self._fh = self.path.open("a", encoding="utf-8")

    def append(self, event: Event) -> Event:
        self._seq += 1
        event.seq = self._seq
        if self._closed:
            # The run has been finalised. Keep numbering consistent for any
            # in-flight publisher, but never raise on a closed handle.
            return event
        try:
            self._fh.write(event.to_json() + "\n")
            self._fh.flush()
        except (ValueError, OSError):
            self._closed = True
        return event

    def close(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            self._fh.close()

    @staticmethod
    def read(path: Path, *, after_seq: int = 0, limit: int | None = None) -> list[Event]:
        if not path.is_file():
            return []
        out: list[Event] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(d.get("seq", 0)) <= after_seq:
                    continue
                out.append(Event.from_dict(d))
                if limit is not None and len(out) >= limit:
                    break
        return out


# --------------------------------------------------------------------------
# fan-out hub
# --------------------------------------------------------------------------


class Subscription:
    """One client's view: a queue plus the set of topics it wants."""

    __slots__ = ("queue", "topics", "id")

    def __init__(self, sub_id: str, topics: set[str]) -> None:
        self.id = sub_id
        self.topics = topics
        self.queue: asyncio.Queue[tuple[str, Event]] = asyncio.Queue(maxsize=2048)

    def wants(self, topic: str) -> bool:
        return topic in self.topics or "*" in self.topics


class EventHub:
    """In-process pub/sub with a small per-topic replay buffer.

    One multiplexed SSE stream per browser tab subscribes here; HTTP/1.1's
    ~6-connections-per-origin cap makes per-panel streams unworkable.
    """

    REPLAY = 400

    def __init__(self) -> None:
        self._subs: dict[str, Subscription] = {}
        self._buffers: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=self.REPLAY))
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def publish(self, topic: str, event: Event) -> None:
        self._counters[topic] += 1
        self._buffers[topic].append(event)
        for sub in list(self._subs.values()):
            if not sub.wants(topic):
                continue
            try:
                sub.queue.put_nowait((topic, event))
            except asyncio.QueueFull:
                # A stalled client must never block the run. Drop its oldest
                # event and record the gap so the UI can resync from disk.
                with contextlib.suppress(asyncio.QueueEmpty):
                    sub.queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    sub.queue.put_nowait(
                        (topic, Event(type=NOTICE, run_id=event.run_id,
                                      payload={"message": "stream lagged; resync", "resync": True}))
                    )

    async def subscribe(self, sub_id: str, topics: set[str]) -> Subscription:
        async with self._lock:
            sub = Subscription(sub_id, topics)
            self._subs[sub_id] = sub
            return sub

    async def unsubscribe(self, sub_id: str) -> None:
        async with self._lock:
            self._subs.pop(sub_id, None)

    async def set_topics(self, sub_id: str, topics: set[str]) -> None:
        async with self._lock:
            if sub := self._subs.get(sub_id):
                sub.topics = topics

    def replay(self, topic: str, after_seq: int = 0) -> list[Event]:
        return [e for e in self._buffers.get(topic, ()) if e.seq > after_seq]

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


_hub: EventHub | None = None


def get_hub() -> EventHub:
    global _hub
    if _hub is None:
        _hub = EventHub()
    return _hub


# --------------------------------------------------------------------------
# small helpers used across modules
# --------------------------------------------------------------------------


def topic_run(run_id: str) -> str:
    return f"run:{run_id}"


def topic_run_deltas(run_id: str) -> str:
    return f"run:{run_id}:deltas"


TOPIC_RUNS = "runs"
TOPIC_PROBLEMS = "problems"
TOPIC_ACCOUNTS = "accounts"


class Stopwatch:
    """Wall-clock and active-time tracking that survives freezes."""

    __slots__ = ("_start", "_frozen_at", "_frozen_total", "_active_total", "_active_since")

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._frozen_at: float | None = None
        self._frozen_total = 0.0
        self._active_total = 0.0
        self._active_since: float | None = None

    def freeze(self) -> None:
        if self._frozen_at is None:
            self._frozen_at = time.monotonic()

    def thaw(self) -> None:
        if self._frozen_at is not None:
            self._frozen_total += time.monotonic() - self._frozen_at
            self._frozen_at = None

    def mark_active(self) -> None:
        """Called when the model starts producing; measures model-active time."""
        if self._active_since is None:
            self._active_since = time.monotonic()

    def mark_idle(self) -> None:
        if self._active_since is not None:
            self._active_total += time.monotonic() - self._active_since
            self._active_since = None

    @property
    def wall(self) -> float:
        return time.monotonic() - self._start

    @property
    def frozen(self) -> float:
        extra = (time.monotonic() - self._frozen_at) if self._frozen_at else 0.0
        return self._frozen_total + extra

    @property
    def active(self) -> float:
        extra = (time.monotonic() - self._active_since) if self._active_since else 0.0
        return self._active_total + extra
