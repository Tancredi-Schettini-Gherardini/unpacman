"""Readings: each time the pipeline has read a paper, and where its files are.

Since Antoine's change of 2026-09-30 a reading runs in a snapshot of the paper
under its own run directory and is archived to `runs/<id>/reading/` when it
ends. So a paper can hold an Expert and a Non-expert companion at once, and
reading it again leaves the last companion where it was. One that stopped
before it could be archived is archived when it is next looked at: once a
reading has stopped, its snapshot is never used as it stands (`_where`).

Readings made before that wrote into the paper's own `progress/reading/`, one
after another, and are still found there. Only one of them can be the author of
what that folder now holds — the newest whose first pass finished — and only
that one is credited with it: a later reading that stopped early did not write
the companion, and must not be said to have.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atelier.config import Config

from .library import ALL_LEVELS, LEVELS, Paper

FAMILY = "deslopify"
# The harness variant that reads at each level: a paper's two, the two audiences
# for a collection, and a search of the literature.
VARIANTS = {"expert": "expert", "general": "nonexpert",
            "collection-expert": "multi-expert",
            "collection-nonexpert": "multi-nonexpert", "search": "search"}
LEVEL_OF_VARIANT = {f"{FAMILY}-{v}": level for level, v in VARIANTS.items()}
# Keep the short-lived placeholder collection runs visible after the migration.
LEVEL_OF_VARIANT[f"{FAMILY}-multi"] = "collection-expert"
FAMILY_IDS = (FAMILY, *LEVEL_OF_VARIANT)


def harness_id(level: str) -> str:
    return f"{FAMILY}-{VARIANTS[level]}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass(slots=True)
class Reading:
    run_id: str | None          # None: files on disk with no run behind them
    level: str
    harness: str
    status: str                 # the run's own status; "kept" when there is no run
    started: str
    dir: Path                   # where this reading's files are
    isolated: bool
    live: bool = False
    drafted: bool = False       # its first pass finished
    row: dict[str, Any] = field(default_factory=dict)
    number: int = 0             # 1 for the paper's first reading, and so on
    moment: datetime = field(default_factory=lambda: NEVER)
    delivery: dict[str, Any] = field(default_factory=dict)

    @property
    def guide(self) -> Path:
        return self.dir / "guide.md"

    @property
    def when(self) -> str:
        """When it started, spelled one way whatever the database holds."""
        return self.moment.isoformat() if self.moment != NEVER else self.started


NEVER = datetime.min.replace(tzinfo=UTC)


def _moment(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        try:
            # A run id, or how `atelier reindex` once spelled the start of a
            # run it rebuilt without a result: `2026-09-16T22-15-31Z`.
            moment = datetime.strptime(text[:20], "%Y-%m-%dT%H-%M-%S%z")
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def started_moment(started: str, run_id: str | None = None) -> datetime:
    """When a reading started, as something that sorts.

    The database keeps it as text, which sorts correctly only while every row
    spells it the same way — and one did not: a run rebuilt by `atelier
    reindex` kept its id's spelling, which sorts below a run started earlier in
    the same hour. A run id begins with the moment it started, so it is the
    fallback; what has neither sorts as the oldest.
    """
    for text in (started, run_id or ""):
        if (moment := _moment(text)) is not None:
            return moment
    return NEVER


def level_of(harness: str, notes: str) -> str:
    """Which level a run read at: from its harness, or from its notes for the
    runs made before the levels were separate harnesses."""
    if harness in LEVEL_OF_VARIANT:
        return LEVEL_OF_VARIANT[harness]
    level = notes.removeprefix("reader level: ").strip()
    return level if level in ALL_LEVELS else "expert"


def _where(cfg: Config, paper: Paper, run_id: str, *, live: bool = False,
           settled: bool = False) -> tuple[Path, bool]:
    """A run's reading folder, and whether it is the run's own.

    A reading in a snapshot of its own is archived to `runs/<id>/reading/` as
    it ends. While it runs, its files are in the snapshot — the folder its AI
    works in. Once it has stopped, that folder is never used as it stands: a
    program the AI left running may still change it, and a link put there
    would have unpacman's own code read or write through it (security check
    of 2026-11-02). A stopped reading that was never archived — unpacman was
    stopped in the middle of it — is archived on first sight (`settled`),
    from a copy that follows nothing; until then, or if it cannot be, it has
    no files to show.
    """
    run_dir = cfg.runs_dir / run_id
    archived = run_dir / "reading"
    if archived.is_dir():
        return archived, True
    resolved = _read_json(run_dir / "config.json").get("resolved") or {}
    cwd, root = resolved.get("cwd"), resolved.get("problem_root")
    if cwd and root and Path(cwd) != Path(root):
        if live:
            # Still running: the snapshot it works in.
            return Path(cwd) / "progress" / "reading", True
        if settled:
            _archive(run_dir, Path(cwd))
        return archived, True
    return paper.reading, False


# A run's status once it has stopped for good.
SETTLED = ("done", "incomplete", "failed", "cancelled")

# Stopped readings this process could not archive (no reading folder in the
# snapshot, a link in its place, or an error), not tried again before it next
# starts: a listing must not redo a failing copy every few seconds.
_UNARCHIVABLE: set[Path] = set()


def _settled(manager: Any, row: dict[str, Any]) -> bool:
    """Whether a run has stopped for good and is not being finished here: its
    status says it has stopped, and this process holds no run of that id —
    one it holds archives its own reading, perhaps at this moment."""
    if row.get("status") not in SETTLED:
        return False
    get = getattr(manager, "get", None)
    return get is None or get(row["id"]) is None


def _archive(run_dir: Path, workspace: Path) -> None:
    """Archive a stopped run's reading from its snapshot, as the run itself
    would have: handover.archive copies it without following anything — not
    even a link in place of progress/ or reading/ on the way to it — and sets
    aside, in the run's rejected-files/, anything that is not a plain file or
    folder."""
    if run_dir in _UNARCHIVABLE:
        return
    from atelier import handover
    try:
        kept = handover.archive(workspace, "progress/reading", run_dir / "reading",
                                run_dir / handover.QUARANTINE)
    except Exception:                                     # noqa: BLE001 - never the listing's
        kept = None
    if kept is None and not (run_dir / "reading").is_dir():
        _UNARCHIVABLE.add(run_dir)


ROW = ("SELECT id, harness, status, started_at, ended_at, wall_seconds, "
       "cost_computed, ended_reason, error, provider, account, model, effort "
       "FROM runs WHERE problem = ? AND harness IN ({}) ORDER BY started_at DESC")


def all_readings(cfg: Config, db: Any, manager: Any, paper: Paper) -> list[Reading]:
    """Every reading of this paper, newest first, each numbered from the first.

    The order is decided here, by when each started (the run id breaking a
    tie), and not left to how the database happens to spell a time: every
    list the page shows, and every "newest" this module picks, follows it.
    """
    rows = db.query(ROW.format(", ".join("?" * len(FAMILY_IDS))),
                    (paper.slug, *FAMILY_IDS))
    live = {h.run_id for h in manager.active() if h.plan.problem.slug == paper.slug}
    out: list[Reading] = []
    for raw in rows:
        row = dict(raw)
        run_dir = cfg.runs_dir / row["id"]
        spec = _read_json(run_dir / "config.json").get("spec") or {}
        where, own = _where(cfg, paper, row["id"], live=row["id"] in live,
                            settled=_settled(manager, row))
        passes = _read_json(run_dir / "loop.json").get("passes") or []
        reading = Reading(
            run_id=row["id"], level=level_of(row["harness"], str(spec.get("notes") or "")),
            harness=row["harness"], status=row["status"], started=row["started_at"],
            dir=where, isolated=own, live=row["id"] in live,
            drafted=any(p.get("role") == "draft" for p in passes), row=row,
            moment=started_moment(str(row["started_at"] or ""), row["id"]))
        if not reading.live:
            reading.delivery = _read_json(run_dir / "delivery.json")
            if not reading.delivery and reading.status in ("incomplete", "failed", "cancelled"):
                from atelier.delivery import delivery_report
                contract = "companion" if reading.level in LEVELS else "guide"
                reading.delivery = delivery_report(where, contract, completed=False)
                # Older interrupted runs have no delivery.json. Their saved
                # guide may be readable, but review never finished; keep the
                # interrupted run's original incomplete state in the library.
                if reading.delivery.get("status") == "review_unfinished":
                    reading.delivery.update(status="incomplete", label="Incomplete draft")
            if not reading.delivery and reading.status == "done":
                reading.delivery = {"status": "completed", "label": "Completed", "can_continue": False}
            if reading.delivery:
                reading.delivery["can_continue"] = bool(
                    reading.delivery.get("can_continue") and own
                    and (run_dir / "workspace").is_dir() and (run_dir / "result.json").is_file())
        out.append(reading)
    if not out and (paper.reading / "guide.md").is_file():
        # A companion on disk that no run explains — a test, or a copy. It is
        # shown for what it is, at the level the paper was last read at.
        out.append(Reading(run_id=None, level=paper.level or "expert", harness=FAMILY,
                           status="kept", started=paper.created, dir=paper.reading,
                           isolated=False, drafted=True,
                           moment=started_moment(paper.created)))
    out.sort(key=lambda r: (r.moment, r.run_id or ""), reverse=True)
    _number(paper, out)
    return out


# Where a paper keeps the number each of its runs was given.
NUMBERS = Path(".atelier") / "run-numbers.json"


def _number(paper: Paper, found: list[Reading]) -> None:
    """Give each reading the number it keeps for life: 1 for the paper's first
    run, and so on, in the order they started.

    Kept beside the paper since runs can be deleted (removal.py): counted again
    from what is left, the run that was "Run 3" would become "Run 2" when an
    earlier one went — and a reader who deleted run 1 would find another run 1
    in its place. A number is never given twice, even once its run is gone.
    """
    path = paper.root / NUMBERS
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        known = {}
    changed = False
    top = max(known.values(), default=0)
    for reading in reversed(found):                 # oldest first
        key = reading.run_id or "kept"
        if key not in known:
            top += 1
            known[key] = top
            changed = True
        reading.number = known[key]
    if changed:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(known, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass                    # numbered for this answer; kept next time


def with_companion(readings: list[Reading]) -> list[Reading]:
    """The readings that may be credited with a companion, newest first.

    An isolated reading owns its folder outright. Of the readings that shared
    the paper's folder, only the newest that finished its first pass wrote
    what is there now. A reading still in progress has a draft, not a
    companion: until it ends, the companion it will replace is the one to read.
    """
    shared_author = next((r for r in readings
                          if not r.isolated and r.drafted and r.guide.is_file()), None)
    return [r for r in readings
            if not r.live and r.guide.is_file() and (r.isolated or r is shared_author)]


@dataclass(slots=True)
class Level:
    level: str
    latest: Reading | None = None        # the newest run at this level
    companion: Reading | None = None     # the newest one with a companion

    @property
    def state(self) -> str:
        """reading | incomplete | ready | stopped | unread — as the library says it."""
        if self.latest and self.latest.live:
            return "reading"
        if self.latest and self.latest.delivery.get("status") in ("incomplete", "review_unfinished"):
            return self.latest.delivery["status"]
        if self.latest and self.latest.status == "incomplete":
            return "incomplete"
        # A finished companion stands even if a later reading at this level
        # stopped: the page says so, but the paper is still ready to read.
        if self.companion and self.companion.status in ("done", "kept"):
            return "ready"
        return "stopped" if self.latest else "unread"


def by_level(readings: list[Reading], levels: tuple[str, ...] = LEVELS) -> dict[str, Level]:
    """Each of the levels a paper is read at (`Paper.levels`), with its newest
    reading and its newest companion. A reading at a level the paper is not
    read at — a lab run on the folder, say — is in the list of runs, not here."""
    out = {level: Level(level) for level in levels}
    for r in readings:
        if (at := out.get(r.level)) is not None and at.latest is None:
            at.latest = r
    for r in with_companion(readings):
        if (at := out.get(r.level)) is not None and at.companion is None:
            at.companion = r
    return out


def default_level(readings: list[Reading], paper: Paper) -> str | None:
    """The level a paper opens at.

    Its newest companion's, if it has one: a paper opened from the library is
    opened to be read, and a reading at the other level that stopped early is
    no reason to show nothing. Then its newest reading's, then the last level
    chosen. Starting a reading goes to its own level explicitly.
    """
    credited = with_companion(readings)
    if credited:
        return credited[0].level
    if readings:
        return readings[0].level
    return paper.level or None


def find(readings: list[Reading], run_id: str | None) -> Reading | None:
    if not run_id:
        return None
    return next((r for r in readings if r.run_id == run_id), None)
