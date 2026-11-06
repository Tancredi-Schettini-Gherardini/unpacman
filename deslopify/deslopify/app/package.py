"""A package: unpacman in one zip, for people who do not use GitHub — the
download on unpacman's website and on its public repository's releases.

A package for a Mac is a zip of one folder:

    unpacman-2026-10-07/
        READ ME FIRST.txt
        install-unpacman.sh      the installer, from the packaged version
        unpacman.bundle          the `mac` branch, history and all (git bundle)
        package.json             when it was made, and from which commit

The installer finds the bundle beside itself and installs from it instead of
GitHub. The installed copy is a real git checkout, as one cloned from GitHub
would be, and it then follows unpacman's public repository: it looks there for
new versions by itself (updates.py), and a newer package, run the same way,
moves it forward too (install-unpacman.sh says how). A package for Windows
(`make_windows_package`) holds the same for the `windows` branch, at the zip's
top, with a small `install.sh` that runs `install-unpacman-wsl.sh` from it.

Both are made from the public repository's branches — a clone of it, with
the branches publish.py writes — so that what a copy has, the public
repository has too, and every later version follows from it. A package is
always made from a commit, never from files not yet committed, so what a
reader runs is a version that exists.

    python -m deslopify.app --make-package [--from REPOSITORY] [--windows] [--out FOLDER]
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

BRANCH = "mac"                  # what the installer installs
INSTALLER = "install-unpacman.sh"
BUNDLE = "unpacman.bundle"
WINDOWS_BRANCH = "windows"      # what the Windows installer installs
WINDOWS_INSTALLER = "install-unpacman-wsl.sh"
WINDOWS_BOOTSTRAP = "install-unpacman.ps1"
WINDOWS_LAUNCHER = "Install unpacman.cmd"
WINDOWS_LAUNCH = ('@echo off\r\nsetlocal\r\ntitle Install unpacman\r\n'
                  'if not exist "%~dp0install-unpacman.ps1" (\r\n'
                  '  echo Extract the entire ZIP first: right-click it and choose Extract All.\r\n'
                  '  echo Then open Install unpacman.cmd in the extracted folder.\r\n'
                  '  pause\r\n  exit /b 1\r\n)\r\n'
                  '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-unpacman.ps1" -PackageDirectory "%~dp0." %*\r\n'
                  'set "unpacman_exit=%errorlevel%"\r\n'
                  'echo.\r\npause\r\nexit /b %unpacman_exit%\r\n')
# Where a copy installed from a package looks for new versions afterwards.
PUBLIC = "https://github.com/Tancredi-Schettini-Gherardini/unpacman.git"
SUPPORT = "unpacman.support@gmail.com"


class PackageError(RuntimeError):
    """A package could not be made, said plainly."""


def _git(root: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)
    if check and done.returncode != 0:
        raise PackageError(f"git {' '.join(args[:2])} failed: "
                           + (done.stderr.strip().splitlines() or ["no reason given"])[-1])
    return done.stdout.strip()


def _committed(root: Path, sha: str, branch: str, path: str) -> bytes:
    """The file at `path` as committed in `sha`, byte for byte."""
    shown = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=root,
                           capture_output=True, stdin=subprocess.DEVNULL, check=False)
    if shown.returncode != 0:
        raise PackageError(f"The branch {branch} has no {path} to put in the package.")
    return shown.stdout


def readme(made: str, commit: str, folder: str) -> str:
    """READ ME FIRST.txt — for someone who has never used Terminal."""
    return f"""unpacman — a reading companion for hard papers
Package made {made} (version {commit[:7]}).

unpacman runs on your own Mac. Give it a paper as a PDF and it gives back a
companion to it: the ideas explained, questions a reader would ask answered,
and an honest account of where the paper is thin. You need a paid plan on
Claude (Pro, Max, Team or Enterprise) or on ChatGPT (Plus or above).

TO INSTALL IT (about ten minutes, mostly downloads)

1. Open Terminal: press Command and the space bar together, type Terminal,
   press Return.

2. Type this line exactly and press Return:

       bash ~/Downloads/{folder}/install-unpacman.sh

   If it says "No such file or directory", this folder is somewhere other
   than Downloads. Then type   bash   and a space, drag the file
   install-unpacman.sh from this folder onto the Terminal window, and press
   Return.

   macOS may ask whether Terminal may use files in your Downloads folder:
   click Allow. If it offers to install "command line developer tools",
   click Install and wait (5 to 15 minutes); the installer carries on by
   itself. It never asks for your password.

3. When it says "Done", unpacman opens in your browser, on a page called
   Getting ready. Press its buttons in order: install the AI you have a plan
   for, sign in to it (a sign-in page opens in your browser), install LaTeX.
   Then press Continue. None of this costs anything.

Afterwards, open unpacman from Launchpad, or press Command-Space and type
unpacman. You can close Terminal; you will not need it again.

UPDATES come by themselves: when a new version is ready, unpacman says so on
its first page; press Update now. Your papers and readings are kept. (It
looks for new versions on GitHub, where unpacman is public; nothing about
you or your papers is sent there.)

IF SOMETHING GOES WRONG, or you have an idea: press Feedback at the top of
unpacman. It writes an email to the people who make unpacman, with a report
on your copy attached (versions and error messages, never your papers), and
you send it from your own email program. If unpacman itself will not open,
write to {SUPPORT}.

TO REMOVE IT: delete Unpacman from your Applications folder, and the folder
.unpacman in your home folder (in Finder press Shift-Command-G and type
~/.unpacman). That folder also holds your papers and readings.
"""


def make_package(root: Path, out_dir: Path, *, branch: str = BRANCH,
                 today: dt.date | None = None, name: str | None = None) -> Path:
    """Make the zip from the committed `branch` of the repository at `root`;
    its path. Checks the result before returning it. `name`, if given, is the
    zip's own name and its folder's — `unpacman-mac` for a release, whose
    download address and install line must not change — replacing one made
    before."""
    root = root.resolve()
    sha = _git(root, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    installer = _committed(root, sha, branch, INSTALLER)
    made = (today or dt.date.today()).isoformat()
    version_date = _git(root, "log", "-1", "--format=%cs", sha)

    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = name or f"unpacman-{made}"
    if name:
        target = out_dir / f"{name}.zip"
    else:
        n = 1
        while (out_dir / f"{stem}.zip").exists():
            n += 1
            stem = f"unpacman-{made}-{n}"
        target = out_dir / f"{stem}.zip"

    with tempfile.TemporaryDirectory(prefix="unpacman-package-") as raw:
        tmp = Path(raw)
        bundle = tmp / BUNDLE
        _git(root, "bundle", "create", str(bundle), f"refs/heads/{branch}")
        _git(root, "bundle", "verify", str(bundle))
        info = {"made": made, "commit": sha, "branch": branch,
                "version_date": version_date}
        files = {
            "READ ME FIRST.txt": readme(made, sha, stem).encode("utf-8"),
            INSTALLER: installer,
            "package.json": (json.dumps(info, indent=2) + "\n").encode("utf-8"),
        }
        staged = target.with_name(f".{target.name}.part")
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            folder = zipfile.ZipInfo(f"{stem}/", date_time=_now())
            folder.external_attr = (0o40755 << 16) | 0x10
            zf.writestr(folder, b"")
            for name, data in files.items():
                entry = zipfile.ZipInfo(f"{stem}/{name}", date_time=_now())
                mode = 0o755 if name == INSTALLER else 0o644
                entry.external_attr = (0o100000 | mode) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(entry, data)
            entry = zipfile.ZipInfo(f"{stem}/{BUNDLE}", date_time=_now())
            entry.external_attr = (0o100644) << 16
            entry.compress_type = zipfile.ZIP_STORED          # already compressed
            with bundle.open("rb") as fh:
                zf.writestr(entry, fh.read())
        staged.replace(target)
    check_package(target, sha=sha, branch=branch)
    return target


def on_github(root: Path, branch: str = BRANCH) -> bool | None:
    """Whether the packaged version is also on GitHub (as last fetched) — so
    that a copy installed from the package finds its history there when it
    looks for a new version. None if this copy follows no remote branch."""
    remote = _git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}",
                  check=False)
    if not remote:
        return None
    local = _git(root, "rev-parse", f"refs/heads/{branch}")
    done = subprocess.run(["git", "merge-base", "--is-ancestor", local, remote], cwd=root,
                          capture_output=True, stdin=subprocess.DEVNULL, check=False)
    return done.returncode == 0


def _now() -> tuple[int, int, int, int, int, int]:
    return dt.datetime.now().timetuple()[:6]


def check_package(path: Path, *, sha: str, branch: str = BRANCH) -> None:
    """Unpack the zip somewhere temporary and clone from it, as the installer
    will: the branch must come out at `sha`, with the installer runnable."""
    with tempfile.TemporaryDirectory(prefix="unpacman-package-check-") as raw:
        tmp = Path(raw)
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise PackageError(f"The package is damaged ({bad}).")
            zf.extractall(tmp)
        [folder] = [p for p in tmp.iterdir() if p.is_dir()]
        for name in ("READ ME FIRST.txt", INSTALLER, BUNDLE, "package.json"):
            if not (folder / name).is_file():
                raise PackageError(f"The package lacks {name}.")
        clone = tmp / "clone"
        done = subprocess.run(["git", "clone", "--quiet", "--branch", branch,
                               str(folder / BUNDLE), str(clone)],
                              capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise PackageError("The package's copy of unpacman cannot be installed: "
                               + done.stderr.strip())
        head = _git(clone, "rev-parse", "HEAD")
        if head != sha:
            raise PackageError(f"The package holds {head[:7]}, not {sha[:7]}.")
        if (clone / INSTALLER).read_bytes() != (folder / INSTALLER).read_bytes():
            raise PackageError("The package's installer differs from its version's.")
        shutil.rmtree(clone, ignore_errors=True)


# --------------------------------------------------------------------------
# Windows: unpacman runs inside WSL, Windows' own Linux
# --------------------------------------------------------------------------

# Run in Ubuntu from the unzipped folder. It installs from the bundle beside it
# and the copy then follows the public repository (install-unpacman-wsl.sh,
# UNPACMAN_FOLLOW), so nothing of this folder is needed once it has run.
WINDOWS_START = """#!/bin/bash
# Install unpacman inside WSL (Ubuntu) from this package: no GitHub account
# is needed. Run it in Ubuntu, from this folder:   bash install.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
if [ "$(uname -s)" != "Linux" ]; then
  echo "Run this in Ubuntu (WSL) — see READ ME FIRST.txt — not in PowerShell or on a Mac." >&2
  exit 1
fi
export UNPACMAN_REPO="$here/{bundle}"
export UNPACMAN_BRANCH="{branch}"
export UNPACMAN_FOLLOW="${{UNPACMAN_FOLLOW:-{follow}}}"
exec bash "$here/{installer}"
"""


def windows_readme(made: str, commit: str, folder: str) -> str:
    """READ ME FIRST.txt for Windows — for someone who has never used a terminal."""
    return f"""unpacman for Windows — a reading companion for hard papers
Package made {made} (version {commit[:7]}).

unpacman runs on Windows inside WSL — Windows' own Linux, Ubuntu — and opens
in your Windows browser. You need a paid plan on Claude (Pro, Max, Team or
Enterprise) or on ChatGPT (Plus or above).

BEFORE YOU START

Use Windows 11, or 64-bit Windows 10 version 2004 or newer. A fresh setup
needs an internet connection, at least 10 GB free on C:, and permission
to approve Windows administrator prompts. Downloads can take several minutes.

TO INSTALL UNPACMAN

1. Unzip this package: right-click the zip, choose "Extract All…", then
   "Extract". Open the folder {folder} — the one with this file.

2. Double-click "Install unpacman.cmd" under your usual Windows account.
   Do not choose "Run as administrator". Keep the setup window open and
   follow its three stages:

   WINDOWS REQUIREMENTS: if WSL is missing, setup downloads Microsoft's
   installer, checks its signature and checksum, and asks for administrator
   approval. Approve that prompt. If a restart is needed, save your work,
   type RESTART in the setup window and press Enter. This finishes pending
   Windows changes and may also apply other queued Windows updates. It does
   not force applications closed. An ordinary Start-menu Restart can leave
   these changes pending. Press Enter without typing to finish later; open
   this installer again when ready. After restarting, sign in to the SAME
   Windows account and double-click "Install unpacman.cmd" in this folder
   again. Keep the extracted folder until installation is finished.

   UBUNTU SETUP: if Ubuntu is missing, setup downloads it and opens its
   user setup here. Choose a Linux username and password. No characters
   appear while typing the password. When you see a prompt ending in $,
   type exit and press Enter to return to the unpacman installer.

   INSTALL UNPACMAN: enter your Ubuntu password when sudo asks for it.
   Setup installs the Linux programs it needs, creates an Unpacman shortcut
   in the Start menu, and opens unpacman in your Windows browser.

   Existing WSL/Ubuntu installations are checked and reused. If setup is
   interrupted, open "Install unpacman.cmd" again; completed steps are reused.

3. unpacman's first page is Getting ready. Install the AI you have a plan
   for and sign in to it (a sign-in page opens in your browser), and install
   LaTeX. "A sandbox for each reading" should say Ready. Then press Continue.
   None of this costs anything.

FROM THEN ON: open unpacman from the Start menu (type unpacman). It opens in
a small window of its own, and that window IS unpacman while it runs: keep
it open (minimise it). Closing it stops unpacman, and any reading. Quit is in
Advanced settings, under This computer. This folder is no longer needed.

UPDATES come by themselves: when a new version is ready, unpacman says so on
its first page; press Update now. Your papers and readings are kept. (It
looks for new versions on GitHub, where unpacman is public; nothing about
you or your papers is sent there.)

IF SOMETHING GOES WRONG, or you have an idea: press Feedback at the top of
unpacman: it writes an email with a report on this copy attached (versions
and error messages, never your papers), which you send yourself. If unpacman
itself will not open, write to {SUPPORT}.

If INSTALLATION stops, keep the window open and copy the SETUP STOPPED
message. Windows prerequisite failures also give a log-folder location.
Send that error with your feedback; no GitHub account or token is needed.

TO REMOVE IT: in the Start menu, right-click Unpacman, choose "Open file
location", and delete the shortcut there. Then in Ubuntu type
    rm -rf ~/.unpacman
— that folder also holds your papers and readings.
"""


def make_windows_package(root: Path, out_dir: Path, *, branch: str = WINDOWS_BRANCH,
                         today: dt.date | None = None, name: str | None = None,
                         follow: str = PUBLIC) -> Path:
    """Make the Windows zip from the committed `branch` of the repository at
    `root`; its path, checked. Its files are at the zip's top: Windows' Extract
    All makes a folder named after the zip, and one inside it would be a level
    too deep — so the folder the instructions name is the zip's own name."""
    root = root.resolve()
    sha = _git(root, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    installer = _committed(root, sha, branch, WINDOWS_INSTALLER)
    bootstrap = _committed(root, sha, branch, WINDOWS_BOOTSTRAP)
    made = (today or dt.date.today()).isoformat()
    version_date = _git(root, "log", "-1", "--format=%cs", sha)

    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = name or f"unpacman-windows-{made}"
    if not name:
        n = 1
        while (out_dir / f"{stem}.zip").exists():
            n += 1
            stem = f"unpacman-windows-{made}-{n}"
    target = out_dir / f"{stem}.zip"

    start = WINDOWS_START.format(bundle=BUNDLE, branch=branch, follow=follow,
                                 installer=WINDOWS_INSTALLER)
    info = {"made": made, "commit": sha, "branch": branch, "version_date": version_date}
    files = {
        # Notepad reads either, but a file written on Windows has CRLF.
        "READ ME FIRST.txt": (windows_readme(made, sha, stem).replace("\n", "\r\n")
                              .encode("utf-8"), 0o644),
        "install.sh": (start.encode("utf-8"), 0o755),
        WINDOWS_INSTALLER: (installer, 0o755),
        WINDOWS_BOOTSTRAP: (bootstrap, 0o644),
        WINDOWS_LAUNCHER: (WINDOWS_LAUNCH.encode("utf-8"), 0o644),
        "package.json": ((json.dumps(info, indent=2) + "\n").encode("utf-8"), 0o644),
    }
    with tempfile.TemporaryDirectory(prefix="unpacman-package-") as raw:
        bundle = Path(raw) / BUNDLE
        _git(root, "bundle", "create", str(bundle), f"refs/heads/{branch}")
        _git(root, "bundle", "verify", str(bundle))
        staged = target.with_name(f".{target.name}.part")
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_name, (data, mode) in files.items():
                entry = zipfile.ZipInfo(file_name, date_time=_now())
                entry.external_attr = (0o100000 | mode) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(entry, data)
            entry = zipfile.ZipInfo(BUNDLE, date_time=_now())
            entry.external_attr = (0o100644) << 16
            entry.compress_type = zipfile.ZIP_STORED          # already compressed
            with bundle.open("rb") as fh:
                zf.writestr(entry, fh.read())
        staged.replace(target)
    check_windows_package(target, sha=sha, branch=branch)
    return target


def check_windows_package(path: Path, *, sha: str, branch: str = WINDOWS_BRANCH) -> None:
    """As check_package, for the Windows zip's layout."""
    with tempfile.TemporaryDirectory(prefix="unpacman-package-check-") as raw:
        tmp = Path(raw) / "unzipped"
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise PackageError(f"The package is damaged ({bad}).")
            zf.extractall(tmp)
        for file_name in ("READ ME FIRST.txt", "install.sh", WINDOWS_INSTALLER,
                          WINDOWS_BOOTSTRAP, WINDOWS_LAUNCHER, BUNDLE, "package.json"):
            if not (tmp / file_name).is_file():
                raise PackageError(f"The package lacks {file_name}.")
        clone = Path(raw) / "clone"
        done = subprocess.run(["git", "clone", "--quiet", "--branch", branch,
                               str(tmp / BUNDLE), str(clone)],
                              capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise PackageError("The package's copy of unpacman cannot be installed: "
                               + done.stderr.strip())
        head = _git(clone, "rev-parse", "HEAD")
        if head != sha:
            raise PackageError(f"The package holds {head[:7]}, not {sha[:7]}.")
        if (clone / WINDOWS_INSTALLER).read_bytes() != (tmp / WINDOWS_INSTALLER).read_bytes():
            raise PackageError("The package's installer differs from its version's.")
        if (clone / WINDOWS_BOOTSTRAP).read_bytes() != (tmp / WINDOWS_BOOTSTRAP).read_bytes():
            raise PackageError("The package's PowerShell installer differs from its version's.")
