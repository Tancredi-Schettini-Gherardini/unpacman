"""Packages to send (package.py), and the installer that installs from them.

The installer is the real `install-unpacman.sh`, run on throwaway
repositories into a throwaway home folder. Only uv and the new copy's Python
are stand-ins (a `uv sync` that makes a `.venv/bin/python` which can make an
icon), so nothing is downloaded and these take seconds; what is tested is the
installer's own judgement — where unpacman comes from, when a copy is moved
forward and when it is left alone, how an open unpacman is closed first, and
that every copy then follows the public repository (a local stand-in for it
here, UNPACMAN_FOLLOW): a packaged one, and one made before it was public.
"""

from __future__ import annotations

import datetime as dt
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from deslopify.app import package, updates

REAL_INSTALLER = Path(__file__).resolve().parents[2] / "install-unpacman.sh"
DAY = dt.date(2026, 10, 7)


def git(cwd: Path, *args: str, branch: str = "mac") -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                           "-c", f"init.defaultBranch={branch}", *args],
                          cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def release(src: Path, subject: str) -> str:
    (src / "VERSION").write_text(subject + "\n")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", subject)
    return git(src, "rev-parse", "HEAD")


@pytest.fixture
def src(tmp_path) -> Path:
    """A stand-in for the public repository: the real installer, and what a
    copy ignores, on a branch called mac."""
    root = tmp_path / "src"
    root.mkdir()
    git(root, "init", "-q")
    shutil.copy2(REAL_INSTALLER, root / "install-unpacman.sh")
    (root / ".gitignore").write_text("var/\n.venv/\npapers/\n")
    (root / "README.md").write_text("unpacman\n")
    release(root, "First version")
    return root


# --------------------------------------------------------------------------
# making a package
# --------------------------------------------------------------------------


def test_a_package_holds_the_committed_branch_and_how_to_install_it(src, tmp_path):
    sha = git(src, "rev-parse", "HEAD")
    (src / "README.md").write_text("not committed\n")          # never packaged
    made = package.make_package(src, tmp_path / "out", today=DAY)
    assert made.name == "unpacman-2026-10-07.zip"
    with zipfile.ZipFile(made) as zf:
        names = sorted(zf.namelist())
        assert names == ["unpacman-2026-10-07/", "unpacman-2026-10-07/READ ME FIRST.txt",
                         "unpacman-2026-10-07/install-unpacman.sh",
                         "unpacman-2026-10-07/package.json",
                         "unpacman-2026-10-07/unpacman.bundle"]
        mode = zf.getinfo("unpacman-2026-10-07/install-unpacman.sh").external_attr >> 16
        assert mode & 0o111                                      # executable
        assert zf.read("unpacman-2026-10-07/install-unpacman.sh") == REAL_INSTALLER.read_bytes()
        info = json.loads(zf.read("unpacman-2026-10-07/package.json"))
        assert info["commit"] == sha and info["made"] == "2026-10-07"
        text = zf.read("unpacman-2026-10-07/READ ME FIRST.txt").decode()
        assert "bash ~/Downloads/unpacman-2026-10-07/install-unpacman.sh" in text
    # Made again the same day, it does not overwrite the first.
    again = package.make_package(src, tmp_path / "out", today=DAY)
    assert again.name == "unpacman-2026-10-07-2.zip" and made.is_file()


def test_no_package_without_the_branch_or_its_installer(src, tmp_path):
    with pytest.raises(package.PackageError):
        package.make_package(src, tmp_path / "out", branch="nonexistent")
    git(src, "rm", "-q", "install-unpacman.sh")
    git(src, "commit", "-q", "-m", "no installer")
    with pytest.raises(package.PackageError, match="no install-unpacman.sh"):
        package.make_package(src, tmp_path / "out", today=DAY)


def test_whether_the_packaged_version_is_on_github_is_said(src, tmp_path):
    assert package.on_github(src) is None                      # follows no remote
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(src, "remote", "add", "origin", str(origin))
    git(src, "push", "-q", "origin", "mac")
    git(src, "fetch", "-q", "origin")
    assert package.on_github(src) is True
    release(src, "Not pushed")
    assert package.on_github(src) is False


# --------------------------------------------------------------------------
# installing from it, with the real installer
# --------------------------------------------------------------------------


needs_mac = pytest.mark.skipif(
    sys.platform != "darwin"
    or subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0,
    reason="the installer is for Macs with Apple's command-line tools")


@pytest.fixture
def home(tmp_path) -> Path:
    """A home folder, and stand-ins for uv and the copy's Python."""
    home = tmp_path / "home"
    fake = tmp_path / "fakebin"
    fake.mkdir(parents=True)
    (home / "Downloads").mkdir(parents=True)
    python = r"""#!/bin/bash
# Stand-in for the copy's Python: only the icon is made — and where from is
# noted, since unpacman finds its folder from there first (config.find_root).
if [ "$3" = "--make-app" ]; then mkdir -p "$5/Unpacman.app"; pwd -P > "$5/made-from"; fi
exit 0
"""
    (fake / "uv").write_text("#!/bin/bash\n"
                             '[ "$1" = sync ] || exit 0\n'
                             "mkdir -p .venv/bin\n"
                             f"cat > .venv/bin/python <<'PY'\n{python}PY\n"
                             "chmod 755 .venv/bin/python\n")
    (fake / "uv").chmod(0o755)
    return home


def install(home: Path, script: Path, **env: str) -> subprocess.CompletedProcess[str]:
    fake = home.parent / "fakebin"
    return subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, timeout=180,
        env={"HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin",
             "TERM": "dumb", "UNPACMAN_NO_OPEN": "1",
             "UNPACMAN_APPS": str(home / "Applications"), **env})


def unpack(made: Path, home: Path) -> Path:
    with zipfile.ZipFile(made) as zf:
        zf.extractall(home / "Downloads")
    return home / "Downloads" / made.stem / "install-unpacman.sh"


def public(tmp_path: Path, src: Path, *branches: str) -> Path:
    """A stand-in for unpacman's public repository on GitHub, holding `branches`
    of `src` as they are now."""
    github = tmp_path / "github.git"
    if not github.exists():
        git(tmp_path, "init", "-q", "--bare", str(github))
        # Its default branch, as on GitHub: the front page, which no copy follows.
        git(github, "symbolic-ref", "HEAD", "refs/heads/main")
    for branch in branches or ("mac",):
        git(src, "push", "-q", "--force", str(github), f"HEAD:refs/heads/{branch}")
    return github


@needs_mac
def test_a_package_installs_without_github_then_follows_it(src, home, tmp_path):
    sha = git(src, "rev-parse", "HEAD")
    script = unpack(package.make_package(src, tmp_path / "out", today=DAY), home)
    # Nothing is fetched from GitHub while installing: it is only named.
    done = install(home, script)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "From the package beside this installer" in done.stdout
    copy = home / ".unpacman"
    assert git(copy, "rev-parse", "HEAD") == sha
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "mac"
    assert git(copy, "remote", "get-url", "origin") == package.PUBLIC
    assert git(copy, "config", "--get-all", "remote.origin.fetch") == \
        "+refs/heads/mac:refs/remotes/origin/mac"
    assert git(copy, "rev-parse", "--abbrev-ref", "mac@{u}") == "origin/mac"
    assert not (copy / "var" / "package").exists()
    assert json.loads((copy / "var/unpacman-package.json").read_text())["commit"] == sha
    assert (home / "Applications" / "Unpacman.app").is_dir()
    # Made from inside the copy, though the installer ran elsewhere (here, the
    # repository the tests run from, which has an atelier.toml of its own).
    assert (home / "Applications" / "made-from").read_text().strip() == str(copy.resolve())
    assert "It tells you when a new version is ready" in done.stdout
    # The app then looks for new versions there, as any copy does.
    status = updates.measure(copy)
    assert status.package is None and status.why_not == "" and status.behind == 0


@needs_mac
def test_a_packaged_copy_sees_a_new_version_published_after_it(src, home, tmp_path):
    github = public(tmp_path, src)
    script = unpack(package.make_package(src, tmp_path / "out", today=DAY), home)
    done = install(home, script, UNPACMAN_FOLLOW=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    copy = home / ".unpacman"
    assert git(copy, "remote", "get-url", "origin") == f"file://{github}"
    new = release(src, "Published later")
    public(tmp_path, src)
    # What the page's banner reads (updates.Updater.refresh): fetched, then measured.
    assert updates.fetch(copy) == ""
    status = updates.measure(copy)
    assert status.behind == 1 and status.to_dict()["available"] is True
    assert [n["subject"] for n in status.news] == ["Published later"]
    assert git(copy, "rev-parse", "origin/mac") == new
    assert git(copy, "rev-parse", "HEAD") != new           # taken in only when asked


@needs_mac
def test_a_newer_package_moves_the_copy_forward_and_keeps_its_papers(src, home, tmp_path):
    install(home, unpack(package.make_package(src, tmp_path / "out", today=DAY), home))
    copy = home / ".unpacman"
    (copy / "papers").mkdir()
    (copy / "papers" / "mine.pdf").write_text("a paper")
    new = release(src, "Second version")
    later = package.make_package(src, tmp_path / "out", today=DAY + dt.timedelta(days=3))
    done = install(home, unpack(later, home))
    assert done.returncode == 0, done.stdout + done.stderr
    assert git(copy, "rev-parse", "HEAD") == new
    assert json.loads((copy / "var/unpacman-package.json").read_text())["commit"] == new
    assert (copy / "papers" / "mine.pdf").read_text() == "a paper"


@needs_mac
def test_a_copy_with_edits_of_its_own_is_left_alone(src, home, tmp_path):
    install(home, unpack(package.make_package(src, tmp_path / "out", today=DAY), home))
    copy = home / ".unpacman"
    before = git(copy, "rev-parse", "HEAD")
    (copy / "README.md").write_text("changed by hand\n")
    release(src, "Second version")
    done = install(home, unpack(package.make_package(
        src, tmp_path / "out", today=DAY + dt.timedelta(days=1)), home))
    assert done.returncode == 0 and "left as it is" in done.stdout
    assert git(copy, "rev-parse", "HEAD") == before
    assert json.loads((copy / "var/unpacman-package.json").read_text())["commit"] == before


class FakeUnpacman:
    """A running unpacman, as far as the installer can tell: it answers
    /api/hello, and /api/quit with 200 (then stops answering) or 409."""

    def __init__(self, copy: Path, *, refuse: bool) -> None:
        state = {"up": True, "quits": 0}
        self.state = state

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                ok = self.path == "/api/hello" and state["up"]
                self.send_response(200 if ok else 404)
                self.end_headers()
                self.wfile.write(json.dumps({"app": "unpacman", "root": str(copy)}).encode())

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                state["quits"] += 1
                if refuse:
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b'{"detail": "1 reading is running, and quitting stops it."}')
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"stopping": true}')
                    state["up"] = False

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        record = copy / "var" / "unpacman-server.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({
            "pid": os.getpid(), "url": f"http://127.0.0.1:{self.server.server_port}/"}))

    def stop(self) -> None:
        self.server.shutdown()


@needs_mac
@pytest.mark.parametrize("reading", [False, True])
def test_an_open_unpacman_is_closed_for_the_update_unless_a_reading_runs(
        src, home, tmp_path, reading):
    install(home, unpack(package.make_package(src, tmp_path / "out", today=DAY), home))
    copy = home / ".unpacman"
    before = git(copy, "rev-parse", "HEAD")
    new = release(src, "Second version")
    running = FakeUnpacman(copy, refuse=reading)
    try:
        done = install(home, unpack(package.make_package(
            src, tmp_path / "out", today=DAY + dt.timedelta(days=1)), home))
    finally:
        running.stop()
    assert done.returncode == 0, done.stdout + done.stderr
    assert running.state["quits"] == 1
    if reading:
        assert "A reading is running" in done.stdout
        assert git(copy, "rev-parse", "HEAD") == before
    else:
        assert "closed while it is updated" in done.stdout
        assert git(copy, "rev-parse", "HEAD") == new


@needs_mac
def test_a_copy_following_an_older_package_switches_to_github_keeping_everything(
        src, home, tmp_path):
    install(home, unpack(package.make_package(src, tmp_path / "out", today=DAY), home))
    copy = home / ".unpacman"
    (copy / "papers").mkdir()
    (copy / "papers" / "mine.pdf").write_text("a paper")
    # As an installer before unpacman was public left it: following a kept bundle.
    kept = copy / "var" / "package" / "unpacman.bundle"
    kept.parent.mkdir(parents=True)
    git(src, "bundle", "create", "-q", str(kept), "mac")
    git(copy, "remote", "set-url", "origin", str(kept))
    assert updates.measure(copy).package is not None        # it says so on the page
    # "GitHub", now public: the same history, moved on.
    new = release(src, "Released in public")
    github = public(tmp_path, src)
    done = install(home, src / "install-unpacman.sh", UNPACMAN_REPO=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    assert git(copy, "remote", "get-url", "origin") == f"file://{github}"
    assert git(copy, "rev-parse", "HEAD") == new
    assert not (copy / "var" / "package").exists()
    assert (copy / "papers" / "mine.pdf").read_text() == "a paper"
    assert updates.measure(copy).package is None


def private_copy(tmp_path: Path, home: Path) -> tuple[Path, Path]:
    """A copy as it was installed before unpacman was public: another history
    (the private repository's, on its branch unpackman), with a file the
    public versions leave out, papers of its own, and gh's sign-in for git."""
    private = tmp_path / "private"
    private.mkdir()
    git(private, "init", "-q", branch="unpackman")
    (private / ".gitignore").write_text("var/\n.venv/\npapers/\n")
    (private / "CLAUDE.md").write_text("notes\n")
    git(private, "add", "-A")
    git(private, "commit", "-q", "-m", "Private version")
    copy = home / ".unpacman"
    git(tmp_path, "clone", "-q", "--branch", "unpackman", str(private), str(copy))
    (copy / "papers").mkdir()
    (copy / "papers" / "mine.pdf").write_text("a paper")
    git(copy, "config", "--local", "--add", "credential.https://github.com.helper", "!gh")
    return private, copy


@needs_mac
def test_a_copy_from_before_unpacman_was_public_moves_to_it_keeping_everything(
        src, home, tmp_path):
    _, copy = private_copy(tmp_path, home)
    github = public(tmp_path, src, "mac", "windows")
    sha = git(src, "rev-parse", "HEAD")
    done = install(home, unpack(package.make_package(src, tmp_path / "out", today=DAY), home),
                   UNPACMAN_FOLLOW=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "From now on it follows unpacman's public version" in done.stdout
    assert git(copy, "rev-parse", "HEAD") == sha
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "mac"
    assert git(copy, "branch", "--list") == "* mac"
    assert git(copy, "for-each-ref", "--format=%(refname)", "refs/remotes") == \
        "refs/remotes/origin/mac"
    assert git(copy, "remote", "get-url", "origin") == f"file://{github}"
    assert not (copy / "CLAUDE.md").exists()                  # not in the public version
    assert (copy / "papers" / "mine.pdf").read_text() == "a paper"
    assert "credential" not in git(copy, "config", "--local", "--list")
    assert updates.measure(copy).why_not == ""
    git(copy, "fetch", "-q", "origin")                    # its branch only, from now on
    assert git(copy, "for-each-ref", "--format=%(refname)", "refs/remotes") == \
        "refs/remotes/origin/mac"


@needs_mac
def test_a_copy_from_before_with_commits_of_its_own_is_left_at_its_version(
        src, home, tmp_path):
    private, copy = private_copy(tmp_path, home)
    (copy / "mine.txt").write_text("my change\n")
    git(copy, "add", "mine.txt")
    git(copy, "commit", "-q", "-m", "Mine")
    before = git(copy, "rev-parse", "HEAD")
    done = install(home, unpack(package.make_package(src, tmp_path / "out", today=DAY), home))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "It has changes of its own, so it is left at its version" in done.stdout
    assert git(copy, "rev-parse", "HEAD") == before
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "unpackman"
    assert git(copy, "remote", "get-url", "origin") == str(private)


@needs_mac
def test_an_older_package_leaves_a_newer_copy_as_it_is(src, home, tmp_path):
    older = package.make_package(src, tmp_path / "out", today=DAY)
    new = release(src, "Second version")
    install(home, unpack(package.make_package(
        src, tmp_path / "out", today=DAY + dt.timedelta(days=1)), home))
    copy = home / ".unpacman"
    assert git(copy, "rev-parse", "HEAD") == new
    done = install(home, unpack(older, home))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "It is newer than this version" in done.stdout
    assert git(copy, "rev-parse", "HEAD") == new
    assert updates.measure(copy).ahead == 0                  # nothing claimed as its own


@needs_mac
def test_a_copy_from_github_holds_only_its_own_branch(src, home, tmp_path):
    github = public(tmp_path, src, "mac")
    # The public repository's other branches: its front page, the Windows version.
    (src / "unpacman-mac.zip").write_bytes(b"0" * 100_000)
    release(src, "Front page")
    public(tmp_path, src, "main", "windows")
    git(src, "reset", "-q", "--hard", "HEAD~1")
    done = install(home, src / "install-unpacman.sh", UNPACMAN_REPO=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    copy = home / ".unpacman"
    assert git(copy, "rev-parse", "HEAD") == git(src, "rev-parse", "HEAD")
    assert git(copy, "for-each-ref", "--format=%(refname)", "refs/remotes") == \
        "refs/remotes/origin/mac"
    assert not (copy / "unpacman-mac.zip").exists()
    git(copy, "fetch", "-q", "origin")                    # still that branch only
    assert git(copy, "for-each-ref", "--format=%(refname)", "refs/remotes") == \
        "refs/remotes/origin/mac"


# --------------------------------------------------------------------------
# a package for a release, and the Windows one
# --------------------------------------------------------------------------


def test_a_release_package_keeps_its_name_for_its_folder_too(src, tmp_path):
    made = package.make_package(src, tmp_path / "out", today=DAY, name="unpacman-mac")
    assert made.name == "unpacman-mac.zip"
    with zipfile.ZipFile(made) as zf:
        assert "unpacman-mac/install-unpacman.sh" in zf.namelist()
        text = zf.read("unpacman-mac/READ ME FIRST.txt").decode()
    # The line the website gives, whatever the version.
    assert "bash ~/Downloads/unpacman-mac/install-unpacman.sh" in text
    assert "UPDATES come by themselves" in text and package.SUPPORT in text
    new = release(src, "Second version")
    again = package.make_package(src, tmp_path / "out", today=DAY, name="unpacman-mac")
    assert again == made                                       # replaced, same address
    with zipfile.ZipFile(again) as zf:
        assert json.loads(zf.read("unpacman-mac/package.json"))["commit"] == new


def test_the_support_address_is_unpacmans_feedback_address():
    from deslopify.app import server

    assert package.SUPPORT == server.FEEDBACK_TO


@pytest.fixture
def windows_src(src) -> Path:
    """The stand-in repository with a windows branch holding a WSL installer."""
    git(src, "checkout", "-q", "-b", "windows")
    (src / package.WINDOWS_INSTALLER).write_text("#!/bin/bash\necho wsl installer\n")
    (src / package.WINDOWS_BOOTSTRAP).write_text("Write-Host 'bootstrap'\n")
    release(src, "Windows version")
    git(src, "checkout", "-q", "mac")
    return src


def test_a_windows_package_holds_the_windows_branch_at_its_top(windows_src, tmp_path):
    sha = git(windows_src, "rev-parse", "windows")
    made = package.make_windows_package(windows_src, tmp_path / "out", today=DAY)
    assert made.name == "unpacman-windows-2026-10-07.zip"
    with zipfile.ZipFile(made) as zf:
        assert sorted(zf.namelist()) == sorted([
            "READ ME FIRST.txt", "install.sh", package.WINDOWS_INSTALLER, "package.json",
            package.WINDOWS_BOOTSTRAP, package.WINDOWS_LAUNCHER, package.BUNDLE])
        for name in ("install.sh", package.WINDOWS_INSTALLER):
            assert (zf.getinfo(name).external_attr >> 16) & 0o111
        committed = subprocess.run(["git", "show", f"windows:{package.WINDOWS_INSTALLER}"],
                                   cwd=windows_src, capture_output=True, check=True).stdout
        assert zf.read(package.WINDOWS_INSTALLER) == committed
        readme = zf.read("READ ME FIRST.txt").decode()
        start = zf.read("install.sh").decode()
        info = json.loads(zf.read("package.json"))
    assert "\r\n" in readme and "unpacman-windows-2026-10-07" in readme
    assert "Install unpacman.cmd" in readme and "UPDATES come by themselves" in readme
    assert ".unpacman-package" not in readme
    assert 'UNPACMAN_BRANCH="windows"' in start
    assert f'UNPACMAN_FOLLOW="${{UNPACMAN_FOLLOW:-{package.PUBLIC}}}"' in start
    assert info["commit"] == sha and info["branch"] == "windows"
    named = package.make_windows_package(windows_src, tmp_path / "out", today=DAY,
                                         name="unpacman-windows")
    with zipfile.ZipFile(named) as zf:
        assert "Open the folder unpacman-windows " in zf.read("READ ME FIRST.txt").decode()


def test_no_windows_package_without_its_installer(src, tmp_path):
    git(src, "checkout", "-q", "-b", "windows")
    with pytest.raises(package.PackageError, match="no install-unpacman-wsl.sh"):
        package.make_windows_package(src, tmp_path / "out", today=DAY)


@needs_mac
def test_the_windows_package_starts_its_installer_from_itself_then_github(
        windows_src, tmp_path):
    made = package.make_windows_package(windows_src, tmp_path / "out", today=DAY)
    folder = tmp_path / "Downloads" / "unpacman windows"         # a space, as Windows has
    with zipfile.ZipFile(made) as zf:
        zf.extractall(folder)
    # A stand-in for the WSL installer, and for Linux's uname.
    (folder / package.WINDOWS_INSTALLER).write_text(
        '#!/bin/bash\nprintf "%s|%s|%s" "$UNPACMAN_REPO" "$UNPACMAN_BRANCH" "$UNPACMAN_FOLLOW"\n')
    fake = tmp_path / "linux"
    fake.mkdir()
    (fake / "uname").write_text("#!/bin/bash\necho Linux\n")
    (fake / "uname").chmod(0o755)
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)}
    done = subprocess.run(["/bin/bash", str(folder / "install.sh")], capture_output=True,
                          text=True, env=env, timeout=30)
    assert done.stdout == f"{folder / package.BUNDLE}|windows|{package.PUBLIC}", done.stderr
    done = subprocess.run(["/bin/bash", str(folder / "install.sh")], capture_output=True,
                          text=True, env=env | {"UNPACMAN_FOLLOW": "file:///elsewhere"},
                          timeout=30)
    assert done.stdout.endswith("|file:///elsewhere")
