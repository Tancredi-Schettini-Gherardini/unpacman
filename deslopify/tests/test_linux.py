"""unpacman on a Linux computer — not Linux inside Windows (linux.py,
2026-11-05): its menu entry, keeping the computer awake, and the line that
installs what that Linux lacks; and the installer's way there
(install-unpacman-linux.sh, then install-unpacman-wsl.sh on a Linux computer).
Linux is simulated: these run anywhere, with stand-in programs only — GitHub's
own Linux machines run the real thing (deslopify/public/github/linux.yml).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from deslopify.app import instance, linux, ready
from test_package_windows import git, github, home, release, src  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------- the menu entry

def test_an_exec_argument_is_quoted_as_the_specification_asks():
    assert linux.exec_argument("/home/ada/.unpacman/.venv/bin/python") == \
        "/home/ada/.unpacman/.venv/bin/python"
    assert linux.exec_argument("/home/a b/python") == '"/home/a b/python"'
    # Inside quotes `"`, `` ` ``, `$` and `\` take a backslash — and then every
    # backslash is doubled again, as in any string value.
    assert linux.exec_argument('/x/a"b') == '"/x/a\\\\"b"'
    assert linux.exec_argument("/x/$HOME") == '"/x/\\\\$HOME"'
    assert linux.exec_argument("/x/a\\b") == '"/x/a\\\\\\\\b"'
    assert linux.exec_argument("/x/100%") == "/x/100%%"
    with pytest.raises(linux.LinuxAppError):
        linux.exec_argument("/x/two\nlines")


def test_the_menu_entry_opens_this_copy_as_the_macs_icon_does(tmp_path):
    root = tmp_path / "home" / ".unpacman"
    text = linux.desktop_entry(root, root / ".venv" / "bin" / "python",
                               tmp_path / "icons" / "unpacman.svg")
    lines = text.splitlines()
    assert lines[0] == "[Desktop Entry]"
    entry = dict(line.split("=", 1) for line in lines[1:])
    assert entry["Type"] == "Application" and entry["Name"] == "Unpacman"
    assert entry["Exec"] == f"{root}/.venv/bin/python -m deslopify.app --app"
    assert entry["Path"] == str(root)
    assert entry["Icon"] == str(tmp_path / "icons" / "unpacman.svg")
    assert entry["Terminal"] == "false"
    assert entry["Categories"].endswith(";")


def test_the_menu_entry_is_made_with_its_icon_and_marks_the_copy(tmp_path, monkeypatch):
    share = tmp_path / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(share))
    monkeypatch.setattr(linux.shutil, "which", lambda name, *a, **k: None)
    root = tmp_path / "copy"
    (root / "var").mkdir(parents=True)
    made = linux.make_app(root, python=root / ".venv" / "bin" / "python")
    assert made == share / "applications" / "unpacman.desktop"
    text = made.read_text()
    assert f"Exec={root.resolve()}/.venv/bin/python -m deslopify.app --app" in text
    icon = share / "icons" / "hicolor" / "scalable" / "apps" / "unpacman.svg"
    assert icon.read_bytes() == linux.ICON.read_bytes()
    assert f"Icon={icon}" in text
    assert oct(made.stat().st_mode & 0o777) == "0o644"
    # Made again: replaced, nothing left beside it.
    linux.make_app(root, python=root / ".venv" / "bin" / "python")
    assert sorted(p.name for p in (share / "applications").iterdir()) == ["unpacman.desktop"]
    # The copy now looks for new versions by itself, as one with an icon does.
    marker = json.loads(instance.app_marker(root.resolve()).read_text())
    assert marker["app"] == str(made)


def test_a_relative_data_home_is_not_one(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "relative/share")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert linux.data_home() == tmp_path / ".local" / "share"


def test_the_command_line_makes_the_menu_entry_on_linux(tmp_path, monkeypatch):
    from deslopify.app import __main__ as cli

    made = []
    monkeypatch.setattr(ready, "platform_name", lambda: "linux")
    monkeypatch.setattr(linux, "make_app", lambda root, python=None: made.append(root)
                        or tmp_path / "unpacman.desktop")
    assert cli.main(["--make-app"]) == 0
    assert len(made) == 1


# ------------------------------------------------------------- keeping awake

def test_without_systemd_nothing_is_held(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", lambda name, *a, **k: None)
    assert linux.keep_awake() is None


def test_the_lock_on_sleep_watches_unpacman(monkeypatch):
    started = []

    class Started:
        def __init__(self, argv, **kwargs):
            started.append(argv)

    monkeypatch.setattr(linux.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    monkeypatch.setattr(linux.subprocess, "Popen", Started)
    assert linux.keep_awake() is not None
    argv = started[0]
    assert argv[0] == "/usr/bin/systemd-inhibit" and "--what=idle:sleep" in argv
    assert "--mode=block" in argv
    # The program it holds the lock around ends when this process does.
    assert argv[-1] == f"while kill -0 {instance.os.getpid()} 2>/dev/null; do sleep 5; done"


def test_a_linux_computer_is_kept_awake_by_systemd(monkeypatch):
    from deslopify.app import wsl

    monkeypatch.setattr(instance.sys, "platform", "linux")
    monkeypatch.setattr(wsl, "is_wsl", lambda: False)
    monkeypatch.setattr(linux, "keep_awake", lambda: "the lock")
    assert instance.KeepAwake._start() == "the lock"
    monkeypatch.setattr(wsl, "is_wsl", lambda: True)
    monkeypatch.setattr(wsl, "keep_awake", lambda: "Windows' own")
    assert instance.KeepAwake._start() == "Windows' own"


# ------------------------------------------------- what this Linux lacks

def test_each_package_manager_has_its_own_line():
    tools = ["bwrap", "socat", "rg", "xz", "pdftoppm"]
    assert linux.install_command(tools, "apt") == \
        "sudo apt install bubblewrap socat ripgrep xz-utils poppler-utils"
    assert linux.install_command(tools, "dnf") == \
        "sudo dnf install bubblewrap socat ripgrep xz poppler-utils"
    assert linux.install_command(tools, "pacman") == \
        "sudo pacman -S --needed bubblewrap socat ripgrep xz poppler"
    assert linux.install_command(tools, "zypper") == \
        "sudo zypper install bubblewrap socat ripgrep xz poppler-tools"


def test_the_package_manager_is_the_one_this_linux_has(monkeypatch):
    have: set[str] = set()
    monkeypatch.setattr(linux.shutil, "which",
                        lambda name, *a, **k: f"/usr/bin/{name}" if name in have else None)
    assert linux.package_manager() == "apt"           # none found: Ubuntu's
    have.add("pacman")
    assert linux.package_manager() == "pacman"
    have.add("apt-get")
    assert linux.package_manager() == "apt"


# ------------------------------------------- the installer, on a Linux computer

def _install(home: Path, script: Path, **env: str) -> subprocess.CompletedProcess[str]:
    fake = home.parent / "fakebin"
    return subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, timeout=180,
        env={"HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb",
             "UNPACMAN_NO_APT": "1", **env})


def _record_python(home: Path) -> Path:
    """The copy's Python, as uv makes it here: one that writes down what it is
    asked to do, and from where."""
    log = home.parent / "python-calls"
    (home.parent / "fakebin" / "uv").write_text(
        "#!/bin/bash\n"
        '[ "$1" = sync ] || exit 0\n'
        "mkdir -p .venv/bin\n"
        f"printf '#!/bin/bash\\necho \"$(pwd -P) $*\" >> {log}\\nexit 0\\n' > .venv/bin/python\n"
        "chmod 755 .venv/bin/python\n")
    return log


def test_on_a_linux_computer_it_makes_the_menu_entry_and_opens_in_the_background(
        src, github, home, tmp_path):
    # As the public branch `linux` is: the same installer, and its entry point.
    for name in ("install-unpacman-wsl.sh", "install-unpacman-linux.sh"):
        (src / name).write_bytes((ROOT / name).read_bytes())
        (src / name).chmod(0o755)
    release(src, "With the Linux entry point")
    git(src, "push", "-q", str(github), "HEAD:refs/heads/linux")
    log = _record_python(home)
    done = _install(home, src / "install-unpacman-linux.sh", UNPACMAN_REPO=f"file://{github}")
    assert done.returncode == 0, done.stdout + done.stderr
    copy = home / ".unpacman"
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "linux"
    assert git(copy, "for-each-ref", "--format=%(refname)", "refs/remotes") == \
        "refs/remotes/origin/linux"
    calls = log.read_text().splitlines()
    here = str(copy.resolve())
    assert calls == [f"{here} -m deslopify.app --make-app", f"{here} -m deslopify.app --app"]
    assert "applications menu" in done.stdout and "Start menu" not in done.stdout
    assert "Ubuntu" not in done.stdout


def test_the_entry_point_takes_its_installer_from_github_when_alone(tmp_path):
    """Run from the one line, nothing is beside it: it fetches the installer of
    its branch (here a stand-in `curl` says what was asked) — or says plainly it
    could not."""
    fake = tmp_path / "bin"
    fake.mkdir()
    asked = tmp_path / "asked"
    (fake / "curl").write_text(f'#!/bin/bash\necho "$@" > {asked}\n'
                               "echo 'echo installer ran with \"$UNPACMAN_BRANCH\"'\n")
    (fake / "curl").chmod(0o755)
    script = (ROOT / "install-unpacman-linux.sh").read_text()
    done = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True,
                          timeout=60, env={"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "installer ran with linux"
    assert asked.read_text().split()[-1] == ("https://raw.githubusercontent.com/"
                                             "Tancredi-Schettini-Gherardini/unpacman/linux/"
                                             "install-unpacman-wsl.sh")
    (fake / "curl").write_text("#!/bin/bash\nexit 22\n")
    # Nor wget, which a Linux computer may have (GitHub's do).
    (fake / "wget").write_text("#!/bin/bash\nexit 8\n")
    (fake / "wget").chmod(0o755)
    done = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True,
                          timeout=60, env={"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)})
    assert done.returncode == 1 and "could not be downloaded" in done.stderr


def test_the_installer_is_valid_bash():
    for name in ("install-unpacman-linux.sh", "install-unpacman-wsl.sh"):
        done = subprocess.run(["/bin/bash", "-n", str(ROOT / name)], capture_output=True,
                              text=True)
        assert done.returncode == 0, done.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="needs Linux's own desktop-file-validate")
def test_the_menu_entry_is_valid_where_it_can_be_checked(tmp_path):
    import shutil

    validate = shutil.which("desktop-file-validate")
    if not validate:
        pytest.skip("desktop-file-validate is not installed")
    entry = tmp_path / "unpacman.desktop"
    entry.write_text(linux.desktop_entry(tmp_path / "a b", tmp_path / "a b" / "python",
                                         tmp_path / "unpacman.svg"))
    done = subprocess.run([validate, str(entry)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
