"""The handover of a reading's files between its AI and the controller
(handover.py, 2026-10-31): nothing outside the working folder is ever read or
written through a link, whatever changes while the controller works.

Every "outside" file here is a dummy in the test's own temporary folder.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import threading
from pathlib import Path

import pytest

from atelier import handover
from atelier.access import AccessPolicyError

SECRET = "DUMMY-SECRET outside the working folder\n"
VICTIM = "a dummy file outside the working folder, never to be changed\n"


@pytest.fixture
def ws(tmp_path):
    """A run with a reading's working folder, and two dummy files outside it."""
    run = tmp_path / "run"
    work = run / "workspace"
    (work / "progress" / "reading" / "figures").mkdir(parents=True)
    (work / "progress" / "reading" / "guide.md").write_text("# A guide\n\nIts text.\n")
    (work / "progress" / "reading" / "figures" / "f.png").write_bytes(b"\x89PNG figure")
    (work / "progress" / "source.pdf").write_bytes(b"%PDF-1.4 the paper")
    (work / "progress" / "_session_notes.md").write_text("Notes.\n")
    (work / "work").mkdir()
    (work / "work" / "download.pdf").write_bytes(b"%PDF-1.4 downloaded")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(SECRET)
    (outside / "victim.txt").write_text(VICTIM)
    return run, work, outside


def _contents(root: Path) -> dict[str, bytes]:
    """Every plain file under `root`, by relative path, read without following links."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def _nothing_outside_changed(outside: Path) -> None:
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt", "victim.txt"]
    assert (outside / "secret.txt").read_text() == SECRET
    assert (outside / "victim.txt").read_text() == VICTIM


def _secret_nowhere(*roots: Path) -> None:
    for root in roots:
        for rel, data in _contents(root).items():
            assert SECRET.encode() not in data, f"{root}/{rel} holds the outside file"


def test_collect_copies_what_it_takes_exactly_and_only_checks_the_rest(ws):
    run, work, outside = ws
    guide = work / "progress" / "reading" / "guide.md"
    os.utime(guide, ns=(1_600_000_000_123_456_789, 1_700_000_000_987_654_321))
    (work / "progress" / "source.pdf").chmod(0o444)
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    copy = took.root
    assert took.unsafe == []
    assert _contents(copy) == {k: v for k, v in _contents(work).items()
                               if k.startswith("progress/")}
    assert not (copy / "work").exists()
    copied = (copy / "progress" / "reading" / "guide.md").stat()
    assert copied.st_mtime_ns == 1_700_000_000_987_654_321
    assert stat.S_IMODE((copy / "progress" / "source.pdf").stat().st_mode) == 0o444
    assert not (run / handover.QUARANTINE).exists()
    # Taken again: the copy is made afresh, not added to.
    (copy / "progress" / "stale").write_text("left by an interrupted controller")
    again = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    assert not (again.root / "progress" / "stale").exists()
    _nothing_outside_changed(outside)


def test_collect_names_what_it_leaves_out_and_nothing_it_copies_or_refuses(ws):
    run, work, outside = ws
    (work / "refs").mkdir()
    (work / "refs" / "paper.pdf").write_bytes(b"%PDF-1.4 a reference")
    (work / "refs" / "other.pdf").write_bytes(b"%PDF-1.4 not taken")
    (work / ".unpacman-tmp").mkdir()
    (work / ".unpacman-tmp" / "t.txt").write_text("scratch\n")
    (work / "problem.md").write_text("# The paper\n")
    (work / "work" / "l").symlink_to(outside / "secret.txt")
    took = handover.collect(work, run / handover.HELD, ["progress", "refs/paper.pdf"],
                            run / handover.QUARANTINE)
    assert took.unsafe == ["work/l"]
    # Neither the parts, nor the folder on the way to one, nor the link.
    assert took.left_out == {".unpacman-tmp", ".unpacman-tmp/t.txt", "problem.md",
                             "refs/other.pdf", "work", "work/download.pdf"}
    _nothing_outside_changed(outside)


def test_collect_refuses_every_kind_of_link_and_never_reads_its_target(ws):
    run, work, outside = ws
    reading = work / "progress" / "reading"
    (reading / "notes.md").symlink_to(outside / "secret.txt")
    (reading / "sub").symlink_to(outside, target_is_directory=True)
    (work / "progress" / "gone").symlink_to(outside / "nothing-there")
    (work / "work" / "l").symlink_to(outside / "secret.txt")          # only checked
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    assert sorted(took.unsafe) == ["progress/gone", "progress/reading/notes.md",
                                   "progress/reading/sub", "work/l"]
    # Each set aside as itself — still a link, to the same place — and gone
    # from the working folder; none of their targets copied, read or changed.
    kept = sorted(p for p in (run / handover.QUARANTINE).iterdir())
    assert len(kept) == 4 and all(p.is_symlink() for p in kept)
    assert {os.readlink(p) for p in kept} == {str(outside / "secret.txt"), str(outside),
                                             str(outside / "nothing-there")}
    assert not any(p.is_symlink() for p in work.rglob("*"))
    _secret_nowhere(took.root)
    _nothing_outside_changed(outside)
    assert (took.root / "progress" / "reading" / "guide.md").read_text() == "# A guide\n\nIts text.\n"


def test_collect_refuses_hard_links_and_pipes_without_waiting_on_them(ws):
    run, work, outside = ws
    reading = work / "progress" / "reading"
    os.link(reading / "guide.md", reading / "guide-again.md")
    os.mkfifo(reading / "pipe")
    old = signal.signal(signal.SIGALRM, lambda *_: pytest.fail("collect waited on a pipe"))
    signal.alarm(10)
    try:
        took = handover.collect(work, run / handover.HELD, ["progress"],
                                run / handover.QUARANTINE)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert sorted(took.unsafe) == ["progress/reading/guide-again.md",
                                   "progress/reading/guide.md", "progress/reading/pipe"]
    assert not (took.root / "progress" / "reading" / "guide.md").exists()
    _nothing_outside_changed(outside)


def test_collect_refuses_a_link_put_in_place_of_a_folder(ws):
    run, work, outside = ws
    reading = work / "progress" / "reading"
    (work / "progress" / "reading-real").mkdir()
    reading.rename(work / "progress" / "reading-real" / "kept")
    reading.symlink_to(outside, target_is_directory=True)
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    assert took.unsafe == ["progress/reading"]
    assert not (took.root / "progress" / "reading").exists()
    _secret_nowhere(took.root)
    _nothing_outside_changed(outside)


def test_a_link_swapped_in_while_collecting_is_never_followed(ws):
    """A program left running by the step swaps the guide for a link and back,
    as fast as it can, while the controller takes its copy again and again:
    the copy either has the guide, or the link is refused — never the file
    the link names."""
    run, work, outside = ws
    reading = work / "progress" / "reading"
    stop = threading.Event()

    def swap() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            link, plain = reading / f".l{n}", reading / f".p{n}"
            try:
                link.symlink_to(outside / "secret.txt")
                os.replace(link, reading / "guide.md")
                plain.write_text("# A guide\n\nIts text.\n")
                os.replace(plain, reading / "guide.md")
            except OSError:
                pass

    swapper = threading.Thread(target=swap, daemon=True)
    swapper.start()
    seen = {"copied": 0, "refused": 0}
    try:
        for _ in range(150):
            took = handover.collect(work, run / handover.HELD, ["progress"],
                                    run / handover.QUARANTINE)
            copied = took.root / "progress" / "reading" / "guide.md"
            if "progress/reading/guide.md" in took.unsafe:
                seen["refused"] += 1
            elif copied.is_file():
                seen["copied"] += 1
                assert copied.read_text() == "# A guide\n\nIts text.\n"
            _secret_nowhere(took.root)
    finally:
        stop.set()
        swapper.join(5)
    assert seen["copied"] + seen["refused"] > 0
    _nothing_outside_changed(outside)


def _swap_when_opened(monkeypatch, name: str, swap) -> list[int]:
    """Make `swap` happen at the worst moment: after the controller has looked
    at `name` and found a plain file or folder, just before it opens it."""
    real_open, done = os.open, []

    def opening(path, flags, *args, **kwargs):
        if path == name and kwargs.get("dir_fd") is not None and not done:
            done.append(1)
            swap()
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", opening)
    return done


def test_a_file_swapped_for_a_link_after_being_looked_at_is_not_read_through(ws, monkeypatch):
    run, work, outside = ws
    reading = work / "progress" / "reading"

    def swap():
        (reading / ".l").symlink_to(outside / "secret.txt")
        os.replace(reading / ".l", reading / "guide.md")

    done = _swap_when_opened(monkeypatch, "guide.md", swap)
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    monkeypatch.undo()
    assert done and took.unsafe == ["progress/reading/guide.md"]
    _secret_nowhere(took.root)
    _nothing_outside_changed(outside)


def test_a_folder_swapped_for_a_link_after_being_looked_at_is_not_entered(ws, monkeypatch):
    run, work, outside = ws
    progress = work / "progress"

    def swap():
        progress.joinpath("reading").rename(work / "reading-moved")
        (progress / ".l").symlink_to(outside, target_is_directory=True)
        os.replace(progress / ".l", progress / "reading")

    done = _swap_when_opened(monkeypatch, "reading", swap)
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    monkeypatch.undo()
    assert done and took.unsafe == ["progress/reading"]
    _secret_nowhere(took.root)
    _nothing_outside_changed(outside)


def test_deliver_puts_the_copy_back_and_reports_links_that_appeared(ws):
    run, work, outside = ws
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    copy = took.root / "progress" / "reading"
    (copy / "guide.md").write_text("# The guide, as the controller left it\n")
    (copy / "companion.md").write_text("Exported.\n")
    # Meanwhile, in the AI's folder, a program that outlived the step:
    reading = work / "progress" / "reading"
    (reading / "guide.md").unlink()
    (reading / "guide.md").symlink_to(outside / "victim.txt")
    (reading / "zz.md").symlink_to(outside / "secret.txt")
    (reading / "paper-profile.json").symlink_to(outside / "would-be-new.plist")
    appeared = handover.deliver(took.root, work, ["progress"], run / handover.SET_ASIDE)
    assert sorted(appeared) == ["progress/reading/guide.md", "progress/reading/paper-profile.json",
                                "progress/reading/zz.md"]
    assert (reading / "guide.md").read_text() == "# The guide, as the controller left it\n"
    assert (reading / "companion.md").read_text() == "Exported.\n"
    assert not any(p.is_symlink() for p in (work / "progress").rglob("*"))
    assert not took.root.exists()
    # What stood there is set aside, as it was: the links still links.
    aside = run / handover.SET_ASIDE
    links = [p for p in aside.rglob("*") if p.is_symlink()]
    assert len(links) == 3
    _nothing_outside_changed(outside)


def test_deliver_never_writes_through_a_link_put_in_place_of_the_part(ws):
    run, work, outside = ws
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    expected = _contents(took.root / "progress")
    progress = work / "progress"
    progress.rename(work / "moved-away")
    progress.symlink_to(outside, target_is_directory=True)
    appeared = handover.deliver(took.root, work, ["progress"], run / handover.SET_ASIDE)
    assert appeared == ["progress"]
    assert progress.is_dir() and not progress.is_symlink()
    assert _contents(progress) == expected
    _nothing_outside_changed(outside)


def test_deliver_with_nothing_appeared_reports_nothing(ws):
    run, work, outside = ws
    before = _contents(work)
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    assert handover.deliver(took.root, work, ["progress"], run / handover.SET_ASIDE) == []
    assert _contents(work) == before
    _nothing_outside_changed(outside)


def test_a_part_that_is_a_single_file_elsewhere_goes_and_comes_back(ws):
    run, work, outside = ws
    (work / "refs").mkdir()
    (work / "refs" / "paper.pdf").write_bytes(b"%PDF-1.4 a reference")
    (work / "refs" / "other.pdf").write_bytes(b"%PDF-1.4 not taken")
    parts = ["progress", "refs/paper.pdf"]
    took = handover.collect(work, run / handover.HELD, parts, run / handover.QUARANTINE)
    assert (took.root / "refs" / "paper.pdf").read_bytes() == b"%PDF-1.4 a reference"
    assert not (took.root / "refs" / "other.pdf").exists()
    (took.root / "refs" / "paper.pdf").write_bytes(b"%PDF-1.4 restored")
    # The folder on the way replaced by a link meanwhile: set aside, not followed.
    (work / "refs").rename(work / "refs-moved")
    (work / "refs").symlink_to(outside, target_is_directory=True)
    appeared = handover.deliver(took.root, work, parts, run / handover.SET_ASIDE)
    assert appeared
    assert (work / "refs").is_dir() and not (work / "refs").is_symlink()
    assert (work / "refs" / "paper.pdf").read_bytes() == b"%PDF-1.4 restored"
    _nothing_outside_changed(outside)


def test_copy_without_links_refuses_a_link_and_leaves_the_source(ws, tmp_path):
    run, work, outside = ws
    (work / "progress" / "reading" / "deep").mkdir()
    (work / "progress" / "reading" / "deep" / "x.md").symlink_to(outside / "secret.txt")
    destination = tmp_path / "continued" / "workspace"
    with pytest.raises(AccessPolicyError, match="Unsafe link or special file") as caught:
        handover.copy_without_links(work, destination)
    assert "deep/x.md" in str(caught.value)
    assert (work / "progress" / "reading" / "deep" / "x.md").is_symlink()
    if destination.exists():
        _secret_nowhere(destination)
    _nothing_outside_changed(outside)


def test_copy_without_links_copies_a_clean_folder_and_can_merge_into_its_own(ws, tmp_path):
    run, work, outside = ws
    destination = tmp_path / "continued" / "workspace"
    handover.copy_without_links(work, destination)
    assert _contents(destination) == _contents(work)
    archived = tmp_path / "archived"
    archived.mkdir()
    (archived / "guide.md").write_text("# The archived guide\n")
    reading = destination / "progress" / "reading"
    handover.copy_without_links(archived, reading, into_existing=True)
    assert (reading / "guide.md").read_text() == "# The archived guide\n"
    assert (reading / "figures" / "f.png").read_bytes() == b"\x89PNG figure"
    _nothing_outside_changed(outside)


def test_unsafe_entries_only_looks(ws):
    run, work, outside = ws
    (work / "progress" / "l").symlink_to(outside / "secret.txt")
    assert handover.unsafe_entries(work) == ["progress/l"]
    assert (work / "progress" / "l").is_symlink()
    assert handover.unsafe_entries(work / "progress" / "l") == ["."]
    assert handover.unsafe_entries(work / "missing") == []


@pytest.mark.parametrize("part", ["", ".", "../x", "progress/../.."])
def test_parts_are_names_inside_the_folder(ws, part):
    run, work, outside = ws
    with pytest.raises(ValueError):
        handover.collect(work, run / handover.HELD, [part], run / handover.QUARANTINE)


# --------------------------------------------------------------------------
# archive: a reading kept after its run had stopped (2026-11-02)
# --------------------------------------------------------------------------


def _archive(run: Path, work: Path) -> list[str] | None:
    """The reading's folder archived as readings._archive archives it."""
    return handover.archive(work, "progress/reading", run / "reading", run / handover.QUARANTINE)


def test_archive_keeps_a_reading_whole_and_sets_aside_what_is_unsafe(ws):
    run, work, outside = ws
    reading = work / "progress" / "reading"
    os.utime(reading / "guide.md", ns=(1_600_000_000_123_456_789, 1_700_000_000_987_654_321))
    (reading / "figures" / "f.png").chmod(0o444)
    (reading / "zz-notes.md").symlink_to(outside / "secret.txt")
    (reading / "sub").symlink_to(outside, target_is_directory=True)
    os.link(outside / "victim.txt", reading / "hard.md")
    os.mkfifo(reading / "pipe")
    old = signal.signal(signal.SIGALRM, lambda *_: pytest.fail("archive waited on a pipe"))
    signal.alarm(10)
    try:
        set_aside = _archive(run, work)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert sorted(set_aside) == ["hard.md", "pipe", "sub", "zz-notes.md"]
    kept = run / "reading"
    assert _contents(kept) == {"figures/f.png": b"\x89PNG figure",
                               "guide.md": b"# A guide\n\nIts text.\n"}
    assert not any(p.is_symlink() for p in kept.rglob("*"))
    assert (kept / "guide.md").stat().st_mtime_ns == 1_700_000_000_987_654_321
    assert stat.S_IMODE((kept / "figures" / "f.png").stat().st_mode) == 0o444
    assert stat.S_IMODE(kept.stat().st_mode) == stat.S_IMODE(reading.stat().st_mode)
    # Each set aside as itself, out of the folder; nothing half built left.
    quarantined = sorted(p.name.split("-", 1)[1] for p in (run / handover.QUARANTINE).iterdir())
    assert quarantined == ["hard.md", "pipe", "sub", "zz-notes.md"]
    assert sorted(p.name for p in reading.iterdir()) == ["figures", "guide.md"]
    assert sorted(p.name for p in run.iterdir()) == sorted(
        [handover.QUARANTINE, "reading", "workspace"])
    _secret_nowhere(kept)
    _nothing_outside_changed(outside)


def test_archive_never_overwrites_a_record_nor_follows_a_link_in_its_place(ws):
    run, work, outside = ws
    reading = work / "progress" / "reading"
    (run / "reading").mkdir()
    (run / "reading" / "guide.md").write_text("# The record\n")
    assert _archive(run, work) is None
    assert (run / "reading" / "guide.md").read_text() == "# The record\n"
    # The reading folder itself a link: nothing kept, nothing followed.
    shutil.rmtree(run / "reading")
    reading.rename(reading.parent / "reading-real")
    reading.symlink_to(outside, target_is_directory=True)
    assert _archive(run, work) is None
    assert not (run / "reading").exists() and not (run / "reading").is_symlink()
    # And none where there is no reading at all.
    reading.unlink()
    assert _archive(run, work) is None
    assert sorted(p.name for p in run.iterdir()) == ["workspace"]
    _nothing_outside_changed(outside)


def test_archive_is_found_whole_or_not_at_all(ws, monkeypatch):
    run, work, outside = ws
    reading = work / "progress" / "reading"
    copy, seen = handover._Walk._copy, []

    def failing(self, parent, name, rel, target):
        if rel == "guide.md":
            # Built beside it: while it is being made there is no record yet.
            seen.append((run / "reading").exists())
            raise OSError("the disk is full")
        return copy(self, parent, name, rel, target)

    monkeypatch.setattr(handover._Walk, "_copy", failing)
    with pytest.raises(OSError, match="disk is full"):
        _archive(run, work)
    assert seen == [False]
    assert sorted(p.name for p in run.iterdir()) == ["workspace"]


def test_archive_never_reads_a_file_swapped_for_a_link_while_it_copies(ws, monkeypatch):
    run, work, outside = ws
    reading = work / "progress" / "reading"

    def swap():
        (reading / ".l").symlink_to(outside / "secret.txt")
        os.replace(reading / ".l", reading / "guide.md")

    done = _swap_when_opened(monkeypatch, "guide.md", swap)
    set_aside = _archive(run, work)
    monkeypatch.undo()
    assert done and set_aside == ["guide.md"]
    assert not (run / "reading" / "guide.md").exists()
    _secret_nowhere(run / "reading")
    _nothing_outside_changed(outside)


def _beyond(outside: Path) -> Path:
    """A folder of the user's — a dummy beside the outside files — that holds
    progress/reading/ of its own, with a link, a hard-linked file and a pipe
    in it: what a link left on the way to a reading could lead to."""
    beyond = outside.parent / "beyond"
    reading = beyond / "progress" / "reading"
    reading.mkdir(parents=True)
    (reading / "guide.md").write_text(SECRET)
    (reading / "l").symlink_to(outside / "secret.txt")
    os.link(outside / "victim.txt", reading / "h")
    os.mkfifo(reading / "p")
    return beyond


@pytest.mark.parametrize("where", ["progress", "progress/reading", "workspace"])
def test_archive_follows_no_link_on_the_way_to_the_reading(ws, where):
    # Another agent's review, 2026-11-02: opened by its whole path, the
    # reading was reached through a link in place of progress/ — copied in,
    # and the entries beyond it moved out into rejected-files/.
    run, work, outside = ws
    beyond = _beyond(outside)
    there = beyond / "progress" / "reading"
    before = sorted(p.name for p in there.iterdir())
    if where == "workspace":
        work.rename(run / "workspace-real")
        work.symlink_to(beyond, target_is_directory=True)
    else:
        (work / where).rename(work / f"{where}-real")
        (work / where).symlink_to(beyond / where, target_is_directory=True)
    old = signal.signal(signal.SIGALRM, lambda *_: pytest.fail("archive waited on a pipe"))
    signal.alarm(10)
    try:
        assert _archive(run, work) is None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    # Nothing kept, nothing set aside, nothing half built — and nothing beyond
    # the link read, moved or changed.
    assert not (run / "reading").exists() and not (run / handover.QUARANTINE).exists()
    assert not [p for p in run.iterdir() if p.name.startswith(".reading")]
    assert sorted(p.name for p in there.iterdir()) == before
    assert (there / "guide.md").read_text() == SECRET
    assert (there / "l").is_symlink() and (there / "h").stat().st_nlink == 2
    _nothing_outside_changed(outside)


@pytest.mark.parametrize("kind", ["pipe", "file"])
def test_archive_refuses_what_is_not_a_folder_on_the_way(ws, kind):
    run, work, outside = ws
    (work / "progress").rename(work / "progress-real")
    if kind == "pipe":
        os.mkfifo(work / "progress")
    else:
        (work / "progress").write_text("not a folder")
    old = signal.signal(signal.SIGALRM, lambda *_: pytest.fail("archive waited on a pipe"))
    signal.alarm(10)
    try:
        assert _archive(run, work) is None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert sorted(p.name for p in run.iterdir()) == ["workspace"]
    assert sorted(p.name for p in work.iterdir()) == ["progress", "progress-real", "work"]


def test_archive_never_follows_a_link_swapped_in_on_the_way(ws, monkeypatch):
    run, work, outside = ws
    beyond = _beyond(outside)

    def swap():
        (work / "progress").rename(work / "progress-real")
        (work / "progress").symlink_to(beyond / "progress", target_is_directory=True)

    # At the worst moment: just before the folder is entered.
    done = _swap_when_opened(monkeypatch, "progress", swap)
    assert _archive(run, work) is None
    monkeypatch.undo()
    assert done
    assert not (run / "reading").exists() and not (run / handover.QUARANTINE).exists()
    assert sorted(p.name for p in (beyond / "progress" / "reading").iterdir()) == \
        ["guide.md", "h", "l", "p"]
    _nothing_outside_changed(outside)


@pytest.mark.parametrize("part", ["", ".", "../x", "progress/../.."])
def test_archive_takes_only_a_part_inside_the_folder(ws, part):
    run, work, outside = ws
    with pytest.raises(ValueError):
        handover.archive(work, part, run / "reading", run / handover.QUARANTINE)
    assert sorted(p.name for p in run.iterdir()) == ["workspace"]
