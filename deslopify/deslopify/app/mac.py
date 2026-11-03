"""The Unpacman icon: an application in the person's Applications folder that
opens unpacman with a double-click, from Launchpad, Spotlight or the Dock.

It is a small AppleScript application, compiled on the person's own Mac by
`osacompile` (part of macOS) when the installer runs `python -m deslopify.app
--make-app`. Two things about that are deliberate, both measured on macOS 26
(2026-10-06):

- An application bundle whose program is a shell script — the obvious way to
  write a launcher — is refused by macOS ("error -10669"), signed or not. An
  AppleScript applet is a real program, which macOS launches.
- Made on the Mac rather than downloaded, it carries no quarantine mark, so
  macOS opens it without the "unidentified developer" warning a downloaded
  unsigned application gets. After its icon is put in, it is signed again
  locally ("ad hoc", which needs no Apple account), since changing a file in
  a signed application breaks its signature.

All it does is run `python -m deslopify.app --app` for this copy, which starts
unpacman in the background if it is not running and opens it in the browser.
Nothing else is installed: no login item, no background service. Deleting the
icon removes it; the copy of unpacman it opens is untouched.
"""

from __future__ import annotations

import json
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

APP_NAME = "Unpacman"
BUNDLE_ID = "org.unpacman.launcher"
HERE = Path(__file__).resolve().parent / "mac"
ICON = HERE / "unpacman.icns"
MISSING = 3      # the shell's exit code when the copy has gone


class MacAppError(RuntimeError):
    """The icon could not be made, said plainly."""


def default_apps_dir() -> Path:
    return Path.home() / "Applications"


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def launcher_command(root: Path) -> str:
    """The shell command the icon runs: open this copy of unpacman."""
    python = root / ".venv" / "bin" / "python"
    return (f"if [ ! -x {shlex.quote(str(python))} ]; then exit {MISSING}; fi; "
            f"cd {shlex.quote(str(root))} && "
            f"exec {shlex.quote(str(python))} -m deslopify.app --app >/dev/null 2>&1")


def applet_source(root: Path) -> str:
    """The icon's program. `--app` says itself, in a dialog, when unpacman
    cannot start; this says only what `--app` cannot — that the copy has gone."""
    missing = (f"Its folder, {root}, is missing or incomplete. Run the installer "
               "again to repair it.")
    return "\n".join([
        "on run",
        "  try",
        f"    do shell script {_applescript_string(launcher_command(root))}",
        "  on error message number code",
        f"    if code is {MISSING} then",
        '      display alert "unpacman cannot start" message '
        f"{_applescript_string(missing)} as critical",
        "    end if",
        "  end try",
        "end run",
        "",
    ])


def _run(argv: list[str]) -> None:
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise MacAppError(f"{Path(argv[0]).name} failed"
                          + (f": {detail[-1]}" if detail else "."))


def make_app(root: Path, apps_dir: Path | None = None) -> Path:
    """Write the icon for the copy of unpacman at `root`; its path.

    Made beside the old one and swapped in, so a failure halfway leaves the
    old icon working.
    """
    if shutil.which("osacompile") is None:
        raise MacAppError("The icon can only be made on a Mac (osacompile is missing).")
    root = root.resolve()
    apps = (apps_dir or default_apps_dir()).expanduser()
    apps.mkdir(parents=True, exist_ok=True)
    target = apps / f"{APP_NAME}.app"
    # osacompile makes an application only when the name ends in ".app".
    staged = apps / f".{APP_NAME}-new.app"
    shutil.rmtree(staged, ignore_errors=True)

    with tempfile.TemporaryDirectory(prefix="unpacman-icon-") as raw:
        source = Path(raw) / "unpacman.applescript"
        source.write_text(applet_source(root), encoding="utf-8")
        _run(["osacompile", "-o", str(staged), str(source)])

    contents = staged / "Contents"
    info_path = contents / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    version = time.strftime("%Y.%m.%d")
    info.update({
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.education",
    })
    if ICON.is_file():
        icon_name = str(info.get("CFBundleIconFile") or "applet")
        icon_name = icon_name[:-5] if icon_name.endswith(".icns") else icon_name
        shutil.copy2(ICON, contents / "Resources" / f"{icon_name}.icns")
        # Newer applets also carry an asset catalogue whose icon would win.
        info.pop("CFBundleIconName", None)
        with_assets = contents / "Resources" / "Assets.car"
        if with_assets.exists():
            with_assets.unlink()
    info_path.write_bytes(plistlib.dumps(info))
    # The files changed after osacompile signed it: sign it again, locally.
    _run(["codesign", "--force", "--sign", "-", str(staged)])

    old = apps / f".{APP_NAME}-old.app"
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        target.rename(old)
    staged.rename(target)
    shutil.rmtree(old, ignore_errors=True)
    # Finder caches icons; touching the bundle tells it to look again.
    target.touch()

    marker = root / "var" / "unpacman-app.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"app": str(target), "made": time.time()}),
                      encoding="utf-8")
    return target
