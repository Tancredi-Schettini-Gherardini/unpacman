"""unpacman on Linux itself — a Linux computer, not Linux inside Windows
(wsl.py): its entry in the applications menu, keeping the computer awake while
a reading runs, and the line that installs what Linux lacks, in the words of
its own package manager (2026-11-05; branch unpacman_windows, published as
`linux`, whose code is the same as `windows`).

The menu entry is a desktop file, ~/.local/share/applications/unpacman.desktop
(freedesktop.org's Desktop Entry Specification, which GNOME, KDE, Xfce,
Cinnamon, MATE and the others read), with unpacman's icon in the person's own
icon folder. Like the Mac's icon (mac.py) and Windows' shortcut (wsl.py), all it
does is run `python -m deslopify.app --app` for this copy: unpacman starts in
the background if it is not running, and opens in the browser. Making it marks
the copy as installed (instance.app_marker), so that it looks for new versions
by itself (updates.Updater.watch). Deleting the file removes it; the copy it
opens is untouched.

Keeping awake: systemd's logind, asked through `systemd-inhibit` for a lock on
idle sleep, held while a reading runs and let go when it ends — or when
unpacman stops, whose process the lock's own program watches. Closing the lid
still suspends, as on a Mac (logind ignores such locks for the lid unless told
otherwise). Without systemd, or where logind refuses, nothing is held.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "Unpacman"
DESKTOP_FILE = "unpacman.desktop"
ICON = Path(__file__).resolve().parent / "web" / "unpacman-logo.svg"


class LinuxAppError(RuntimeError):
    """A menu entry that could not be made, said plainly."""


def data_home() -> Path:
    """$XDG_DATA_HOME, or ~/.local/share, where a person's own menu entries and
    icons go (the Base Directory Specification)."""
    raw = os.environ.get("XDG_DATA_HOME", "")
    return Path(raw) if raw and Path(raw).is_absolute() else Path.home() / ".local" / "share"


# Characters that oblige an Exec argument to be quoted (Desktop Entry
# Specification, "The Exec key").
_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")


def exec_argument(text: str) -> str:
    """One argument of an Exec key, as the specification asks: `%` doubled;
    quoted, if it holds a reserved character, with `"`, `` ` ``, `$` and `\\`
    escaped inside the quotes — and then every backslash doubled again, by the
    escape rule of any string value (so a backslash in a path takes four)."""
    if "\n" in text or "\r" in text:
        raise LinuxAppError("A folder whose name has a line break in it cannot be named in "
                            "a menu entry.")
    text = text.replace("%", "%%")
    if not any(c in _RESERVED for c in text):
        return text
    inner = "".join("\\" + c if c in '"`$\\' else c for c in text)
    return '"' + inner.replace("\\", "\\\\") + '"'


def string_value(text: str) -> str:
    """A string value of a desktop file (Path=, Icon=): its escapes only."""
    if "\n" in text or "\r" in text:
        raise LinuxAppError("A folder whose name has a line break in it cannot be named in "
                            "a menu entry.")
    return text.replace("\\", "\\\\").replace("\t", "\\t")


def desktop_entry(root: Path, python: Path, icon: Path | None) -> str:
    """The menu entry's text for the copy at `root`, run with `python`."""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        f"Name={APP_NAME}",
        "GenericName=Reading companion",
        "Comment=A reading companion for hard papers",
        "Exec=" + " ".join(exec_argument(a) for a in (str(python), "-m", "deslopify.app",
                                                       "--app")),
        f"Path={string_value(str(root))}",
        "Terminal=false",
        "StartupNotify=false",
        "Categories=Education;Science;",
        "Keywords=paper;mathematics;reading;PDF;companion;",
    ]
    if icon is not None:
        lines.insert(7, f"Icon={string_value(str(icon))}")
    return "\n".join(lines) + "\n"


def make_app(root: Path, python: Path | None = None) -> Path:
    """The menu entry "Unpacman" for the copy of unpacman at `root`; its path.

    Written beside the old one and swapped in, so a failure halfway leaves the
    old entry working. The icon is copied into the person's own icon folder,
    where it stays when the copy moves on to a new version."""
    root = root.resolve()
    python = python or Path(sys.executable)
    share = data_home()
    apps = share / "applications"
    try:
        apps.mkdir(parents=True, exist_ok=True)
        icon: Path | None = None
        if ICON.is_file():
            icon = share / "icons" / "hicolor" / "scalable" / "apps" / "unpacman.svg"
            icon.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ICON, icon)
        entry = apps / DESKTOP_FILE
        staged = apps / f".{DESKTOP_FILE}.new"
        staged.write_text(desktop_entry(root, python, icon), encoding="utf-8")
        staged.chmod(0o644)
        staged.replace(entry)
    except OSError as exc:
        raise LinuxAppError(f"The menu entry could not be written in {apps}: {exc}") from exc
    # Most desktops see a new entry by themselves; some read a cache, which this
    # brings up to date where its program is installed.
    refresh = shutil.which("update-desktop-database")
    if refresh:
        try:
            subprocess.run([refresh, "-q", str(apps)], capture_output=True, timeout=30,
                           stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    # As the Mac's icon does (mac.make_app): a copy with one checks for new
    # versions by itself.
    from .instance import app_marker

    marker = app_marker(root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"app": str(entry), "made": time.time()}), encoding="utf-8")
    return entry


def keep_awake() -> subprocess.Popen[bytes] | None:
    """A lock on idle sleep while a reading runs (instance.KeepAwake), held by
    `systemd-inhibit` around a program that ends when this process does — so
    the lock can never outlive unpacman. None where there is no systemd."""
    inhibit = shutil.which("systemd-inhibit")
    if not inhibit or not Path("/bin/sh").exists():
        return None
    watch = f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 5; done"
    try:
        return subprocess.Popen(
            [inhibit, "--what=idle:sleep", "--who=unpacman", "--why=A reading is running",
             "--mode=block", "/bin/sh", "-c", watch],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


# --------------------------------------------------------------------------
# what Linux lacks, in its own package manager's words
# --------------------------------------------------------------------------

# Each program unpacman may need, by the package that brings it, per package
# manager: Debian and Ubuntu (apt), Fedora (dnf), Arch (pacman), openSUSE (zypper).
PACKAGES = {
    "apt":    {"bwrap": "bubblewrap", "rg": "ripgrep", "xz": "xz-utils",
               "pdftoppm": "poppler-utils", "pdftotext": "poppler-utils"},
    "dnf":    {"bwrap": "bubblewrap", "rg": "ripgrep", "pdftoppm": "poppler-utils",
               "pdftotext": "poppler-utils"},
    "pacman": {"bwrap": "bubblewrap", "rg": "ripgrep", "pdftoppm": "poppler",
               "pdftotext": "poppler"},
    "zypper": {"bwrap": "bubblewrap", "rg": "ripgrep", "pdftoppm": "poppler-tools",
               "pdftotext": "poppler-tools"},
}
_COMMAND = {
    "apt": "sudo apt install",
    "dnf": "sudo dnf install",
    "pacman": "sudo pacman -S --needed",
    "zypper": "sudo zypper install",
}
_PROGRAM = {"apt": "apt-get", "dnf": "dnf", "pacman": "pacman", "zypper": "zypper"}


def package_manager() -> str:
    """"apt", "dnf", "pacman" or "zypper" — whichever this Linux has; "apt"
    when none is found (Ubuntu, under WSL or not, first of all)."""
    for name, program in _PROGRAM.items():
        if shutil.which(program):
            return name
    return "apt"


def install_command(tools: list[str], manager: str | None = None) -> str:
    """The one line that installs these programs with this Linux's package manager."""
    manager = manager or package_manager()
    names = PACKAGES.get(manager, PACKAGES["apt"])
    packages = dict.fromkeys(names.get(tool, tool) for tool in tools)
    return f"{_COMMAND.get(manager, _COMMAND['apt'])} " + " ".join(packages)


def apparmor_restricts_namespaces() -> bool:
    """Whether this Linux has Ubuntu's switch (since 23.10) that keeps programs
    other than its own profiled ones from making user namespaces."""
    return Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").exists()
