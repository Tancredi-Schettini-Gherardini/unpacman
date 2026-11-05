"""Where the reader keeps what is its own on this computer — what a reader
tells it (their background, their answer about news of new versions, keys for
hosted models) and what it caches: the checkout's `var/`, gitignored and never
shared.

A test server (`deslopify/tools/isolated_server.py`) has papers, runs and a
database of its own, and names a `var/` of its own in `UNPACMAN_VAR` as well,
so that a test never writes into the real one (2026-10-13: a test's background
landed in the real `var/` before this existed).
"""

from __future__ import annotations

import os
from pathlib import Path

from atelier.config import Config


def var_dir(cfg: Config) -> Path:
    override = os.environ.get("UNPACMAN_VAR", "").strip()
    return Path(override) if override else cfg.root / "var"
