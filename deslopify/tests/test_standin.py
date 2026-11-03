"""The stand-in AI and the test server that uses it (deslopify/tools).

Both must keep up with the reader, or free tests stop being free or stop
working — and nothing else in the suite would notice (2026-10-15): the
method's checks grew stricter, so every stand-in reading ended "incomplete";
and once Getting ready had installed a Codex in ~/.local/bin, it put that
folder first on PATH, ahead of the stand-ins, and a test server ran the real
Claude Code.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

from atelier.hooks import check_reading
from deslopify.app import ready

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_stand_in_companion_passes_the_methods_own_check(tmp_path):
    standin = load(TOOLS / "standin" / "standin.py")
    guide = tmp_path / "guide.md"
    guide.write_text(standin.GUIDE.format(model="deepseek-v4-pro[1m]", role="draft"))
    missing: list[str] = []
    check_reading.check_guide(tmp_path, missing)
    assert missing == []
    # Companions written with the former singular heading remain valid.
    guide.write_text(guide.read_text().replace("About the proofs", "About the proof"))
    check_reading.check_guide(tmp_path, missing)
    assert missing == []
    # And still after the stand-in revision's edit.
    guide.write_text(guide.read_text().replace(
        "count its sections, and the count is seven.",
        "count its sections (the headings), and the count is seven."))
    check_reading.check_guide(tmp_path, missing)
    assert missing == []


def test_worked_examples_and_thin_section_are_optional_for_guides(tmp_path):
    standin = load(TOOLS / "standin" / "standin.py")
    guide = tmp_path / "guide.md"
    pure = standin.GUIDE.format(model="test-model", role="draft").replace(
        "## Worked examples\n\nTake this companion: count its sections, and the count is seven.\n\n",
        "",
    )
    assert "## Worked examples" not in pure
    applied = pure.replace("## Main results", "## Main findings").replace(
        "## Main ideas\n\nThe central idea is that a worked example makes an abstract statement concrete.\n\n",
        "",
    ).replace("## About the proofs\n\nThe proof is a count of the sections, checked one by one.\n\n",
              "").replace("## Where the document is thin",
                          "## About the method and experimental validation\n\n"
                          "The proof is a count of the sections, checked one by one.\n\n"
                          "## Where the document is thin")
    legacy_applied = applied.replace("## Main findings", "## Main results").replace(
        "## About the method and experimental validation",
        "## About the validation, experiments, and proofs")
    without_thin = applied.replace(
        "## Where the document is thin\n\nNothing here is mathematics; it is a fixture.\n\n",
        "")
    for text in (pure, applied, legacy_applied, without_thin):
        guide.write_text(text)
        missing: list[str] = []
        check_reading.check_guide(tmp_path, missing)
        assert missing == []


def test_the_stand_in_records_the_profile_its_guide_is_checked_against(tmp_path, monkeypatch):
    standin = load(TOOLS / "standin" / "standin.py")
    monkeypatch.setattr(standin, "companion_files", lambda *a, **k: None)
    standin.do_pass(str(tmp_path), "draft", "claude-haiku-4-5", 1, "companion",
                    "Read SKILL-pure-nonexpert.md for a non-expert reader.")
    profile = json.loads((tmp_path / "progress/reading/paper-profile.json").read_text())
    assert [(p["profile"], p["skill"]) for p in profile["papers"]] == [
        ("pure", "SKILL-pure-nonexpert.md")]


def test_a_test_server_keeps_its_stand_ins_ahead_of_the_real_programs(tmp_path, monkeypatch):
    server = load(TOOLS / "isolated_server.py")
    standin = TOOLS / "standin"
    home = tmp_path / "home"
    mine = home / ".local" / "bin"
    mine.mkdir(parents=True)
    for name in ("claude", "codex"):
        program = mine / name
        program.write_text("#!/bin/sh\nexit 1\n")
        program.chmod(0o755)
    # Whatever keep_standin_first replaces is put back after the test.
    monkeypatch.setattr(ready, "prepare_environment", ready.prepare_environment)
    monkeypatch.setattr(ready, "_prepend_path", ready._prepend_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("PATH", os.pathsep.join([str(standin), str(mine), "/usr/bin", "/bin"]))

    # Unwrapped, Getting ready would put the real programs first.
    env = {"PATH": os.environ["PATH"]}
    ready.prepare_environment(env, home)
    assert env["PATH"].split(os.pathsep)[0] == str(mine)

    server.keep_standin_first(standin)
    for name in ("claude", "codex"):
        assert Path(shutil.which(name)).parent == standin
    ready.prepare_environment(home=home)               # as each measure does
    ready._prepend_path(str(mine))                     # as an install does
    for name in ("claude", "codex"):
        assert Path(shutil.which(name)).parent == standin


def test_the_stand_in_knows_its_step_without_a_stop_hook(tmp_path, monkeypatch):
    """Reading passes have had no Stop hook since Antoine's fa61b08
    (2026-10-16), and the stand-in had read its role from it: every pass then
    took itself for the draft, so every stand-in reading stopped as "Draft —
    review unfinished". Its step is now the stage the controller records
    before each pass; its round, the review file it is asked for."""
    standin = load(TOOLS / "standin" / "standin.py")
    run = tmp_path / "runs" / "R"
    (run / "loop").mkdir(parents=True)
    (run / "pass-2").mkdir()
    (run / "pass-2" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": []}}))
    work = tmp_path / "workspace"
    (work / "progress").mkdir(parents=True)

    def stage(role):
        (run / "loop" / "time-remaining.json").write_text(json.dumps({"stage": role}))

    # The first pass: its folder is the run's own.
    stage("draft")
    monkeypatch.setenv("ATELIER_RUN_DIR", str(run))
    assert standin.pass_role([], "Write progress/reading/guide.md.", str(work)) == \
        ("draft", 0, "companion")
    # A later pass: its folder is `pass-N`, the controller's record one above.
    stage("auditor")
    monkeypatch.setenv("ATELIER_RUN_DIR", str(run / "pass-2"))
    argv = ["--settings", str(run / "pass-2" / "settings.json")]
    assert standin.pass_role(argv, "write `progress/reading/review-1.json`", str(work)) == \
        ("auditor", 1, "companion")
    # A later round quotes the earlier one too: the newest is the round.
    stage("balanced-reviser")
    asked = "earlier: review-1.json … answer review-2.json in dispositions-2.json"
    assert standin.pass_role(argv, asked, str(work))[:2] == ("balanced-reviser", 2)
    # Several papers, or a search, have a source.md: the "guide" contract.
    (work / "progress" / "source.md").write_text("## Paper 1 of 2\n")
    assert standin.pass_role(argv, asked, str(work))[2] == "guide"
    # A pass that still has a Stop hook (a solving harness) is read from it.
    (run / "pass-2" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"command": "python check_contract.py --role critic --round 3"}]}]}}))
    assert standin.pass_role(argv, asked, str(work)) == ("critic", 3, "companion")
