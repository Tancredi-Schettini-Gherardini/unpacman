"""Desktop notifications. Best effort — never raises, never blocks."""

from __future__ import annotations

import shutil
import subprocess
import sys

_ENABLED = True


def set_enabled(value: bool) -> None:
    global _ENABLED
    _ENABLED = value


def notify(title: str, message: str, *, sound: bool = False) -> None:
    if not _ENABLED or sys.platform != "darwin":
        return
    osascript = shutil.which("osascript")
    if not osascript:
        return
    script = (
        f'display notification {_esc(message)} with title "Atelier" '
        f'subtitle {_esc(title)}' + (' sound name "Glass"' if sound else "")
    )
    try:
        subprocess.Popen(
            [osascript, "-e", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _esc(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"')[:200] + '"'
