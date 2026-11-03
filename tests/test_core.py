"""Unit tests for the pure logic: pricing, problems, harnesses, compilation,
lab book, latex parsing, events, and the Claude stream-json parser.

Nothing here touches a provider API.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

# ==========================================================================
# pricing
# ==========================================================================


def test_price_lookup_respects_date_windows(tmp_cfg):
    p = tmp_cfg.prices
    intro = p.lookup("claude-sonnet-5", date(2026, 8, 9))
    after = p.lookup("claude-sonnet-5", date(2026, 9, 15))
    assert intro is not None and intro.input == 2.0
    assert after is not None and after.input == 3.0


def test_cost_includes_cache_multipliers(tmp_cfg):
    cost = tmp_cfg.prices.cost(
        "claude-opus-5", tok_in=1_000_000, tok_out=1_000_000,
        cache_read=1_000_000, cache_write=1_000_000, when=date(2026, 8, 9))
    # 5 (in) + 25 (out) + 5*0.10 (cache read) + 5*1.25 (cache write)
    assert cost == pytest.approx(5 + 25 + 0.5 + 6.25)


def test_price_prefix_match_handles_model_variants(tmp_cfg):
    assert tmp_cfg.prices.lookup("claude-opus-5[1m]", date(2026, 8, 9)) is not None
    # A dated snapshot is the model it is a snapshot of.
    snapshot = tmp_cfg.prices.lookup("claude-haiku-4-5-20251001", date(2026, 8, 9))
    assert snapshot is not None and snapshot.model == "claude-haiku-4-5"


def test_a_newer_model_never_takes_an_older_ones_price(tmp_cfg):
    """`claude-opus-5-5` begins with `claude-opus-5`, and is another model at
    another price: unpriced until it is listed, never Opus 5's price."""
    from atelier.config import Price, PriceTable

    when = date(2026, 10, 15)
    assert tmp_cfg.prices.lookup("claude-opus-5-5", when) is None
    assert tmp_cfg.prices.lookup("claude-opus-5-5[1m]", when) is None
    table = PriceTable([
        Price(model="claude-opus-5", input=5.0, output=25.0, valid_from=date(2026, 1, 1)),
        Price(model="claude-opus-5-5", input=4.0, output=20.0, cache_read_mult=0.05,
              valid_from=date(2026, 9, 1)),
    ])
    assert table.lookup("claude-opus-5-5[1m]", when).input == 4.0
    assert table.lookup("claude-opus-5[1m]", when).input == 5.0
    # A snapshot priced on its own is its own price, with a window or without:
    # the longest name a variant belongs to wins, whatever order they came in.
    table.add([Price(model="claude-opus-5-20260601", input=6.0, output=30.0,
                     valid_from=date(2026, 1, 1))])
    assert table.lookup("claude-opus-5-20260601[1m]", when).input == 6.0
    assert table.lookup("claude-opus-5-20260701", when).input == 5.0


def test_the_price_table_prices_the_latest_claude_models():
    """pricing.toml as shipped: the models of 2026-09/10 at Anthropic's prices
    (checked 2026-10-15), each with its own cache-read rate, and Sonnet 5 at
    its launch price, which stayed."""
    from atelier.config import _load_prices

    repo = Path(__file__).resolve().parents[1]
    prices = _load_prices(repo)
    when = date(2026, 10, 15)
    expect = {"claude-opus-5-5": (4.0, 20.0, 0.05), "claude-fable-5-1": (10.0, 50.0, 0.025),
              "claude-sonnet-5-5": (2.0, 10.0, 0.05), "claude-sonnet-5": (2.0, 10.0, 0.10),
              # Haiku 5.5 at its price above 100,000 prompt tokens: the higher one.
              "claude-haiku-5-5": (0.5, 2.5, 0.10)}
    for model, (inp, out, read) in expect.items():
        p = prices.lookup(model, when)
        assert p is not None and p.model == model, model
        assert (p.input, p.output, p.cache_read_mult) == (inp, out, read), model
    # A million tokens read from the cache of Opus 5.5 cost $0.20, not $0.40.
    assert prices.cost("claude-opus-5-5", cache_read=1_000_000, when=when) == pytest.approx(0.2)


def test_every_claude_model_offered_has_a_price():
    """A model the lab or the reader offers by default must be held to a money
    limit: a Claude model missing from pricing.toml would run on time alone."""
    from atelier.config import load_config

    cfg = load_config(Path(__file__).resolve().parents[1])
    claude = cfg.models.for_provider("claude")
    assert claude
    assert [m for m in claude if cfg.prices.lookup(m) is None] == []


def test_unknown_model_costs_none_not_zero(tmp_cfg):
    assert tmp_cfg.prices.cost("some-other-model", tok_in=1000) is None


# ==========================================================================
# environment checks
# ==========================================================================


class _DoctorAdapter:
    def __init__(self, name: str, path: str | None):
        self.name = name
        self._path = path

    def executable(self):
        return self._path


@pytest.mark.asyncio
async def test_doctor_accepts_either_provider(tmp_cfg, tmp_db, monkeypatch):
    from atelier.doctor import run_checks

    monkeypatch.setattr("atelier.providers.all_adapters", lambda: {
        "claude": _DoctorAdapter("claude", None),
        "codex": _DoctorAdapter("codex", "/usr/bin/codex"),
    })

    report = await run_checks(tmp_cfg, tmp_db)

    assert report["worst"] != "fail"
    assert not any(c["name"] == "providers" for c in report["checks"])


@pytest.mark.asyncio
async def test_doctor_fails_when_no_provider_is_available(tmp_cfg, tmp_db, monkeypatch):
    from atelier.doctor import run_checks

    monkeypatch.setattr("atelier.providers.all_adapters", lambda: {
        "claude": _DoctorAdapter("claude", None),
        "codex": _DoctorAdapter("codex", None),
    })

    report = await run_checks(tmp_cfg, tmp_db)

    assert report["worst"] == "fail"
    assert any(c["name"] == "providers" and c["level"] == "fail"
               for c in report["checks"])


# ==========================================================================
# problems
# ==========================================================================


def test_classification_of_static_and_writable_paths(sample_problem):
    from atelier.problems import classify

    meta = sample_problem.meta
    assert classify("problem.md", meta) == "static"
    assert classify("strategy/01-plan.md", meta) == "static"
    assert classify("refs/paper.pdf", meta) == "static"
    assert classify("progress/math.tex", meta) == "writable"
    assert classify("work/scratch.py", meta) == "writable"
    assert classify("work/data/out.csv", meta) == "writable"


def test_custom_statement_filename_is_static(tmp_cfg):
    from atelier.problems import ProblemMeta, classify

    meta = ProblemMeta(title="X", slug="x", statement="BRIEF.md")
    assert classify("BRIEF.md", meta) == "static"


def test_safe_join_refuses_traversal(sample_problem):
    from atelier.problems import ProblemError, safe_join

    with pytest.raises(ProblemError):
        safe_join(sample_problem.root, "../../etc/passwd")


def test_scaffold_creates_contract_files(sample_problem):
    sample_problem.ensure_scaffold()
    for rel in ("progress/state.json", "progress/labbook.md",
                "progress/math.tex", "progress/preamble.tex"):
        assert (sample_problem.root / rel).is_file(), rel


def test_state_validation_catches_real_mistakes():
    from atelier.problems import validate_state

    assert validate_state({"status": "partial", "one_line": "ok"}) == []
    assert any("status" in e for e in validate_state({"status": "nonsense",
                                                      "one_line": "x"}))
    assert any("one_line" in e for e in validate_state({"status": "open"}))
    assert any("confidence" in e for e in
               validate_state({"status": "open", "one_line": "x", "confidence": 5}))
    assert any("established" in e for e in
               validate_state({"status": "open", "one_line": "x",
                               "established": "not a list"}))


def test_lock_is_exclusive_and_reclaims_dead_owners(sample_problem):
    from atelier.problems import LockHeld, acquire_lock, read_lock, release_lock

    acquire_lock(sample_problem.root, "run-a", "solo")
    with pytest.raises(LockHeld):
        acquire_lock(sample_problem.root, "run-b", "solo")

    # Simulate the owning process having died.
    lock_file = sample_problem.root / ".atelier" / "lock.json"
    data = json.loads(lock_file.read_text())
    data["pid"] = 999_999
    lock_file.write_text(json.dumps(data))
    assert read_lock(sample_problem.root).alive() is False

    acquire_lock(sample_problem.root, "run-c", "solo")
    assert read_lock(sample_problem.root).run_id == "run-c"
    assert release_lock(sample_problem.root, "run-c") is True


def test_release_lock_refuses_wrong_owner(sample_problem):
    from atelier.problems import acquire_lock, release_lock

    acquire_lock(sample_problem.root, "run-a", "solo")
    assert release_lock(sample_problem.root, "someone-else") is False
    assert release_lock(sample_problem.root, "run-a") is True


def test_oneshot_snapshot_carries_no_prior_work(sample_problem, tmp_path):
    """The workspace gets a *fresh* scaffold, never the real progress. A blind
    control that can see previous sessions is not a control."""
    from atelier.problems import snapshot_static, write_state

    sample_problem.ensure_scaffold()
    write_state(sample_problem.root, {"status": "partial", "one_line": "SECRET-STATE"})
    (sample_problem.progress_dir / "math.tex").write_text("SECRET-MATH")
    (sample_problem.progress_dir / "labbook.md").write_text("SECRET-LABBOOK")

    dest = tmp_path / "ws"
    snapshot_static(sample_problem, dest)

    assert (dest / "problem.md").is_file()
    assert (dest / "strategy" / "01-plan.md").is_file()

    blob = "".join(
        p.read_text(errors="replace")
        for p in dest.rglob("*") if p.is_file()
    )
    for secret in ("SECRET-STATE", "SECRET-MATH", "SECRET-LABBOOK"):
        assert secret not in blob, f"{secret} leaked into the one-shot workspace"

    # …but it must still be able to build a document.
    assert (dest / "progress" / "preamble.tex").is_file()
    assert (dest / "progress" / "math.tex").is_file()
    assert not (dest / "progress" / "labbook.md").exists()


def test_reading_snapshot_copies_current_inputs_without_process_lock(
        sample_problem, tmp_path):
    from atelier.problems import snapshot_problem

    source = sample_problem.progress_dir / "source.md"
    source.write_text("# Paper\n\nThe source to read.\n", encoding="utf-8")
    old_reading = sample_problem.progress_dir / "reading"
    old_reading.mkdir()
    (old_reading / "guide.md").write_text("stale", encoding="utf-8")
    (sample_problem.progress_dir / "_session_notes.md").write_text(
        "stale notes", encoding="utf-8")
    lock_dir = sample_problem.root / ".atelier"
    lock_dir.mkdir(exist_ok=True)
    (lock_dir / "lock.json").write_text('{"private": true}', encoding="utf-8")

    dest = tmp_path / "reading-workspace"
    snapshot_problem(sample_problem, dest)

    assert (dest / "progress" / "source.md").read_text() == source.read_text()
    assert (dest / "progress" / "state.json").is_file()
    assert list((dest / "progress" / "reading").iterdir()) == []
    assert not (dest / "progress" / "_session_notes.md").exists()
    assert not (dest / ".atelier").exists()


# ==========================================================================
# harnesses
# ==========================================================================


def test_all_shipped_harnesses_parse(tmp_cfg):
    from atelier.harnesses import discover

    found, errors = discover(tmp_cfg.harnesses_dir)
    assert errors == {}
    ids = {h.id for h in found}
    assert {"chat", "solo", "solo-deep", "explore-verify"} <= ids
    assert {"deslopify-expert", "deslopify-nonexpert"} <= ids
    assert "deslopify" not in ids  # retained only as a CLI compatibility alias


def test_explore_verify_loads_its_subagents(tmp_cfg):
    from atelier.harnesses import get_harness

    h = get_harness(tmp_cfg.harnesses_dir, "explore-verify")
    assert set(h.agents) == {"referee", "computer"}
    ref = h.agents["referee"]
    assert "REJECT" in ref.prompt
    assert ref.tools and "Read" in ref.tools
    cli = ref.to_cli()
    assert set(cli) >= {"description", "prompt"}


def test_chat_harness_disables_tools(tmp_cfg):
    from atelier.harnesses import get_harness

    h = get_harness(tmp_cfg.harnesses_dir, "chat")
    assert h.tools.none is True
    assert h.context.include_progress is False


def test_labbook_count_parsing(tmp_cfg):
    from atelier.harnesses import ContextPolicy

    assert ContextPolicy(include_labbook="none").labbook_count() == 0
    assert ContextPolicy(include_labbook="all").labbook_count() is None
    assert ContextPolicy(include_labbook="last:5").labbook_count() == 5


def test_broken_harness_does_not_break_discovery(tmp_cfg):
    (tmp_cfg.harnesses_dir / "broken").mkdir()
    (tmp_cfg.harnesses_dir / "broken" / "harness.toml").write_text("this is not toml {{{")
    from atelier.harnesses import discover

    found, errors = discover(tmp_cfg.harnesses_dir)
    assert "broken" in errors
    assert len(found) >= 4


def test_frontmatter_parser():
    from atelier.harnesses import parse_frontmatter

    meta, body = parse_frontmatter(
        '---\nname: x\ndescription: "a thing"\ntools: Read, Bash\n---\n\nBody here\n')
    assert meta["name"] == "x"
    assert meta["description"] == "a thing"
    assert body.strip() == "Body here"

    meta2, body2 = parse_frontmatter("No frontmatter at all")
    assert meta2 == {}
    assert body2 == "No frontmatter at all"


# ==========================================================================
# compilation
# ==========================================================================


def _plan(tmp_cfg, problem, harness_id, **spec_kw):
    from atelier.compile import plan_run
    from atelier.harnesses import get_harness
    from atelier.spec import RunSpec

    h = get_harness(tmp_cfg.harnesses_dir, harness_id)
    spec = RunSpec(problem=problem.slug, harness=harness_id, **spec_kw)
    return plan_run(tmp_cfg, problem, h, spec, session_number=1)


def test_prompt_contains_statement_strategy_and_task(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "solo",
                 task_kind="text", task_text="Prove the base case.")
    assert "Show every widget is a gadget" in plan.prompt
    assert "induction on the number of sprockets" in plan.prompt
    assert "Prove the base case." in plan.prompt
    assert "<before_you_finish>" in plan.prompt


def test_a_harness_may_carry_its_own_copy_of_a_shared_policy(tmp_cfg, sample_problem, tmp_path):
    """A reading run with an edited method carries its rules with it: the
    harness's own policy/<name>.md wins over shared/policy/<name>.md, in the
    first pass and in the passes planned after it. Without one, the shared
    policy is used as before."""
    import shutil

    from atelier.compile import build_role_system_append, build_system_append
    from atelier.harnesses import get_harness, load_harness
    from atelier.spec import RunSpec

    shared = (tmp_cfg.shared_dir / "policy" / "fidelity.md").read_text(encoding="utf-8")
    plain = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    spec = RunSpec(problem=sample_problem.slug, harness=plain.id)
    first_line = shared.split("---", 2)[-1].strip().splitlines()[0]
    assert first_line in build_system_append(tmp_cfg, sample_problem, plain, spec)

    mine = tmp_path / "mine"
    shutil.copytree(plain.root, mine)
    (mine / "policy").mkdir()
    (mine / "policy" / "fidelity.md").write_text("MY OWN FIDELITY RULE\n", encoding="utf-8")
    own = load_harness(mine, variant="expert")
    assert "MY OWN FIDELITY RULE" in build_system_append(tmp_cfg, sample_problem, own, spec)
    assert first_line not in build_system_append(tmp_cfg, sample_problem, own, spec)
    later = build_role_system_append(tmp_cfg, own, own.roles["auditor"])
    assert "MY OWN FIDELITY RULE" in later and first_line not in later


def test_oneshot_prompt_hides_progress(tmp_cfg, sample_problem):
    from atelier.problems import write_state

    sample_problem.ensure_scaffold()
    write_state(sample_problem.root, {"status": "partial",
                                      "one_line": "LEAKED-PROGRESS-MARKER"})
    plan = _plan(tmp_cfg, sample_problem, "solo", mode="oneshot")
    assert "LEAKED-PROGRESS-MARKER" not in plan.prompt
    assert "ONE-SHOT" in plan.prompt
    assert plan.cwd != sample_problem.root
    assert plan.cwd.is_dir()


def test_argv_is_hermetic_and_well_formed(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "solo")
    argv = plan.argv
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    # Hermetic: an empty --setting-sources means personal config is ignored.
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--settings" in argv
    assert "--session-id" in argv
    assert argv[argv.index("--effort") + 1] in ("low", "medium", "high", "xhigh", "max")


def test_chat_harness_passes_empty_tools(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "chat", mode="oneshot")
    assert argv_value(plan.argv, "--tools") == ""
    assert "--allowedTools" not in plan.argv


def argv_value(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


def test_native_budget_cap_leaves_room_for_our_wrapup(tmp_cfg, sample_problem):
    """The provider's own cap is a backstop, not a competitor. If it sits too
    close to our soft cap it kills the session mid-wrap-up and the file
    contract never completes."""
    plan = _plan(tmp_cfg, sample_problem, "solo", max_usd=10.0)
    native = float(argv_value(plan.argv, "--max-budget-usd"))
    assert native >= 20.0

    small = _plan(tmp_cfg, sample_problem, "solo", max_usd=0.05)
    assert float(argv_value(small.argv, "--max-budget-usd")) >= 1.0


def test_settings_deny_dangerous_commands(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "solo")
    settings = json.loads((plan.run_dir / "settings.json").read_text())
    deny = settings["permissions"]["deny"]
    assert any("git push" in d for d in deny)
    assert any("sudo" in d for d in deny)
    assert settings["permissions"]["defaultMode"] == "acceptEdits"


def test_settings_register_the_contract_hooks(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "solo")
    hooks = json.loads((plan.run_dir / "settings.json").read_text())["hooks"]
    assert "Stop" in hooks
    pre = json.dumps(hooks.get("PreToolUse", []))
    assert "guard_static.py" in pre
    post = json.dumps(hooks.get("PostToolUse", []))
    assert "compile_tex.py" in post and "log_command.py" in post


def test_chat_harness_has_no_stop_hook(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "chat", mode="oneshot")
    hooks = json.loads((plan.run_dir / "settings.json").read_text())["hooks"]
    assert "Stop" not in hooks


def test_env_never_leaks_ambient_api_key(tmp_cfg, sample_problem, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-appear")
    plan = _plan(tmp_cfg, sample_problem, "solo")
    assert "ANTHROPIC_API_KEY" not in plan.env
    assert "should-not-appear" not in json.dumps(plan.redacted_env())


def test_run_dir_records_everything_needed_to_reproduce(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "explore-verify")
    for name in ("prompt.md", "system-append.md", "settings.json",
                 "argv.json", "env.json", "config.json", "agents.json"):
        assert (plan.run_dir / name).is_file(), name
    agents = json.loads((plan.run_dir / "agents.json").read_text())
    assert set(agents) == {"referee", "computer"}


def test_tex_outline_mode_shortens_context(tmp_cfg, sample_problem):
    from atelier.compile import _tex_outline

    sample_problem.ensure_scaffold()
    text = (sample_problem.progress_dir / "math.tex").read_text()
    outline = _tex_outline(text)
    assert "\\section{Established results}" in outline
    assert len(outline) < len(text) / 2


def test_task_from_files(tmp_cfg, sample_problem):
    plan = _plan(tmp_cfg, sample_problem, "solo",
                 task_kind="files", task_files=["strategy/01-plan.md"])
    assert "induction on the number of sprockets" in plan.prompt
    assert "### strategy/01-plan.md" in plan.prompt


# ==========================================================================
# lab book
# ==========================================================================


def test_labbook_roundtrip_and_preserves_handwritten_text(tmp_path):
    from atelier.labbook import append_entry, compose_entry, read_entries

    book = tmp_path / "labbook.md"
    book.write_text("# Lab book\n\nMy own note that must survive.\n")

    entry = compose_entry(
        run_id="run-1", session_number=1,
        started_at=datetime(2026, 8, 9, 14, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 9, 15, 12, tzinfo=UTC),
        harness="explore-verify", harness_version=1, runner="native",
        provider="claude", model="claude-opus-5", effort="xhigh",
        account="max", mode="standard",
        stats={"tok_in": 1_412_300, "tok_out": 96_441, "tok_cache_read": 1_104_882,
               "tok_cache_write": 41_220, "turns": 87, "tool_calls": 214,
               "cost_computed": 3.41, "cost_reported": 3.44,
               "wall_seconds": 4328, "active_seconds": 3521,
               "tool_breakdown": {"Read": 91, "Bash": 44}},
        task="Push on the mod-3 obstruction.",
        notes="Established Lemma 3.4. The analytic route died at step 2.",
        files_changed=[("progress/math.tex", "+142 −18")],
        status_before="open", status_after="partial",
        ended_reason="wrap-up completed normally",
    )
    append_entry(book, entry)

    text = book.read_text()
    assert "My own note that must survive." in text
    assert "1,412,300" in text and "$3.41" in text
    assert "`open` → `partial`" in text
    assert "1 h 12 m" in text

    entries = read_entries(book)
    assert len(entries) == 1 and entries[0].run_id == "run-1"


def test_labbook_detects_existing_entry(tmp_path):
    from atelier.labbook import append_entry, has_entry

    book = tmp_path / "labbook.md"
    append_entry(book, "<!-- atelier:entry id=abc -->\nx\n<!-- /atelier:entry -->\n")
    assert has_entry(book, "abc") is True
    assert has_entry(book, "def") is False


def test_consume_notes_reads_then_removes(tmp_path):
    from atelier.labbook import consume_notes

    prog = tmp_path / "progress"
    prog.mkdir()
    (prog / "_session_notes.md").write_text("  what I did  ")
    assert consume_notes(prog) == "what I did"
    assert not (prog / "_session_notes.md").exists()
    assert consume_notes(prog) == ""


# ==========================================================================
# latex
# ==========================================================================


def test_latex_error_extraction():
    from atelier.latex import extract_errors

    log = (
        "This is pdfTeX, Version 3.14\n"
        "./math.tex:42: Undefined control sequence.\n"
        "! Missing $ inserted.\n"
        "l.57 \\frac{a}{b}\n"
        "Output written on math.pdf\n"
    )
    errs = extract_errors(log)
    assert any("math.tex:42" in e for e in errs)
    assert any("Missing $ inserted" in e and "l.57" in e for e in errs)


# ==========================================================================
# events
# ==========================================================================


def test_event_log_roundtrip_and_sequencing(tmp_path):
    from atelier.events import Event, EventLog

    log = EventLog(tmp_path / "events.jsonl")
    for i in range(5):
        log.append(Event(type="assistant.text", run_id="r", payload={"text": f"m{i}"}))
    log.close()

    all_events = EventLog.read(tmp_path / "events.jsonl")
    assert [e.seq for e in all_events] == [1, 2, 3, 4, 5]
    assert EventLog.read(tmp_path / "events.jsonl", after_seq=3)[0].seq == 4


async def test_hub_delivers_only_subscribed_topics():
    from atelier.events import Event, EventHub

    hub = EventHub()
    sub = await hub.subscribe("s1", {"run:1"})
    hub.publish("run:1", Event(type="x", run_id="1"))
    hub.publish("run:2", Event(type="y", run_id="2"))
    topic, event = sub.queue.get_nowait()
    assert topic == "run:1" and event.type == "x"
    assert sub.queue.empty()


def test_stopwatch_excludes_frozen_time():
    import time

    from atelier.events import Stopwatch

    sw = Stopwatch()
    sw.freeze()
    time.sleep(0.05)
    sw.thaw()
    assert sw.frozen >= 0.04
    assert sw.wall >= sw.frozen


# ==========================================================================
# claude stream-json parsing
# ==========================================================================


def _adapter():
    from atelier.providers import get_adapter

    return get_adapter("claude")


def test_parse_assistant_text_thinking_and_tools():
    events = _adapter().parse_event({
        "type": "assistant",
        "message": {
            "model": "claude-haiku-4-5",
            "content": [
                {"type": "thinking", "thinking": "let me consider"},
                {"type": "text", "text": "The answer is 6."},
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "echo hi"}},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10},
        },
    }, "run-1")
    kinds = [e.type for e in events]
    assert kinds == ["assistant.thinking", "assistant.text", "tool.use", "usage"]
    usage = events[-1].payload
    assert (usage["tok_in"], usage["tok_out"]) == (100, 20)
    assert (usage["cache_read"], usage["cache_write"]) == (50, 10)


def test_parse_tool_result_flattens_block_lists():
    events = _adapter().parse_event({
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
             "content": [{"type": "text", "text": "hi"}]},
        ]},
    }, "run-1")
    assert events[0].type == "tool.result"
    assert events[0].payload["content"] == "hi"


def test_parse_result_marks_final_and_carries_cost():
    events = _adapter().parse_event({
        "type": "result", "subtype": "success", "is_error": False,
        "num_turns": 7, "total_cost_usd": 0.0123, "duration_ms": 5000,
        "usage": {"input_tokens": 900, "output_tokens": 300,
                  "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5},
    }, "run-1")
    assert len(events) == 1
    p = events[0].payload
    assert p["final"] is True and p["num_turns"] == 7
    assert p["total_cost_usd"] == pytest.approx(0.0123)


def test_parse_error_result_emits_error_event():
    events = _adapter().parse_event(
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "result": "boom", "usage": {}}, "run-1")
    assert [e.type for e in events] == ["usage", "error"]


def test_subagent_events_are_labelled_separately():
    events = _adapter().parse_event({
        "type": "assistant", "parent_tool_use_id": "toolu_abcdef123456",
        "message": {"content": [{"type": "text", "text": "sub speaking"}]},
    }, "run-1")
    assert events[0].agent.startswith("sub:")


def test_unknown_event_is_kept_verbatim():
    events = _adapter().parse_event({"type": "brand_new_thing", "x": 1}, "run-1")
    assert events[0].type == "provider.raw"
    assert events[0].payload["raw"]["x"] == 1


def test_steering_encoding_is_one_json_line():
    payload = _adapter().encode_steering("wrap up now")
    assert payload.endswith("\n")
    obj = json.loads(payload)
    assert obj["type"] == "user"
    assert obj["message"]["content"][0]["text"] == "wrap up now"


def test_partial_message_deltas_parse():
    events = _adapter().parse_event({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "text_delta", "text": "abc"}},
    }, "run-1")
    assert events[0].type == "assistant.delta"
    assert events[0].payload["text"] == "abc"


# ==========================================================================
# ledger
# ==========================================================================


def test_rolling_windows_and_totals(tmp_cfg, tmp_db):
    from datetime import timedelta

    from atelier.config import Account
    from atelier.ledger import account_health

    acct = Account(id="a1", provider="claude", auth="subscription",
                   soft_cap_5h_usd=10.0, soft_cap_7d_usd=100.0)
    now = datetime.now(UTC)
    tmp_db.add_usage(run_id="r1", account="a1", ts=now.isoformat(),
                     model="claude-opus-5", cost=2.5)
    tmp_db.add_usage(run_id="r2", account="a1",
                     ts=(now - timedelta(hours=2)).isoformat(),
                     model="claude-opus-5", cost=1.5)
    tmp_db.add_usage(run_id="r3", account="a1",
                     ts=(now - timedelta(days=3)).isoformat(),
                     model="claude-opus-5", cost=10.0)

    h = account_health(tmp_cfg, tmp_db, acct)
    w5 = next(w for w in h.windows if w.label == "5h")
    w7 = next(w for w in h.windows if w.label == "7d")
    assert w5.spend == pytest.approx(4.0)
    assert w5.fraction == pytest.approx(0.4)
    assert w7.spend == pytest.approx(14.0)


def test_rate_limit_reset_parsing():
    from atelier.ledger import _guess_reset

    base = datetime(2026, 8, 9, 14, 0, tzinfo=UTC).isoformat()
    assert _guess_reset("rate limit; resets in 30 minutes", base).startswith("2026-08-09T14:30")
    assert _guess_reset("limit reached, resets at 18:00", base).startswith("2026-08-09T18:00")
    # Unparseable wording still yields a conservative estimate rather than nothing.
    assert _guess_reset("you are rate limited", base) is not None


# ==========================================================================
# db
# ==========================================================================


def test_run_upsert_is_idempotent(tmp_db):
    row = {"id": "r1", "problem": "p", "harness": "solo", "provider": "claude",
           "mode": "standard", "started_at": "2026-08-09T10:00:00Z", "status": "running"}
    tmp_db.upsert_run(row)
    tmp_db.upsert_run(row | {"status": "done", "cost_computed": 1.25})
    got = tmp_db.one("SELECT * FROM runs WHERE id = 'r1'")
    assert got["status"] == "done"
    assert got["cost_computed"] == pytest.approx(1.25)
    assert tmp_db.one("SELECT COUNT(*) n FROM runs")["n"] == 1


def test_steering_delivery_marking(tmp_db):
    sid = tmp_db.add_steering("r1", "2026-08-09T10:00:00Z", "wrap up")
    tmp_db.mark_steering_delivered(sid, "2026-08-09T10:00:05Z")
    row = tmp_db.one("SELECT * FROM steering WHERE id = ?", (sid,))
    assert row["delivered_at"] == "2026-08-09T10:00:05Z"


# ==========================================================================
# render
# ==========================================================================


def test_markdown_renders_math_for_katex():
    from atelier.render import render_markdown

    html = render_markdown("Let $x^2 = 4$ so\n\n$$\\int_0^1 x\\,dx = \\tfrac12$$\n")
    assert "math" in html
    assert "x^2" in html


def test_statement_display_without_blank_lines_is_a_separate_math_block():
    from atelier.render import render_markdown

    html = render_markdown(
        "**Example (paper, Example 2).** Under these hypotheses,\n"
        "$$\nQ(x)=1.\\tag{C.1}\n$$\n"
        "**Source correspondence.** This reproduces the paper's calculation.\n")

    assert "<p><strong>Example (paper, Example 2).</strong> Under these hypotheses,</p>" in html
    assert '<div class="math block">' in html
    assert '<div class="math inline">' not in html
    assert "</div>\n<p><strong>Source correspondence.</strong>" in html


def test_markdown_renders_latex_bracket_math_for_katex():
    from atelier.render import render_markdown

    html = render_markdown(
        "The speed diverges as \\(t\\uparrow1\\).\n\n"
        "\\[\n\\limsup_{t\\uparrow1}\\lVert u(t)\\rVert=\\infty.\n\\]\n")

    assert '<span class="math inline">t\\uparrow1</span>' in html
    assert '<div class="math block">' in html
    assert "\\limsup_{t\\uparrow1}\\lVert u(t)\\rVert=\\infty." in html
    assert "(t\\uparrow1)" not in html


def test_latex_bracket_delimiters_inside_code_remain_code():
    from atelier.render import render_markdown

    html = render_markdown(
        "`\\(not math\\)`\n\n```tex\n\\[also not math\\]\n```\n")

    assert "math_inline_bracket" not in html
    assert "math block" not in html
    assert "<code>\\(not math\\)</code>" in html


def test_standalone_math_delimiters_inside_fences_remain_code():
    from atelier.render import _separate_display_math, render_markdown

    source = "~~~tex\nfirst\n$$\nx=y\n$$\nlast\n~~~\n"
    assert _separate_display_math(source) == source
    html = render_markdown(source)
    assert "math block" not in html


def test_markdown_renders_tables_and_code():
    from atelier.render import render_markdown

    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nx = 1\n```\n")
    assert "<table>" in html
    assert "hl" in html


# ==========================================================================
# regressions found by probing the real CLI
# ==========================================================================


def test_usage_carries_message_id_for_deduplication():
    """The CLI repeats one API response's usage on every streamed block of that
    response. Without the message id the accumulator double-counts."""
    usage_events = []
    for blocks in ([{"type": "thinking", "thinking": "hm"}],
                   [{"type": "text", "text": "answer"}]):
        evs = _adapter().parse_event({
            "type": "assistant",
            "message": {"id": "msg_SAME", "model": "claude-haiku-4-5",
                        "content": blocks,
                        "usage": {"input_tokens": 10, "output_tokens": 8}},
        }, "r")
        usage_events += [e for e in evs if e.type == "usage"]

    assert len(usage_events) == 2
    assert {e.payload["message_id"] for e in usage_events} == {"msg_SAME"}


def test_cache_write_split_is_parsed():
    evs = _adapter().parse_event({
        "type": "assistant",
        "message": {"id": "m1", "content": [{"type": "text", "text": "x"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                              "cache_creation_input_tokens": 5886,
                              "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                 "ephemeral_1h_input_tokens": 5886}}},
    }, "r")
    p = [e for e in evs if e.type == "usage"][0].payload
    assert p["cache_write_1h"] == 5886 and p["cache_write_5m"] == 0


def test_one_hour_cache_writes_cost_more_than_five_minute(tmp_cfg):
    cheap = tmp_cfg.prices.cost("claude-haiku-4-5", cache_write=1_000_000,
                                cache_write_5m=1_000_000, when=date(2026, 8, 9))
    dear = tmp_cfg.prices.cost("claude-haiku-4-5", cache_write=1_000_000,
                               cache_write_1h=1_000_000, when=date(2026, 8, 9))
    assert cheap == pytest.approx(1.25)      # 1.25x input
    assert dear == pytest.approx(2.00)       # 2x input


def test_rate_limit_event_is_parsed_not_dropped():
    """The CLI volunteers its own window state; it is the best credits signal
    available for a subscription, so it must not fall through to provider.raw."""
    evs = _adapter().parse_event({
        "type": "rate_limit_event",
        "rate_limit_info": {"status": "allowed", "resetsAt": 1786280400,
                            "rateLimitType": "five_hour", "isUsingOverage": False},
    }, "r")
    assert len(evs) == 1 and evs[0].type == "rate_limit"
    assert evs[0].payload["window"] == "five_hour"
    assert evs[0].payload["resets_at_epoch"] == 1786280400
    assert evs[0].payload["status"] == "allowed"


def test_provider_budget_stop_is_not_a_crash():
    """`error_max_budget_usd` is a planned outcome: the work is on disk and the
    record should say so rather than reporting a generic failure."""
    evs = _adapter().parse_event({
        "type": "result", "subtype": "error_max_budget_usd", "is_error": True,
        "num_turns": 4, "total_cost_usd": 0.063, "usage": {"output_tokens": 1413},
    }, "r")
    usage = [e for e in evs if e.type == "usage"][0]
    assert usage.payload["subtype"] == "error_max_budget_usd"
    assert usage.payload["final"] is True


def test_event_log_tolerates_append_after_close(tmp_path):
    """Steering a finished run must not 500 on a closed log handle."""
    from atelier.events import Event, EventLog

    log = EventLog(tmp_path / "e.jsonl")
    log.append(Event(type="notice", run_id="r"))
    log.close()
    log.append(Event(type="notice", run_id="r"))       # must not raise
    assert len(EventLog.read(tmp_path / "e.jsonl")) == 1


def test_oneshot_never_widens_filesystem_access(tmp_cfg, sample_problem):
    """additionalDirectories must stay empty in one-shot mode. Leaking the real
    problem folder here would let a 'blind' control read progress/ and silently
    invalidate every benchmark comparison."""
    plan = _plan(tmp_cfg, sample_problem, "solo", mode="oneshot")
    settings = json.loads((plan.run_dir / "settings.json").read_text())
    assert settings["permissions"]["additionalDirectories"] == []
    assert "--add-dir" not in plan.argv
    assert str(sample_problem.root) not in json.dumps(settings)


def test_hooks_target_the_session_writable_root(tmp_cfg, sample_problem):
    """In one-shot the agent writes to the workspace, so the Stop hook must
    check the workspace — not the problem folder it cannot see."""
    plan = _plan(tmp_cfg, sample_problem, "solo", mode="oneshot")
    hooks = json.dumps(json.loads((plan.run_dir / "settings.json").read_text())["hooks"])
    assert str(plan.cwd) in hooks
    assert f'--problem "{sample_problem.root}"' not in hooks

    std = _plan(tmp_cfg, sample_problem, "solo", mode="standard")
    std_hooks = json.dumps(json.loads((std.run_dir / "settings.json").read_text())["hooks"])
    assert str(sample_problem.root) in std_hooks


# --- markdown → LaTeX, for typesetting a tool-less session's answer -----------

def test_links_protect_urls_from_emphasis_and_escape_them_once():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(
        "**See [Numdam record](https://www.numdam.org/issues/AST_2009__324__R1_0/).** "
        "[A *label* with $x_1$](https://example.org/~author/a_b?q=10%25&x=2#part)")
    assert (r"\textbf{See \href{https://www.numdam.org/issues/AST\_2009\_\_324\_\_R1\_0/}"
            r"{Numdam record}.}") in out
    assert r"\href{https://example.org/~author/a\_b?q=10\%25\&x=2\#part}" in out
    assert r"{A \emph{label} with $x_1$}" in out
    assert "\x00" not in out and "\x01" not in out


def test_math_survives_conversion_untouched():
    """The one thing this converter must never do is edit mathematics. Markdown
    metacharacters are common inside TeX (_ for subscripts, * for products,
    \\ for every macro) and mangling them silently corrupts the result."""
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(
        r"Take $a_1 * a_2$ and $$\sum_{i=1}^n \binom{n}{i} x^i$$ done.")
    assert r"$a_1 * a_2$" in out
    assert r"\sum_{i=1}^n \binom{n}{i} x^i" in out
    assert r"\_" not in out          # no escaping leaked into the math
    assert r"\emph" not in out       # the asterisk was not read as emphasis


def test_wide_markdown_tables_are_constrained_to_the_text_measure():
    """Companion tables should wrap in the PDF rather than run into its margin."""
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(
        "| left | middle | right |\n"
        "|:---|:---:|---:|\n"
        "| a deliberately long description | a second long description | 123 |\n")

    assert r"\begin{tabularx}{\linewidth}" in out
    assert r">{\raggedright\arraybackslash}X" in out
    assert r">{\centering\arraybackslash}X" in out
    assert r">{\raggedleft\arraybackslash}X" in out
    assert r"\end{tabularx}" in out


def test_table_rows_starting_with_brackets_do_not_become_latex_row_spacing():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(
        "| Reference | Meaning |\n"
        "|---|---|\n"
        "| [LMN18] | First citation |\n"
        "| [GG18] | Second citation |\n"
        "| [0,1] | An interval |\n")

    assert r"\relax [LMN18] & First citation" in out
    assert r"\relax [GG18] & Second citation" in out
    assert r"\relax [0,1] & An interval" in out


def test_known_citations_render_as_citations_without_changing_other_brackets():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(
        "| Reference | Meaning |\n"
        "|---|---|\n"
        "| [GG18] | Cited work |\n"
        "| [0,1] | An interval |\n\n"
        "See [GG18] and [Missing24]. Keep `[GG18]` as code and "
        "[GG18](https://example.org) as a link.\n",
        citation_keys=frozenset({"GG18"}))

    assert r"\cite{GG18} & Cited work" in out
    assert r"\cite{GG18}" in out
    assert r"\relax [0,1] & An interval" in out
    assert "[Missing24]" in out
    assert r"\texttt{[GG18]}" in out
    assert r"\href{https://example.org}{GG18}" in out


def test_prose_specials_are_escaped_but_math_is_not():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex("100% of a_b costs $5, but $x_y$ is math")
    assert r"100\%" in out
    assert r"a\_b" in out
    assert r"\$5" in out
    assert "$x_y$" in out


def test_inline_code_wins_over_math():
    """`$x$` in backticks is a shell variable, not a formula."""
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex("run `echo $HOME` now")
    assert r"\texttt{echo \$HOME}" in out


def test_structure_becomes_latex():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(
        "# Claim\n\nText with **bold**.\n\n- one\n- two\n\n## Proof\n\n1. first\n")
    assert r"\section*{Claim}" in out
    assert r"\subsection*{Proof}" in out
    assert r"\textbf{bold}" in out
    assert r"\begin{itemize}" in out and r"\end{itemize}" in out
    assert r"\begin{enumerate}" in out and r"\end{enumerate}" in out


def test_a_local_markdown_illustration_becomes_a_bounded_latex_image():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(
        "![Figure 2: population response](figures/figure-2.png)\n")
    assert r"\includegraphics[width=\linewidth,height=.72\textheight" in out
    assert r"\detokenize{figures/figure-2.png}" in out
    assert r"\emph{Figure 2: population response}" in out


def test_fenced_code_is_verbatim():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex("```python\nx = {1: '_a_'}\n```")
    assert r"\begin{verbatim}" in out
    assert "x = {1: '_a_'}" in out   # untouched inside the block


def test_answer_document_is_self_contained():
    from atelier.mdtex import answer_document

    doc = answer_document("# Result\n\n$1+1=2$\n", title="Test & Co",
                          provenance="chat · haiku")
    assert doc.startswith(r"\documentclass")
    assert r"\input{preamble}" in doc
    assert r"\title{Test \& Co}" in doc
    assert doc.rstrip().endswith(r"\end{document}")


def test_unicode_symbols_cannot_kill_the_compile():
    """pdflatex dies on any character outside Latin-1, and a real run lost its
    PDF to a single ✓ in a summary table. Everything must survive as ASCII."""
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex("Screen 5 ✗, others ✓ — α ≤ β → ℝ, ∀ε ∃δ")
    assert out.isascii(), [c for c in out if not c.isascii()]
    for macro in (r"\checkmark", r"\times", r"\alpha", r"\leq", r"\to",
                  r"\mathbb{R}", r"\forall", r"\exists"):
        assert macro in out, macro
    assert "---" in out           # the em dash


def test_unicode_inside_math_is_converted_too():
    """A model writing `$α ≤ β$` would otherwise pass the glyphs straight into
    the formula, where they break the compile just as surely."""
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex(r"we have $α ≤ β$ throughout")
    assert out.isascii()
    assert r"\alpha" in out and r"\leq" in out


def test_unmapped_characters_are_reported_not_hidden():
    from atelier.mdtex import markdown_to_latex, unmapped_characters

    assert unmapped_characters("plain ✓ α text") == []
    assert unmapped_characters("emoji 🎉 here") == ["🎉"]
    assert markdown_to_latex("emoji 🎉 here").isascii()


# --- surfacing results the problem folder never sees -------------------------

def test_subscripts_and_minus_survive():
    """A run produced `k₁ + k₂` and `−2`; both became `k?` and `?2`, which is a
    wrong document that reads as a right one. Nothing may degrade silently."""
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex("Let k₁, k₂ be integers, and take −2 and x².")
    assert out.isascii()
    assert r"\ensuremath{_{1}}" in out and r"\ensuremath{_{2}}" in out
    assert r"\ensuremath{-}2" in out
    assert r"\ensuremath{^{2}}" in out
    assert "?" not in out


def test_underscore_escaping_cannot_break_subscript_macros():
    """The blanket `_` → `\_` pass runs before Unicode macros are introduced;
    the other order turns \ensuremath{_{1}} into nonsense."""
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex("a_b and k₁")
    assert r"a\_b" in out
    assert r"\ensuremath{_{1}}" in out
    assert r"\_{1}" not in out


def test_unknown_characters_are_visible_not_silent():
    from atelier.mdtex import markdown_to_latex

    out = markdown_to_latex("emoji 🎉 here")
    assert "[U+1F389]" in out and out.isascii()


def test_problems_inside_the_checkout_still_get_their_own_history(tmp_path, monkeypatch):
    """The default layout puts problems inside the atelier folder. Asking git
    only "are we inside a repository" answers yes — about *atelier's* repository
    — so no history was ever created and every session commit silently did
    nothing, because the path is ignored there."""
    import subprocess

    from atelier.git import ensure_repo, is_repo

    outer = tmp_path / "atelier"
    problems = outer / "problems"
    problems.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    (outer / ".gitignore").write_text("problems/\n")

    cfg = type("C", (), {
        "problems_dir": problems,
        "git": type("G", (), {"author_name": "t", "author_email": "t@e",
                              "enabled": True})(),
    })()

    assert is_repo(cfg) is False          # atelier's repo must not count
    assert ensure_repo(cfg) is True
    assert is_repo(cfg) is True
    assert (problems / ".git").is_dir()


def test_allowed_warning_is_not_a_block():
    """The CLI reports `allowed_warning` when a utilisation threshold is crossed.
    Recording it as a block put a false 'Rate limited until …' chip on the
    account and made a healthy session look as though it had been stopped."""
    from atelier.runner import rate_limit_kind

    assert rate_limit_kind("allowed") == "window"
    assert rate_limit_kind("allowed_warning") == "window"
    assert rate_limit_kind(None) == "window"
    assert rate_limit_kind("rejected") == "rate_limited"


def test_each_oneshot_session_keeps_its_own_document(tmp_path):
    """One-shot sessions are independent attempts. Each keeps its own PDF in its
    own run directory: none overwrites another, and none is promoted to being the
    problem's document, because a blind attempt is not the problem's state."""
    from atelier.web.api import _run_pdf

    runs = tmp_path / "runs"
    for name in ("r1", "r2", "r3"):
        (runs / name).mkdir(parents=True)
    (runs / "r1" / "answer.pdf").write_bytes(b"%PDF-one")
    (runs / "r2" / "answer.pdf").write_bytes(b"%PDF-two")

    one, two = _run_pdf(runs, "r1"), _run_pdf(runs, "r2")
    assert one and two
    assert one["name"] == two["name"] == "answer.pdf"
    # separate files, so one attempt can never stand in for another
    assert (runs / "r1" / "answer.pdf").read_bytes() != (runs / "r2" / "answer.pdf").read_bytes()
    assert _run_pdf(runs, "r3") is None      # a session that produced none

    # a session's own compiled document outranks a typeset answer
    (runs / "r3" / "answer.pdf").write_bytes(b"%PDF-a")
    (runs / "r3" / "math.pdf").write_bytes(b"%PDF-m")
    assert _run_pdf(runs, "r3")["name"] == "math.pdf"


def test_the_problem_pdf_is_not_taken_from_a_session(tmp_cfg, sample_problem, tmp_db):
    """The problem's PDF tab must show only progress/math.pdf. A one-shot
    document appearing there would make a blind attempt look like the problem's
    established state."""
    import asyncio

    from atelier.web.api import api_problem

    run_dir = tmp_cfg.runs_dir / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "answer.pdf").write_bytes(b"%PDF-blind")
    tmp_db.upsert_run({"id": "r1", "problem": sample_problem.slug, "harness": "chat",
                       "provider": "claude", "mode": "oneshot",
                       "started_at": "2026-08-17T10:00:00", "status": "done"})

    request = type("R", (), {"app": type("A", (), {"state": type("S", (), {
        "atelier": type("St", (), {
            "cfg": tmp_cfg, "db": tmp_db,
            "manager": type("M", (), {"for_problem": staticmethod(lambda s: None)})(),
        })()})()})()})()

    data = asyncio.run(api_problem(request, sample_problem.slug))
    assert data["has_pdf"] is False           # the problem itself has none
    assert "session_pdf" not in data          # and none is borrowed from a run
    assert data["runs"][0]["pdf"]["name"] == "answer.pdf"   # it lives on its session


# --- Codex ------------------------------------------------------------------
# Each of these encodes something learned by probing codex-cli 0.147.0, where
# the wrong assumption fails silently rather than loudly.

def test_codex_must_close_stdin():
    """`codex exec` waits for end-of-input before it does anything. Holding
    stdin open for steering — as the Claude adapter does — hangs it forever."""
    from atelier.providers import get_adapter

    codex = get_adapter("codex")
    assert codex.capabilities.closes_stdin_after_prompt is True
    assert codex.capabilities.steering == "none"
    assert get_adapter("claude").capabilities.closes_stdin_after_prompt is False


def test_codex_cannot_be_steered_and_says_so():
    from atelier.providers import get_adapter

    with pytest.raises(NotImplementedError):
        get_adapter("codex").encode_steering("wrap up")


def test_codex_usage_does_not_double_count_cached_input():
    """Codex's `input_tokens` already includes `cached_input_tokens`."""
    from atelier.providers import get_adapter

    events = get_adapter("codex").parse_event({
        "type": "turn.completed",
        "usage": {"input_tokens": 10379, "cached_input_tokens": 4480,
                  "cache_write_input_tokens": 0, "output_tokens": 33,
                  "reasoning_output_tokens": 22},
    }, "r1")
    [usage] = [e for e in events if e.type == "usage"]
    assert usage.payload["tok_in"] == 10379 - 4480
    assert usage.payload["cache_read"] == 4480
    assert usage.payload["tok_out"] == 33
    assert usage.payload["turn_delta"] == 1      # or every run reports 0 turns


def test_codex_parses_the_shapes_it_actually_emits():
    from atelier.providers import get_adapter

    codex = get_adapter("codex")
    kinds = lambda evs: [e.type for e in evs]  # noqa: E731

    assert "notice" in kinds(codex.parse_event(
        {"type": "thread.started", "thread_id": "t1"}, "r"))
    assert "assistant.text" in kinds(codex.parse_event(
        {"type": "item.completed",
         "item": {"id": "i", "type": "agent_message", "text": "hello"}}, "r"))
    assert "tool.use" in kinds(codex.parse_event(
        {"type": "item.started",
         "item": {"id": "i", "type": "command_execution", "command": "ls"}}, "r"))
    assert "tool.result" in kinds(codex.parse_event(
        {"type": "item.completed",
         "item": {"id": "i", "type": "command_execution", "command": "ls",
                  "aggregated_output": "x", "exit_code": 0}}, "r"))
    # nothing is ever dropped
    assert "provider.raw" in kinds(codex.parse_event({"type": "brand.new"}, "r"))


def test_codex_unwraps_its_nested_error_messages():
    """Codex puts a JSON document inside the `message` string."""
    from atelier.providers import get_adapter

    events = get_adapter("codex").parse_event({
        "type": "turn.failed",
        "error": {"message": json.dumps({
            "type": "error", "status": 400,
            "error": {"type": "invalid_request_error",
                      "message": "The 'x' model is not supported."}})},
    }, "r")
    assert events[0].type == "error"
    assert events[0].payload["message"] == "The 'x' model is not supported."


def test_codex_home_is_sanitised_of_standing_instructions(tmp_path):
    """`--ignore-user-config` ignores config.toml but NOT $CODEX_HOME/AGENTS.md,
    which still reaches the model — verified against the real binary. Standing
    instructions there would apply to every session and quietly destroy any
    comparison between harnesses."""
    from atelier.providers import get_adapter

    cfg = type("C", (), {"root": tmp_path})()
    home = tmp_path / "var" / "codex" / "default"
    home.mkdir(parents=True)
    (home / "AGENTS.md").write_text("always say banana")
    (home / "config.toml").write_text("model = 'x'")

    got = get_adapter("codex")._sanitised_home(cfg, None)
    assert got == home
    assert not (home / "AGENTS.md").exists()
    assert not (home / "config.toml").exists()


def test_an_unpriced_model_never_runs_uncapped(tmp_cfg, sample_problem):
    """A dollar cap cannot bind when the model has no price — cost stays zero.
    Without a fallback the session would run with no limit at all."""
    plan = _plan(tmp_cfg, sample_problem, "chat", provider="codex",
                 model="gpt-5.4-mini", max_usd=5.0)
    assert plan.budget_usd is None
    assert plan.budget_minutes, "an unpriced model must still get a wall-clock cap"


def test_choosing_an_account_chooses_its_provider(tmp_cfg, sample_problem):
    """Picking the Codex account must not launch Claude with Codex's env."""
    plan = _plan(tmp_cfg, sample_problem, "chat", account="codex")
    assert "codex" in plan.argv[0]
    assert "CODEX_HOME" in plan.env


def test_web_launch_takes_the_provider_from_the_account(tmp_cfg):
    """The dialog sends an account, not a provider. Defaulting the provider here
    launched Claude with a Codex model id."""
    from atelier.web.api import _spec_from_body

    st = type("S", (), {"cfg": tmp_cfg})()
    spec = _spec_from_body(st, {"problem": "p", "harness": "chat", "account": "codex"})
    assert spec.provider == "codex"

    spec = _spec_from_body(st, {"problem": "p", "harness": "chat", "account": "default"})
    assert spec.provider == "claude"

    # an explicit provider still wins
    spec = _spec_from_body(st, {"problem": "p", "harness": "chat",
                                "account": "codex", "provider": "claude"})
    assert spec.provider == "claude"


def test_a_method_that_cannot_be_reproduced_is_not_offered():
    """explore-verify sends each claim to a separate referee agent. Codex has no
    subagents, so offering it there would be a different method under the same
    name — and a benchmark that compares two different things."""
    from pathlib import Path as _P

    from atelier.harnesses import discover

    repo = _P(__file__).resolve().parents[1]
    harnesses = {h.id: h for h in discover(repo / "harnesses")[0]}
    assert harnesses["explore-verify"].supports("claude")
    assert not harnesses["explore-verify"].supports("codex")
    for name in ("chat", "solo", "solo-deep"):
        assert harnesses[name].supports("codex"), name


# --- a tool-less standard session must still advance the document -----------

def _fold(progress_dir, text, *, run_id="R1", session=1, harness="chat",
          model="m", notices=None):
    """Call the real _append_answer_to_document against a stub session."""
    import datetime as dt

    from atelier.runner import RunHandle

    sink = notices if notices is not None else []

    handle = type("H", (), {
        "run_id": run_id,
        "session_number": session,
        "started_at": dt.datetime(2026, 8, 17, 12, 0),
        # `notices or []` would swap an empty list for a throwaway one.
        "notice": lambda self, msg, **kw: sink.append(msg),
        "plan": type("P", (), {
            "problem": type("Pr", (), {"progress_dir": progress_dir})(),
            "harness": type("Ha", (), {"id": harness})(),
            "model": model,
        })(),
    })()
    RunHandle._append_answer_to_document(handle, text)


SCAFFOLD = (
    "\\documentclass[11pt]{article}\n\\input{preamble}\n\n"
    "\\begin{document}\n\\maketitle\n\n"
    "\\section{Problem}\n% Restate the problem.\n\n"
    "\\end{document}\n"
)


def test_a_toolless_standard_session_reaches_the_document(tmp_path):
    """`chat` has no filesystem, so it cannot fold its own results into
    progress/math.tex. That left a standard session's mathematics sitting only in
    the run while the problem's PDF stayed an empty scaffold of headings."""
    progress = tmp_path / "progress"
    progress.mkdir()
    (progress / "math.tex").write_text(SCAFFOLD)

    _fold(progress, "# Claim\n\nThe sum of two even numbers is even, since\n"
                    "$2j + 2k = 2(j+k)$.\n")
    out = (progress / "math.tex").read_text()

    assert "Session 1" in out
    assert "$2j + 2k = 2(j+k)$" in out          # the mathematics, untouched
    assert out.rstrip().endswith("\\end{document}")   # still a valid document
    assert out.index("Session 1") < out.rindex("\\end{document}")
    assert "\\section{Problem}" in out          # nothing existing was removed


def test_folding_in_never_happens_twice(tmp_path):
    """Finalize can be reached more than once; the document must not grow a
    duplicate copy of the same session's work."""
    progress = tmp_path / "progress"
    progress.mkdir()
    (progress / "math.tex").write_text(SCAFFOLD)

    _fold(progress, "First.", run_id="R1")
    once = (progress / "math.tex").read_text()
    _fold(progress, "First.", run_id="R1")
    assert (progress / "math.tex").read_text() == once


def test_each_session_appends_below_the_last(tmp_path):
    """Standard sessions accumulate, in order, and none overwrites another."""
    progress = tmp_path / "progress"
    progress.mkdir()
    (progress / "math.tex").write_text(SCAFFOLD)

    _fold(progress, "About six.", run_id="R1", session=1)
    _fold(progress, "About twenty-eight.", run_id="R2", session=2)
    out = (progress / "math.tex").read_text()
    assert out.count("\\end{document}") == 1
    assert out.index("About six.") < out.index("About twenty-eight.")
    assert "Session 1" in out and "Session 2" in out


def test_folding_in_is_marked_as_machine_inserted(tmp_path):
    """A reader must be able to tell curated mathematics from text the app
    dropped in on a session's behalf."""
    progress = tmp_path / "progress"
    progress.mkdir()
    (progress / "math.tex").write_text(SCAFFOLD)

    notices = []
    _fold(progress, "Something.", notices=notices)
    out = (progress / "math.tex").read_text()
    assert "% atelier:session R1" in out
    assert "no file access" in out
    assert any("math.tex" in n for n in notices)


def test_folding_in_tolerates_a_document_without_an_end(tmp_path):
    progress = tmp_path / "progress"
    progress.mkdir()
    (progress / "math.tex").write_text("\\section{Notes}\nnothing else\n")
    _fold(progress, "Appended anyway.")
    assert "Appended anyway." in (progress / "math.tex").read_text()


def test_nothing_is_written_when_there_is_no_document(tmp_path):
    progress = tmp_path / "progress"
    progress.mkdir()
    _fold(progress, "Some text.")
    assert not (progress / "math.tex").exists()


def test_fold_in_reports_whether_it_worked(tmp_path):
    """The runner falls back to giving the session its own PDF only when the
    document could not take the work, so the answer has to be trustworthy."""
    import datetime as dt

    from atelier.runner import RunHandle

    def attempt(progress):
        handle = type("H", (), {
            "run_id": "R1", "session_number": 1,
            "started_at": dt.datetime(2026, 8, 17, 12, 0),
            "notice": lambda self, m, **k: None,
            "plan": type("P", (), {
                "problem": type("Pr", (), {"progress_dir": progress})(),
                "harness": type("Ha", (), {"id": "chat"})(), "model": "m",
            })(),
        })()
        return RunHandle._append_answer_to_document(handle, "Some mathematics.")

    good = tmp_path / "good"
    good.mkdir()
    (good / "math.tex").write_text(SCAFFOLD)
    assert attempt(good) is True
    assert attempt(good) is True          # idempotent, still "it is in there"

    missing = tmp_path / "missing"
    missing.mkdir()
    assert attempt(missing) is False      # no document: caller must keep a PDF


def test_the_folded_heading_uses_local_time(tmp_path):
    """started_at is UTC. Printed raw, two sessions an hour apart looked hours
    apart in the document, and out of order against the lab book."""
    import datetime as dt

    from atelier.runner import RunHandle

    progress = tmp_path / "progress"
    progress.mkdir()
    (progress / "math.tex").write_text(SCAFFOLD)

    utc_noon = dt.datetime(2026, 8, 17, 12, 0, tzinfo=dt.UTC)
    handle = type("H", (), {
        "run_id": "R1", "session_number": 1, "started_at": utc_noon,
        "notice": lambda self, m, **k: None,
        "plan": type("P", (), {
            "problem": type("Pr", (), {"progress_dir": progress})(),
            "harness": type("Ha", (), {"id": "chat"})(), "model": "m",
        })(),
    })()
    RunHandle._append_answer_to_document(handle, "Text.")

    expected = f"{utc_noon.astimezone():%d %B %Y %H:%M}"
    assert expected in (progress / "math.tex").read_text()


def test_a_rebuilt_run_sorts_among_the_others(tmp_cfg, capsys):
    """`reindex` gives a run with no result the moment its id begins with,
    spelled as the runner spells it — not the id's own spelling, which sorts
    below a run started earlier in the same hour."""
    from atelier import db as dbmod
    from atelier.__main__ import cmd_reindex, started_from_id

    assert started_from_id("2026-09-16T22-15-31Z-bbe1") == "2026-09-16T22:15:31+00:00"
    assert started_from_id("probe-read") == "probe-read"

    for run_id, result in (("2026-09-16T22-10-39Z-64f5", True),
                           ("2026-09-16T22-15-31Z-bbe1", False)):
        rd = tmp_cfg.runs_dir / run_id
        rd.mkdir()
        (rd / "config.json").write_text(json.dumps(
            {"run_id": run_id, "resolved": {"problem": "p", "harness": "deslopify"}}))
        if result:
            (rd / "result.json").write_text(json.dumps(
                {"started_at": "2026-09-16T22:10:39.446459+00:00", "state": "done"}))
    dbmod.reset_db()
    try:
        assert cmd_reindex(tmp_cfg) == 0
        rows = dbmod.get_db(tmp_cfg.db_path).query(
            "SELECT id FROM runs ORDER BY started_at DESC")
        assert [r["id"] for r in rows] == ["2026-09-16T22-15-31Z-bbe1",
                                           "2026-09-16T22-10-39Z-64f5"]
    finally:
        dbmod.reset_db()


def test_an_arxiv_category_is_not_taken_for_a_web_address():
    """`math.GT` became a link to http://math.GT (GT is Guatemala's domain);
    real addresses are still linked."""
    from atelier.render import render_markdown

    out = render_markdown("Searched math.GT, math.AT and q-bio.NC; see "
                          "https://arxiv.org/abs/2609.01234, www.example.org and arxiv.org/list.")
    assert "math.GT" in out and 'href="http://math.GT"' not in out
    assert 'href="http://math.AT"' not in out and 'href="http://q-bio.NC"' not in out
    assert 'href="https://arxiv.org/abs/2609.01234"' in out
    assert 'href="http://www.example.org"' in out and 'href="http://arxiv.org/list"' in out
