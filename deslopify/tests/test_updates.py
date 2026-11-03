"""Updates in one click (updates.py), against throwaway repositories: an
"upstream" standing in for GitHub, and a copy cloned from it standing in for
an installation. `uv` and the new version's start are stand-ins too, so these
check the decisions — when to offer, when to refuse, when to go back — and
never touch this checkout.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from deslopify.app import updates

pytestmark = pytest.mark.skipif(shutil.which("git") is None or not updates.git_usable(),
                                reason="needs git")


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                           "-c", "init.defaultBranch=unpackman", *args],
                          cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def commit(repo: Path, name: str, text: str, subject: str) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def copies(tmp_path):
    """An upstream with one commit, a copy cloned from it, and a second working
    copy through which upstream moves on."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    author = tmp_path / "author"
    author.mkdir()
    git(author, "init", "-q")
    commit(author, "README.md", "one\n", "First version")
    git(author, "remote", "add", "origin", str(origin))
    git(author, "push", "-q", "origin", "HEAD:unpackman")
    installed = tmp_path / "installed"
    git(tmp_path, "clone", "-q", "--branch", "unpackman", str(origin), str(installed))
    (installed / ".gitignore").write_text("papers/\n.venv/\n")
    git(installed, "add", ".gitignore")
    git(installed, "commit", "-q", "-m", "ignore")
    git(installed, "push", "-q", "origin", "unpackman")
    git(author, "pull", "-q", "origin", "unpackman")
    return origin, author, installed


def publish(author: Path, subject: str) -> str:
    sha = commit(author, "README.md", f"{subject}\n", subject)
    git(author, "push", "-q", "origin", "HEAD:unpackman")
    return sha


def test_a_new_version_is_seen_once_fetched(copies):
    _, author, installed = copies
    assert updates.measure(installed).behind == 0
    publish(author, "Clearer words on the first page")
    assert updates.measure(installed).behind == 0            # not fetched yet
    assert updates.fetch(installed) == ""
    status = updates.measure(installed)
    assert status.behind == 1 and not status.why_not
    assert status.news[0]["subject"] == "Clearer words on the first page"
    assert status.to_dict()["available"] is True
    assert status.version.branch == "unpackman" and status.version.upstream == "origin/unpackman"


def test_a_copy_with_changes_of_its_own_is_left_to_git(copies):
    _, author, installed = copies
    publish(author, "Upstream moves on")
    updates.fetch(installed)
    (installed / "README.md").write_text("edited here\n")
    status = updates.measure(installed)
    assert status.dirty == ["README.md"] and "edited file" in status.why_not
    assert status.to_dict()["available"] is False
    # Untracked and ignored files — papers, readings, settings — are not changes.
    git(installed, "checkout", "-q", "README.md")
    (installed / "papers").mkdir()
    (installed / "papers" / "a.pdf").write_text("x")
    (installed / "notes.txt").write_text("mine")
    assert updates.measure(installed).why_not == ""
    commit(installed, "local.txt", "x", "A commit of its own")
    assert "changes of its own" in updates.measure(installed).why_not


def test_a_copy_that_is_not_a_checkout_says_so(tmp_path):
    status = updates.measure(tmp_path)
    assert status.version is None and "not connected" in status.why_not
    assert updates.fetch(tmp_path) == ""


def test_an_unreachable_upstream_is_said_plainly(copies, tmp_path):
    _, _, installed = copies
    git(installed, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert "could not reach GitHub" in updates.fetch(installed)


@pytest.fixture
def stand_ins(copies, tmp_path, monkeypatch):
    """uv that succeeds, a new version whose start succeeds unless told not to,
    and no Claude Code to update."""
    _, _, installed = copies
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/bash\nexit 0\n")
    uv.chmod(0o755)
    monkeypatch.setattr(updates, "find_uv", lambda: str(uv))
    monkeypatch.setattr(updates, "_update_claude", lambda: "")
    python = installed / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)

    def starts(ok: bool) -> None:
        python.write_text("#!/bin/bash\n" + ("echo ok; exit 0" if ok else
                                              "echo 'ImportError: broken' >&2; exit 1") + "\n")
        python.chmod(0o755)
    starts(True)
    return starts


async def follow(updater: updates.Updater) -> None:
    for _ in range(400):
        if updater.state not in ("updating", "checking"):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"update still at: {updater.step}")


async def test_an_update_moves_the_copy_forward_and_asks_for_a_restart(copies, stand_ins):
    _, author, installed = copies
    new = publish(author, "A better first page")
    updater = updates.Updater(installed)
    await updater.refresh(fetch_first=True)
    assert updater.status()["available"]
    updater.start(readings_running=0)
    await follow(updater)
    assert updater.state == "restarting" and updater.restart
    assert git(installed, "rev-parse", "HEAD") == new


async def test_a_version_that_cannot_start_is_undone(copies, stand_ins):
    _, author, installed = copies
    before = git(installed, "rev-parse", "HEAD")
    publish(author, "A broken version")
    stand_ins(False)
    updater = updates.Updater(installed)
    await updater.refresh(fetch_first=True)
    updater.start(readings_running=0)
    await follow(updater)
    assert updater.state == "failed" and not updater.restart
    assert "did not start: ImportError: broken. Nothing was changed" in updater.error
    assert git(installed, "rev-parse", "HEAD") == before
    assert updates.measure(installed).dirty == []


async def test_an_update_waits_for_readings_and_for_something_to_take(copies, stand_ins):
    _, author, installed = copies
    updater = updates.Updater(installed)
    with pytest.raises(updates.UpdateRefused, match="already up to date"):
        updater.start(readings_running=0)
    publish(author, "Something new")
    await updater.refresh(fetch_first=True)
    with pytest.raises(updates.UpdateRefused, match="1 reading is running"):
        updater.start(readings_running=1)
    assert updater.state == "idle"
