"""The Windows package and its installer, `install-unpacman-wsl.sh`, as the
Mac's are tested in test_package.py: the real installer run on throwaway
repositories into a throwaway home folder — here on any Unix, with stand-ins
for uv, the copy's Python and Linux's `uname`, and the installer's own test
switches (no apt, no Start-menu shortcut, nothing opened). What is tested is
its judgement: where unpacman comes from, that the copy then follows the
public repository (a local stand-in, UNPACMAN_FOLLOW), and that a copy made
before unpacman was public moves to it only when it has nothing of its own.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from deslopify.app import package, updates

REAL_INSTALLER = Path(__file__).resolve().parents[2] / package.WINDOWS_INSTALLER


def git(cwd: Path, *args: str, branch: str = "windows") -> str:
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
    """A stand-in for the public repository's windows branch: the real WSL
    installer, and what a copy ignores."""
    root = tmp_path / "src"
    root.mkdir()
    git(root, "init", "-q")
    (root / package.WINDOWS_INSTALLER).write_bytes(REAL_INSTALLER.read_bytes())
    (root / ".gitignore").write_text("var/\n.venv/\npapers/\n")
    release(root, "First version")
    return root


@pytest.fixture
def github(tmp_path, src) -> Path:
    """The public repository, its default branch the front page, as on GitHub."""
    bare = tmp_path / "github.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    git(src, "push", "-q", str(bare), "HEAD:refs/heads/windows")
    return bare


@pytest.fixture
def home(tmp_path) -> Path:
    """A home folder, and stand-ins for uv, the copy's Python and Linux."""
    home = tmp_path / "home"
    fake = tmp_path / "fakebin"
    fake.mkdir(parents=True)
    home.mkdir()
    (fake / "uv").write_text("#!/bin/bash\n"
                             '[ "$1" = sync ] || exit 0\n'
                             "mkdir -p .venv/bin\n"
                             "printf '#!/bin/bash\\n[ \"$3\" = --make-app ] && pwd -P > made-from\\nexit 0\\n'"
                             " > .venv/bin/python\n"
                             "chmod 755 .venv/bin/python\n")
    (fake / "uname").write_text('#!/bin/bash\ncase "$1" in -m) echo x86_64 ;; *) echo Linux ;; esac\n')
    for name in ("uv", "uname"):
        (fake / name).chmod(0o755)
    return home


def run(home: Path, script: Path, **env: str) -> subprocess.CompletedProcess[str]:
    fake = home.parent / "fakebin"
    return subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, timeout=180,
        env={"HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin",
             "TERM": "dumb", "UNPACMAN_NO_OPEN": "1", "UNPACMAN_NO_APT": "1",
             "UNPACMAN_NO_SHORTCUT": "1", **env})


def unpack(made: Path, where: Path) -> Path:
    folder = where / made.stem                 # as Windows' Extract All names it
    with zipfile.ZipFile(made) as zf:
        zf.extractall(folder)
    return folder / "install.sh"


def test_the_windows_package_installs_then_follows_github(src, github, home, tmp_path):
    sha = git(src, "rev-parse", "HEAD")
    made = package.make_windows_package(src, tmp_path / "out", name="unpacman-windows")
    done = run(home, unpack(made, tmp_path / "Downloads"), UNPACMAN_FOLLOW=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    copy = home / ".unpacman"
    assert git(copy, "rev-parse", "HEAD") == sha
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "windows"
    assert git(copy, "remote", "get-url", "origin") == f"file://{github}"
    assert git(copy, "config", "--get-all", "remote.origin.fetch") == \
        "+refs/heads/windows:refs/remotes/origin/windows"
    assert "It tells you when a new version is ready" in done.stdout
    # A new version, published: what the page's banner reads.
    new = release(src, "Published later")
    git(src, "push", "-q", str(github), "HEAD:refs/heads/windows")
    assert updates.fetch(copy) == ""
    status = updates.measure(copy)
    assert status.behind == 1 and status.to_dict()["available"] is True
    assert git(copy, "rev-parse", "origin/windows") == new
    # Left as it is by default, the package names the public repository.
    made = package.make_windows_package(src, tmp_path / "out", name="unpacman-windows-2")
    other = tmp_path / "home2"                             # the same stand-ins
    other.mkdir()
    done = run(other, unpack(made, tmp_path / "Downloads"))
    assert done.returncode == 0, done.stdout + done.stderr
    assert git(other / ".unpacman", "remote", "get-url", "origin") == package.PUBLIC


def private_copy(tmp_path: Path, home: Path) -> Path:
    """A Windows copy as installed before unpacman was public: the private
    history (branch unpacman_windows), following the package kept in
    ~/.unpacman-package, with papers of its own."""
    private = tmp_path / "private"
    private.mkdir()
    git(private, "init", "-q", branch="unpacman_windows")
    (private / ".gitignore").write_text("var/\n.venv/\npapers/\n")
    (private / "WINDOWS.md").write_text("notes\n")
    git(private, "add", "-A")
    git(private, "commit", "-q", "-m", "Private version")
    kept = home / ".unpacman-package" / "unpacman.bundle"
    kept.parent.mkdir(parents=True)
    git(private, "bundle", "create", "-q", str(kept), "unpacman_windows")
    copy = home / ".unpacman"
    git(tmp_path, "clone", "-q", "--branch", "unpacman_windows", str(kept), str(copy))
    (copy / "papers").mkdir()
    (copy / "papers" / "mine.pdf").write_text("a paper")
    return copy


def test_a_windows_copy_from_before_moves_to_the_public_version(src, github, home, tmp_path):
    copy = private_copy(tmp_path, home)
    made = package.make_windows_package(src, tmp_path / "out", name="unpacman-windows")
    done = run(home, unpack(made, tmp_path / "Downloads"), UNPACMAN_FOLLOW=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "From now on it follows unpacman's public version" in done.stdout
    assert git(copy, "rev-parse", "HEAD") == git(src, "rev-parse", "HEAD")
    assert git(copy, "branch", "--list") == "* windows"
    assert git(copy, "for-each-ref", "--format=%(refname)", "refs/remotes") == \
        "refs/remotes/origin/windows"
    assert not (copy / "WINDOWS.md").exists()
    assert not (home / ".unpacman-package").exists()           # nothing follows it now
    assert (copy / "papers" / "mine.pdf").read_text() == "a paper"
    assert updates.measure(copy).why_not == ""


def test_a_windows_copy_with_commits_of_its_own_is_left_alone(src, github, home, tmp_path):
    copy = private_copy(tmp_path, home)
    (copy / "mine.txt").write_text("my change\n")
    git(copy, "add", "mine.txt")
    git(copy, "commit", "-q", "-m", "Mine")
    before = git(copy, "rev-parse", "HEAD")
    made = package.make_windows_package(src, tmp_path / "out", name="unpacman-windows")
    done = run(home, unpack(made, tmp_path / "Downloads"), UNPACMAN_FOLLOW=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "It has changes of its own, so it is left at its version" in done.stdout
    assert git(copy, "rev-parse", "HEAD") == before
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "unpacman_windows"


def test_the_windows_installer_from_github_fetches_its_branch_only(src, github, home, tmp_path):
    (src / "unpacman-mac.zip").write_bytes(b"0" * 100_000)
    release(src, "Front page")
    git(src, "push", "-q", str(github), "HEAD:refs/heads/main", "HEAD:refs/heads/mac")
    git(src, "reset", "-q", "--hard", "HEAD~1")
    done = run(home, src / package.WINDOWS_INSTALLER, UNPACMAN_REPO=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    copy = home / ".unpacman"
    assert git(copy, "for-each-ref", "--format=%(refname)", "refs/remotes") == \
        "refs/remotes/origin/windows"
    assert not (copy / "unpacman-mac.zip").exists()


def test_a_windows_copy_with_edits_of_its_own_is_left_as_it_is(src, github, home, tmp_path):
    made = package.make_windows_package(src, tmp_path / "out", name="unpacman-windows")
    run(home, unpack(made, tmp_path / "Downloads"), UNPACMAN_FOLLOW=f"file://{github}")
    copy = home / ".unpacman"
    before = git(copy, "rev-parse", "HEAD")
    (copy / "VERSION").write_text("changed by hand\n")
    release(src, "Second version")
    later = package.make_windows_package(src, tmp_path / "out", name="unpacman-windows-later")
    done = run(home, unpack(later, tmp_path / "Downloads"), UNPACMAN_FOLLOW=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "It has edited files of its own, so it is left as it is" in done.stdout
    assert git(copy, "rev-parse", "HEAD") == before
    assert (copy / "VERSION").read_text() == "changed by hand\n"


def test_the_windows_shortcut_is_made_from_inside_the_copy(src, github, home, tmp_path):
    made = package.make_windows_package(src, tmp_path / "out", name="unpacman-windows")
    # As inside WSL, with a shortcut; the installer is started from elsewhere.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "atelier.toml").write_text("")
    script = unpack(made, tmp_path / "Downloads")
    fake = home.parent / "fakebin"
    done = subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, timeout=180, cwd=elsewhere,
        env={"HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb",
             "UNPACMAN_NO_OPEN": "1", "UNPACMAN_NO_APT": "1", "WSL_DISTRO_NAME": "Ubuntu",
             "UNPACMAN_FOLLOW": f"file://{github}"})
    assert done.returncode == 0, done.stdout + done.stderr
    copy = home / ".unpacman"
    assert (copy / "made-from").read_text().strip() == str(copy.resolve())
