"""Publishing (publish.py): the private branches made into the public ones.

A throwaway private repository — with the developers' notes, a front page and
a website — is published into a throwaway bare "public" repository. What is
tested is what may reach the public: the code without the notes, under the
publisher's name only, with no private commit in its history, only ever moved
forward — and that a package made from it is the version a copy follows.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from deslopify.app import package, publish

MESSAGE = "unpacman, 3 November 2026: the first public version"


def git(cwd: Path, *args: str, **env: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=Someone", "-c", "user.email=someone@private.example",
                           "-c", "init.defaultBranch=unpackman", *args],
                          cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


@pytest.fixture
def private(tmp_path) -> Path:
    """The private repository: its unpackman and unpacman_windows branches,
    each with code, the licence, and notes that stay private."""
    root = tmp_path / "private"
    root.mkdir()
    git(root, "init", "-q")
    write(root, {
        "LICENSE": "MIT License\n", "README.md": "Atelier\n",
        "install-unpacman.sh": "#!/bin/bash\necho install\n",
        "src/atelier/runner.py": "print('runner')\n",
        "CLAUDE.md": "notes\n", "deslopify/DESIGN.md": "design\n",
        "deslopify/ISSUES.md": "issues\n", "docs/PLAN.md": "plan\n",
        "docs/onboarding-message.md": "invitation\n",
        "docs/unpacman-onboarding-message.md": "message\n",
        "docs/unpacman-access-policy.md": "the access policy, public\n",
        "deslopify/public/README.md": "# unpacman\n\nDownload it.\n",
        "deslopify/public/WINDOWS_RELEASE_HANDOVER.md": "Private Windows review and transfer notes.\n",
        "deslopify/public/website/index.html": "<!doctype html><title>unpacman</title>\n",
        "deslopify/public/website/.nojekyll": "",
    })
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Private work\n\nCo-Authored-By: Someone Else <x@example.org>")
    git(root, "checkout", "-q", "-b", "unpacman_windows")
    write(root, {"WINDOWS.md": "windows notes\n",
                 "install-unpacman-wsl.sh": "#!/bin/bash\necho wsl\n",
                 "install-unpacman.ps1": "Write-Output 'install'\n"})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Windows work")
    git(root, "checkout", "-q", "unpackman")
    return root


@pytest.fixture
def public(tmp_path) -> Path:
    root = tmp_path / "public.git"
    git(tmp_path, "init", "-q", "--bare", str(root))
    return root


def files(repo: Path, ref: str) -> list[str]:
    return sorted(git(repo, "ls-tree", "-r", "--name-only", ref).splitlines())


def test_the_first_publication_holds_the_code_without_the_notes(private, public):
    done = publish.publish(private, public, MESSAGE)
    assert [r.branch for r in done] == ["mac", "windows", "linux", "main", "website"]
    assert all(r.before is None and r.changed for r in done)
    assert files(public, "mac") == [
        "LICENSE", "README.md", "docs/unpacman-access-policy.md", "install-unpacman.sh",
        "src/atelier/runner.py"]
    assert files(public, "windows") == [
        "LICENSE", "README.md", "docs/unpacman-access-policy.md", "install-unpacman-wsl.sh",
        "install-unpacman.ps1", "install-unpacman.sh", "src/atelier/runner.py"]
    # Linux copies follow the same code as Windows' (Linux inside Windows).
    assert files(public, "linux") == files(public, "windows")
    assert files(public, "main") == ["LICENSE", "README.md"]
    assert git(public, "show", "main:README.md") == "# unpacman\n\nDownload it."
    assert files(public, "website") == [".nojekyll", "index.html"]
    # The rest of the code is exactly the private branch's.
    for branch, source in publish.CODE.items():
        for name in files(public, branch):
            assert git(public, "show", f"{branch}:{name}") == \
                git(private, "show", f"{source}:{name}")


def test_public_commits_name_their_publisher_only(private, public):
    publish.publish(private, public, MESSAGE)
    for branch in ("mac", "windows", "main", "website"):
        log = git(public, "log", "--format=%an|%ae|%cn|%ce|%P|%B", branch)
        name, email = publish.NAME, publish.EMAIL
        assert log.startswith(f"{name}|{email}|{name}|{email}||{MESSAGE}"), log
        assert "Co-Authored-By" not in log and "private.example" not in log
    with pytest.raises(publish.PublishError, match="publisher only"):
        publish.publish(private, public, MESSAGE + "\n\nCo-Authored-By: A <a@example.org>")
    with pytest.raises(publish.PublishError, match="message"):
        publish.publish(private, public, "  ")
    with pytest.raises(publish.PublishError, match="e-mail address"):
        publish.publish(private, public, MESSAGE + " Write to someone@example.org.")
    with pytest.raises(publish.PublishError, match="no-reply"):
        publish.publish(private, public, MESSAGE, email="me@example.org")
    # Another publisher, under their own no-reply address, is a publisher too.
    write(private, {"src/atelier/runner.py": "print('by another')\n"})
    git(private, "commit", "-q", "-am", "By another")
    publish.publish(private, public, "By another", name="Another Publisher",
                    email="7+another@users.noreply.github.com")
    assert git(public, "log", "-1", "--format=%an <%ae>", "mac") == \
        "Another Publisher <7+another@users.noreply.github.com>"


def test_no_private_commit_reaches_the_public_repository(private, public):
    publish.publish(private, public, MESSAGE)
    for ref in ("unpackman", "unpacman_windows"):
        sha = git(private, "rev-parse", ref)
        done = subprocess.run(["git", "cat-file", "-e", sha], cwd=public, capture_output=True)
        assert done.returncode != 0                     # not even as an object
    # Nor is anything left behind in the private one, whose checkout is as it was.
    assert git(private, "for-each-ref", "refs/publish") == ""
    assert git(private, "status", "--porcelain") == ""


def test_a_second_publication_moves_each_changed_branch_forward_only(private, public):
    first = {r.branch: r.after for r in publish.publish(private, public, MESSAGE)}
    write(private, {"src/atelier/runner.py": "print('runner, better')\n",
                    "CLAUDE.md": "more notes\n"})
    git(private, "commit", "-q", "-am", "Better")
    done = {r.branch: r for r in publish.publish(private, public, "Better readings")}
    assert done["mac"].before == first["mac"] and done["mac"].changed
    assert git(public, "rev-parse", "mac^") == first["mac"]
    assert git(public, "log", "-1", "--format=%s", "mac") == "Better readings"
    # The other branches had nothing new: no empty commit.
    for branch in ("windows", "main", "website"):
        assert not done[branch].changed and git(public, "rev-parse", branch) == first[branch]
    # The notes changed too, but are still not public.
    assert "CLAUDE.md" not in files(public, "mac")


def test_a_public_branch_is_never_rewritten(private, public, tmp_path):
    publish.publish(private, public, MESSAGE)
    # Someone else published meanwhile, from elsewhere: the next publication
    # must not overwrite it.
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "--branch", "mac", str(public), str(other))
    write(other, {"elsewhere.txt": "x\n"})
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "Elsewhere")
    git(other, "push", "-q", "origin", "mac")
    elsewhere = git(public, "rev-parse", "mac")
    write(private, {"src/atelier/runner.py": "print('runner, mine')\n"})
    git(private, "commit", "-q", "-am", "Mine")
    with pytest.raises(publish.PublishError, match="someone else"):
        publish.publish(private, public, "Mine")
    assert git(public, "rev-parse", "mac") == elsewhere          # left exactly as it was


def test_a_page_edited_on_github_stops_publishing_until_carried_over_and_named(
        private, public, tmp_path, monkeypatch):
    """The front page edited in GitHub's own editor, under its author's address:
    the next publication would remake main from deslopify/public and undo the
    edit, so it stops first. Carried into deslopify/public and named in
    ACCEPTED, the edit stays and publishing goes on."""
    publish.publish(private, public, MESSAGE)
    web = tmp_path / "web"
    git(tmp_path, "clone", "-q", "--branch", "main", str(public), str(web))

    def edit_on_github(text: str, message: str) -> str:
        write(web, {"README.md": text})
        git(web, "-c", "user.name=GitHub", "-c", "user.email=noreply@github.com",
            "commit", "-q", "-am", message,
            "--author=A Collaborator <collaborator@university.example>")
        git(web, "push", "-q", "origin", "main")
        return git(public, "rev-parse", "main")

    edit = edit_on_github("# unpacman\n\nAsk it about papers.\n", "Clarify the description")
    write(private, {"src/atelier/runner.py": "print('runner, later')\n"})
    git(private, "commit", "-q", "-am", "Later")
    with pytest.raises(publish.PublishError, match="carry it into deslopify/public"):
        publish.publish(private, public, "Later")
    assert git(public, "rev-parse", "main") == edit              # nothing undone

    write(private, {"deslopify/public/README.md": "# unpacman\n\nAsk it about papers.\n"})
    git(private, "commit", "-q", "-am", "The front page as edited on GitHub")
    monkeypatch.setitem(publish.ACCEPTED, edit, "main: a test edit")
    done = {r.branch: r for r in publish.publish(private, public, "Later")}
    assert done["mac"].changed and not done["main"].changed
    assert git(public, "show", "main:README.md") == "# unpacman\n\nAsk it about papers."

    # Named or not, no public commit carries a co-author line.
    other = edit_on_github("# unpacman\n\nAsk it.\n",
                           "Shorter\n\nCo-authored-by: Someone Else <x@example.org>")
    monkeypatch.setitem(publish.ACCEPTED, other, "main: with a co-author line")
    with pytest.raises(publish.PublishError, match="co-author"):
        publish.publish(private, public, "Later still")


def test_accepted_commits_are_named_in_full():
    assert publish.ACCEPTED
    assert all(len(sha) == 40 and set(sha) <= set("0123456789abcdef") and why
               for sha, why in publish.ACCEPTED.items())


def test_without_the_licence_nothing_is_published(private, public):
    git(private, "rm", "-q", "LICENSE")
    git(private, "commit", "-q", "-m", "No licence")
    with pytest.raises(publish.PublishError, match="LICENSE"):
        publish.publish(private, public, MESSAGE)
    assert git(public, "for-each-ref") == ""


def test_a_named_source_is_published_in_place_of_the_branch(private, public):
    git(private, "checkout", "-q", "-b", "prepared")
    write(private, {"src/atelier/runner.py": "print('prepared')\n"})
    git(private, "commit", "-q", "-am", "Prepared")
    git(private, "checkout", "-q", "unpackman")
    publish.publish(private, public, MESSAGE, sources={"mac": "refs/heads/prepared"})
    assert git(public, "show", "mac:src/atelier/runner.py") == "print('prepared')"


def test_a_package_of_the_public_branch_is_the_version_it_follows(private, public, tmp_path):
    publish.publish(private, public, MESSAGE)
    made = package.make_package(public, tmp_path / "out", name="unpacman-mac")
    assert made.name == "unpacman-mac.zip"
    with zipfile.ZipFile(made) as zf:
        [folder] = {n.split("/")[0] for n in zf.namelist()}
        zf.extractall(tmp_path / "unzipped")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--branch", "mac",
        str(tmp_path / "unzipped" / folder / package.BUNDLE), str(clone))
    assert git(clone, "rev-parse", "HEAD") == git(public, "rev-parse", "mac")
    assert "Private work" not in git(clone, "log", "--format=%B")
    windows = package.make_windows_package(public, tmp_path / "out", name="unpacman-windows")
    assert windows.name == "unpacman-windows.zip"


def test_linux_is_windows_code_with_the_tests_github_runs(private, public):
    """The branch Linux copies follow: the code of the Windows branch, and — in
    it only — the tests GitHub runs on its own Linux machines, kept privately
    with the other public-only files, so they never run in the private one."""
    git(private, "checkout", "-q", "unpacman_windows")
    write(private, {"deslopify/public/github/linux.yml": "name: Linux\non: push\n"})
    git(private, "add", "-A")
    git(private, "commit", "-q", "-m", "Tests on GitHub's Linux machines")
    git(private, "checkout", "-q", "unpackman")
    publish.publish(private, public, MESSAGE)
    assert files(public, "linux") == sorted([*files(public, "windows"),
                                             ".github/workflows/linux.yml"])
    assert git(public, "show", "linux:.github/workflows/linux.yml") == "name: Linux\non: push"
    for branch in ("mac", "windows", "main", "website"):
        assert not any(f.startswith(".github/") for f in files(public, branch)), branch
    assert not any(f.startswith("deslopify/public") for f in files(public, "linux"))


def test_linux_comes_from_wherever_windows_does_unless_told(private, public):
    git(private, "checkout", "-q", "unpacman_windows")
    write(private, {"src/atelier/runner.py": "print('windows, later')\n"})
    git(private, "commit", "-q", "-am", "Later")
    later = git(private, "rev-parse", "HEAD")
    git(private, "checkout", "-q", "unpackman")
    earlier = git(private, "rev-parse", "unpacman_windows~1")
    publish.publish(private, public, MESSAGE, sources={"windows": earlier})
    assert git(public, "rev-parse", "linux^{tree}") == git(public, "rev-parse", "windows^{tree}")
    assert git(public, "show", "linux:src/atelier/runner.py") == "print('runner')"
    done = {r.branch: r for r in publish.publish(private, public, "Linux first",
                                                 sources={"windows": earlier, "linux": later})}
    assert not done["windows"].changed and done["linux"].changed
    assert git(public, "show", "linux:src/atelier/runner.py") == "print('windows, later')"
    assert git(public, "rev-parse", "linux^") == done["linux"].before
