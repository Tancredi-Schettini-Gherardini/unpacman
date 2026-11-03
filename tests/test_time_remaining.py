from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from atelier.time_remaining import state_path, time_remaining


def clock_state(**changes):
    return {
        "stage": "draft", "state": "running", "active": True,
        "sampled_at": 100.0, "elapsed_seconds": 0.0, "stage_started_at": 0.0,
        "stage_allocation_seconds": 1800.0, "run_limit_seconds": 2400.0,
        "stage_limit_kind": "deadline", **changes,
    }


@pytest.mark.parametrize("elapsed,wrap,left", [
    (1619.9, False, 180.1), (1620, True, 180),
    (1800, True, 0), (2500, True, 0),
])
def test_warning_uses_stage_time_and_advances_between_updates(elapsed, wrap, left):
    report = time_remaining(clock_state(), now=100 + elapsed)
    assert report["wrap_up"] is wrap
    assert report["seconds_remaining"] == left
    assert report["run_deadline_reached"] is (elapsed >= 2400)


def test_unlimited_and_ended_clocks():
    unlimited = time_remaining(clock_state(
        stage_allocation_seconds=None, run_limit_seconds=None,
        stage_limit_kind="unlimited"), now=10000)
    assert unlimited["seconds_remaining"] is None
    assert unlimited["wrap_up"] is False
    ended = time_remaining(clock_state(
        active=False, state="done", elapsed_seconds=1200), now=10000)
    assert ended["seconds_remaining"] == 600
    assert ended["stage_elapsed_seconds"] == 1200


def test_revision_has_its_own_wrapup_point():
    state = clock_state(stage="balanced-reviser", elapsed_seconds=2160,
                        stage_started_at=2160, stage_allocation_seconds=240)
    assert not time_remaining(state, now=100)["wrap_up"]
    report = time_remaining(state, now=316)
    assert report["wrap_up"] and report["seconds_remaining"] == 24


@pytest.mark.parametrize("total,draft", [(11, 5.5), (20, 10), (30, 20),
                                           (59, 49), (60, 40), (75, 55)])
def test_clock_wraps_up_at_ninety_percent_of_each_drafting_allocation(total, draft):
    from atelier.delivery import time_targets

    assert time_targets(total, ["draft", "auditor", "balanced-reviser"])["draft"] == draft
    allocation = draft * 60
    state = clock_state(stage_allocation_seconds=allocation,
                        run_limit_seconds=total * 60)
    assert not time_remaining(state, now=100 + allocation * 0.89)["wrap_up"]
    assert time_remaining(state, now=100 + allocation * 0.9)["wrap_up"]


def test_shell_command_needs_no_app_config_or_inherited_environment(tmp_path):
    from atelier.access import clean_environment

    path = tmp_path / "clock with spaces.json"
    path.write_text(json.dumps(clock_state(sampled_at=time.monotonic() - 1620)))
    result = subprocess.run(
        [sys.executable, "-m", "atelier.time_remaining", "--state", str(path)],
        cwd=tmp_path, env=clean_environment(), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["available"] is True and report["wrap_up"] is True
    assert 0 < report["seconds_remaining"] <= 180
    assert report["stage_limit_kind"] == "deadline"
    missing = subprocess.run(
        [sys.executable, "-m", "atelier.time_remaining", "--state", str(tmp_path / "missing")],
        cwd=tmp_path, env=clean_environment(), capture_output=True, text=True, timeout=10)
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["available"] is False


@pytest.mark.parametrize("continuation", [False, True])
@pytest.mark.parametrize("provider", ["codex", "claude", "open"])
async def test_runner_preserves_stage_clock_on_retry_and_updates_it_for_review(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, continuation, provider):
    from atelier.compile import plan_next_pass, plan_run, plan_writer_completion
    from atelier.config import Account
    from atelier.events import EventHub
    from atelier.harnesses import get_harness
    from atelier.runner import RunHandle, RunState
    from atelier.spec import RunSpec

    sample_problem.ensure_scaffold()
    (sample_problem.progress_dir / "math.tex").write_text(
        r"\documentclass{article}\begin{document}A source paper.\end{document}")
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    account = "codex" if provider == "codex" else "default"
    if provider == "open":
        account = "local-test"
        tmp_cfg.accounts[account] = Account(id=account, provider="open", auth="local",
                                            base_url="http://127.0.0.1:11434")
    plan = plan_run(tmp_cfg, sample_problem, harness, RunSpec(
        problem=sample_problem.slug, harness=harness.id, provider=provider, account=account,
        model="test-model", max_minutes=40), isolate_reading=True)
    if continuation:
        plan.spec.mode = "continue"
    handle = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    handle.loop_state = object()
    elapsed = 0
    monkeypatch.setattr(type(handle), "elapsed", property(lambda _: elapsed))

    class LaunchObserved(Exception):
        pass

    async def observe_launch(*args, **kwargs):
        # Verify the clock exists before the provider gets its initial prompt.
        assert state_path(plan.run_dir).is_file()
        raise LaunchObserved

    monkeypatch.setattr("atelier.runner.asyncio.create_subprocess_exec", observe_launch)
    try:
        with pytest.raises(LaunchObserved):
            await handle._begin_pass(plan.first)
        original = json.loads(state_path(plan.run_dir).read_text())
        assert original["stage_allocation_seconds"] == 1800
        handle._wrapup_sent = True
        elapsed = 1500
        completion = plan_writer_completion(tmp_cfg, plan, "writer-session", ["Missing section"])
        with pytest.raises(LaunchObserved):
            await handle._begin_pass(completion, completion=True)
        retry = json.loads(state_path(plan.run_dir).read_text())
        assert retry["stage_started_at"] == original["stage_started_at"]
        assert retry["stage_allocation_seconds"] == original["stage_allocation_seconds"]
        assert handle._wrapup_sent is True
        report = time_remaining(retry, now=retry["sampled_at"])
        assert report["seconds_remaining"] == 300
        assert report["stage_limit_kind"] == "deadline"

        audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1,
                               index=2, remaining_minutes=15)
        assert all("-m atelier.time_remaining" in stage.prompt
                   for stage in (plan.first, completion, audit))
        with pytest.raises(LaunchObserved):
            await handle._begin_pass(audit)
        review = json.loads(state_path(plan.run_dir).read_text())
        assert review["stage"] == "auditor" and review["stage_started_at"] == 1500
        # Nine planned minutes, plus the review's own five-minute allowance.
        assert review["stage_allocation_seconds"] == 840
        assert handle._wrapup_sent is False

        elapsed = 2160
        revision = plan_next_pass(tmp_cfg, plan, role_name="balanced-reviser", round_no=1,
                                  index=3, remaining_minutes=4)
        with pytest.raises(LaunchObserved):
            await handle._begin_pass(revision)
        revision_clock = json.loads(state_path(plan.run_dir).read_text())
        # The revision's own five minutes past the planned 40; the review used
        # none of its allowance, and unused review time does not transfer.
        assert revision_clock["stage_allocation_seconds"] == 540
        assert revision_clock["run_limit_seconds"] == 2700
        assert time_remaining(revision_clock, now=revision_clock["sampled_at"])["seconds_remaining"] == 540
        elapsed = 2450  # past the planned 40-minute limit, within revision allowance
        retry = plan_writer_completion(tmp_cfg, plan, "reviser-session", ["Missing record"],
                                       stage=revision)
        with pytest.raises(LaunchObserved):
            await handle._begin_pass(retry, completion=True)
        retry_clock = json.loads(state_path(plan.run_dir).read_text())
        assert retry_clock["stage_started_at"] == 2160
        assert retry_clock["run_limit_seconds"] == 2700
        assert time_remaining(retry_clock, now=retry_clock["sampled_at"])["seconds_remaining"] == 250
        handle.set_state(RunState.DONE)
        ended = json.loads(state_path(plan.run_dir).read_text())
        report = time_remaining(ended, now=ended["sampled_at"] + 10000)
        assert report["stage_elapsed_seconds"] == 290
        assert report["state"] == "done"
    finally:
        handle.log.close()
