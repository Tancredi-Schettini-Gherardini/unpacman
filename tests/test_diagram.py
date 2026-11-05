"""Requested pure-paper diagrams survive drafting and get independent review."""

from dataclasses import replace
import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from atelier import loop
from atelier.compile import plan_next_pass, plan_run
from atelier.delivery import DIAGRAM_FILES, diagram_problems
from atelier.events import EventHub
from atelier.harnesses import get_harness
from atelier.runner import RunHandle, RunState
from atelier.spec import RunSpec
from deslopify.app.server import _reading_harness
from deslopify.summary_pdf import dated_guide
from test_loop_run import DOCUMENT, GRAPH, GUIDE


def reading_plan(cfg, problem, requested=True, audience="expert"):
    problem.ensure_scaffold()
    (problem.progress_dir / "math.tex").write_text(DOCUMENT)
    harness = _reading_harness(get_harness(cfg.harnesses_dir, f"deslopify-{audience}"),
                               include_map=requested)
    return plan_run(cfg, problem, harness, RunSpec(
        problem=problem.slug, harness=harness.id, provider="codex", account="codex",
        model="test-model"), isolate_reading=True)


def save_draft(plan, profile):
    reading = plan.cwd / "progress/reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text(GUIDE)
    (reading / "paper-profile.json").write_text(json.dumps({
        "papers": [{"profile": profile, "source": "progress/math.tex"}]}))
    (plan.cwd / "progress/_session_notes.md").write_text("Saved source analysis.")
    return reading


def save_diagram(reading):
    (reading / "graph.json").write_text(json.dumps(GRAPH))
    (reading / "map.mmd").write_text("graph TD\n H0 --> Q1\n")
    (reading / "map.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")


def save_audit(reading):
    (reading / "companion.md").write_text(dated_guide(reading / "guide.md") + "\n")
    (reading / "companion.tex").write_text("Compiled source.")
    (reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (reading / "review-1.json").write_text(json.dumps({
        "round": 1, "summary": "Checked against the source.", "findings": []}))


@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize("profile", ["pure", "applied"])
@pytest.mark.parametrize("audience", ["expert", "nonexpert"])
def test_writer_diagram_requires_both_request_and_pure_profile(
        tmp_cfg, sample_problem, requested, profile, audience):
    plan = reading_plan(tmp_cfg, sample_problem, requested, audience)
    reading = save_draft(plan, profile)
    save_diagram(reading)  # Even an unsolicited diagram must not survive the handoff.
    expected = requested and profile == "pure"
    assert ("# Optional one-page diagram" in plan.system_append) is requested
    state = loop.start(plan.harness, plan.cwd, plan.run_dir, Path(plan.extra["document"]))
    outcome = loop.assess(state, 1, "draft", 0, None, ok=True)
    assert not outcome.stop
    for name in DIAGRAM_FILES:
        assert (reading / name).exists() is expected
        assert (state.snapshot_dir(0) / name).exists() is expected
    assert diagram_problems(reading, requested, DOCUMENT) == []


def test_auditor_cannot_change_the_writers_diagram(tmp_cfg, sample_problem):
    plan = reading_plan(tmp_cfg, sample_problem)
    reading = save_draft(plan, "pure")
    save_diagram(reading)
    originals = {name: (reading / name).read_bytes() for name in DIAGRAM_FILES}
    state = loop.start(plan.harness, plan.cwd, plan.run_dir, Path(plan.extra["document"]))
    loop.assess(state, 1, "draft", 0, None, ok=True)
    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    assert not set(audit.writes) & {f"progress/reading/{name}" for name in DIAGRAM_FILES}
    assert "do not edit diagram files" in audit.system_append
    for name in DIAGRAM_FILES:
        (reading / name).write_bytes(b"unauthorized auditor edit")
    save_audit(reading)
    outcome = loop.assess(state, 2, "auditor", 1, plan.harness.roles["auditor"],
                          ok=True, writes=audit.writes)
    assert state.completed, outcome
    assert {name: (reading / name).read_bytes() for name in DIAGRAM_FILES} == originals


def test_missing_requested_diagram_requires_repair_even_with_a_clean_audit(
        tmp_cfg, sample_problem):
    plan = reading_plan(tmp_cfg, sample_problem)
    reading = save_draft(plan, "pure")
    state = loop.start(plan.harness, plan.cwd, plan.run_dir, Path(plan.extra["document"]))
    loop.assess(state, 1, "draft", 0, None, ok=True)
    save_audit(reading)
    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    outcome = loop.assess(state, 2, "auditor", 1, plan.harness.roles["auditor"],
                          ok=True, writes=audit.writes)
    assert not state.completed
    assert state.pending() == ("balanced-reviser", 1)
    assert any("graph.json" in task for task in outcome.repair_tasks)
    revision = plan_next_pass(tmp_cfg, plan, role_name="balanced-reviser", round_no=1, index=3)
    assert {f"progress/reading/{name}" for name in DIAGRAM_FILES} <= set(revision.writes)
    save_diagram(reading)
    (reading / "dispositions-1.json").write_text('{"round":1,"dispositions":[]}')
    outcome = loop.assess(state, 3, "balanced-reviser", 1,
                          plan.harness.roles["balanced-reviser"], ok=True,
                          writes=revision.writes)
    assert state.completed, outcome


@pytest.mark.parametrize("defect,message", [
    ("quotation", "quotation does not appear"), ("missing_pdf", "map.pdf"),
    ("stale_map", "older than graph.json"),
])
def test_diagram_validation_checks_evidence_and_rendered_outputs(tmp_path, defect, message):
    (tmp_path / "paper-profile.json").write_text('{"papers":[{"profile":"pure"}]}')
    save_diagram(tmp_path)
    if defect == "quotation":
        graph = json.loads((tmp_path / "graph.json").read_text())
        graph["nodes"][0]["quote"] = "This quotation never appeared in the source."
        (tmp_path / "graph.json").write_text(json.dumps(graph))
    elif defect == "missing_pdf":
        (tmp_path / "map.pdf").unlink()
    else:
        os.utime(tmp_path / "map.mmd", (1, 1))
    assert any(message in problem for problem in diagram_problems(tmp_path, True, DOCUMENT))


@pytest.mark.parametrize("profile,has_map,exit_code", [
    ("pure", False, 2), ("pure", True, 0), ("applied", False, 0),
])
def test_writer_completion_hook_uses_the_requested_paper_profile(
        tmp_cfg, sample_problem, monkeypatch, profile, has_map, exit_code):
    from atelier.hooks import check_reading

    plan = reading_plan(tmp_cfg, sample_problem)
    reading = save_draft(plan, profile)
    if has_map:
        save_diagram(reading)
    monkeypatch.setattr(check_reading, "read_payload", lambda: {})
    monkeypatch.setattr(check_reading, "parse_args", lambda: SimpleNamespace(
        problem=plan.cwd, run=plan.run_dir, role="draft", round=0,
        validate="draft", contract="companion", expect="progress/reading/guide.md"))
    with pytest.raises(SystemExit) as stopped:
        check_reading.main()
    assert stopped.value.code == exit_code


@pytest.mark.parametrize("profile,has_map,first_role", [
    ("pure", False, "draft"), ("pure", True, "auditor"),
    ("applied", False, "auditor"), (None, False, "draft"),
])
def test_continuation_finishes_a_missing_diagram_before_review(
        tmp_cfg, sample_problem, profile, has_map, first_role):
    parent = reading_plan(tmp_cfg, sample_problem)
    reading = save_draft(parent, profile)
    if profile is None:
        (reading / "paper-profile.json").unlink()
    if has_map:
        save_diagram(reading)
    (parent.run_dir / "result.json").write_text('{"state":"incomplete"}')
    plan = plan_run(tmp_cfg, parent.problem, parent.harness, replace(
        parent.spec, run_id="continued-diagram", mode="continue",
        parent_run_id=parent.spec.run_id), isolate_reading=True)
    assert plan.first.role == first_role
    assert (reading / "guide.md").read_text() == GUIDE


@pytest.mark.parametrize("requested,profile", [(False, "pure"), (True, "applied")])
async def test_interrupted_writer_does_not_deliver_an_ineligible_diagram(
        tmp_cfg, tmp_db, sample_problem, requested, profile):
    plan = reading_plan(tmp_cfg, sample_problem, requested)
    reading = save_draft(plan, profile)
    save_diagram(reading)
    save_audit(reading)
    handle = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    try:
        handle._reading_outcome(RunState.CANCELLED, "run budget exhausted")
        assert not any((reading / name).exists() for name in DIAGRAM_FILES)
    finally:
        handle.log.close()


@pytest.mark.parametrize("has_map", [False, True])
async def test_final_delivery_requires_the_requested_diagram(tmp_cfg, tmp_db, sample_problem, has_map):
    plan = reading_plan(tmp_cfg, sample_problem)
    reading = save_draft(plan, "pure")
    save_audit(reading)
    if has_map:
        save_diagram(reading)
    handle = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    try:
        state, reason = handle._reading_outcome(RunState.DONE, "completed normally")
        assert state == (RunState.DONE if has_map else RunState.INCOMPLETE)
        if not has_map:
            assert "graph.json" in reason
    finally:
        handle.log.close()


@pytest.mark.parametrize("role", ["draft", "balanced-reviser"])
@pytest.mark.parametrize("requested,profile", [
    (False, "pure"), (False, "applied"), (True, "pure"), (True, "applied"),
])
async def test_missing_diagram_is_retried_within_the_stage_budget(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, role, requested, profile):
    plan = reading_plan(tmp_cfg, sample_problem, requested)
    reading = save_draft(plan, profile)
    save_audit(reading)
    (reading / "dispositions-1.json").write_text('{"round":1,"dispositions":[]}')
    handle = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    handle.loop_state = loop.start(plan.harness, plan.cwd, plan.run_dir,
                                   Path(plan.extra["document"]))
    if role != "draft":
        handle.current = plan_next_pass(tmp_cfg, plan, role_name=role, round_no=1, index=3)
    handle._provider_session_id = "diagram-session"
    resumed = []

    async def begin(next_pass, **kwargs):
        resumed.append(next_pass)

    async def exported():
        pass

    monkeypatch.setattr(handle, "_begin_pass", begin)
    monkeypatch.setattr(handle, "_export_reading", exported)
    try:
        expected = requested and profile == "pure"
        assert await handle._complete_writer_if_needed(ok=True) is expected
        if expected:
            assert "graph.json" in resumed[0].prompt
            assert "diagram-session" in resumed[0].argv
        else:
            assert not resumed
    finally:
        handle.log.close()


def test_diagram_commands_and_audit_respect_the_supplied_source_scope(tmp_cfg, sample_problem):
    sample_problem.ensure_scaffold()
    early = "Clearly the initial hypothesis needs justification."
    late = "Clearly this late assertion lies beyond the supplied source scope."
    (sample_problem.progress_dir / "source.md").write_text(
        "START OF PAPER\n" + early + "\n" + "An early result.\n" * 15_000 + late + "\n")
    harness = _reading_harness(get_harness(tmp_cfg.harnesses_dir, "deslopify-expert"),
                               include_map=True)
    plan = plan_run(tmp_cfg, sample_problem, harness, RunSpec(
        problem=sample_problem.slug, harness=harness.id, provider="codex", account="codex",
        model="test-model"), isolate_reading=True, source_char_limit=100_000)
    save_draft(plan, "pure")
    command = next(line for line in plan.system_append.splitlines()
                   if " -m deslopify appeals " in line)
    result = subprocess.run(shlex.split(command), cwd=plan.cwd,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert early in result.stdout
    assert late not in result.stdout
    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    assert early in audit.prompt
    assert late not in audit.prompt


async def test_diagram_checks_use_the_controller_copy_after_handover(
        tmp_cfg, tmp_db, sample_problem):
    plan = reading_plan(tmp_cfg, sample_problem)
    reading = save_draft(plan, "pure")
    save_diagram(reading)
    save_audit(reading)
    handle = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    try:
        assert handle._take_files() == []
        held = handle._files / "progress/reading"
        assert held != reading
        # A late AI write must not change the controller's diagram decisions.
        reading.mkdir(parents=True, exist_ok=True)
        save_draft(plan, "applied")
        save_diagram(reading)
        assert handle._diagram_problems() == []
        state, reason = handle._reading_outcome(RunState.DONE, "completed normally")
        assert state == RunState.DONE, reason
        assert all((held / name).is_file() for name in DIAGRAM_FILES)
        assert all((reading / name).is_file() for name in DIAGRAM_FILES)
    finally:
        handle.log.close()
