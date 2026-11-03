"""Delivery uses real controller transitions and local stand-ins, never model APIs."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
import pytest

from atelier.compile import plan_next_pass, plan_run, plan_writer_completion
from atelier.config import Account
from atelier.delivery import ensure_source_scope_notice, guide_problems
from atelier.events import EventHub
from atelier.harnesses import get_harness
from atelier.providers import get_adapter
from atelier.runner import RunHandle, RunState
from atelier.spec import RunSpec
from test_loop_run import DOCUMENT, GUIDE


def test_partial_source_notice_is_inserted_per_paper_and_is_idempotent(tmp_path):
    reading = tmp_path / "reading"
    reading.mkdir()
    guide = reading / "guide.md"
    guide.write_text(
        "# Collection\n\n## Paper 1: A\n\n### Main results\n\nA result.\n\n"
        "## Paper 2: B\n\n### Main findings\n\nB finding.\n\n"
        "## Paper 3: C\n\n### Main results\n\nC result.\n")
    scope = tmp_path / "source-scope.json"
    scope.write_text(json.dumps({"collection": True, "omitted": ["A", "C"],
                                 "omitted_indices": [1, 3]}))
    ensure_source_scope_notice(reading, scope)
    first = guide.read_text()
    assert first.count("source text was too long") == 2
    assert first.index("source text was too long") < first.index("## Paper 2: B")
    assert "source text was too long" not in first.split("## Paper 2: B")[1].split(
        "## Paper 3: C")[0]
    ensure_source_scope_notice(reading, scope)
    assert guide.read_text() == first


def test_partial_solo_notice_sits_in_main_results(tmp_path):
    reading = tmp_path / "reading"
    reading.mkdir()
    guide = reading / "guide.md"
    guide.write_text("# Paper\n\n## Main results\n\nA result.\n\n## Historical context\n\nHistory.\n")
    scope = tmp_path / "source-scope.json"
    scope.write_text(json.dumps({"collection": False, "omitted": ["the paper"]}))
    ensure_source_scope_notice(reading, scope)
    text = guide.read_text()
    assert text.index("## Main results") < text.index("source text was too long")
    assert text.index("source text was too long") < text.index("A result.")


async def test_collection_exports_between_passes_without_exporting_search(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch):
    calls = []

    def fake_export(reading, audience):
        calls.append(audience)
        for extension in ("md", "tex", "pdf"):
            (reading / f"companion.{extension}").write_bytes(
                b"%PDF-1.4\n%%EOF\n" if extension == "pdf" else b"current")

    monkeypatch.setattr("atelier.runner.export_companion", fake_export)
    reading = delivery_plan.cwd / "progress" / "reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text("# Collection\n\n## Paper 1: A\n\nA result.\n")
    for audience in ("expert", "nonexpert"):
        harness = get_harness(tmp_cfg.harnesses_dir, f"deslopify-multi-{audience}")
        handle = RunHandle(tmp_cfg, replace(delivery_plan, harness=harness), EventHub(), tmp_db)
        await handle._export_reading()
        handle.log.close()
    search = get_harness(tmp_cfg.harnesses_dir, "deslopify-search")
    handle = RunHandle(tmp_cfg, replace(delivery_plan, harness=search), EventHub(), tmp_db)
    await handle._export_reading()
    handle.log.close()
    assert calls == ["expert", "non-expert"]


def test_reading_allocations_and_remaining_time_are_enforced(delivery_plan, tmp_cfg):
    from atelier.delivery import time_targets
    roles = ["draft", "auditor", "balanced-reviser"]
    assert time_targets(30, roles) == {"draft": 20, "auditor": 6, "balanced-reviser": 4}
    assert time_targets(60, roles) == {"draft": 40, "auditor": 12, "balanced-reviser": 8}
    assert time_targets(15, roles) == {"draft": 7.5, "auditor": 4.5, "balanced-reviser": 3}
    assert time_targets(11, roles) == {"draft": 5.5, "auditor": 3.3, "balanced-reviser": 2.2}
    assert time_targets(10, roles) == {"draft": 5, "auditor": 3, "balanced-reviser": 2}
    assert time_targets(8, roles) == {"draft": 4, "auditor": 2.4, "balanced-reviser": 1.6}
    audit = plan_next_pass(tmp_cfg, delivery_plan, role_name="auditor", round_no=1,
                          index=2, remaining_minutes=3)
    assert "Approximately 8 min remain" in audit.prompt
    assert "about 6.8 min" in audit.prompt
    assert "Stage handoffs are enforced" in audit.prompt
    assert "every required section" in delivery_plan.prompt


def test_standard_and_continuation_runs_preserve_the_same_checking_reserve():
    from atelier.delivery import reading_run_limit, reading_stage_deadline, stage_time_available, time_targets
    from atelier.compile import reading_budget_line
    roles = ["draft", "auditor", "balanced-reviser"]
    assert time_targets(60, roles) == {"draft": 40, "auditor": 12, "balanced-reviser": 8}
    assert time_targets(20, roles) == {"draft": 10, "auditor": 6, "balanced-reviser": 4}
    assert time_targets(59, roles) == {"draft": 49, "auditor": 6, "balanced-reviser": 4}
    assert reading_stage_deadline(20, roles, 0, 0) == 10
    assert reading_stage_deadline(20, roles, 0, 9) == 10  # no retry reset
    assert reading_stage_deadline(11, roles, 0, 5) == 5.5
    assert reading_stage_deadline(60, roles, 0, 39) == 40
    assert reading_stage_deadline(10, roles, 0, 0) == 5
    assert reading_stage_deadline(10, roles, 0, 4) == 5
    assert reading_stage_deadline(10, roles, 1, 5) == 8
    assert reading_stage_deadline(10, roles, 1, 0) == 6  # complete draft
    assert reading_stage_deadline(10, roles, 1, 2) == pytest.approx(6.8)
    assert reading_stage_deadline(10, roles, 2, 8) is None
    assert reading_stage_deadline(None, roles, 0, 0) is None
    assert reading_stage_deadline(10, ["draft"], 0, 0) is None
    assert reading_run_limit(30) == 30
    assert reading_run_limit(30, review=True) == 35
    assert reading_run_limit(30, revision=True) == 35
    assert reading_run_limit(30, revision=True, review_extra_used=5) == 40
    assert reading_stage_deadline(10, roles, 1, 5, review=True) == 13
    assert stage_time_available(30, roles, 2, 26, revision=True) == 9
    assert stage_time_available(30, roles, 1, 20, review=True) == 11
    prompt = reading_budget_line(60, roles, "draft", continuation=True)
    assert "Drafting stops at 40 min" in prompt and "reserving 20 min" in prompt
    assert "flexible, not hard caps" not in prompt
    revision = reading_budget_line(30, roles, "balanced-reviser", remaining=4,
                                   revision=True)
    assert "ending by 35 min elapsed" in revision
    assert "approximately 9 min remain" in revision.lower()
    assert "about 9 min" in revision
    # The review's five minutes are separate: only time actually spent shifts
    # the revision deadline, while its original remaining time is preserved.
    for used in (0, 2, 5):
        assert stage_time_available(10, roles, 2, 8 + used, revision=True,
                                    review_extra_used=used) == 7
    review = reading_budget_line(10, roles, "auditor", remaining=5, review=True)
    assert "about 8 min" in review
    assert "5 min for review and 5 min for revision" in review


@pytest.mark.parametrize("mode,role,elapsed,expected", [
    ("continue", "draft", 300, True),
    ("continue", "draft", 299, False),
    ("continue", "auditor", 480, False),
    ("continue", "auditor", 779, False),
    ("continue", "auditor", 780, True),
    ("continue", "balanced-reviser", 590, False),
    ("standard", "draft", 300, True),
    ("standard", "draft", 299, False),
    ("standard", "auditor", 480, False),
    ("standard", "auditor", 779, False),
    ("standard", "auditor", 780, True),
    ("standard", "balanced-reviser", 590, False),
])
async def test_watchdog_enforces_reading_stage_deadlines(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch, mode, role, elapsed, expected):
    from dataclasses import replace
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    h.plan.spec.mode = mode
    h.plan.budget_minutes = 10
    h.loop_state = object()  # watchdog only needs to know a loop exists
    index = ["draft", "auditor", "balanced-reviser"].index(role) + 1
    h.current = replace(h.current, role=role, index=index)
    h._pass_started_at = 300 if role == "auditor" else 0
    monkeypatch.setattr(type(h), "elapsed", property(lambda _: elapsed))
    monkeypatch.setattr("atelier.runner.WATCHDOG_INTERVAL", 0.001)
    stops = []
    async def stop_pass(reason):
        stops.append(reason)
        h.state = RunState.DONE
    monkeypatch.setattr(h, "_stop_pass", stop_pass)
    task = asyncio.create_task(h._watchdog())
    await asyncio.sleep(0.015)
    h.state = RunState.DONE
    await task
    assert bool(stops) is expected
    h.log.close()


@pytest.mark.parametrize("role,used,elapsed,should_stop", [
    ("auditor", 0, 600, False),
    ("auditor", 0, 900, True),
    ("balanced-reviser", 0, 899, False),
    ("balanced-reviser", 0, 900, True),
    ("balanced-reviser", 2, 1019, False),
    ("balanced-reviser", 2, 1020, True),
    ("balanced-reviser", 5, 1199, False),
    ("balanced-reviser", 5, 1200, True),
])
async def test_review_and_revision_have_separate_five_minute_allowances(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch, role, used, elapsed, should_stop):
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    h.plan.budget_minutes = 10
    h.plan.extra["review_extra_minutes"] = used
    h.loop_state = object()
    h.current = replace(h.current, role=role,
                        index=2 if role == "auditor" else 3)
    h._pass_started_at = 480
    monkeypatch.setattr(type(h), "elapsed", property(lambda _: elapsed))
    monkeypatch.setattr("atelier.runner.WATCHDOG_INTERVAL", 0.001)
    stopped = []
    async def stop(reason):
        stopped.append(reason)
        h.state = RunState.DONE
    monkeypatch.setattr(h, "stop", stop)
    task = asyncio.create_task(h._watchdog())
    await asyncio.sleep(0.015)
    h.state = RunState.DONE
    await task
    assert bool(stopped) is should_stop
    assert h._run_time_limit() == (15 + used if role == "balanced-reviser" else 15)
    h.log.close()


@pytest.mark.parametrize("elapsed,used", [(470, 0), (600, 2), (780, 5)])
async def test_review_handoff_carries_only_spent_grace_to_revision(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch, elapsed, used):
    from atelier import loop

    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    h.plan.budget_minutes = 10
    h.loop_state = loop.start(delivery_plan.harness, delivery_plan.cwd,
                              delivery_plan.run_dir, delivery_plan.cwd / "progress/math.tex")
    reading = delivery_plan.cwd / "progress/reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text(
        "# Guide\n\n## Main results\n\nSaved result.\n\n## Historical context\n\n")
    loop.assess(h.loop_state, 1, "draft", 0, None, ok=True)
    loop.take_lane_snapshot(h.loop_state)
    h.current = plan_next_pass(tmp_cfg, delivery_plan, role_name="auditor",
                               round_no=1, index=2, remaining_minutes=5)
    h._pass_started_at = 300
    (reading / "review-1.json").write_text(
        '{"round":1,"summary":"The draft needs completion.","findings":[]}')
    monkeypatch.setattr(type(h), "elapsed", property(lambda _: elapsed))
    monkeypatch.setattr("atelier.runner.export_companion", fake_exports)
    launched = []
    async def begin(plan):
        launched.append(plan)
    monkeypatch.setattr(h, "_begin_pass", begin)
    assert await h._advance(ok=True)
    assert h.plan.extra["review_extra_minutes"] == used
    assert launched[0].role == "balanced-reviser"
    assert h._run_time_limit("balanced-reviser") == 15 + used
    assert f"ending by {15 + used} min elapsed" in launched[0].prompt
    h.log.close()


@pytest.mark.parametrize("provider", ["codex", "claude", "open"])
@pytest.mark.parametrize("elapsed", [539.9, 540])
async def test_wrapup_warning_matches_the_clock_and_uses_supported_steering(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch, provider, elapsed):
    from atelier.time_remaining import state_path, time_remaining

    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    h.loop_state = object()
    h._adapter = get_adapter(provider)
    h._stage_time_allocation = 600
    monkeypatch.setattr(type(h), "elapsed", property(lambda _: elapsed))
    monkeypatch.setattr("atelier.runner.WATCHDOG_INTERVAL", 0.001)
    sent = []
    async def write(payload):
        sent.append(json.loads(payload))
        return True
    monkeypatch.setattr(h, "_write_stdin", write)
    h._write_time_remaining()
    task = asyncio.create_task(h._watchdog())
    await asyncio.sleep(0.02)
    h.state = RunState.DONE
    await task
    timing = json.loads(state_path(delivery_plan.run_dir).read_text())
    report = time_remaining(timing, now=timing["sampled_at"])
    assert report["wrap_up"] is (elapsed >= 540)
    assert len(sent) == int(elapsed >= 540 and provider != "codex")
    if sent:
        message = sent[0]["message"]["content"][0]["text"]
        assert "90% of this stage's time allocation" in message
        assert "60 seconds remain in this stage" in message
    events = [json.loads(s) for s in (delivery_plan.run_dir / "events.jsonl").read_text().splitlines()]
    warnings = [e for e in events if e["type"] == "budget" and e["payload"]["level"] == "wrapup"]
    assert len(warnings) == int(elapsed >= 540)
    h.log.close()


@pytest.mark.parametrize("total,deadline", [
    (8, 4), (10, 5), (11, 5.5), (15, 7.5), (20, 10), (40, 30), (60, 40), (75, 55),
])
async def test_drafting_retry_cannot_use_the_checking_reserve(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch, total, deadline):
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    h.plan.budget_minutes = total
    h.loop_state = object()
    h._provider_session_id = "writer-session"
    monkeypatch.setattr(type(h), "elapsed", property(lambda _: deadline * 60))
    assert not await h._complete_writer_if_needed(ok=True)
    assert not (delivery_plan.run_dir / "writer-completion").exists()
    h.log.close()


async def test_a_late_draft_can_start_review_with_the_remaining_budget(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch):
    from atelier import loop
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    h.loop_state = loop.start(delivery_plan.harness, delivery_plan.cwd,
                              delivery_plan.run_dir, delivery_plan.cwd / "progress/math.tex")
    reading = delivery_plan.cwd / "progress/reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text(GUIDE)
    monkeypatch.setattr("atelier.runner.export_companion", fake_exports)
    monkeypatch.setattr(h, "budget_fraction", lambda: 0.96)
    launched = []
    async def begin(plan): launched.append(plan.role)
    monkeypatch.setattr(h, "_begin_pass", begin)
    assert await h._advance(ok=True)
    assert launched == ["auditor"]
    h.log.close()


@pytest.mark.parametrize("complete", [False, True])
def test_continuation_preserves_work_in_a_new_workspace(tmp_cfg, delivery_plan, complete):
    parent = delivery_plan
    reading = parent.cwd / "progress/reading"
    reading.mkdir(exist_ok=True)
    guide = (GUIDE if complete else
             "# Guide\n\n## Main results\n\nSaved explanation.\n\n## Historical context\n\n")
    (reading / "guide.md").write_text(guide)
    (reading / "figures").mkdir()
    (reading / "figures/saved.png").write_bytes(b"saved figure")
    (reading / "review-1.json").write_text('{"round":1,"summary":"Earlier audit","findings":[]}')
    (parent.cwd / "work/research.json").write_text('{"source":"a checked source"}')
    (parent.run_dir / "notes.md").write_text("Saved research notes.")
    (parent.run_dir / "result.json").write_text('{"state":"incomplete"}')
    (parent.run_dir / "delivery.json").write_text(json.dumps({
        "issues": [r"Companion export failed: Undefined control sequence: \OldMacro"]}))
    plan = plan_run(tmp_cfg, parent.problem, parent.harness, RunSpec(
        problem=parent.problem.slug, harness=parent.harness.id, mode="continue",
        parent_run_id=parent.spec.run_id, model="test-model", provider="codex", account="codex",
        max_minutes=12), isolate_reading=True)
    assert plan.cwd != parent.cwd
    assert (plan.cwd / "progress/reading/guide.md").read_text() == guide
    assert (plan.cwd / "progress/reading/figures/saved.png").read_bytes() == b"saved figure"
    assert (plan.cwd / "work/research.json").read_text() == '{"source":"a checked source"}'
    assert (plan.cwd / "work/continuation-notes.md").read_text() == "Saved research notes."
    assert (plan.cwd / "work/previous-review" / parent.spec.run_id / "review-1.json").exists()
    assert not (plan.cwd / "progress/reading/review-1.json").exists()
    assert (reading / "guide.md").read_text() == guide
    assert (reading / "review-1.json").exists()
    assert plan.budget_minutes == 12
    assert plan.first.role == ("auditor" if complete else "draft")
    assert "Drafting stops at 6 min" in plan.prompt and "reserving 6 min" in plan.prompt
    assert r"Undefined control sequence: \OldMacro" in plan.prompt
    assert "Repair" in plan.prompt or "repair" in plan.prompt
    if not complete:
        assert "continue_saved_draft" in plan.prompt
    (plan.cwd / "progress/reading/guide.md").write_text("A later draft")
    assert (reading / "guide.md").read_text() == guide


@pytest.mark.parametrize("bad_parent", ["../escape", "missing"])
def test_continuation_rejects_invalid_parent(tmp_cfg, delivery_plan, bad_parent):
    from atelier.delivery import copy_reading_work
    with pytest.raises(ValueError):
        copy_reading_work(tmp_cfg.runs_dir, bad_parent, delivery_plan.problem.slug,
                          tmp_cfg.root / "invalid-copy")


@pytest.fixture
def delivery_plan(tmp_cfg, sample_problem):
    sample_problem.ensure_scaffold()
    (sample_problem.progress_dir / "math.tex").write_text(DOCUMENT)
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    return plan_run(tmp_cfg, sample_problem, harness, RunSpec(
        problem=sample_problem.slug, harness=harness.id, provider="codex", account="codex",
        model="test-model", max_minutes=20), isolate_reading=True)


def fake_exports(reading, audience):
    from deslopify.summary_pdf import dated_guide

    text = dated_guide(reading / "guide.md") + "\n"
    (reading / "companion.md").write_text(text)
    (reading / "companion.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "Reader's guide to Test — non-expert edition\n\\end{document}\n")
    (reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")


@pytest.mark.parametrize("provider", ["codex", "claude", "open"])
def test_resume_retains_provider_state_and_review_is_fresh(
        tmp_cfg, sample_problem, provider):
    account = "codex" if provider == "codex" else "default"
    if provider == "open":
        account = "local-test"
        tmp_cfg.accounts[account] = Account(id=account, provider="open", auth="local",
                                            base_url="http://127.0.0.1:11434")
    h = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    plan = plan_run(tmp_cfg, sample_problem, h, RunSpec(
        problem=sample_problem.slug, harness=h.id, provider=provider, account=account,
        model="test-model", max_minutes=20), isolate_reading=True)
    completion = plan_writer_completion(tmp_cfg, plan, "writer-session", ["Missing guide"])
    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    assert completion.index == 1 and completion.role == "draft"
    assert completion.budget_minutes == plan.first.budget_minutes == 20
    assert completion.env.get("CODEX_HOME") == plan.first.env.get("CODEX_HOME")
    assert completion.env.get("CLAUDE_CONFIG_DIR") == plan.first.env.get("CLAUDE_CONFIG_DIR")
    assert "writer-session" in completion.argv
    assert "writer-session" not in audit.argv
    assert "You will be told" not in plan.prompt + audit.prompt
    assert "section by section" in plan.prompt
    if provider == "codex":
        assert completion.argv.index("--cd") < completion.argv.index("resume")
        assert completion.argv[completion.argv.index("resume") + 1] == "writer-session"
        assert completion.argv.index("--model") > completion.argv.index("resume")
        assert completion.argv.index("--json") > completion.argv.index("resume")
        assert completion.argv[-1] == "-"
        assert audit.env["CODEX_HOME"] != completion.env["CODEX_HOME"]
    else:
        assert "--resume" in completion.argv and "--session-id" not in completion.argv
        assert "--resume" not in audit.argv and "--session-id" in audit.argv


def test_delivery_requires_a_saved_guide_and_rejects_empty_sections(tmp_path):
    assert guide_problems(tmp_path, "companion")
    (tmp_path / "guide.md").write_text(GUIDE)
    assert not guide_problems(tmp_path, "companion")
    (tmp_path / "guide.md").write_text(GUIDE.replace("$a = 2$, $b = 4$.", ""))
    assert any("Empty section" in p for p in guide_problems(tmp_path, "companion"))
    (tmp_path / "guide.md").write_text("# Guide\n\nA short collection.\n")
    assert not guide_problems(tmp_path, "companion")
    assert not guide_problems(tmp_path, "guide")
    (tmp_path / "guide.md").write_text("  \n")
    assert guide_problems(tmp_path, "guide")


@pytest.mark.parametrize("group", [
    "[Tes26] [BS65] [Tat66]",
    "[Tes26; BS65; Tat66]", "[Tes26, BS65, Tat66]",
    "[Tes26, pp. 2–5; BS65, Theorem 1; Tat66, §2]",
])
def test_delivery_leaves_citation_review_to_the_reader(tmp_path, group):
    text = GUIDE.replace("[Tes26].", group + ".") + (
        "- [BS65] A. Author, An early mathematical research article, Journal, 1965.\n"
        "- [Tat66] B. Author, Another early mathematical research article, Journal, 1966.\n")
    (tmp_path / "guide.md").write_text(text)
    assert not guide_problems(tmp_path, "companion")
    (tmp_path / "guide.md").write_text(text.replace(group, group[:-1] + "; Mis26]"))
    assert not guide_problems(tmp_path, "companion")
    (tmp_path / "guide.md").write_text(text.replace(group, group + "(https://example.org)"))
    assert not guide_problems(tmp_path, "companion")


def test_delivery_does_not_parse_bibliography_keys(tmp_path):
    guide = GUIDE.replace("[Tes26].", "[Tes26, Appendix A.10].")
    (tmp_path / "guide.md").write_text(guide)
    assert not guide_problems(tmp_path, "companion")
    (tmp_path / "guide.md").write_text(guide.replace(
        "[Tes26, Appendix A.10]", "[Tes26, A.10] [Mis26]"))
    assert not guide_problems(tmp_path, "companion")


async def test_exports_refresh_only_when_inputs_or_outputs_change(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch):
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    reading = delivery_plan.cwd / "progress" / "reading"
    reading.mkdir(exist_ok=True)
    guide = reading / "guide.md"
    guide.write_text(GUIDE)
    calls = []
    def build(path, audience):
        calls.append(audience)
        fake_exports(path, audience)
    monkeypatch.setattr("atelier.runner.export_companion", build)
    await h._export_reading()
    await h._export_reading()
    assert calls == ["non-expert"]
    guide.write_text(GUIDE + "\nA revision.\n")
    await h._export_reading()
    assert len(calls) == 2
    (reading / "companion.pdf").write_bytes(b"broken")
    await h._export_reading()
    assert len(calls) == 3
    h.log.close()


async def test_staged_source_graphic_does_not_trigger_another_export(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch):
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    reading = delivery_plan.cwd / "progress" / "reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text(GUIDE)
    calls = []

    def build(path, audience):
        calls.append(audience)
        fake_exports(path, audience)
        figures = path / "figures"
        figures.mkdir(exist_ok=True)
        (figures / "source-graphic.pdf").write_bytes(b"%PDF-1.4")

    monkeypatch.setattr("atelier.runner.export_companion", build)
    await h._export_reading()
    await h._export_reading()
    assert calls == ["non-expert"]
    h.log.close()


async def test_failed_export_keeps_markdown_and_is_not_retried_repeatedly(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch):
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    reading = delivery_plan.cwd / "progress" / "reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text(GUIDE)
    calls = []
    def fail(path, audience):
        calls.append(1)
        (path / "companion.md").write_text(GUIDE)
        raise RuntimeError("TeX unavailable")
    monkeypatch.setattr("atelier.runner.export_companion", fail)
    await h._export_reading()
    await h._export_reading()
    state, reason = h._reading_outcome(RunState.DONE, "completed normally")
    assert state == RunState.INCOMPLETE and "TeX unavailable" in reason
    assert (reading / "companion.md").read_text() == GUIDE
    assert calls == [1]
    from atelier.delivery import export_problems
    assert "Companion export failed: TeX unavailable" in export_problems(reading, "companion")
    audit = plan_next_pass(tmp_cfg, delivery_plan, role_name="auditor", round_no=1, index=2)
    assert "TeX unavailable" in audit.prompt
    monkeypatch.setattr("atelier.runner.export_companion", fake_exports)
    (reading / "guide.md").write_text(GUIDE + "\nCorrected notation.\n")
    await h._export_reading()
    assert not export_problems(reading, "companion")
    h.log.close()


async def test_revision_retries_failed_export_even_when_text_is_complete(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch):
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    reading = delivery_plan.cwd / "progress/reading"
    reading.mkdir(exist_ok=True)
    (reading / "guide.md").write_text(GUIDE)
    (reading / "dispositions-1.json").write_text('{"round":1,"dispositions":[]}')
    h.current = plan_next_pass(tmp_cfg, delivery_plan, role_name="balanced-reviser",
                               round_no=1, index=3)
    h._provider_session_id = "revision-session"
    def fail(*args):
        raise RuntimeError(r"Undefined control sequence: \UnexpandedMacro")
    monkeypatch.setattr("atelier.runner.export_companion", fail)
    resumed = []
    async def begin(plan, **kwargs): resumed.append(plan)
    monkeypatch.setattr(h, "_begin_pass", begin)
    assert await h._complete_writer_if_needed(ok=True)
    assert r"\UnexpandedMacro" in resumed[0].prompt
    assert "revision-session" in resumed[0].argv
    h.log.close()


# Each stand-in uses the actual provider's input/output framing. Its context
# marker and observed resume arguments prove that recovery reused the writer.
STANDIN = r'''
import json, re, shlex, signal, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
codex = "exec" in args
prompt = sys.stdin.read() if codex else json.loads(sys.stdin.readline())["message"]["content"][0]["text"]
resumed = "resume" in args or "--resume" in args
role = "revision" if any("pass-3" in a for a in args) else "audit" if any("pass-2" in a for a in args) else "draft"
cfg = json.loads(Path("work/test-provider.json").read_text()); mode = cfg["mode"]
reading = Path("progress/reading"); reading.mkdir(exist_ok=True)
guide = Path("work/fixture-guide.md").read_text()
def emit(obj): print(json.dumps(obj), flush=True)
def stall():
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    time.sleep(60)
session = role + "-session" if role != "draft" else "writer-session"
emit({"type":"thread.started", "thread_id":session} if codex else
     {"type":"system", "subtype":"init", "session_id":session})
clock_command = shlex.split(re.search(r"<time_remaining>\s*Run `([^`]+)`", prompt).group(1))
def check_clock():
    return json.loads(subprocess.check_output(clock_command, text=True))
with Path("work/calls.jsonl").open("a") as f:
    f.write(json.dumps({"role":role,"resumed":resumed,"args":args,"clock":check_clock()})+"\n")
if role == "draft":
    if not resumed:
        Path("work/context-marker").write_text("prior research")
    else:
        assert Path("work/context-marker").read_text() == "prior research"
        assert "writer-session" in args and "Begin no new web research" in prompt
    if mode not in ("missing", "never") or (mode == "missing" and resumed):
        (reading/"guide.md").write_text(
            "# Partial guide\n\n## Main results\n\nThe saved result.\n\n## Historical context\n\n"
            if mode in ("partial", "unfixed", "gradual_revision", "stall", "stall_revision", "continued_stall") else guide)
    if mode in ("stall", "continued_stall"): stall()
    if mode == "slow_writer": time.sleep(1.5)
    if mode == "clock_wrap":
        report = check_clock()
        while not report["wrap_up"]:
            time.sleep(0.05)
            report = check_clock()
        Path("work/wrap-clock.json").write_text(json.dumps(report))
if role == "audit":
    assert not resumed and (reading/"companion.pdf").is_file()
    if mode == "stall_review": stall()
    if mode == "slow_review": time.sleep(1.5)
    if mode in ("partial", "unfixed", "gradual_revision", "stall_revision", "continued_stall"):
        assert "unfinished_work" in prompt and "Empty section: Historical context" in prompt
    (reading/"review-1.json").write_text(json.dumps({"round":1,"summary":"Checked.","findings":[]}))
if role == "revision":
    assert "unfinished_work" in prompt or resumed
    attempt = Path("work/revision-attempt")
    n = int(attempt.read_text()) + 1 if attempt.exists() else 1
    attempt.write_text(str(n))
    if mode != "unfixed" and (mode != "gradual_revision" or n >= 3):
        (reading/"guide.md").write_text(guide)
    if mode == "unfixed": time.sleep(0.4)
    if mode == "stall_revision": stall()
    (reading/"dispositions-1.json").write_text(json.dumps({"round":1,"dispositions":[]}))
Path("progress/_session_notes.md").write_text("Saved sources and limitations.\n")
emit({"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3,"cached_input_tokens":0}}
     if codex else {"type":"result","subtype":"success","num_turns":1,"total_cost_usd":0,
     "usage":{"input_tokens":10,"output_tokens":3},"session_id":session})
'''


@pytest.mark.parametrize("provider", ["codex", "claude", "open"])
@pytest.mark.parametrize("mode", ["normal", "missing", "never", "partial", "unfixed",
                                  "stall", "stall_review", "stall_revision", "slow_writer", "slow_review",
                                  "continued", "continued_stall", "gradual_revision", "clock_wrap"])
async def test_controller_delivers_or_reports_failure_uniformly(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, provider, mode):
    # Exact grace boundaries are covered above; keep these provider/framing
    # scenarios short even when the reviewer deliberately stalls.
    for module in ("atelier.delivery", "atelier.runner", "atelier.compile"):
        monkeypatch.setattr(f"{module}.REVIEW_EXTRA_MINUTES", 0)
    if mode in ("unfixed", "stall_revision"):
        # Exercise the same overrun path without waiting five real minutes.
        monkeypatch.setattr("atelier.delivery.REVISION_EXTRA_MINUTES", 0.02)
        monkeypatch.setattr("atelier.runner.REVISION_EXTRA_MINUTES", 0.02)
        monkeypatch.setattr("atelier.compile.REVISION_EXTRA_MINUTES", 0.02)
    stub = tmp_cfg.root / "provider-stub"
    stub.write_text(f"#!{sys.executable}\n" + STANDIN)
    stub.chmod(0o700)
    monkeypatch.setattr(type(get_adapter(provider)), "executable", staticmethod(lambda: str(stub)))
    monkeypatch.setattr("atelier.runner.WATCHDOG_INTERVAL", 0.02)
    monkeypatch.setattr("atelier.runner.export_companion", fake_exports)
    account = "codex" if provider == "codex" else "default"
    if provider == "open":
        account = "local-test"
        tmp_cfg.accounts[account] = Account(id=account, provider="open", auth="local",
                                            base_url="http://127.0.0.1:11434")
    sample_problem.ensure_scaffold()
    (sample_problem.progress_dir / "math.tex").write_text(DOCUMENT)
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    # Leave room for local process startup under load, as well as the stub's
    # deliberate 1.5-second delay. The watchdog unit tests check exact boundaries.
    test_minutes = 0.2 if mode == "clock_wrap" else 0.1
    plan = plan_run(tmp_cfg, sample_problem, harness, RunSpec(
        problem=sample_problem.slug, harness=harness.id, provider=provider, account=account,
        model="test-model", max_minutes=test_minutes), isolate_reading=True)
    parent = None
    if mode in ("continued", "continued_stall"):
        parent = plan
        (parent.cwd / "progress/reading").mkdir(exist_ok=True)
        (parent.cwd / "progress/reading/guide.md").write_text(
            "# Partial guide\n\n## Main results\n\nSaved explanation.\n\n## Historical context\n\n"
            if mode == "continued_stall" else GUIDE)
        (parent.run_dir / "result.json").write_text('{"state":"incomplete"}')
        plan = plan_run(tmp_cfg, sample_problem, harness, RunSpec(
            problem=sample_problem.slug, harness=harness.id, provider=provider, account=account,
            model="test-model", max_minutes=0.1 if mode == "continued_stall" else test_minutes,
            mode="continue", parent_run_id=parent.spec.run_id),
            isolate_reading=True)
    (plan.cwd / "work" / "test-provider.json").write_text(json.dumps({"mode": mode}))
    (plan.cwd / "work" / "fixture-guide.md").write_text(GUIDE)
    h = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    await h.start()
    try:
        await asyncio.wait_for(h.finished.wait(), timeout=12)
    finally:
        if not h.state.terminal:
            await h.stop("test cleanup")
    calls = [json.loads(s) for s in (plan.cwd / "work" / "calls.jsonl").read_text().splitlines()]
    assert all(c["clock"]["available"] and c["clock"]["stage_limit_kind"] == "deadline"
               for c in calls)
    if mode == "clock_wrap":
        report = json.loads((plan.cwd / "work/wrap-clock.json").read_text())
        assert report["wrap_up"] and report["seconds_remaining"] > 0
        assert report["stage_elapsed_seconds"] >= 0.9 * report["stage_allocated_seconds"]
    result = json.loads((plan.run_dir / "result.json").read_text())
    resumed = sum(c["resumed"] for c in calls)
    if mode == "unfixed":
        assert resumed >= 2
        assert h.elapsed >= test_minutes * 60
    else:
        assert resumed == (2 if mode == "gradual_revision" else mode in ("missing", "never"))
    assert not any(c["resumed"] for c in calls if c["role"] == "audit")
    if mode == "never":
        assert h.state == RunState.FAILED
        assert not result["produced_output"] and "No guide" in result["ended_reason"]
        assert not any(c["role"] == "audit" for c in calls)
    else:
        report = json.loads((plan.run_dir / "delivery.json").read_text())
        expected = ("incomplete" if mode == "unfixed" else
                    "review_unfinished" if mode in ("stall_review", "stall_revision") else "completed")
        assert report["status"] == expected, (report, result)
        assert h.state == (RunState.DONE if expected == "completed" else RunState.INCOMPLETE)
        assert result["produced_output"]
        assert (plan.run_dir / "reading" / "companion.pdf").is_file()
        if mode == "partial":
            assert [c["role"] for c in calls] == ["draft", "audit", "revision"]
            assert (plan.run_dir / "reading" / "guide.md").read_text() == GUIDE
        if parent:
            if mode == "continued_stall":
                assert [c["role"] for c in calls] == ["draft", "audit", "revision"]
                assert (plan.run_dir / "reading/guide.md").read_text() == GUIDE
                assert "Partial guide" in (parent.cwd / "progress/reading/guide.md").read_text()
            else:
                assert [c["role"] for c in calls] == ["audit"]
                assert h.loop_state.outcomes[0].continued_from == parent.spec.run_id
                assert (parent.cwd / "progress/reading/guide.md").read_text() == GUIDE
    expected_tokens = 10 * (len(calls) - (mode.startswith("stall") or mode == "continued_stall"))
    events = [json.loads(s) for s in (plan.run_dir / "events.jsonl").read_text().splitlines()]
    timed_handoff = any(e.get("payload", {}).get("kind") == "pass_stopped" for e in events)
    if mode == "unfixed" or (mode == "slow_writer" and timed_handoff):
        # A deadline can interrupt final usage reporting after the files were saved.
        assert expected_tokens - 10 <= h.totals.tok_in <= expected_tokens
    else:
        assert h.totals.tok_in == expected_tokens
    assert (plan.cwd / "progress" / "math.tex").read_text() == DOCUMENT
    assert h.elapsed < test_minutes * 60 + 3


@pytest.mark.parametrize("stop_condition", ["user", "spent", "attempted", "provider_error"])
async def test_recovery_never_overrides_stops_or_retries_twice(
        tmp_cfg, tmp_db, delivery_plan, monkeypatch, stop_condition):
    h = RunHandle(tmp_cfg, delivery_plan, EventHub(), tmp_db)
    h._provider_session_id = "writer-session"
    if stop_condition == "user":
        h._ending_run = True
    elif stop_condition == "spent":
        monkeypatch.setattr(h, "budget_fraction", lambda: 1.0)
    elif stop_condition == "attempted":
        h._writer_completion_used = True
    assert not await h._complete_writer_if_needed(ok=stop_condition != "provider_error")
    assert not (delivery_plan.run_dir / "writer-completion").exists()
    h.log.close()
