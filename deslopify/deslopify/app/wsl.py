"""unpacman inside WSL — Windows' built-in Linux (2026-10-17, branch
unpacman_windows).

On a Windows computer unpacman runs inside WSL 2 — an Ubuntu there — exactly as
on any Linux: its readings, their sandbox (bubblewrap) and the AI programs are
Linux's. What belongs to Windows is reached through WSL's interop, which lets
a Linux program start a Windows one: the page opens in the Windows browser,
unpacman gets a Start-menu shortcut, and Windows is kept awake while a reading
runs. Each is done here, by Windows PowerShell, with its script sent as
-EncodedCommand (UTF-16LE, base64), so that no quoting stands between Linux
and PowerShell.

The shortcut runs unpacman in a window of its own, minimised: that window is
unpacman while it runs. A program WSL started in the background may be
stopped when no window of that Linux is left open, which would stop a
reading; a window that stays open cannot be.

Not yet run on a Windows computer: written from Microsoft's and the
programs' own documentation, and tested on a Mac with stand-ins (WINDOWS.md,
at the top of the repository, says what to check there first).
"""

from __future__ import annotations

import base64
import contextlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "Unpacman"
ICON = Path(__file__).resolve().parent / "windows" / "unpacman.ico"

# Where WSL shows Windows' own drive, and Windows' programs on it, when they
# are not on PATH already (WSL adds Windows' folders to it, unless told not to).
WINDOWS_PLACES = ("/mnt/c/Windows/System32/WindowsPowerShell/v1.0",
                  "/mnt/c/Windows/System32", "/mnt/c/Windows")

# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: Windows does not sleep while the thread
# that asked lives — here, until unpacman closes this script's input (or ends,
# which closes it too).
AWAKE_SCRIPT = r"""
$signature = '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);'
$kernel = Add-Type -MemberDefinition $signature -Name Awake -Namespace Unpacman -PassThru
$null = $kernel::SetThreadExecutionState([uint32]2147483649)
$null = [Console]::In.ReadToEnd()
"""


class WindowsAppError(RuntimeError):
    """Something Windows could not be asked to do, said plainly."""


# --------------------------------------------------------------------------
# where this is
# --------------------------------------------------------------------------


def _osrelease() -> str:
    try:
        return Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def is_wsl() -> bool:
    """Whether this is Linux inside Windows: WSL names itself in the processes
    it starts, and in its kernel's name."""
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    return "microsoft" in _osrelease()


def wsl_version() -> int | None:
    """2 or 1; None outside WSL. WSL 1 has no Linux kernel, and cannot make a
    sandbox: what it calls its kernel is "4.4.0-<build>-Microsoft". WSL 2 runs
    a real one: "…-microsoft-standard-WSL2" — "…-microsoft-standard" in its
    first versions — or one of the person's own, which only WSL 2 can run."""
    if not is_wsl():
        return None
    release = _osrelease()
    if not release:
        return None
    if "microsoft-standard" in release or "wsl2" in release:
        return 2
    return 1 if release.endswith("-microsoft") else 2


def distro() -> str:
    """This Linux's name in WSL — "Ubuntu", "Ubuntu-24.04" — as WSL tells it."""
    return os.environ.get("WSL_DISTRO_NAME", "")


def windows_program(name: str) -> str | None:
    """A Windows program, as Linux reaches it: on PATH, in the Windows folders
    unpacman took off it (ready.prepare_environment), or where Windows keeps it."""
    found = shutil.which(name)
    if found:
        return found
    from .ready import WINDOWS_PATH

    if WINDOWS_PATH and (found := shutil.which(name, path=os.pathsep.join(WINDOWS_PATH))):
        return found
    for folder in WINDOWS_PLACES:
        candidate = Path(folder) / name
        if candidate.is_file():
            return str(candidate)
    return None


def _windows_cwd() -> str | None:
    """A folder on Windows' own drive to start a Windows program in: started in
    a Linux folder, some say so before anything else ("UNC paths are not
    supported")."""
    return "/mnt/c" if Path("/mnt/c").is_dir() else None


# --------------------------------------------------------------------------
# PowerShell
# --------------------------------------------------------------------------


def ps_quote(text: str) -> str:
    """A PowerShell string literal: in single quotes, which only themselves
    escape, doubled."""
    return "'" + text.replace("'", "''") + "'"


def _encoded(script: str) -> list[str]:
    exe = windows_program("powershell.exe")
    if not exe:
        raise WindowsAppError("Windows PowerShell could not be reached from this Linux. "
                              "Is WSL's interop turned off?")
    # Its output in UTF-8, whatever the console's own code page; and no
    # progress bars, which PowerShell would otherwise write among its errors.
    script = ("$ProgressPreference = 'SilentlyContinue'\n"
              "try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }\n" + script)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]


def powershell(script: str, *, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Run a PowerShell script in Windows, and its outcome; WindowsAppError if
    it could not be run at all. Its output is read as UTF-8, without the mark
    Windows may put first ("utf-8-sig")."""
    try:
        return subprocess.run(_encoded(script), capture_output=True, text=True,
                              timeout=timeout, cwd=_windows_cwd(), stdin=subprocess.DEVNULL,
                              encoding="utf-8-sig", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsAppError(f"Windows PowerShell could not be run: {exc}") from exc


def said(stderr: str | None) -> str:
    """What PowerShell said went wrong, as text: started by another program,
    it may write its errors as CLIXML ("#< CLIXML"), whose error lines are
    kept here."""
    text = (stderr or "").strip()
    if not text.startswith("#< CLIXML"):
        return text
    lines = re.findall(r'<S S="Error">(.*?)</S>', text, flags=re.S)
    plain = "".join(lines).replace("_x000D_", "").replace("_x000A_", "\n")
    return html.unescape(plain).strip()


# --------------------------------------------------------------------------
# the browser, sleep, the shortcut
# --------------------------------------------------------------------------


def open_url(url: str) -> bool:
    """Open `url` in the Windows browser; whether it could be asked to."""
    with contextlib.suppress(WindowsAppError):
        done = powershell(f"Start-Process {ps_quote(url)}", timeout=30)
        if done.returncode == 0:
            return True
    # wslu's own opener, where it is installed.
    if (viewer := shutil.which("wslview")):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            return subprocess.run([viewer, url], capture_output=True, timeout=30,
                                  stdin=subprocess.DEVNULL).returncode == 0
    return False


def keep_awake() -> subprocess.Popen[bytes] | None:
    """A PowerShell keeping Windows awake for as long as its input is open:
    closed by KeepAwake.stop, or by the end of unpacman itself. None if
    Windows could not be asked."""
    try:
        argv = _encoded(AWAKE_SCRIPT)
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, cwd=_windows_cwd())
    except (WindowsAppError, OSError):
        return None


def cmd_quote(text: str) -> str:
    """One argument on a Windows command line (wsl.exe's), quoted if it must be."""
    if text and not any(c in text for c in ' \t"'):
        return text
    return '"' + text.replace('"', '\\"') + '"'


def shortcut_arguments(root: Path, python: Path, name: str) -> str:
    """What the shortcut gives wsl.exe: this Linux, this copy, unpacman."""
    return " ".join(["-d", cmd_quote(name), "--cd", cmd_quote(str(root)), "-e",
                     cmd_quote(str(python)), "-m", "deslopify.app"])


def shortcut_script(arguments: str, icon: str) -> str:
    """The PowerShell that makes the Start-menu shortcut, and says where it is."""
    return "\n".join([
        "$link = Join-Path ([Environment]::GetFolderPath('Programs')) "
        f"{ps_quote(APP_NAME + '.lnk')}",
        "$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)",
        "$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\\wsl.exe'",
        f"$shortcut.Arguments = {ps_quote(arguments)}",
        "$shortcut.WorkingDirectory = $env:USERPROFILE",
        f"$shortcut.IconLocation = {ps_quote(icon + ',0')}",
        "$shortcut.WindowStyle = 7",                  # minimised
        "$shortcut.Description = 'unpacman: read a mathematics paper'",
        "$shortcut.Save()",
        "Write-Output $link",
    ])


def make_shortcut(root: Path, python: Path | None = None) -> str:
    """A Start-menu shortcut "Unpacman" that opens this copy of unpacman — in
    a minimised window of this Linux, which is unpacman while it runs — with
    unpacman's icon, kept in the person's own AppData\\Local\\unpacman, where
    Windows can read it without starting Linux. Its Windows path."""
    if not is_wsl():
        raise WindowsAppError("A Windows shortcut can only be made from inside WSL.")
    name = distro()
    if not name:
        raise WindowsAppError("WSL did not say which Linux this is (WSL_DISTRO_NAME is "
                              "not set): start this from Ubuntu itself.")
    done = powershell("[Environment]::GetFolderPath('LocalApplicationData')")
    lines = (done.stdout or "").strip().splitlines()
    if done.returncode != 0 or not lines:
        raise WindowsAppError("Windows did not say where its own application data is. "
                              + said(done.stderr)[-300:])
    local = lines[-1].strip().rstrip("\\")
    windows_dir = local + "\\unpacman"
    try:
        # The folder that exists, translated; then unpacman's own in it.
        found = subprocess.run(["wslpath", "-u", local], capture_output=True,
                               text=True, timeout=20, stdin=subprocess.DEVNULL)
        if found.returncode != 0 or not found.stdout.strip():
            raise OSError(found.stderr.strip() or "wslpath said nothing")
        linux_dir = Path(found.stdout.strip()) / "unpacman"
        linux_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ICON, linux_dir / ICON.name)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsAppError(f"unpacman's icon could not be put in {windows_dir}: {exc}") from exc
    python = python or Path(sys.executable)
    done = powershell(shortcut_script(shortcut_arguments(root, python, name),
                                      windows_dir + "\\" + ICON.name))
    lines = (done.stdout or "").strip().splitlines()
    if done.returncode != 0 or not lines:
        raise WindowsAppError("The Start-menu shortcut could not be made. "
                              + said(done.stderr)[-400:])
    link = lines[-1].strip()
    # As the Mac's icon does (mac.make_app): a copy with one checks for new
    # versions by itself.
    from .instance import app_marker

    marker = app_marker(root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"app": link, "made": time.time()}), encoding="utf-8")
    return link
