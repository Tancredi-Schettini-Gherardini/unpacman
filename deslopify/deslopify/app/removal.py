"""Deleting what has been read: one run, a paper's earlier runs, a paper.

Every reading keeps a record of its own — a snapshot of the paper it read
(`runs/<id>/workspace/`), its transcript, its companion — so a paper read a few
times, and a library used for a few months, grow without end. These are the
ways back, each asked for on the page and confirmed there:

- **a run**, from a paper's list of runs: its folder, its rows in the
  database, and what unpacman kept for it beside the paper (a companion PDF
  rebuilt with its contents, its key terms);
- **the earlier runs** of a paper, or of every paper: all but the companion
  each level opens with, and anything still running;
- **a paper**, with every run that read it.

A run still reading is never deleted — it is stopped first, by the reader.
Explanations are kept: they are answers about the paper, asked for and paid
for, and still true of it. Nothing here touches anything outside the papers
folder and the runs folder, and a run is found only through the paper it read.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atelier.config import Config

from . import readings
from .library import Paper

# A run id as Atelier makes them (`2026-10-08T06-56-43Z-8b59`), and the older
# spellings `reindex` and the tests have used — never a path.
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
KEPT = "kept"


class RemovalError(RuntimeError):
    """Something could not be deleted; said so a reader can act on it."""


def size_of(path: Path) -> int:
    """Bytes under `path`, links not followed and not counted."""
    if not path.exists():
        return 0
    if path.is_file() and not path.is_symlink():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        with contextlib.suppress(OSError):
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
    return total


def run_folder(cfg: Config, run_id: str) -> Path:
    """The folder of a run, refused unless it is one directly in the runs folder."""
    if not _RUN_ID.fullmatch(run_id or ""):
        raise RemovalError("That is not a run of this paper.")
    root = cfg.runs_dir.resolve()
    folder = (cfg.runs_dir / run_id).resolve()
    if folder.parent != root:
        raise RemovalError("That is not a run of this paper.")
    return folder


def _key(reading: readings.Reading) -> str:
    return reading.run_id or KEPT


def belongs(cfg: Config, paper: Paper, run_id: str, harness: str = "") -> bool:
    """Whether a run recorded under this paper's name read this paper.

    The database knows a run's folder only by its name, which a problem of the
    lab can share with a paper of the reader. The run's own record says which
    folder it read: this paper's — it belongs; another that still exists —
    it is someone else's, and is never touched; one that is gone (a test copy, a
    library moved) or none — it belongs if it is a reading of unpacman's.
    """
    try:
        record = json.loads((run_folder(cfg, run_id) / "config.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError, RemovalError):
        record = {}
    resolved = record.get("resolved") if isinstance(record, dict) else None
    root = (resolved or {}).get("problem_root") if isinstance(resolved, dict) else None
    if root:
        named = Path(str(root))
        with contextlib.suppress(OSError):
            if named.resolve() == paper.root.resolve():
                return True
        if named.exists():
            return False
    return harness in readings.FAMILY_IDS


@dataclass(slots=True)
class Plan:
    """What a deletion would remove, measured before anything is removed."""
    runs: list[readings.Reading] = field(default_factory=list)
    bytes: int = 0

    def describe(self) -> dict[str, Any]:
        return {"runs": [_key(r) for r in self.runs], "count": len(self.runs),
                "bytes": self.bytes}


def _own_bytes(cfg: Config, paper: Paper, reading: readings.Reading,
               shared_author: bool) -> int:
    total = 0
    if reading.run_id:
        with contextlib.suppress(RemovalError):
            total += size_of(run_folder(cfg, reading.run_id))
    if shared_author or reading.run_id is None:
        total += size_of(paper.reading)
    for kept in _kept_pdfs(paper, reading):
        with contextlib.suppress(OSError):
            total += kept.stat().st_size
    return total


def _kept_pdfs(paper: Paper, reading: readings.Reading) -> list[Path]:
    folder = paper.root / ".atelier" / "companions"
    if not folder.is_dir():
        return []
    prefix = f"{_key(reading)}-"
    return [p for p in folder.glob("*.pdf") if p.name.startswith(prefix)]


def _shared_author(found: list[readings.Reading]) -> readings.Reading | None:
    """The one reading credited with the paper's own `progress/reading/`, if any
    (readings made before they were isolated wrote there, one after another)."""
    return next((r for r in readings.with_companion(found)
                 if not r.isolated and r.run_id is not None), None)


def earlier(cfg: Config, paper: Paper, found: list[readings.Reading]) -> list[readings.Reading]:
    """Every reading of this paper but the companion each level opens with,
    and those running (and any recorded under its name that read another)."""
    keep = {_key(v.companion) for v in readings.by_level(found, paper.levels).values()
            if v.companion is not None}
    return [r for r in found if not r.live and _key(r) not in keep
            and (r.run_id is None or belongs(cfg, paper, r.run_id, r.harness))]


def footprint(cfg: Config, paper: Paper, found: list[readings.Reading]) -> int:
    """All a paper takes: its folder and the folder of every run that read it,
    each file counted once."""
    total = size_of(paper.root)
    for reading in found:
        if reading.run_id and belongs(cfg, paper, reading.run_id, reading.harness):
            with contextlib.suppress(RemovalError):
                total += size_of(run_folder(cfg, reading.run_id))
    return total


def plan(cfg: Config, paper: Paper, found: list[readings.Reading],
         chosen: list[readings.Reading]) -> Plan:
    author = _shared_author(found)
    out = Plan(runs=list(chosen))
    out.bytes = sum(_own_bytes(cfg, paper, r, r is author) for r in chosen)
    return out


def delete_runs(cfg: Config, db: Any, manager: Any, paper: Paper,
                found: list[readings.Reading], chosen: list[readings.Reading]) -> int:
    """Delete the readings `chosen` (from `found`, this paper's readings).

    Returns the bytes freed. A reading still running is refused before anything
    is touched; a folder that cannot be removed stops the deletion with its
    database row still in place, so that the run is still listed and can be
    deleted again.
    """
    live = [r for r in chosen if r.live]
    if live:
        raise RemovalError("A reading of this paper is running. Stop it first, then delete it.")
    if any(r.run_id and not belongs(cfg, paper, r.run_id, r.harness) for r in chosen):
        raise RemovalError("That run read another folder of the same name (a problem of the "
                           "lab, perhaps), so it is not deleted from here.")
    author = _shared_author(found)
    freed = 0
    for reading in chosen:
        freed += _own_bytes(cfg, paper, reading, reading is author)
        if reading.run_id:
            folder = run_folder(cfg, reading.run_id)
            if folder.exists():
                try:
                    shutil.rmtree(folder)
                except OSError as exc:
                    raise RemovalError(f"Run {reading.number or ''} could not be deleted "
                                       f"completely ({exc.strerror or exc}).") from exc
            _forget_rows(db, reading.run_id)
            if manager is not None:
                handle = manager.get(reading.run_id)
                if handle is not None and handle.state.terminal:
                    manager.runs.pop(reading.run_id, None)
        if reading is author or reading.run_id is None:
            # Its companion is the paper's own folder: written by this run,
            # or by no run at all.
            with contextlib.suppress(OSError):
                shutil.rmtree(paper.reading)
        for kept in _kept_pdfs(paper, reading):
            with contextlib.suppress(OSError):
                kept.unlink()
        _forget_terms(paper, _key(reading))
    return freed


def delete_paper(cfg: Config, db: Any, manager: Any, paper: Paper,
                 found: list[readings.Reading]) -> int:
    """Delete a paper and every run that read it. Returns the bytes freed."""
    if any(r.live for r in found):
        raise RemovalError("This paper is being read. Stop the reading first, then delete it.")
    freed = 0
    # Every run recorded against the folder, not only the readings: a session
    # from the lab on it would otherwise be left pointing at nothing.
    ids = [str(row["id"]) for row in db.query(
        "SELECT id, harness FROM runs WHERE problem = ?", (paper.slug,))
           if belongs(cfg, paper, str(row["id"]), str(row["harness"] or ""))]
    for run_id in ids:
        with contextlib.suppress(RemovalError):
            folder = run_folder(cfg, run_id)
            freed += size_of(folder)
            if folder.exists():
                try:
                    shutil.rmtree(folder)
                except OSError as exc:
                    raise RemovalError(f"A run of this paper could not be deleted "
                                       f"({exc.strerror or exc}).") from exc
        _forget_rows(db, run_id)
        if manager is not None:
            handle = manager.get(run_id)
            if handle is not None and handle.state.terminal:
                manager.runs.pop(run_id, None)
    freed += size_of(paper.root)
    try:
        shutil.rmtree(paper.root)
    except OSError as exc:
        raise RemovalError(f"The paper could not be deleted completely "
                           f"({exc.strerror or exc}).") from exc
    return freed


def _forget_rows(db: Any, run_id: str) -> None:
    for table in ("usage_samples", "steering", "verdicts"):
        db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
    db.execute("DELETE FROM runs WHERE id = ?", (run_id,))


def _forget_terms(paper: Paper, key: str) -> None:
    """The key terms kept for one reading's companion."""
    from .explain import forget_glossary

    with contextlib.suppress(OSError):
        forget_glossary(paper, key)
