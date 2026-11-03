"""The reading sandbox on Linux — Ubuntu under WSL, on a Windows computer
(atelier/access.py, 2026-10-17, branch unpacman_windows).

These build the sandbox's command and check what goes into it; they run
anywhere, Linux simulated. That bubblewrap then enforces it is shown by the
live tests in test_access.py (UNPACMAN_SANDBOX_TEST=1), on Linux itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from atelier import access


@pytest.fixture
def linux(monkeypatch):
    """Linux, as far as access.py asks."""
    monkeypatch.setattr(access, "_linux", lambda: True)


def options(cmd: list[str]) -> list[tuple[str, ...]]:
    """A bubblewrap command read back as (option, its arguments…)."""
    takes = {"--ro-bind": 2, "--ro-bind-try": 2, "--bind": 2, "--bind-try": 2,
             "--symlink": 2, "--proc": 1, "--dev": 1, "--tmpfs": 1, "--remount-ro": 1}
    out, i = [], 1
    while i < len(cmd) and cmd[i].startswith("--"):
        n = takes.get(cmd[i], 0)
        out.append(tuple(cmd[i:i + 1 + n]))
        i += 1 + n
    return out


def test_linux_runtime_paths_are_the_system_and_this_program_never_home(linux):
    paths = access.runtime_paths()
    assert Path("/usr").resolve() in paths
    assert Path(sys.prefix).resolve() in paths
    for private in (Path.home(), Path("/"), Path("/home"), Path("/mnt"), Path("/root")):
        assert private.resolve() not in paths
    # Of /etc, never the whole folder.
    assert Path("/etc").resolve() not in paths


def test_bubblewrap_is_given_only_what_the_job_needs(linux, tmp_path):
    read, write = tmp_path / "paper", tmp_path / "work"
    read.mkdir()
    write.mkdir()
    cmd = access.bwrap_command(["/bin/echo", "hi"], read=[read], write=[write],
                               bwrap="/fake/bwrap")
    assert cmd[:4] == ["/fake/bwrap", "--die-with-parent", "--new-session", "--unshare-all"]
    assert cmd[-2:] == ["/bin/echo", "hi"]
    said = options(cmd)
    assert ("--share-net",) not in said                    # no network unless asked
    assert ("--tmpfs", "/tmp") in said and ("--proc", "/proc") in said
    assert said[-1] == ("--remount-ro", "/")
    real = lambda p: str(p.resolve())                       # noqa: E731
    assert ("--ro-bind-try", real(read), real(read)) in said
    assert ("--bind-try", real(write), real(write)) in said
    assert ("--ro-bind", "/usr", "/usr") in said or Path("/usr").is_symlink()
    destinations = [o[-1] for o in said if o[0].startswith(("--ro-bind", "--bind"))]
    for private in (str(Path.home()), "/", "/home", "/mnt"):
        assert private not in destinations
    # A file of /etc is given at its own name, wherever it leads; all else
    # where it really is.
    for option in said:
        if option[0] == "--ro-bind" and option[1] != option[2]:
            assert option[2].startswith("/etc/"), option
    # Private /tmp before anything put in it.
    assert said.index(("--tmpfs", "/tmp")) < min(
        i for i, o in enumerate(said) if o[0].endswith("bind-try"))


def test_folders_go_in_before_what_is_in_them_and_writable_wins(linux, tmp_path):
    outer, inner = tmp_path / "run", tmp_path / "run" / "workspace"
    inner.mkdir(parents=True)
    cmd = access.bwrap_command(["/bin/true"], read=[inner, outer], write=[inner],
                               bwrap="/fake/bwrap")
    said = options(cmd)
    at = {o: i for i, o in enumerate(said)}
    o_read = ("--ro-bind-try", str(outer.resolve()), str(outer.resolve()))
    i_read = ("--ro-bind-try", str(inner.resolve()), str(inner.resolve()))
    i_write = ("--bind-try", str(inner.resolve()), str(inner.resolve()))
    assert at[o_read] < at[i_read] < at[i_write]


def test_the_network_only_when_asked(linux, tmp_path):
    cmd = access.bwrap_command(["/bin/true"], read=[], write=[tmp_path], network=True,
                               bwrap="/fake/bwrap")
    assert ("--share-net",) in options(cmd)


def test_the_program_and_the_links_on_its_way_are_given(linux, tmp_path, monkeypatch):
    # ~/.local/bin/claude -> ~/.local/share/claude/versions/2.1.300, as installed.
    versions = tmp_path / "share" / "claude" / "versions"
    versions.mkdir(parents=True)
    (versions / "2.1.300").write_text("#!/bin/sh\n")
    (versions / "2.1.300").chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").symlink_to(versions / "2.1.300")
    cmd = access.bwrap_command([str(bindir / "claude"), "--version"], read=[], write=[],
                               bwrap="/fake/bwrap")
    given = {o[-1] for o in options(cmd) if o[0] == "--ro-bind-try"}
    assert str(bindir.resolve()) in given and str(versions.resolve()) in given


def test_without_bubblewrap_nothing_runs(linux, monkeypatch):
    monkeypatch.setattr(access.shutil, "which", lambda name: None)
    with pytest.raises(access.AccessPolicyError, match="no unrestricted fallback"):
        access.sandbox_command(["echo", "unsafe"], read=[], write=[])


def test_the_whole_computer_is_never_given(linux):
    with pytest.raises(access.AccessPolicyError):
        access.bwrap_command(["/bin/true"], read=[Path("/")], write=[], bwrap="/fake/bwrap")


def test_sandbox_command_is_bubblewrap_on_linux(linux, monkeypatch, tmp_path):
    real_which = access.shutil.which
    monkeypatch.setattr(access.shutil, "which",
                        lambda name, *a, **k: "/fake/bwrap" if name == "bwrap"
                        else real_which(name, *a, **k))
    cmd = access.sandbox_command(["/bin/echo", "x"], read=[], write=[tmp_path])
    assert cmd[0] == "/fake/bwrap" and cmd[-2:] == ["/bin/echo", "x"]


def test_a_job_in_another_mount_namespace_is_inside_its_sandbox(linux, monkeypatch):
    """The kernel's word, as on macOS: the marker alone is not evidence."""
    monkeypatch.setenv("UNPACMAN_RESTRICTED_JOB", "1")
    monkeypatch.setenv("UNPACMAN_HOST_NS", "mnt:[4026531840]")
    monkeypatch.setattr(access, "host_namespace", lambda: "mnt:[4026532999]")
    assert access._inside_job_sandbox()
    # An unsandboxed hook, in the server's own namespace, is not.
    monkeypatch.setattr(access, "host_namespace", lambda: "mnt:[4026531840]")
    assert not access._inside_job_sandbox()
    # Nor anything without the server's word, or without the marker.
    monkeypatch.setattr(access, "host_namespace", lambda: "mnt:[4026532999]")
    monkeypatch.delenv("UNPACMAN_HOST_NS")
    assert not access._inside_job_sandbox()
    monkeypatch.setenv("UNPACMAN_HOST_NS", "mnt:[4026531840]")
    monkeypatch.delenv("UNPACMAN_RESTRICTED_JOB")
    assert not access._inside_job_sandbox()


def test_jobs_are_told_the_servers_namespace_on_linux_only(monkeypatch, tmp_path):
    monkeypatch.setattr(access, "host_namespace", lambda: "mnt:[7]")
    monkeypatch.setattr(access, "_linux", lambda: True)
    _, env = access.protect_provider(["claude"], {}, cwd=tmp_path / "w", state=tmp_path / "s",
                                     readable=[], provider="claude", tools=True)
    assert env["UNPACMAN_HOST_NS"] == "mnt:[7]" and env["UNPACMAN_RESTRICTED_JOB"] == "1"
    shell = dict(v.split("=", 1) for v in access.codex_permissions(tmp_path)[1::2])
    assert 'UNPACMAN_HOST_NS="mnt:[7]"' in shell["shell_environment_policy.set"]
    # A Mac is as it was.
    monkeypatch.setattr(access, "_linux", lambda: False)
    _, env = access.protect_provider(["claude"], {}, cwd=tmp_path / "w2", state=tmp_path / "s2",
                                     readable=[], provider="claude", tools=True)
    assert "UNPACMAN_HOST_NS" not in env
    shell = dict(v.split("=", 1) for v in access.codex_permissions(tmp_path)[1::2])
    assert "UNPACMAN_HOST_NS" not in shell["shell_environment_policy.set"]


def test_bubblewrap_says_why_it_cannot_and_is_tried_again_until_it_can(tmp_path, monkeypatch):
    tried = tmp_path / "tried"
    bwrap = tmp_path / "bwrap"
    bwrap.write_text(f"#!/bin/sh\necho x >> {tried}\n"
                     "echo 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n")
    bwrap.chmod(0o755)
    monkeypatch.setattr(access, "_BWRAP_TRIED", {})
    assert access.bwrap_problem(str(bwrap)) == "bwrap: setting up uid map: Permission denied"
    # Mended (Getting ready says how): seen at the next check, not after a restart.
    assert access.bwrap_problem(str(bwrap)).startswith("bwrap: setting up uid map")
    assert tried.read_text().count("x") == 2
    bwrap.write_text(f"#!/bin/sh\necho x >> {tried}\nexit 0\n")
    assert access.bwrap_problem(str(bwrap)) == ""
    assert access.bwrap_problem(str(bwrap)) == ""          # and then not tried again
    assert tried.read_text().count("x") == 3
    monkeypatch.setattr(access.shutil, "which", lambda name: None)
    assert access.bwrap_problem() == "bubblewrap is not installed"


@pytest.mark.skipif(not sys.platform.startswith("linux") or not os.path.exists("/proc/self/ns/mnt"),
                    reason="reads Linux's own /proc")
def test_this_linux_names_its_mount_namespace():
    assert access.host_namespace().startswith("mnt:[")
