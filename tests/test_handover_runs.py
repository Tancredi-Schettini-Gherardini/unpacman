"""Whole readings through the runner with the handover (handover.py,
2026-10-31): a program a step leaves running may change the AI's folder at
any moment while the controller works, and nothing outside the run is ever
read or written for it.

The AI is the local stand-in of test_delivery (real CLI framing, no model);
every "outside" file is a dummy in the test's temporary folder.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from atelier import handover, loop
from atelier.compile import plan_next_pass, plan_run
from atelier.delivery import copy_reading_work
from atelier.events import EventHub
from atelier.harnesses import get_harness
from atelier.providers import get_adapter
from atelier.runner import RunHandle, RunState
from atelier.spec import RunSpec
from test_delivery import STANDIN, fake_exports
from test_loop_run import DOCUMENT, GUIDE

SECRET = "DUMMY-SECRET outside the run\n"
VICTIM = "a dummy file outside the run, never to be changed\n"


@pytest.fixture
def stub(tmp_cfg, monkeypatch):
    """The stand-in, in place of Claude Code and Codex before anything is
    planned: a plan records its program's path when it is made."""
    # In a folder of its own, as an installed program is: a Codex pass may
    # read the folder its program is in (access.codex_program_paths).
    path = tmp_cfg.root / "programs" / "provider-stub"
    path.parent.mkdir()
    path.write_text(f"#!{sys.executable}\n" + STANDIN)
    path.chmod(0o700)
    for provider in ("claude", "codex"):
        monkeypatch.setattr(type(get_adapter(provider)), "executable",
                            staticmethod(lambda: str(path)))
    return path


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "outside"
    folder.mkdir()
    (folder / "secret.txt").write_text(SECRET)
    (folder / "victim.txt").write_text(VICTIM)
    return folder


def _nothing_outside_changed(outside: Path) -> None:
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt", "victim.txt"]
    assert (outside / "secret.txt").read_text() == SECRET
    assert (outside / "victim.txt").read_text() == VICTIM


def _secret_nowhere(root: Path) -> None:
    """Neither outside file was read into anything of the run's."""
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            assert SECRET.encode() not in data, f"{path} holds the outside file"
            assert VICTIM.encode() not in data, f"{path} holds the other outside file"


def _plant(work: Path, outside: Path) -> None:
    """What a program the step left running might do to the AI's folder: a new
    link to a private file, the guide replaced by a link to another, and a link
    to a file that does not exist yet."""
    reading = work / "progress" / "reading"
    reading.mkdir(parents=True, exist_ok=True)
    for name, target in (("zz-notes.md", outside / "secret.txt"),
                         ("guide.md", outside / "victim.txt"),
                         ("paper-profile.json", outside / "would-be-new.plist")):
        temp = reading / f".planted-{name}"
        temp.unlink(missing_ok=True)
        temp.symlink_to(target)
        temp.replace(reading / name)


def _plan(tmp_cfg, sample_problem, provider="claude", minutes=0.5):
    sample_problem.ensure_scaffold()
    (sample_problem.progress_dir / "math.tex").write_text(DOCUMENT)
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    account = "codex" if provider == "codex" else "default"
    return plan_run(tmp_cfg, sample_problem, harness, RunSpec(
        problem=sample_problem.slug, harness=harness.id, provider=provider, account=account,
        model="test-model", max_minutes=minutes), isolate_reading=True)


async def _read(tmp_cfg, tmp_db, plan, monkeypatch, stub, *, mode="partial", guide=GUIDE):
    # Never a real AI program: the plan must name the stand-in.
    assert plan.first.argv[0] == str(stub), plan.first.argv[:1]
    for module in ("atelier.delivery", "atelier.runner", "atelier.compile"):
        monkeypatch.setattr(f"{module}.REVIEW_EXTRA_MINUTES", 0)
    monkeypatch.setattr("atelier.runner.WATCHDOG_INTERVAL", 0.02)
    (plan.cwd / "work" / "test-provider.json").write_text(json.dumps({"mode": mode}))
    (plan.cwd / "work" / "fixture-guide.md").write_text(guide)
    h = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    await h.start()
    try:
        await asyncio.wait_for(h.finished.wait(), timeout=20)
    finally:
        if not h.state.terminal:
            await h.stop("test cleanup")
    return h


def _notices(plan) -> list[dict]:
    return [json.loads(s)["payload"] for s in (plan.run_dir / "events.jsonl").read_text().splitlines()
            if json.loads(s)["type"] == "notice"]


@pytest.mark.parametrize("provider", ["claude", "codex"])
async def test_a_reading_hands_its_files_over_and_back(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, stub, provider):
    plan = _plan(tmp_cfg, sample_problem, provider)
    taken, given = [], []
    collect, deliver = handover.collect, handover.deliver
    monkeypatch.setattr(handover, "collect",
                        lambda *a, **k: taken.append(1) or collect(*a, **k))
    monkeypatch.setattr(handover, "deliver",
                        lambda *a, **k: given.append(1) or deliver(*a, **k))
    monkeypatch.setattr("atelier.runner.export_companion", fake_exports)
    h = await _read(tmp_cfg, tmp_db, plan, monkeypatch, stub)
    calls = [json.loads(s)["role"]
             for s in (plan.cwd / "work" / "calls.jsonl").read_text().splitlines()]
    assert calls == ["draft", "audit", "revision"]
    assert h.state == RunState.DONE, h.ended_reason
    # Taken as each step ended; given back before each next step and at the end.
    assert len(taken) == 3 and len(given) == 3
    assert not (plan.run_dir / handover.HELD).exists()
    assert not (plan.run_dir / handover.SET_ASIDE).exists()
    assert not (plan.run_dir / handover.QUARANTINE).exists()
    # The folder ends as the controller left it, which is what was archived.
    workspace_reading = plan.cwd / "progress" / "reading"
    for name in ("guide.md", "companion.md", "companion.pdf", "review-log.md"):
        assert (workspace_reading / name).read_bytes() \
            == (plan.run_dir / "reading" / name).read_bytes()
    assert (plan.run_dir / "reading" / "guide.md").read_text() == GUIDE
    assert (plan.cwd / "progress" / "math.tex").read_text() == DOCUMENT


@pytest.mark.parametrize("moment", ["while typesetting", "before putting files back",
                                    "before copying for the next pass"])
@pytest.mark.parametrize("which", [1, 2])
async def test_links_put_in_the_folder_while_the_controller_works_are_never_followed(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, stub, outside, moment, which):
    plan = _plan(tmp_cfg, sample_problem)
    work = plan.cwd
    seen = {"n": 0}

    def at_the_moment() -> None:
        seen["n"] += 1
        if seen["n"] == which:
            _plant(work, outside)

    def export(reading, audience):
        if moment == "while typesetting":
            at_the_moment()
        return fake_exports(reading, audience)

    restore, lane = loop.restore_outside_lane, loop.take_lane_snapshot

    def restoring(state, writes, snapshot):
        if moment == "before putting files back":
            at_the_moment()
        return restore(state, writes, snapshot)

    def copying(state):
        if moment == "before copying for the next pass":
            at_the_moment()
        return lane(state)

    monkeypatch.setattr("atelier.runner.export_companion", export)
    monkeypatch.setattr(loop, "restore_outside_lane", restoring)
    monkeypatch.setattr(loop, "take_lane_snapshot", copying)
    h = await _read(tmp_cfg, tmp_db, plan, monkeypatch, stub)

    assert seen["n"] >= which, "the moment never came"
    _nothing_outside_changed(outside)
    _secret_nowhere(plan.run_dir)
    # Seen when the files were given back: the reading stopped, or at its very
    # end said so; what the links were among is kept, links and all.
    notices = _notices(plan)
    assert any(n.get("kind") == "access_policy" for n in notices), notices
    if h.state == RunState.FAILED:
        assert "Unsafe link or special file" in (h.error or "")
    kept = [p for p in (plan.run_dir / handover.QUARANTINE).rglob("*") if p.is_symlink()]
    assert kept
    # The AI's folder holds what the controller gave back: no link in it.
    assert not any(p.is_symlink() for p in (work / "progress").rglob("*"))


async def test_a_reading_whose_files_cannot_be_read_stops_without_touching_its_folder(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, stub):
    plan = _plan(tmp_cfg, sample_problem)

    def unreadable(*_args, **_kwargs):
        raise PermissionError("the working folder cannot be read")

    monkeypatch.setattr(handover, "collect", unreadable)
    monkeypatch.setattr("atelier.runner.export_companion", fake_exports)
    h = await _read(tmp_cfg, tmp_db, plan, monkeypatch, stub)
    assert h.state == RunState.FAILED
    assert "could not be read" in (h.error or "") or "could not be read" in h.ended_reason
    # Nothing was exported, restored, written or archived from the folder itself.
    reading = plan.cwd / "progress" / "reading"
    assert not (reading / "companion.md").exists() and not (reading / "review-log.md").exists()
    assert not (plan.run_dir / "reading").exists()


def test_the_next_pass_is_told_what_the_controllers_copy_holds(tmp_cfg, sample_problem, stub):
    plan = _plan(tmp_cfg, sample_problem)
    (plan.cwd / "progress" / "reading").mkdir(parents=True, exist_ok=True)
    (plan.cwd / "progress" / "reading" / "guide.md").write_text("# In the AI's folder\n")
    copy = plan.run_dir / handover.HELD
    (copy / "progress" / "reading").mkdir(parents=True)
    (copy / "progress" / "reading" / "guide.md").write_text("# In the controller's copy\n")
    document = Path(plan.extra["document"])
    (copy / document.relative_to(plan.cwd)).write_bytes(document.read_bytes())
    nxt = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2, files=copy)
    assert "In the controller's copy" in nxt.prompt
    assert "In the AI's folder" not in nxt.prompt
    # The pass itself is set up for the AI's folder, as ever.
    settings = json.loads((nxt.dir / "settings.json").read_text())
    assert settings["sandbox"]["filesystem"]["allowWrite"] == [str(plan.cwd)]
    assert str(copy) not in json.dumps(settings) and str(copy) not in nxt.system_append
    assert str(document) in nxt.system_append or "{document}" not in nxt.system_append


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_the_controllers_copy_is_out_of_every_pass_s_reach(tmp_cfg, sample_problem, stub,
                                                            provider):
    plan = _plan(tmp_cfg, sample_problem, provider)
    held = plan.run_dir / handover.HELD
    allowed: list[str] = []
    if provider == "claude":
        fs = json.loads((plan.first.dir / "settings.json").read_text())["sandbox"]["filesystem"]
        allowed = fs["allowRead"] + fs["allowWrite"]
    else:
        for item in plan.first.argv:
            if item.startswith("permissions.unpacman.filesystem="):
                allowed = [k for k in json.loads(
                    "{" + item.split("={", 1)[1].replace("=", ":"))]
    assert allowed, "no allowance found to check"
    for place in allowed:
        assert not (held.is_relative_to(place) or Path(place).is_relative_to(held)), place


def test_continuing_never_copies_a_link_from_the_earlier_reading(tmp_cfg, sample_problem, stub,
                                                                   outside):
    parent = _plan(tmp_cfg, sample_problem)
    reading = parent.cwd / "progress" / "reading"
    reading.mkdir(parents=True, exist_ok=True)
    (reading / "guide.md").write_text(GUIDE)
    (parent.run_dir / "result.json").write_text('{"state": "incomplete"}')
    (parent.cwd / "work" / "later.md").symlink_to(outside / "secret.txt")
    destination = tmp_cfg.runs_dir / "continued" / "workspace"
    with pytest.raises(ValueError, match="Unsafe link or special file"):
        copy_reading_work(tmp_cfg.runs_dir, parent.spec.run_id, sample_problem.slug, destination)
    assert not destination.exists()
    assert (parent.cwd / "work" / "later.md").is_symlink()
    _nothing_outside_changed(outside)
    # Without it, the same reading continues as before.
    (parent.cwd / "work" / "later.md").unlink()
    copy_reading_work(tmp_cfg.runs_dir, parent.spec.run_id, sample_problem.slug, destination)
    assert (destination / "progress" / "reading" / "guide.md").read_text() == GUIDE


def test_a_figure_named_by_its_path_in_the_ai_s_folder_is_read_from_the_copy(
        tmp_path, monkeypatch, outside):
    import re

    from deslopify.summary_pdf import _materialize_local_graphics, private_copy_of

    work = tmp_path / "run" / "workspace"
    (work / "progress" / "reading").mkdir(parents=True)
    (work / "progress" / "source.pdf").symlink_to(outside / "secret.txt")
    copy = tmp_path / "run" / handover.HELD
    (copy / "progress" / "reading").mkdir(parents=True)
    (copy / "progress" / "source.pdf").write_bytes(b"%PDF-1.4 the paper, as copied")
    latex = rf"\includegraphics[page=1]{{{work / 'progress' / 'source.pdf'}}}"
    staged = tmp_path / "staged"
    staged.mkdir()
    # As the AI saw its folder, and as an alias of it (/tmp and /private/tmp) —
    # on a Mac; Linux has no such alias.
    with private_copy_of(Path("/private") / work.relative_to("/") if sys.platform == "darwin"
                         and not str(work).startswith("/private/") else work, copy):
        out = _materialize_local_graphics(latex, copy / "progress" / "reading", staged)
    name = re.search(r"\{(figures/[^}]+)\}", out).group(1)
    assert (staged / name).read_bytes() == b"%PDF-1.4 the paper, as copied"
    _nothing_outside_changed(outside)


@pytest.mark.parametrize("named", [
    "progress/reading/figures/inside.png",        # where the skills keep figures
    "progress/source.pdf",                        # the paper itself
    "progress/reading/figures/none.png",          # missing
    "progress/reading/figures",                   # a folder
    "work/elsewhere.png",                         # outside the reading's files
    "work",                                       # a folder there
    "work/none.png",                              # missing there
    "progress/../work/elsewhere.png",             # there, by way of ..
    "../beside.png",                              # outside the AI's folder altogether
])
def test_a_figure_fares_from_the_copy_exactly_as_in_the_ai_s_folder(tmp_path, named):
    """Typeset from the controller's copy, a guide's figure is used, or refused
    in the same words, as when the export ran in the AI's folder itself
    (2026-11-02: the words for one outside the reading's files)."""
    import re

    from deslopify.summary_pdf import (SummaryPdfError, _materialize_local_graphics,
                                       private_copy_of)

    run = tmp_path / "run"
    work = run / "workspace"
    (work / "progress" / "reading" / "figures").mkdir(parents=True)
    (work / "progress" / "reading" / "figures" / "inside.png").write_bytes(b"\x89PNG inside")
    (work / "progress" / "source.pdf").write_bytes(b"%PDF-1.4 the paper")
    (work / "work").mkdir()
    (work / "work" / "elsewhere.png").write_bytes(b"\x89PNG elsewhere")
    (run / "beside.png").write_bytes(b"\x89PNG beside")
    latex = rf"\includegraphics[width=3cm]{{{work}/{named}}}"

    def fate(reading: Path, staged: Path) -> bytes | str:
        staged.mkdir()
        try:
            out = _materialize_local_graphics(latex, reading, staged)
        except SummaryPdfError as exc:
            return str(exc)
        return (staged / re.search(r"\{(figures/[^}]+)\}", out).group(1)).read_bytes()

    in_the_folder = fate(work / "progress" / "reading", tmp_path / "before")
    took = handover.collect(work, run / handover.HELD, ["progress"], run / handover.QUARANTINE)
    with private_copy_of(work, took.root, left_out=took.left_out):
        from_the_copy = fate(took.root / "progress" / "reading", tmp_path / "after")
    assert from_the_copy == in_the_folder


async def test_a_figure_outside_the_reading_s_files_is_refused_in_the_old_words(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, stub):
    """Through the runner: a guide's figure in the AI's work/ folder is refused
    as outside this paper's working files — in the notices and to the next
    step — as before the handover, not as missing (the export is the real
    one; it refuses before any LaTeX runs)."""
    plan = _plan(tmp_cfg, sample_problem)
    figure = plan.cwd / "work" / "elsewhere.png"
    figure.write_bytes(b"\x89PNG elsewhere")
    guide = GUIDE + f"\n$$\n\\includegraphics[width=3cm]{{{figure}}}\n$$\n"
    await _read(tmp_cfg, tmp_db, plan, monkeypatch, stub, mode="normal", guide=guide)
    said = [n.get("message", "") for n in _notices(plan)]
    refused = [m for m in said if "Referenced graphic" in m]
    assert refused, said
    assert all(f"outside this paper's working files: {figure}" in m for m in refused), refused
    prompt = (plan.run_dir / "pass-2" / "prompt.md").read_text()
    assert f"Referenced graphic is outside this paper's working files: {figure}" in prompt
    assert "Referenced graphic does not exist" not in prompt
