"""The first run's suggestion: select some text, right-click it, ask about it.

Tancredi, 2026-10-20: someone using unpacman for the first time — and only
then — is shown a box suggesting they highlight some text, right-click it and
ask about it, in the companion or in the paper's PDF; and the box stays until
they have done so (web/firstask.js). And 2026-10-23: it appears once their
first reading has started, while they wait for it — to show them that the
paper can be read, and asked about, before its companion exists.

Whether this copy's reader is new is decided once, the first time a server of
this version starts, and kept in `var/unpacman-first-ask.json`:

- "pending" — no paper had been given to this copy yet: a new reader. Once a
  reading has been started (kept as `"started": true`), the box is shown
  while it runs, and in the companion, side by side and in the paper, until
  the first selection is asked about.
- "done" — a selection has been asked about: now, or before this was kept (a
  paper's explanations are on record), so that a reader who already knows how
  is never shown it.
- "skipped" — papers were here already and none was ever asked about: a copy
  in use before this existed, whose first run is past.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from atelier.config import Config

from .explain import STORE as EXPLANATIONS          # what was asked, kept with the paper
from .library import MARKER                         # what makes a folder a paper
from .places import var_dir

STATES = ("pending", "done", "skipped")


def store(cfg: Config) -> Path:
    return var_dir(cfg) / "unpacman-first-ask.json"


def _papers(cfg: Config) -> list[Path]:
    try:
        return [p for p in cfg.problems_dir.iterdir() if (p / MARKER).is_file()]
    except OSError:
        return []


def _asked_before(papers: list[Path]) -> bool:
    """Whether any paper has an answer kept — read as explain.load_store does."""
    for paper in papers:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads((paper / EXPLANATIONS).read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
            if isinstance(items, list) and any(isinstance(i, dict) for i in items):
                return True
    return False


def _write(cfg: Config, state: str, *, started: bool = False) -> None:
    path = store(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    record: dict[str, object] = {"state": state, "at": time.time()}
    if started:
        record["started"] = True
    tmp.write_text(json.dumps(record), encoding="utf-8")
    tmp.replace(path)


def state(cfg: Config) -> str:
    """This copy's state — decided, and kept, the first time it is asked."""
    with contextlib.suppress(OSError, ValueError, AttributeError):
        kept = json.loads(store(cfg).read_text(encoding="utf-8")).get("state")
        if kept in STATES:
            return kept
    papers = _papers(cfg)
    decided = "done" if _asked_before(papers) else "skipped" if papers else "pending"
    with contextlib.suppress(OSError):
        _write(cfg, decided)
    return decided


def pending(cfg: Config) -> bool:
    return state(cfg) == "pending"


def started(cfg: Config) -> bool:
    """Whether a reading has been started while the box was still to be shown."""
    with contextlib.suppress(OSError, ValueError, AttributeError):
        return json.loads(store(cfg).read_text(encoding="utf-8")).get("started") is True
    return False


def begun(cfg: Config) -> None:
    """A reading has started: from now on a new reader is shown the box."""
    if state(cfg) == "pending" and not started(cfg):
        with contextlib.suppress(OSError):
            _write(cfg, "pending", started=True)


def asked(cfg: Config) -> None:
    """A selection has been asked about: the box is not shown again."""
    if state(cfg) != "done":
        with contextlib.suppress(OSError):
            _write(cfg, "done")
