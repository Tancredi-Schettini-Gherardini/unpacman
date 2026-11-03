"""The critique loop as Atelier runs it: passes, budgets, lanes and convergence.

The property worth defending here is that a run *without* a loop is byte-for-byte
the run it always was. Everything else in this file is about the loop's own
integrity: that a critic cannot edit the thing it is reviewing, that a document
put back is put back exactly, that a round which polished is recorded as having
polished, and that usage measured across several processes adds up.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from atelier.compile import pass_budgets, pass_sequence, plan_next_pass, plan_run
from atelier.harnesses import get_harness
from atelier.spec import RunSpec

DOCUMENT = r"""\documentclass{article}
\begin{document}
Let $a$ and $b$ be even integers, so $a = 2m$ and $b = 2n$.
Then $a + b = 2(m + n)$, which is even.
A direct search shows there are no further cases.
\end{document}
"""

GRAPH = {
    "document": "math.tex",
    "nodes": [
        {"id": "H0", "label": "a and b are even",
         "quote": "Let $a$ and $b$ be even integers, so $a = 2m$ and $b = 2n$."},
        {"id": "Q1", "label": "the sum is even",
         "quote": "Then $a + b = 2(m + n)$, which is even."},
    ],
    "edges": [{"source": "H0", "target": "Q1", "type": "SUPPORTS",
               "justification": "substitute and factor"}],
    "appeals": [{"line": 5, "verdict": "gap", "note": "no search is shown"}],
}

GUIDE = """\
# Companion guide

> **Notice.** This companion is explanatory, not a certification of
> correctness; readers remain responsible for checking the source.

## Main results

The sum of two even integers is even (Q1).

## Main ideas

Parity survives addition (H0 to Q1).

## About the proofs

H0 supports Q1 and supplies its only input.

## Q&A

Where is H0 used? At Q1.

## Worked examples

$a = 2$, $b = 4$.

## Historical context

This elementary source is the document under discussion [Tes26].

## Where the document is thin

The final case check is asserted, not shown.

## References cited in this companion

- [Tes26] A. Test, “Even integer addition,” unpublished mathematical test fixture, 2026.
"""

SUMMARY = """\
# Paper summary

The document proves that the sum of two even integers is even by writing each
integer as twice an integer and factoring their sum. It separately asserts that
a direct search leaves no further cases, but does not show that search.
"""

REVIEW = {
    "round": 1,
    "summary": "the map is right as far as it goes",
    "findings": [{
        "id": "F1", "kind": "missing", "severity": "blocking",
        "where": "graph:Q1",
        "quote": "A direct search shows there are no further cases.",
        "problem": "the search is never recorded as a gap anywhere",
        "fix": "record it as a gap or show the search",
    }],
}


@pytest.fixture
def reading_problem(tmp_cfg, sample_problem):
    """A problem that already holds a companion, as after a draft pass."""
    p = sample_problem
    p.ensure_scaffold()
    (p.progress_dir / "math.tex").write_text(DOCUMENT, encoding="utf-8")
    reading = p.progress_dir / "reading"
    reading.mkdir(parents=True, exist_ok=True)
    (reading / "summary.md").write_text(SUMMARY, encoding="utf-8")
    (reading / "summary.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (reading / "graph.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    (reading / "guide.md").write_text(GUIDE, encoding="utf-8")
    (reading / "companion.md").write_text(
        SUMMARY.strip() + "\n\n---\n\n" + GUIDE.strip() + "\n", encoding="utf-8")
    (reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (reading / "map.mmd").write_text("graph TD\n  H0 --> Q1\n", encoding="utf-8")
    (reading / "map.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return p


def deslopify_harness(tmp_cfg, **loop):
    h = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    # Most tests below exercise the generic historical critic/reviser engine.
    # Keep that compatibility surface while dedicated pipeline tests exercise
    # the guide-first sequence declared by the current harness.
    legacy = replace(h.loop, sequence=[], critic="critic", rounds=2,
                     share={"draft": 0.40, "critic": 0.12, "reviser": 0.18})
    return replace(h, loop=replace(legacy, **loop))


def plan_for(tmp_cfg, problem, harness, **spec_kw):
    spec = RunSpec(problem=problem.slug, harness=harness.id, account="default",
                   model="claude-haiku-4-5", effort="low", **spec_kw)
    return plan_run(tmp_cfg, problem, harness, spec)


# --- a run without a loop is the run it always was --------------------------

def test_a_harness_with_no_loop_plans_exactly_one_pass(tmp_cfg, sample_problem):
    plan = plan_for(tmp_cfg, sample_problem, get_harness(tmp_cfg.harnesses_dir, "solo"))
    assert len(plan.passes) == 1
    assert plan.passes[0].role == "draft"
    # The fields every existing reader uses still resolve, to the first pass.
    assert plan.argv is plan.passes[0].argv
    assert plan.prompt == plan.passes[0].prompt
    assert plan.passes[0].budget_usd == plan.budget_usd
    assert (plan.run_dir / "prompt.md").is_file()
    assert (plan.run_dir / "settings.json").is_file()
    assert (plan.run_dir / "argv.json").is_file()


def test_a_run_may_turn_the_loop_off(tmp_cfg, reading_problem):
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness, loop_rounds=0)
    assert plan.harness.loop.enabled is False
    assert pass_sequence(plan.harness) == [("draft", "draft", 0)]
    assert plan.passes[0].budget_usd == plan.budget_usd


def test_a_run_may_ask_for_fewer_rounds_without_changing_the_harness(tmp_cfg,
                                                                     reading_problem):
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness, loop_rounds=1)
    assert plan.harness.loop.rounds == 1
    assert harness.loop.rounds == 2        # the harness object is not mutated


def test_the_draft_writes_only_the_guide_before_graph_building(tmp_cfg,
                                                               reading_problem):
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    instructions = (plan.run_dir / "skill-references" / "SKILL-pure-expert.md").read_text()

    assert "progress/reading/guide.md" in instructions
    assert "Generate a new proof map only when the run" in instructions
    assert "summary.md" not in instructions
    assert "summary.pdf" not in plan.prompt
    assert "recorded paper profile is pure mathematics" in plan.prompt
    common = plan.run_dir / "skill-references" / "SKILL-common.md"
    assert str(common) in plan.system_append
    assert common.read_text() == harness.skill_references[common.name]
    assert "References cited in this companion" in common.read_text()
    # Shared instructions stay on demand, preserving the initial source budget.
    assert common.read_text().strip() not in plan.system_append


def test_deslopify_allows_public_research_with_restricted_commands(
        tmp_cfg, reading_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    assert harness.permissions.network is True
    assert "WebSearch" in harness.tools.allow
    assert "WebFetch" in harness.tools.allow

    spec = RunSpec(problem=reading_problem.slug, harness=harness.id,
                   account="codex", provider="codex", model="gpt-5.4",
                   effort="low")
    codex = plan_run(tmp_cfg, reading_problem, harness, spec)
    assert "--search" in codex.argv
    assert "--sandbox" not in codex.argv
    assert 'default_permissions="unpacman"' in codex.argv
    assert 'approval_policy="never"' in codex.argv
    assert 'web_search="live"' in codex.argv
    assert 'features.network_proxy=true' in codex.argv
    assert 'permissions.unpacman.network.allow_local_binding=false' in codex.argv
    assert "Do not look for AGENTS.md" in codex.prompt
    assert "Public literature searches and downloads are allowed" in codex.prompt
    assert "No web research" not in codex.prompt


def test_a_codex_reading_may_read_codex_itself(tmp_cfg, reading_problem, tmp_path,
                                              monkeypatch):
    # Codex starts itself again to apply each file edit; under the reading's
    # default-deny profile it must be let read its own program (2026-10-15).
    release = tmp_path / "codex-home/packages/standalone/releases/9.9.9-test"
    (release / "bin").mkdir(parents=True)
    (release / "bin/codex").write_text("#!/bin/sh\n")
    (release / "bin/codex").chmod(0o755)
    (release / "codex-package.json").write_text("{}")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/codex").symlink_to(release / "bin/codex")
    from atelier.providers import codex as codex_provider
    monkeypatch.setattr(codex_provider.shutil, "which",
                        lambda name, *a, **k: str(tmp_path / "bin/codex") if name == "codex" else None)
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    spec = RunSpec(problem=reading_problem.slug, harness=harness.id,
                   account="codex", provider="codex", model="gpt-5.4", effort="low")
    plan = plan_run(tmp_cfg, reading_problem, harness, spec)
    fs = next(a for a in plan.argv if a.startswith("permissions.unpacman.filesystem="))
    for folder in (tmp_path / "bin", release):
        assert f'"{folder.resolve()}"="read"' in fs
    assert f'"{tmp_path.resolve()}"=' not in fs        # nothing above it


def test_draft_contract_requires_a_cited_academic_bibliography(
        tmp_cfg, reading_problem):
    from pathlib import Path
    import subprocess
    import sys

    reading = reading_problem.progress_dir / "reading"
    guide = reading / "guide.md"
    original = guide.read_text(encoding="utf-8")
    guide.write_text(original.replace(
        "\n## References cited in this companion\n\n"
        "- [Tes26] A. Test, “Even integer addition,” unpublished "
        "mathematical test fixture, 2026.\n",
        "\n"), encoding="utf-8")
    (reading_problem.progress_dir / "_session_notes.md").write_text(
        "Citation-contract test.\n", encoding="utf-8")
    run_dir = tmp_cfg.runs_dir / "hook-guide-citations"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"

    result = subprocess.run(
        [sys.executable, str(hook), "--problem", str(reading_problem.root),
         "--run", str(run_dir), "--cwd", str(reading_problem.root),
         "--role", "draft", "--expect", "progress/reading/guide.md",
         "--validate", "guide"],
        input="{}", capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 2
    assert "References cited in this companion" in result.stderr


def test_draft_contract_rejects_an_unresolved_historical_citation(
        tmp_cfg, reading_problem):
    from pathlib import Path
    import subprocess
    import sys

    reading = reading_problem.progress_dir / "reading"
    guide = reading / "guide.md"
    guide.write_text(
        guide.read_text(encoding="utf-8").replace(
            "document under discussion [Tes26]",
            "document under discussion [Mis26]"),
        encoding="utf-8")
    (reading_problem.progress_dir / "_session_notes.md").write_text(
        "Citation-key test.\n", encoding="utf-8")
    run_dir = tmp_cfg.runs_dir / "hook-guide-citation-key"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"

    result = subprocess.run(
        [sys.executable, str(hook), "--problem", str(reading_problem.root),
         "--run", str(run_dir), "--cwd", str(reading_problem.root),
         "--role", "draft", "--expect", "progress/reading/guide.md",
         "--validate", "guide"],
        input="{}", capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 2
    assert "Mis26" in result.stderr
    assert "Tes26" in result.stderr


@pytest.mark.parametrize("selection", [None, "applied", "pure"])
def test_draft_contract_checks_the_selected_profile(tmp_cfg, reading_problem, selection):
    from pathlib import Path
    import subprocess
    import sys

    reading = reading_problem.progress_dir / "reading"
    applied = GUIDE.replace("## Main results", "## Main findings").replace(
        "## Main ideas\n\nParity survives addition (H0 to Q1).\n\n", ""
    ).replace(
        "## About the proofs\n\nH0 supports Q1 and supplies its only input.\n\n", ""
    ).replace(
        "## Where the document is thin",
        "## About the method and experimental validation\n\n"
        "H0 supports Q1 and supplies its only input.\n\n"
        "## Where the document is thin",
    )
    (reading / "guide.md").write_text(applied, encoding="utf-8")
    if selection is not None:
        (reading / "paper-profile.json").write_text(
            json.dumps({"papers": [{"profile": selection}]}), encoding="utf-8")
    (reading_problem.progress_dir / "_session_notes.md").write_text(
        "Profile contract test.\n", encoding="utf-8")
    run_dir = tmp_cfg.runs_dir / "hook-guide-profile"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"
    result = subprocess.run(
        [sys.executable, str(hook), "--problem", str(reading_problem.root),
         "--run", str(run_dir), "--cwd", str(reading_problem.root),
         "--role", "draft", "--expect", "progress/reading/guide.md",
         "--validate", "guide"],
        input="{}", capture_output=True, text=True, timeout=10,
    )
    if selection == "pure":
        assert result.returncode == 2
        assert "main ideas" in result.stderr and "about the proofs" in result.stderr
    else:
        assert result.returncode == 0, result.stderr


def test_deslopify_reader_modes_route_to_separate_profiles(tmp_cfg):
    from atelier.harnesses import discover

    found, errors = discover(tmp_cfg.harnesses_dir)
    assert errors == {}
    by_id = {h.id: h for h in found}
    for audience in ("expert", "nonexpert"):
        single = by_id[f"deslopify-{audience}"]
        collection = by_id[f"deslopify-multi-{audience}"]
        profiles = {f"SKILL-{profile}-{audience}.md" for profile in ("pure", "applied")}
        names = profiles | {"SKILL-common.md"}
        assert set(single.skill_references) == set(collection.skill_references) == names
        for name in names:
            assert name in single.skill
            assert single.skill_references[name] == collection.skill_references[name]
        assert "## Presentation and clarity" in single.skill_references["SKILL-common.md"]
        # Routing must not load both full methods into the model's prompt.
        assert "## Presentation and clarity" not in single.skill
        pure = single.skill_references[f"SKILL-pure-{audience}.md"]
        applied = single.skill_references[f"SKILL-applied-{audience}.md"]
        assert "### Main ideas\n" in pure and "### About the proofs\n" in pure
        assert "### Main ideas\n" not in applied and "### About the proofs\n" not in applied
        assert "### About the method and experimental validation\n" in applied
        assert "### Applied-mathematics profile\n" not in pure
        assert "### Pure-mathematics profile\n" not in applied
        assert single.loop.sequence == ["auditor", "balanced-reviser"]
        assert single.budget.max_minutes == 50
        assert collection.contract == "guide"
        assert "same depth and conceptual emphasis as in a solo" in collection.skill
        assert collection.loop.sequence == single.loop.sequence
        assert collection.loop.enabled and collection.loop.continue_at == single.loop.continue_at
        assert collection.budget.max_minutes == single.budget.max_minutes
    assert by_id["deslopify-search"].skill_references == {}
    assert by_id["deslopify-search"].loop.enabled is False


@pytest.mark.parametrize("audience", ["expert", "nonexpert"])
def test_balanced_stages_share_provider_caps_and_enforce_time_handoffs(
        tmp_cfg, reading_problem, audience):
    harness = get_harness(tmp_cfg.harnesses_dir, f"deslopify-{audience}")
    plan = plan_for(tmp_cfg, reading_problem, harness, max_minutes=20)
    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    revision = plan_next_pass(tmp_cfg, plan, role_name="balanced-reviser",
                              round_no=1, index=3)

    assert plan.budget_minutes == 20
    assert [p.budget_minutes for p in (plan.first, audit, revision)] == [20, 25, 25]
    assert "Time available for this stage: about 10 min" in plan.first.prompt
    assert "Time available for this stage: about 17 min" in audit.prompt
    assert "Time available for this stage: about 25 min" in revision.prompt
    assert all("Stage handoffs are enforced" in p.prompt for p in (plan.first, audit, revision))


@pytest.mark.parametrize("audience", ["expert", "nonexpert"])
@pytest.mark.parametrize("minutes,draft", [(20, 10), (45, 35), (60, 40)])
def test_collection_uses_solo_time_rules_and_keeps_its_method(
        tmp_cfg, reading_problem, audience, minutes, draft):
    harness = get_harness(tmp_cfg.harnesses_dir, f"deslopify-multi-{audience}")
    plan = plan_for(tmp_cfg, reading_problem, harness, max_minutes=minutes)
    method = plan.run_dir / "skill-references" / "collection-method.md"
    assert method.is_file()
    assert "## Paper k: ``Title of paper k''" in method.read_text()
    assert f"Drafting stops at {draft} min" in plan.first.prompt
    assert "## The non-negotiable collection shape" in plan.system_append

    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    revision = plan_next_pass(tmp_cfg, plan, role_name="balanced-reviser",
                              round_no=1, index=3)
    assert audit and revision
    assert [p.budget_minutes for p in (plan.first, audit, revision)] == [minutes, minutes + 5,
                                                                        minutes + 5]
    assert str(method) not in audit.system_append
    assert "# Collection audit" in audit.system_append
    assert "Check the main claims and Historical context for each paper" \
        in audit.system_append
    assert str(method) in revision.system_append
    assert "only Main results or Main findings, Historical context, and Bibliography" \
        in revision.system_append
    assert f"SKILL-pure-{audience}.md" in method.read_text()
    assert f"--audience {'non-expert' if audience == 'nonexpert' else 'expert'}" \
        in audit.system_append


@pytest.mark.parametrize("audience", ["expert", "nonexpert"])
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_selected_skills_are_saved_for_writer_and_reviser_but_not_auditor(
        tmp_cfg, reading_problem, audience, provider):
    harness = get_harness(tmp_cfg.harnesses_dir, f"deslopify-{audience}")
    spec = RunSpec(problem=reading_problem.slug, harness=harness.id, provider=provider,
                   account="codex" if provider == "codex" else "default",
                   model="gpt-5.4" if provider == "codex" else "claude-haiku-4-5",
                   effort="low")
    plan = plan_run(tmp_cfg, reading_problem, harness, spec)
    directory = plan.run_dir / "skill-references"
    assert str(directory) in plan.system_append
    assert "{skill_directory}" not in plan.system_append
    assert len(plan.passes) == 1  # No extra classifier invocation.
    originals = {}
    for name, text in harness.skill_references.items():
        saved = directory / name
        assert saved.is_file()
        originals[name] = saved.read_text(encoding="utf-8")
        if "{document}" in text:
            assert str(reading_problem.progress_dir / "math.tex") in originals[name]
        assert "{document}" not in originals[name]
        assert "### Presentation and clarity" not in plan.system_append

    chosen = f"SKILL-applied-{audience}.md"
    selection = reading_problem.progress_dir / "reading" / "paper-profile.json"
    selection.write_text(json.dumps({"papers": [{
        "source": str(reading_problem.progress_dir / "math.tex"), "profile": "applied",
        "skill": chosen, "reason": "The main contribution models an application.",
    }]}), encoding="utf-8")
    (harness.root / chosen).write_text("CHANGED AFTER START", encoding="utf-8")
    (harness.root / "SKILL-common.md").write_text("CHANGED SHARED RULES", encoding="utf-8")
    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    revision = plan_next_pass(tmp_cfg, plan, role_name="balanced-reviser",
                              round_no=1, index=3)
    assert str(directory) not in audit.system_append
    assert "paper-profile.json" not in audit.system_append
    assert "# Selected companion instructions" not in audit.system_append
    assert str(directory) in revision.system_append
    assert "paper-profile.json" in revision.system_append
    assert "CHANGED AFTER START" not in revision.system_append
    assert str(directory / "SKILL-common.md") in revision.system_append
    assert (directory / chosen).read_text(encoding="utf-8") == originals[chosen]
    assert (directory / "SKILL-common.md").read_text(encoding="utf-8") == originals["SKILL-common.md"]


@pytest.mark.parametrize("reference", ['"missing.md"', '"../outside.md"', '42'])
def test_bad_skill_references_are_rejected_when_loading(tmp_cfg, reference):
    from atelier.harnesses import HarnessError, load_harness

    root = tmp_cfg.harnesses_dir / "deslopify"
    manifest = root / "harness.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace(
        'skill_references = ["SKILL-common.md", "SKILL-pure-expert.md", "SKILL-applied-expert.md"]',
        f"skill_references = [{reference}]")
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(HarnessError):
        load_harness(root)


def test_deslopify_accepts_a_selected_markdown_source(tmp_cfg, reading_problem):
    source = reading_problem.root / "refs" / "paper.md"
    source.parent.mkdir(exist_ok=True)
    source.write_text("# Independent paper\n\nA Markdown theorem.\n", encoding="utf-8")
    harness = deslopify_harness(tmp_cfg)

    plan = plan_for(tmp_cfg, reading_problem, harness, task_kind="files",
                    task_files=["refs/paper.md"])

    assert '<source_document file="refs/paper.md" format="md">' in plan.prompt
    assert "A Markdown theorem." in plan.prompt
    common = plan.run_dir / "skill-references" / "SKILL-common.md"
    assert str(common) in plan.system_append
    assert "Read all supplied source text before drafting" in common.read_text()
    record = json.loads((plan.run_dir / "document.json").read_text())
    assert record["path"] == str(source)


def test_deslopify_accepts_a_selected_pdf_source(tmp_cfg, reading_problem,
                                                  monkeypatch):
    source = reading_problem.progress_dir / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\nfixture\n")
    monkeypatch.setattr("deslopify.source.read_source",
                        lambda path: "Extracted theorem from the PDF.\n")
    harness = deslopify_harness(tmp_cfg)

    plan = plan_for(tmp_cfg, reading_problem, harness, task_kind="files",
                    task_files=["progress/paper.pdf"])

    assert '<source_document file="progress/paper.pdf" format="pdf">' in plan.prompt
    assert "Extracted theorem from the PDF." in plan.prompt
    assert plan.extra["document"] == str(source)


def test_long_solo_source_uses_and_discloses_the_same_beginning_in_review(
        tmp_cfg, reading_problem):
    source = reading_problem.progress_dir / "source.md"
    text = "START OF PAPER\n" + "early result\n" * 18_000 + "END OF PAPER\n"
    source.write_text(text, encoding="utf-8")
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    spec = RunSpec(problem=reading_problem.slug, harness=harness.id,
                   account="codex", provider="codex", model="gpt-5.4")
    plan = plan_run(tmp_cfg, reading_problem, harness, spec,
                    isolate_reading=True, source_char_limit=100_000)

    assert "START OF PAPER" in plan.prompt and "END OF PAPER" not in plan.prompt
    assert "source text exceeded" in plan.prompt
    assert "based only on the beginning" in plan.system_append
    assert source.read_text() == text
    excerpt = (plan.run_dir / "source-excerpt.txt").read_text()
    assert excerpt.startswith("START OF PAPER") and "END OF PAPER" not in excerpt
    check = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    assert check is not None
    assert "START OF PAPER" in check.prompt and "END OF PAPER" not in check.prompt
    assert "Preserve this notice" in check.system_append


def test_collection_trims_only_the_long_members(tmp_cfg, reading_problem):
    from deslopify.source import beginning_within

    first = "## Paper 1 of 3: Long A\n\nFile: `refs/a.pdf`\n\n" + "A result.\n" * 12_000
    second = "## Paper 2 of 3: Short B\n\nFile: `refs/b.pdf`\n\nAll of B.\n"
    third = "## Paper 3 of 3: Long C\n\nFile: `refs/c.pdf`\n\n" + "C result.\n" * 12_000
    source = "# Three papers\n\n" + first + second + third
    excerpt, omitted = beginning_within(source, 35_000, collection=True)
    assert omitted == ["Long A", "Long C"]
    assert "All of B." in excerpt
    assert excerpt.count("rest of this paper was omitted") == 2

    (reading_problem.progress_dir / "source.md").write_text(source)
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-multi-expert")
    spec = RunSpec(problem=reading_problem.slug, harness=harness.id,
                   account="codex", provider="codex", model="gpt-5.4")
    plan = plan_run(tmp_cfg, reading_problem, harness, spec,
                    isolate_reading=True, source_char_limit=90_000)
    assert "Only the beginning was supplied for: Long A, Long C" in plan.system_append
    assert "All of B." in plan.prompt
    assert "rest of this paper was omitted" in plan.prompt


@pytest.mark.parametrize("variant", ["expert", "nonexpert", "multi-expert", "multi-nonexpert"])
def test_every_reader_audience_and_collection_mode_limits_its_source(
        tmp_cfg, reading_problem, variant):
    source = reading_problem.progress_dir / "source.md"
    if variant.startswith("multi-"):
        source.write_text("# Collection\n\n## Paper 1 of 2: A\n\n" + "A.\n" * 75_000
                          + "## Paper 2 of 2: B\n\n" + "B.\n" * 75_000)
    else:
        source.write_text("Beginning.\n" + "Result.\n" * 50_000)
    harness = get_harness(tmp_cfg.harnesses_dir, f"deslopify-{variant}")
    spec = RunSpec(problem=reading_problem.slug, harness=harness.id,
                   account="codex", provider="codex", model="gpt-5.4")
    plan = plan_run(tmp_cfg, reading_problem, harness, spec,
                    isolate_reading=True, source_char_limit=100_000)
    assert "source-excerpt.txt" in plan.extra["source_excerpt"]
    assert "based only on the beginning" in plan.system_append
    assert len(plan.prompt) + len(plan.system_append) <= 100_000


def test_launched_reading_run_can_use_an_isolated_problem_snapshot(
        tmp_cfg, reading_problem):
    from pathlib import Path

    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    spec = RunSpec(problem=reading_problem.slug, harness=harness.id,
                   account="codex", provider="codex", model="gpt-5.4",
                   effort="low")

    plan = plan_run(
        tmp_cfg, reading_problem, harness, spec, isolate_reading=True)

    assert plan.extra["isolated_reading"] is True
    assert plan.cwd == plan.run_dir / "workspace"
    assert plan.cwd != reading_problem.root
    assert Path(plan.extra["document"]).is_relative_to(plan.cwd)
    assert "an isolated copy of the current problem" in plan.prompt
    settings = json.loads((plan.run_dir / "settings.json").read_text())
    assert settings["permissions"]["additionalDirectories"] == []
    assert "--add-dir" not in plan.argv
    assert str(reading_problem.root) not in json.dumps(plan.redacted_env())

    copied = plan.cwd / "progress" / "math.tex"
    copied.write_text("changed only in the private reading run", encoding="utf-8")
    assert reading_problem.progress_dir.joinpath("math.tex").read_text() != copied.read_text()


def test_isolated_reading_outputs_are_archived_under_their_run(
        tmp_cfg, reading_problem):
    from atelier.runner import RunHandle

    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    spec = RunSpec(problem=reading_problem.slug, harness=harness.id,
                   account="default", model="claude-haiku-4-5", effort="low")
    plan = plan_run(
        tmp_cfg, reading_problem, harness, spec, isolate_reading=True)
    reading = plan.cwd / "progress" / "reading"
    reading.mkdir(parents=True, exist_ok=True)
    (reading / "guide.md").write_text("# Private guide\n", encoding="utf-8")
    (reading / "companion.pdf").write_bytes(b"%PDF-private")
    (plan.cwd / "progress" / "_session_notes.md").write_text(
        "Private notes.\n", encoding="utf-8")

    handle = type("IsolatedHandle", (), {
        "plan": plan,
        "produced_output": False,
        "_answer_text": lambda self: "",
    })()
    RunHandle._finalize_isolated_reading(handle)

    assert (plan.run_dir / "reading" / "guide.md").read_text() == "# Private guide\n"
    assert (plan.run_dir / "reading" / "companion.pdf").read_bytes() \
        == b"%PDF-private"
    assert (plan.run_dir / "notes.md").read_text() == "Private notes.\n"
    assert handle.produced_output is True


def test_the_guide_writer_stop_hook_requires_the_guide(tmp_cfg, reading_problem):
    from pathlib import Path
    import subprocess
    import sys

    reading = reading_problem.progress_dir / "reading"
    (reading / "guide.md").unlink()
    (reading_problem.progress_dir / "_session_notes.md").write_text(
        "The guide still needs to be written.\n", encoding="utf-8")
    run_dir = tmp_cfg.runs_dir / "hook-summary"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"

    result = subprocess.run(
        [sys.executable, str(hook), "--problem", str(reading_problem.root),
         "--run", str(run_dir), "--cwd", str(reading_problem.root),
         "--role", "draft", "--expect", "progress/reading/guide.md",
         "--validate", "guide"],
        input="{}", capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 2
    assert "progress/reading/guide.md" in result.stderr


def test_the_guide_writer_does_not_owe_summary_files(tmp_cfg, reading_problem):
    from pathlib import Path
    import subprocess
    import sys

    reading = reading_problem.progress_dir / "reading"
    (reading / "summary.md").unlink()
    (reading / "summary.pdf").unlink()
    (reading_problem.progress_dir / "_session_notes.md").write_text(
        "The guide is complete.\n", encoding="utf-8")
    run_dir = tmp_cfg.runs_dir / "hook-summary-pdf"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"

    result = subprocess.run(
        [sys.executable, str(hook), "--problem", str(reading_problem.root),
         "--run", str(run_dir), "--cwd", str(reading_problem.root),
         "--role", "draft", "--expect", "progress/reading/guide.md",
         "--validate", "guide"],
        input="{}", capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0


def test_the_reading_stop_hook_requires_the_pdf_map(tmp_cfg, reading_problem):
    from pathlib import Path
    import subprocess
    import sys

    reading = reading_problem.progress_dir / "reading"
    (reading / "map.pdf").unlink()
    (reading_problem.progress_dir / "_session_notes.md").write_text(
        "The map still needs to be rendered.\n", encoding="utf-8")
    run_dir = tmp_cfg.runs_dir / "hook-map-pdf"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"

    result = subprocess.run(
        [sys.executable, str(hook), "--problem", str(reading_problem.root),
         "--run", str(run_dir), "--cwd", str(reading_problem.root)],
        input="{}", capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 2
    assert "progress/reading/map.pdf" in result.stderr


# --- the shape of a looped run ----------------------------------------------

def test_the_sequence_is_guide_graph_two_stage_verification_and_revision(tmp_cfg):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    assert [lab for _, lab, _ in pass_sequence(harness)] == [
        "draft", "independent graph builder", "verifier — blind clarity pass",
        "verifier — source-grounded pass", "reviser"]


def test_pipeline_passes_are_independent_and_blind_phase_has_no_source(tmp_cfg,
                                                                        reading_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    plan = plan_for(tmp_cfg, reading_problem, harness)

    graph = plan_next_pass(tmp_cfg, plan, role_name="graph-builder",
                           round_no=1, index=2)
    assert "<the_document" in graph.prompt and "<the_guide_as_it_stands" in graph.prompt
    assert graph.prompt.index("<the_guide_as_it_stands") < graph.prompt.index(
        "<the_document")
    assert "<the_graph_as_it_stands" not in graph.prompt
    assert graph.writes == [
        "progress/reading/graph.json", "progress/reading/map.mmd",
        "progress/reading/map.pdf", "progress/reading/companion.md",
        "progress/reading/companion.tex", "progress/reading/companion.pdf"]
    assert "6--10 nodes total" in graph.system_append
    assert "Twelve is the normal ceiling" in graph.system_append
    assert "one-page proof roadmap, not an exhaustive database" in graph.system_append
    assert "transitive reduction" in graph.system_append
    assert "solely to\nstudy how mathematicians design diagrams" in graph.system_append
    assert "Anthony W. Knapp, *Basic Real Analysis*" in graph.system_append
    assert "J. F. Adams" in graph.system_append
    assert "This corpus is a repertoire, not a checklist" in graph.system_append
    assert "no item an automatic claim to a node" in graph.system_append
    assert "--audience expert" in graph.system_append

    blind = plan_next_pass(tmp_cfg, plan, role_name="verifier-blind",
                           round_no=1, index=3)
    assert "<the_document" not in blind.prompt
    assert "<problem_statement" not in blind.prompt
    assert "<the_guide_as_it_stands" in blind.prompt
    assert "<the_graph_as_it_stands" in blind.prompt
    assert blind.writes == ["progress/reading/clarity-review-1.md"]
    assert "Do not use web search or outside literature" in blind.system_append

    (reading_problem.progress_dir / "reading" / "clarity-review-1.md").write_text(
        "C1: Why does H0 support Q1?\n", encoding="utf-8")
    verifier = plan_next_pass(tmp_cfg, plan, role_name="verifier",
                              round_no=1, index=4)
    assert "<the_document" in verifier.prompt
    assert "<blind_clarity_review" in verifier.prompt
    assert verifier.writes == ["progress/reading/review-1.json"]
    assert "authoritative online sources" in verifier.system_append
    assert "less-famous contributors" in verifier.system_append
    assert "**adversarially** as an independent literature" in verifier.system_append
    assert "not only\nin **Historical context**" in verifier.system_append
    assert "Build an independent citation inventory" in verifier.system_append
    assert "close variants" in verifier.system_append
    assert "input paper lower your standard" in verifier.system_append
    assert "File a `missing` finding" in verifier.system_append
    assert "Inventory\nevery inline and displayed formula" in verifier.system_append
    assert "Render and inspect every PDF page" in verifier.system_append
    assert "Audit notation at the point where the reader first needs it" in verifier.system_append
    assert "an accurate descriptive definition is sufficient only when" in verifier.system_append
    assert "a precise source locator" in verifier.system_append
    assert "Inspect every technical noun phrase" in verifier.system_append
    assert "physical quantity or mathematical object" in verifier.system_append
    assert "grammatically clear yet scientifically underspecified" in verifier.system_append
    assert "every page-region crop was made from a 400-DPI page rendering" \
        in verifier.system_append
    assert "short, natural caption" in verifier.system_append
    assert "`[Abc00, Figure 1]`" in verifier.system_append


def test_balanced_auditor_combines_the_two_checks_without_assuming_a_graph(
        tmp_cfg, reading_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    for name in ("graph.json", "map.mmd", "map.pdf"):
        (reading_problem.progress_dir / "reading" / name).unlink(missing_ok=True)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    auditor = plan_next_pass(tmp_cfg, plan, role_name="auditor",
                             round_no=1, index=2)

    assert "<the_document" in auditor.prompt
    assert "<the_guide_as_it_stands" in auditor.prompt
    assert "<the_graph_as_it_stands" not in auditor.prompt
    assert "<skipped_work" in auditor.prompt
    assert auditor.writes == [
        "progress/reading/review-1.json",
        "progress/reading/companion.md",
        "progress/reading/companion.tex",
        "progress/reading/companion.pdf",
    ]
    assert "Check faithfulness to the source first" in auditor.system_append
    assert "Say what is unclear to a reader" in auditor.system_append
    assert "Check reasonable historical completeness" in auditor.system_append
    assert "do not need to read\nor apply the writer's skill files" in auditor.system_append
    assert "Your suggestions go in the review file for the reviser" \
        in auditor.system_append
    assert "selected skill's" not in auditor.prompt + auditor.system_append
    assert "# Selected companion instructions" not in auditor.system_append
    assert "deslopify} review" not in auditor.system_append


def test_guide_first_pipeline_advances_through_all_five_sessions(tmp_cfg,
                                                                  reading_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify")
    run_dir = tmp_cfg.runs_dir / "pipeline"
    run_dir.mkdir(parents=True)
    document = reading_problem.progress_dir / "math.tex"
    (run_dir / "document.orig").write_bytes(document.read_bytes())
    state = loopmod.start(harness, reading_problem.root, run_dir, document)

    draft = loopmod.assess(state, 1, "draft", 0, None, ok=True)
    assert draft.stop == "" and state.pending() == ("graph-builder", 1)
    assert not (state.reading / "summary.md").exists()
    assert not (state.reading / "summary.pdf").exists()

    guide = state.reading / "guide.md"
    (state.reading / "graph.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    (state.reading / "map.mmd").write_text("graph TD\n  H0 --> Q1\n", encoding="utf-8")
    (state.reading / "map.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (state.reading / "companion.md").write_text(
        guide.read_text().strip() + "\n", encoding="utf-8")
    (state.reading / "companion.tex").write_text(
        "\\documentclass{article}\n\\title{Reader's guide to test --- expert edition}\n"
        "\\begin{document}test\\end{document}\n", encoding="utf-8")
    (state.reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    graph = loopmod.assess(state, 2, "graph-builder", 1,
                           harness.roles["graph-builder"], ok=True)
    assert graph.stop == "" and state.pending() == ("verifier-blind", 1)

    (state.reading / "clarity-review-1.md").write_text(
        "C1 — graph:Q1 — Why is the asserted search needed?\n", encoding="utf-8")
    blind = loopmod.assess(state, 3, "verifier-blind", 1,
                           harness.roles["verifier-blind"], ok=True)
    assert blind.stop == "" and state.pending() == ("verifier", 1)

    write_review(state, 1, [finding()])
    verified = loopmod.assess(state, 4, "verifier", 1,
                              harness.roles["verifier"], ok=True)
    assert verified.carried == 1 and state.pending() == ("reviser", 1)

    guide.write_text(guide.read_text() + "\nThe asserted search remains unshown.\n",
                     encoding="utf-8")
    (state.reading / "companion.md").write_text(
        guide.read_text().strip() + "\n", encoding="utf-8")
    (state.reading / "companion.tex").write_text(
        "\\documentclass{article}\n\\title{Reader's guide to test --- expert edition}\n"
        "\\begin{document}test\\end{document}\n", encoding="utf-8")
    (state.reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [{
            "id": "F1", "disposition": "fixed",
            "where": "guide:Main ideas", "note": "the omission is now explicit",
        }],
    }), encoding="utf-8")
    revised = loopmod.assess(state, 5, "reviser", 1,
                             harness.roles["reviser"], ok=True)
    assert "pipeline completed" in revised.stop
    assert state.pending() is None


def test_balanced_pipeline_audits_and_revises_without_requiring_a_graph(
        tmp_cfg, reading_problem):
    from deslopify.summary_pdf import dated_guide

    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    run_dir = tmp_cfg.runs_dir / "balanced-pipeline"
    run_dir.mkdir(parents=True)
    document = reading_problem.progress_dir / "math.tex"
    (run_dir / "document.orig").write_bytes(document.read_bytes())
    state = loopmod.start(harness, reading_problem.root, run_dir, document)

    drafted = loopmod.assess(state, 1, "draft", 0, None, ok=True)
    assert drafted.stop == "" and state.pending() == ("auditor", 1)
    assert not (state.reading / "graph.json").exists()

    guide = state.reading / "guide.md"
    (state.reading / "companion.md").write_text(
        dated_guide(guide) + "\n", encoding="utf-8")
    (state.reading / "companion.tex").write_text(
        "\\documentclass{article}\n\\begin{document}test\\end{document}\n",
        encoding="utf-8")
    (state.reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    write_review(state, 1, [finding(
        kind="unclear", where="guide:Main ideas", quote="",
        problem="Which hypothesis is used in the closing step?")])
    audited = loopmod.assess(state, 2, "auditor", 1,
                             harness.roles["auditor"], ok=True)
    assert audited.carried == 1 and state.pending() == ("balanced-reviser", 1)

    guide.write_text(guide.read_text() + "\nThe closing step uses the parity hypothesis.\n",
                     encoding="utf-8")
    (state.reading / "companion.md").write_text(
        dated_guide(guide) + "\n", encoding="utf-8")
    (state.reading / "companion.tex").write_text(
        "\\documentclass{article}\n\\begin{document}test\\end{document}\n",
        encoding="utf-8")
    (state.reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [{
            "id": "F1", "disposition": "fixed", "where": "guide:Main ideas",
            "note": "the closing dependency is now stated explicitly",
        }],
    }), encoding="utf-8")
    revised = loopmod.assess(state, 3, "balanced-reviser", 1,
                             harness.roles["balanced-reviser"], ok=True)
    assert "pipeline completed" in revised.stop
    assert "Generated on " in (state.reading / "companion.md").read_text()
    assert state.pending() is None


def test_bibliography_coverage_finding_requires_balanced_revision(
        tmp_cfg, reading_problem):
    from deslopify.summary_pdf import dated_guide

    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    run_dir = tmp_cfg.runs_dir / "bibliography-coverage"
    run_dir.mkdir(parents=True)
    document = reading_problem.progress_dir / "math.tex"
    (run_dir / "document.orig").write_bytes(document.read_bytes())
    state = loopmod.start(harness, reading_problem.root, run_dir, document)
    loopmod.assess(state, 1, "draft", 0, None, ok=True)
    guide = state.reading / "guide.md"
    (state.reading / "companion.md").write_text(dated_guide(guide) + "\n")
    (state.reading / "companion.tex").write_text(
        "\\documentclass{article}\\begin{document}test\\end{document}\n")
    (state.reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    write_review(state, 1, [finding(
        kind="missing", severity="worth-fixing", where="guide:Historical context",
        quote="", problem="The historical discussion omits a foundational reference.",
        fix="Add the identified work, label any provisional connection, and cite it.",
    )], summary="Coverage check: a foundational strand needs repair.")

    audited = loopmod.assess(state, 2, "auditor", 1, harness.roles["auditor"], ok=True)
    assert audited.stop == ""
    assert audited.carried == 1
    assert state.pending() == ("balanced-reviser", 1)


def test_collection_review_and_revision_preserve_the_three_part_paper_shape(
        tmp_cfg, reading_problem):
    from deslopify.summary_pdf import dated_guide

    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-multi-expert")
    run_dir = tmp_cfg.runs_dir / "collection-review"
    run_dir.mkdir(parents=True)
    document = reading_problem.progress_dir / "math.tex"
    (run_dir / "document.orig").write_bytes(document.read_bytes())
    state = loopmod.start(harness, reading_problem.root, run_dir, document)
    guide = state.reading / "guide.md"
    guide.write_text(
        "# Two papers\n\n## Contents\n\n1. Paper 1: A\n2. Paper 2: B\n\n"
        "## Paper 1: A\n\n### Main results\nResult A.\n\n"
        "### Historical context\nHistory A.\n\n### Bibliography\n- [A1] Work A.\n\n"
        "## Paper 2: B\n\n### Main results\nResult B.\n\n"
        "### Historical context\nHistory B.\n\n### Bibliography\n- [B1] Work B.\n",
        encoding="utf-8")
    drafted = loopmod.assess(state, 1, "draft", 0, None, ok=True)
    assert drafted.stop == "" and state.pending() == ("auditor", 1)

    def exported():
        (state.reading / "companion.md").write_text(dated_guide(guide) + "\n")
        (state.reading / "companion.tex").write_text("\\documentclass{article}\n")
        (state.reading / "companion.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    exported()
    write_review(state, 1, [finding(
        id="F1", kind="unclear", severity="worth-fixing",
        where="guide:Paper 2: B", quote="",
        problem="What is the hypothesis of Result B?",
        fix="State the source's hypothesis in Paper 2's Main results.")])
    audited = loopmod.assess(state, 2, "auditor", 1, harness.roles["auditor"], ok=True)
    assert audited.carried == 1 and state.pending() == ("balanced-reviser", 1)
    guide.write_text(guide.read_text().replace("Result B.", "Under H, Result B."))
    exported()
    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [{"id": "F1", "disposition": "fixed",
                                      "where": "guide:Paper 2: B",
                                      "note": "Added the missing hypothesis."}]}))
    revised = loopmod.assess(state, 3, "balanced-reviser", 1,
                             harness.roles["balanced-reviser"], ok=True)
    assert revised.problems == [] and revised.completed and state.completed
    assert "Generated on " in (state.reading / "companion.md").read_text()
    assert state.pending() is None
    assert [line for line in guide.read_text().splitlines() if line.startswith("### ")] == \
        ["### Main results", "### Historical context", "### Bibliography"] * 2


def test_balanced_auditor_pdf_failure_continues_to_reviser(
        tmp_cfg, reading_problem):
    from deslopify.summary_pdf import dated_guide

    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    run_dir = tmp_cfg.runs_dir / "balanced-pdf-repair"
    run_dir.mkdir(parents=True)
    document = reading_problem.progress_dir / "math.tex"
    (run_dir / "document.orig").write_bytes(document.read_bytes())
    state = loopmod.start(harness, reading_problem.root, run_dir, document)

    drafted = loopmod.assess(state, 1, "draft", 0, None, ok=True)
    assert drafted.stop == "" and state.pending() == ("auditor", 1)

    guide = state.reading / "guide.md"
    (state.reading / "companion.md").write_text(
        dated_guide(guide) + "\n", encoding="utf-8")
    (state.reading / "companion.tex").write_text(
        "\\documentclass{article}\n\\begin{document}test\\end{document}\n",
        encoding="utf-8")
    write_review(state, 1, [finding(
        kind="unclear", severity="blocking", where="guide:Main ideas", quote="",
        problem="Why did the companion PDF fail to compile?",
        fix="Repair the compilation failure and regenerate the PDF.")])

    audited = loopmod.assess(state, 2, "auditor", 1,
                             harness.roles["auditor"], ok=True)

    assert audited.stop == ""
    assert audited.carried == 1
    assert state.pending() == ("balanced-reviser", 1)
    assert any("continuing to the reviser" in problem
               for problem in audited.repair_tasks)


def test_the_budget_is_divided_and_nothing_is_left_unspendable(tmp_cfg):
    for rounds in (1, 2, 3):
        harness = replace(deslopify_harness(tmp_cfg, rounds=rounds), kind="solve")
        budgets = pass_budgets(harness, 12.0, 60.0)
        assert len(budgets) == len(pass_sequence(harness))
        assert sum(b[0] for b in budgets) == pytest.approx(12.0)
        assert sum(b[1] for b in budgets) == pytest.approx(60.0)
        # The draft does the most work by far and gets the largest share.
        assert budgets[0][0] == max(b[0] for b in budgets)


def test_an_unpriced_model_still_divides_the_wall_clock(tmp_cfg):
    harness = replace(deslopify_harness(tmp_cfg, rounds=1), kind="solve")
    budgets = pass_budgets(harness, None, 60.0)
    assert [b[0] for b in budgets] == [None, None, None]
    assert sum(b[1] for b in budgets) == pytest.approx(60.0)


def test_a_token_or_turn_ceiling_divides_too(tmp_cfg):
    """Left whole, the draft could spend all of it and starve exactly the passes
    the division exists to protect."""
    harness = replace(deslopify_harness(tmp_cfg, rounds=1), kind="solve")
    budgets = pass_budgets(harness, None, None, 90_000, 30)
    assert sum(b[2] for b in budgets) <= 90_000
    assert budgets[0][2] > budgets[1][2]          # the draft gets the most
    assert all(b[3] >= 1 for b in budgets)        # nobody gets zero turns


# --- compiling a critique pass ----------------------------------------------

def critic_pass(tmp_cfg, problem, **loop):
    harness = deslopify_harness(tmp_cfg, **loop)
    plan = plan_for(tmp_cfg, problem, harness)
    return plan, plan_next_pass(tmp_cfg, plan, role_name="critic",
                                round_no=1, index=2)


def test_the_critique_pass_is_given_the_document_and_the_companion(tmp_cfg,
                                                                   reading_problem):
    _, p = critic_pass(tmp_cfg, reading_problem)
    assert "Then $a + b = 2(m + n)$, which is even." in p.prompt   # the document
    assert "Parity survives addition" in p.prompt                  # the guide
    assert "H0" in p.prompt and "Q1" in p.prompt                   # the graph view
    # Both fences carry an attribute, so match the open tag rather than the whole.
    assert "<skipped_work" in p.prompt                             # the appeals
    assert "<mechanical_findings" in p.prompt                      # the coverage audit
    assert "line 5" in p.prompt                    # the appeal the document makes
    # The fixture adjudicates line 5 a gap but records no `!` node, so the
    # mechanical audit has already found it before a reader spends attention.
    assert "never reaches the map" in p.prompt


def test_the_critique_pass_does_not_inherit_the_drafting_method(tmp_cfg,
                                                                reading_problem):
    """Hand a reviewer the method for building a companion and it redrafts one."""
    plan, p = critic_pass(tmp_cfg, reading_problem)
    assert "Method: select the expert companion skill" \
        in plan.passes[0].system_append
    assert "Method: build the map, then answer from it" not in p.system_append
    assert "read the document and the companion against each other" in p.system_append


def test_each_pass_is_a_fresh_session(tmp_cfg, reading_problem):
    """A reviewer that can see the drafter's reasoning checks it for internal
    consistency instead of against the document."""
    plan, p = critic_pass(tmp_cfg, reading_problem)
    first = plan.passes[0].argv
    assert first[first.index("--session-id") + 1] != p.argv[p.argv.index("--session-id") + 1]
    assert "--resume" not in p.argv


def test_the_critique_pass_has_no_reading_stop_hook(tmp_cfg, reading_problem):
    plan, p = critic_pass(tmp_cfg, reading_problem)
    for reading_pass in plan.passes + [p]:
        settings = json.loads((reading_pass.dir / "settings.json").read_text())
        assert "Stop" not in settings["hooks"]
    assert "compile_tex.py" not in json.dumps(settings["hooks"].get("PostToolUse", []))


@pytest.mark.parametrize("harness_id", [
    "deslopify-expert", "deslopify-nonexpert",
    "deslopify-multi-expert", "deslopify-multi-nonexpert", "deslopify-search",
])
def test_all_reading_methods_omit_the_stop_hook(tmp_cfg, reading_problem, harness_id):
    harness = get_harness(tmp_cfg.harnesses_dir, harness_id)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    for reading_pass in plan.passes:
        settings = json.loads((reading_pass.dir / "settings.json").read_text())
        assert "Stop" not in settings["hooks"]


def test_review_stop_hook_does_not_grade_the_review(tmp_cfg, reading_problem):
    import subprocess
    import sys
    from pathlib import Path

    reading = reading_problem.progress_dir / "reading"
    (reading / "review-1.json").write_text(json.dumps({
        "round": 1, "summary": "The principal claim needs checking.",
        "findings": [{"id": "F1", "severity": "blocking", "kind": "unfaithful",
                      "where": "somewhere in the middle", "quote": "",
                      "problem": "The theorem is overstated.",
                      "fix": "Compare it with the paper."}],
    }), encoding="utf-8")
    (reading_problem.progress_dir / "_session_notes.md").write_text(
        "Checked the principal claim.\n", encoding="utf-8")
    run_dir = tmp_cfg.runs_dir / "review-without-checker"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"
    result = subprocess.run(
        [sys.executable, str(hook), "--problem", str(reading_problem.root),
         "--run", str(run_dir), "--cwd", str(reading_problem.root),
         "--role", "critic", "--expect", "progress/reading/review-1.json",
         "--validate", "review-output"],
        input="{}", capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_a_later_pass_gets_its_own_directory(tmp_cfg, reading_problem):
    plan, p = critic_pass(tmp_cfg, reading_problem)
    assert p.dir == plan.run_dir / "pass-2"
    assert (p.dir / "prompt.md").is_file()
    # ...and does not overwrite the draft's record of itself.
    assert (plan.run_dir / "prompt.md").read_text() != p.prompt


def test_the_lane_is_stated_in_the_prompt(tmp_cfg, reading_problem):
    _, p = critic_pass(tmp_cfg, reading_problem)
    assert p.writes == ["progress/reading/review-1.json"]
    assert "progress/reading/review-1.json" in p.prompt
    assert "restored from a snapshot" in p.prompt


def test_a_round_may_not_edit_an_earlier_round_s_record(tmp_cfg, reading_problem):
    """The lane pattern is a template resolved per round. Left as
    `review-*.json`, the second critique could rewrite the first round's review —
    the very record it is asked to check the earlier answers against."""
    harness = deslopify_harness(tmp_cfg, rounds=2)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    second = plan_next_pass(tmp_cfg, plan, role_name="critic", round_no=2, index=4)
    assert second.writes == ["progress/reading/review-2.json"]

    (reading_problem.progress_dir / "reading" / "review-1.json").write_text(
        json.dumps(REVIEW), encoding="utf-8")
    reviser = plan_next_pass(tmp_cfg, plan, role_name="reviser", round_no=1, index=3)
    assert "progress/reading/dispositions-1.json" in reviser.writes
    assert not any(w.endswith("-*.json") for w in reviser.writes)


def test_a_second_round_critic_is_told_to_check_the_answers(tmp_cfg, reading_problem):
    reading = reading_problem.progress_dir / "reading"
    (reading / "review-log.md").write_text(
        "# What review found\n\n## Round 1\n\n### F1 \u00b7 blocking \u00b7 missing\n\n"
        "**fixed** \u2014 recorded as a gap  (`graph:Q1`)\n", encoding="utf-8")
    harness = deslopify_harness(tmp_cfg, rounds=2)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    p = plan_next_pass(tmp_cfg, plan, role_name="critic", round_no=2, index=4)
    assert "recorded as *fixed* should be visible at the location it names" in p.prompt
    # ...and it is actually given the earlier round to check against.
    assert "<earlier_rounds>" in p.prompt
    assert "**fixed** \u2014 recorded as a gap" in p.prompt


# --- compiling a revision pass ----------------------------------------------

def test_the_revision_pass_is_given_the_review(tmp_cfg, reading_problem):
    reading = reading_problem.progress_dir / "reading"
    (reading / "review-1.json").write_text(json.dumps(REVIEW), encoding="utf-8")
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    p = plan_next_pass(tmp_cfg, plan, role_name="reviser", round_no=1, index=3)
    assert "the search is never recorded as a gap anywhere" in p.prompt
    assert "dispositions-1.json" in p.prompt
    settings = json.loads((p.dir / "settings.json").read_text())
    assert "Stop" not in settings["hooks"]


def test_the_reviser_may_not_edit_the_review_it_is_answering(tmp_cfg, reading_problem):
    """The critique is a record. A reviser that could edit it could answer a
    finding by deleting it."""
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    p = plan_next_pass(tmp_cfg, plan, role_name="reviser", round_no=1, index=3)
    assert not any("review-" in w for w in p.writes)
    assert "progress/reading/graph.json" in p.writes


# --- the controller between passes ------------------------------------------

from atelier import loop as loopmod                                  # noqa: E402


@pytest.fixture
def state(tmp_cfg, reading_problem):
    """A loop mid-flight: the draft has run, the document is recorded."""
    harness = deslopify_harness(tmp_cfg)
    run_dir = tmp_cfg.runs_dir / "R1"
    run_dir.mkdir(parents=True, exist_ok=True)
    document = reading_problem.progress_dir / "math.tex"
    (run_dir / "document.orig").write_bytes(document.read_bytes())
    return loopmod.start(harness, reading_problem.root, run_dir, document)


def write_review(state, round_no, findings, summary="a reading"):
    path = loopmod.review_path(state, round_no)
    path.write_text(json.dumps({"round": round_no, "summary": summary,
                                "findings": findings}), encoding="utf-8")


def finding(**kw):
    return dict(REVIEW["findings"][0]) | kw


def run_draft(state):
    return loopmod.assess(state, 1, "draft", 0, None, ok=True)


def critic_role(state):
    return state.harness.roles["critic"]


# --- after the draft --------------------------------------------------------

def test_the_draft_is_snapshotted_and_handed_to_a_critic(state):
    outcome = run_draft(state)
    assert outcome.stop == ""
    assert state.pending() == ("critic", 1)
    assert (state.snapshot_dir(0) / "summary.md").is_file()
    assert (state.snapshot_dir(0) / "summary.pdf").is_file()
    assert (state.snapshot_dir(0) / "graph.json").is_file()
    assert (state.snapshot_dir(0) / "guide.md").is_file()
    assert (state.snapshot_dir(0) / "map.pdf").is_file()


def test_a_draft_with_no_summary_ends_the_loop(state):
    (state.reading / "summary.md").unlink()

    outcome = run_draft(state)

    assert "no summary" in outcome.stop
    assert state.pending() is None


def test_a_draft_with_no_graph_ends_the_loop(state):
    (state.reading / "graph.json").unlink()
    outcome = run_draft(state)
    assert "nothing for a second reader" in outcome.stop
    assert state.pending() is None


def test_a_pass_that_did_not_complete_ends_the_loop(state):
    outcome = loopmod.assess(state, 1, "draft", 0, None, ok=False)
    assert "did not complete" in outcome.stop
    assert state.pending() is None


# --- the critic's lane ------------------------------------------------------

def test_a_critic_that_edits_the_guide_has_the_edit_undone(state):
    run_draft(state)
    original = (state.reading / "guide.md").read_text()
    (state.reading / "guide.md").write_text(original + "\nAnd it is all quite clear.\n",
                                            encoding="utf-8")
    write_review(state, 1, [finding()])
    outcome = loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    assert (state.reading / "guide.md").read_text() == original
    assert any("guide.md" in n and "restored" in n for n in outcome.notes)


def test_a_file_a_critic_invents_is_removed(state):
    run_draft(state)
    (state.reading / "suggested-guide.md").write_text("here is how I would say it",
                                                      encoding="utf-8")
    write_review(state, 1, [finding()])
    outcome = loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    assert not (state.reading / "suggested-guide.md").exists()
    assert any("suggested-guide.md" in n for n in outcome.notes)


def test_the_review_itself_survives_the_lane_check(state):
    run_draft(state)
    write_review(state, 1, [finding()])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)
    assert loopmod.review_path(state, 1).is_file()


def test_a_modified_document_is_restored_byte_for_byte(state):
    run_draft(state)
    document = state.document
    document.write_text(DOCUMENT.replace("even integers", "integers"), encoding="utf-8")
    write_review(state, 1, [finding()])
    outcome = loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    assert document.read_text() == DOCUMENT
    assert any("restored" in n and "math.tex" in n for n in outcome.notes)


# --- when the loop stops ----------------------------------------------------

def test_a_critique_that_raises_nothing_ends_the_loop(state):
    run_draft(state)
    write_review(state, 1, [], summary="this companion holds up")
    outcome = loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    assert "raised nothing" in outcome.stop
    assert state.pending() is None


def test_findings_below_the_threshold_do_not_buy_another_round(state):
    run_draft(state)
    write_review(state, 1, [finding(severity="minor")])
    outcome = loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    assert outcome.findings == 1 and outcome.carried == 0
    assert "raised nothing at worth-fixing or worse" in outcome.stop
    assert "1 minor finding" in outcome.stop


def test_a_blocking_finding_buys_a_revision(state):
    run_draft(state)
    write_review(state, 1, [finding()])
    outcome = loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    assert outcome.blocking == 1 and outcome.carried == 1
    assert outcome.stop == ""
    assert state.pending() == ("reviser", 1)


def test_a_parseable_critique_is_not_graded_by_a_second_checker(state):
    """The reviewer owns judgment; the reviser can use even an imperfect finding."""
    run_draft(state)
    write_review(state, 1, [finding(where="somewhere in the middle")])
    outcome = loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    assert outcome.problems == []
    assert outcome.completed
    assert state.pending() == ("reviser", 1)          # the round still happens


# --- after a revision -------------------------------------------------------

def revise(state, *, structural: bool, dispositions=True):
    run_draft(state)
    write_review(state, 1, [finding()])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    guide = state.reading / "guide.md"
    guide.write_text(guide.read_text() + "\nA sharper closing line.\n", encoding="utf-8")
    if structural:
        data = json.loads((state.reading / "graph.json").read_text())
        data["nodes"].append({"id": "!1", "label": "the search is never shown",
                              "quote": ""})
        (state.reading / "graph.json").write_text(json.dumps(data), encoding="utf-8")
    if dispositions:
        loopmod.dispositions_path(state, 1).write_text(json.dumps({
            "round": 1, "dispositions": [
                {"id": "F1", "disposition": "fixed", "where": "graph:!1" if structural
                 else "guide:Main ideas", "note": "recorded as a gap"}]}),
            encoding="utf-8")
    return loopmod.assess(state, 3, "reviser", 1, state.harness.roles["reviser"], ok=True)


def test_a_fix_in_the_guide_alone_is_ordinary_and_not_an_accusation(state):
    """A finding about what the reader is told is fixed in the guide and nowhere
    else. Calling that "polishing" cries wolf on the common case, which a live
    run demonstrated on the first try."""
    outcome = revise(state, structural=False)
    assert outcome.problems == []
    assert any("went into the guide" in n for n in outcome.notes)
    assert outcome.diff["graph"]["structural"] == 0


def test_a_fix_claimed_in_the_graph_that_the_graph_does_not_show_is_a_problem(state):
    """The one way a revision can look like work without being any."""
    run_draft(state)
    write_review(state, 1, [finding()])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    guide = state.reading / "guide.md"
    guide.write_text(guide.read_text() + "\nThe search is not shown.\n", encoding="utf-8")
    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [{"id": "F1", "disposition": "fixed",
                                      "where": "graph:Q1",
                                      "note": "recorded as a gap"}]}),
        encoding="utf-8")
    outcome = loopmod.assess(state, 3, "reviser", 1, state.harness.roles["reviser"],
                             ok=True)
    assert any("recorded as fixed at a location in the graph" in p
               for p in outcome.problems)


def test_a_round_that_moved_the_analysis_is_not_flagged(state):
    outcome = revise(state, structural=True)
    assert outcome.problems == []
    assert not any("went into the guide" in n for n in outcome.notes)
    assert outcome.diff["graph"]["nodes_added"] == ["!1"]
    assert outcome.diff["graph"]["gaps_after"] == 1


def test_missing_dispositions_are_a_recorded_contract_failure(state):
    outcome = revise(state, structural=True, dispositions=False)
    assert any("never written" in p for p in outcome.problems)


def test_the_last_round_ends_the_loop(tmp_cfg, reading_problem):
    harness = deslopify_harness(tmp_cfg, rounds=1)
    run_dir = tmp_cfg.runs_dir / "R2"
    run_dir.mkdir(parents=True)
    document = reading_problem.progress_dir / "math.tex"
    (run_dir / "document.orig").write_bytes(document.read_bytes())
    st = loopmod.start(harness, reading_problem.root, run_dir, document)

    outcome = revise(st, structural=True)
    assert "1 round(s) of review completed" in outcome.stop
    assert st.pending() is None


def test_the_run_record_is_written_after_every_pass(state):
    run_draft(state)
    record = json.loads((state.run_dir / "loop.json").read_text())
    assert record["rounds_configured"] == 2
    assert record["passes"][0]["role"] == "draft"


# --- notes and the log a human reads ----------------------------------------

def test_each_pass_keeps_its_own_notes_and_they_are_reassembled(state):
    notes = state.cwd / "progress" / "_session_notes.md"
    notes.write_text("The document was hard to map past section 4.\n", encoding="utf-8")
    run_draft(state)
    assert not notes.exists()               # stashed, so the next pass starts clean

    notes.write_text("Two claims could not be traced.\n", encoding="utf-8")
    write_review(state, 1, [finding()])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)

    loopmod.collect_notes(state, {1: "draft", 2: "critique 1"})
    text = notes.read_text()
    assert "### draft" in text and "hard to map past section 4" in text
    assert "### critique 1" in text and "Two claims could not be traced" in text


def test_the_review_log_records_what_was_refused_as_well_as_what_was_fixed(state):
    run_draft(state)
    write_review(state, 1, [finding(id="F1"),
                            finding(id="F2", severity="worth-fixing",
                                    problem="the notation table is incomplete")])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)
    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [
            {"id": "F1", "disposition": "rejected",
             "note": "the passage quoted is a definition, not a claim"}]}),
        encoding="utf-8")
    loopmod.assess(state, 3, "reviser", 1, state.harness.roles["reviser"], ok=True)

    log = loopmod.write_review_log(state)
    text = log.read_text()
    assert "F1" in text and "rejected" in text
    assert "the passage quoted is a definition" in text
    assert "A direct search shows there are no further cases." in text   # the quote
    # A finding nobody answered must not vanish from the record.
    assert "F2" in text and "not answered" in text


def test_the_critique_survives_into_the_pass_that_must_answer_it(state):
    """A regression with teeth. The reviser's lane was once checked against the
    snapshot taken at the end of the previous *round*, which predates the
    critique — so the review file counted as something the reviser had invented
    and was deleted, at exactly the moment it was to be acted on."""
    run_draft(state)
    write_review(state, 1, [finding()])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True)
    assert loopmod.review_path(state, 1).is_file()

    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [{"id": "F1", "disposition": "fixed",
                                      "where": "guide:Main ideas",
                                      "note": "named in the guide"}]}),
        encoding="utf-8")
    loopmod.assess(state, 3, "reviser", 1, state.harness.roles["reviser"], ok=True)

    assert loopmod.review_path(state, 1).is_file()
    assert json.loads(loopmod.review_path(state, 1).read_text())["findings"]


# --- usage measured across several processes --------------------------------

def usage_handle(tmp_cfg):
    """A stub session just complete enough to run the real _apply_usage."""
    from collections import Counter

    from atelier.runner import RunHandle, Totals

    handle = type("H", (), {
        "run_id": "R", "totals": Totals(), "_pass_base": Totals(),
        "_counted_messages": set(),
        "cfg": tmp_cfg,
        "db": type("D", (), {"add_usage": lambda self, **kw: None})(),
        "plan": type("P", (), {"model": "claude-haiku-4-5", "account": None})(),
        "current": type("C", (), {"index": 1, "label": "draft", "model": "",
                                  "account": None})(),
    })()
    handle._apply_usage = RunHandle._apply_usage.__get__(handle)
    handle._pass_account = RunHandle._pass_account.__get__(handle)

    def start_pass(model=""):
        handle._pass_base = replace(handle.totals,
                                    tool_breakdown=Counter(handle.totals.tool_breakdown))
        handle.current = type("C", (), {"index": 2, "label": "pass", "model": model,
                                        "account": None})()
    handle._start_pass = start_pass
    return handle


def test_each_pass_is_priced_at_its_own_model(tmp_cfg):
    """A run whose check runs on another model (RunSpec.role_agents) is billed
    pass by pass: the draft at its model's rate, the check at the other's —
    not the whole run at whichever model happened to speak last."""
    h = usage_handle(tmp_cfg)
    h.current.model = "claude-haiku-4-5"
    h._apply_usage({"final": True, "tok_in": 1_000_000, "tok_out": 0})
    assert h.totals.cost_computed == pytest.approx(1.00)        # Haiku: $1 / M in

    h._start_pass("claude-opus-5")
    h._apply_usage({"final": True, "tok_in": 1_000_000, "tok_out": 0, "model": ""})
    assert h.totals.cost_computed == pytest.approx(1.00 + 5.00)  # Opus: $5 / M in
    assert h.totals.priced is True

    # A pass on a model with no price (a ChatGPT subscription) adds nothing it
    # cannot know, and the run is marked as not wholly priced.
    h._start_pass("gpt-5.6-terra")
    h._apply_usage({"final": True, "tok_in": 1_000_000, "tok_out": 0})
    assert h.totals.cost_computed == pytest.approx(6.00)
    assert h.totals.priced is False


def test_a_later_pass_does_not_erase_what_earlier_passes_measured(tmp_cfg):
    """The terminal result of each pass reports that pass's own totals. Taking
    the larger of it and the running sum — right for one process — would have a
    short second pass silently replace everything before it."""
    h = usage_handle(tmp_cfg)
    h._apply_usage({"final": True, "tok_in": 100_000, "tok_out": 40_000,
                    "num_turns": 30, "total_cost_usd": 3.00})
    assert h.totals.tok_out == 40_000

    h._start_pass()
    h._apply_usage({"final": True, "tok_in": 20_000, "tok_out": 5_000,
                    "num_turns": 4, "total_cost_usd": 0.50})

    assert h.totals.tok_in == 120_000
    assert h.totals.tok_out == 45_000
    assert h.totals.turns == 34
    assert h.totals.cost_reported == pytest.approx(3.50)


def test_a_streamed_pass_still_reconciles_against_its_own_contribution(tmp_cfg):
    h = usage_handle(tmp_cfg)
    h._apply_usage({"tok_in": 10_000, "tok_out": 3_000, "message_id": "m1"})
    h._apply_usage({"tok_in": 10_000, "tok_out": 3_000, "message_id": "m1"})   # repeat
    assert h.totals.tok_out == 3_000
    h._apply_usage({"final": True, "tok_in": 10_000, "tok_out": 3_500})
    assert h.totals.tok_out == 3_500          # the authoritative total wins

    h._start_pass()
    h._apply_usage({"tok_in": 1_000, "tok_out": 200, "message_id": "m2"})
    h._apply_usage({"final": True, "tok_in": 1_000, "tok_out": 150})
    assert h.totals.tok_out == 3_700          # the pass's own larger figure kept


# --- the per-pass budget ----------------------------------------------------

def pass_fraction_of(plan_budget, pass_budget, spent, base=0.0):
    from atelier.runner import RunHandle, Totals

    handle = type("H", (), {
        "current": type("C", (), {"budget_usd": pass_budget, "budget_minutes": None,
                                  "budget_tokens_out": None, "budget_turns": None})(),
        "totals": Totals(cost_computed=spent),
        "_pass_base": Totals(cost_computed=base),
        "_pass_started_at": 0.0, "elapsed": 0.0,
    })()
    return RunHandle.pass_fraction(handle)


def test_a_pass_is_measured_against_its_own_share(tmp_cfg):
    # $2.00 spent in this pass out of its $2.80 share: 71%, not 14% of the run.
    assert pass_fraction_of(14.0, 2.80, spent=7.0, base=5.0) == pytest.approx(0.714, abs=1e-3)


def test_a_single_pass_run_measures_exactly_as_it_always_did(tmp_cfg):
    assert pass_fraction_of(14.0, 14.0, spent=7.0) == pytest.approx(0.5)


# --- a pre-existing bug the first Codex run of the loop exposed --------------

def test_the_contract_check_is_measured_before_the_notes_are_consumed(tmp_path):
    """Writing the lab book deletes progress/_session_notes.md. The check that
    fires for providers which cannot be asked to fix anything ran afterwards, so
    every Codex session that wrote its notes was recorded as having written
    none — a warning that is exactly backwards."""
    import inspect

    from atelier.runner import RunHandle

    body = inspect.getsource(RunHandle._finalize_inner)
    measured = body.index("delivered = {")
    consumed = body.index("self._write_labbook(")
    reported = body.index("name not in delivered")
    assert measured < consumed < reported
    # A loop moves each pass's notes aside as it ends (loop.stash_notes): those
    # count as delivered too, or every Codex pass of a loop was reported as
    # having written none (seen on a reading of 2026-10-08).
    stashed = body.index('glob("notes-*.md")')
    assert measured < stashed < reported


def test_a_one_shot_is_a_single_blind_attempt_and_never_loops(tmp_cfg, reading_problem):
    """Leaving the loop on would also have handed the one pass that runs a
    fraction of the budget and reserved the rest for passes that never happen."""
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness, mode="oneshot")
    assert plan.harness.loop.enabled is False
    assert len(plan.passes) == 1
    assert plan.passes[0].budget_usd == plan.budget_usd


def test_the_reviser_is_told_where_the_earlier_state_is(tmp_cfg, reading_problem):
    """Its self-check must be the check that runs on the way out. Without the
    earlier state, a reviser that correctly deleted a wrong edge sees an error
    the Stop hook does not, and sets about "fixing" a fix."""
    reading = reading_problem.progress_dir / "reading"
    (reading / "review-1.json").write_text(json.dumps(REVIEW), encoding="utf-8")
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness)
    p = plan_next_pass(tmp_cfg, plan, role_name="reviser", round_no=1, index=3)

    assert "{lane}" not in p.system_append           # placeholder resolved
    assert str(plan.run_dir / "loop" / "lane" / "graph.json") in p.system_append
    assert "--before-graph" in p.system_append


def test_an_earlier_round_s_review_is_restored_if_a_later_pass_edits_it(state):
    """The enforcement, not just the pattern: round 2's critique may not quietly
    revise what round 1 raised."""
    run_draft(state)
    write_review(state, 1, [finding()])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True,
                   writes=["progress/reading/review-1.json"])
    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [{"id": "F1", "disposition": "rejected",
                                      "note": "the critic misreads the definition"}]}),
        encoding="utf-8")
    loopmod.assess(state, 3, "reviser", 1, state.harness.roles["reviser"], ok=True,
                   writes=["progress/reading/graph.json", "progress/reading/guide.md",
                           "progress/reading/map.mmd",
                           "progress/reading/dispositions-1.json"])

    original = loopmod.review_path(state, 1).read_text()
    write_review(state, 2, [finding(id="F9")])
    loopmod.review_path(state, 1).write_text('{"round": 1, "findings": []}',
                                             encoding="utf-8")
    outcome = loopmod.assess(state, 4, "critic", 2, critic_role(state), ok=True,
                             writes=["progress/reading/review-2.json"])

    assert loopmod.review_path(state, 1).read_text() == original
    assert any("review-1.json" in n and "restored" in n for n in outcome.notes)
    assert loopmod.review_path(state, 2).is_file()


def test_an_interrupted_pass_is_still_held_to_its_lane_and_the_document(tmp_path):
    """A pass stopped by the user never reaches the controller. Finalize is the
    last chance to undo what it changed, and it must take it — otherwise Stop
    during a critique leaves the companion edited and the document modified."""
    import inspect

    from atelier.runner import RunHandle

    body = inspect.getsource(RunHandle._finalize_inner)
    restore = body.index("loop.restore_document")
    lane = body.index("loop.restore_outside_lane")
    labbook = body.index("self._write_labbook(")
    assert restore < labbook and lane < labbook
    # It must run for every terminal path, not only a clean finish.
    assert "if self.loop_state is not None:" in body


def test_the_next_round_can_read_what_the_last_one_found(state):
    """The round-2 critique is asked to check whether a finding recorded as
    *fixed* is visible where its disposition says. It reads that from
    review-log.md — so the log has to exist while that round is running, not
    only once the whole run is over."""
    run_draft(state)
    write_review(state, 1, [finding()])
    loopmod.assess(state, 2, "critic", 1, critic_role(state), ok=True,
                   writes=["progress/reading/review-1.json"])
    loopmod.dispositions_path(state, 1).write_text(json.dumps({
        "round": 1, "dispositions": [{"id": "F1", "disposition": "fixed",
                                      "where": "guide:Main ideas",
                                      "note": "named in the guide"}]}),
        encoding="utf-8")
    loopmod.assess(state, 3, "reviser", 1, state.harness.roles["reviser"], ok=True,
                   writes=["progress/reading/graph.json", "progress/reading/guide.md",
                           "progress/reading/map.mmd",
                           "progress/reading/dispositions-1.json"])

    log = state.reading / "review-log.md"
    assert log.is_file(), "round 2 would be asked to check answers it cannot see"
    text = log.read_text()
    assert "F1" in text and "fixed" in text and "guide:Main ideas" in text
    assert "What changed" in text        # the diff for the round just finished


def test_the_draft_is_told_the_budget_it_actually_has(tmp_cfg, reading_problem):
    """Provider caps cover the run; the prompt specifies the drafting cutoff."""
    harness = deslopify_harness(tmp_cfg)
    plan = plan_for(tmp_cfg, reading_problem, harness, max_usd=14.0, max_minutes=75)
    draft = plan.passes[0]

    assert draft.budget_usd == pytest.approx(14.0)
    assert "Overall run time limit: 75 min" in draft.prompt
    assert "Time available for this stage: about 55 min" in draft.prompt
    assert "Drafting stops at 55 min" in draft.prompt


def test_a_run_with_no_loop_states_its_budget_exactly_as_before(tmp_cfg, sample_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "solo")
    plan = plan_for(tmp_cfg, sample_problem, harness, max_usd=8.0)
    assert "Budget: $8.00" in plan.prompt
    assert "for this first pass" not in plan.prompt
    assert "covers all of them together" not in plan.prompt


def test_a_repeated_final_result_is_not_billed_twice(tmp_cfg):
    h = usage_handle(tmp_cfg)
    h._apply_usage({"final": True, "tok_in": 100, "tok_out": 40, "total_cost_usd": 3.0})
    h._apply_usage({"final": True, "tok_in": 100, "tok_out": 40, "total_cost_usd": 3.0})
    assert h.totals.cost_reported == pytest.approx(3.0)

    h._start_pass()
    h._apply_usage({"final": True, "tok_in": 10, "tok_out": 5, "total_cost_usd": 0.5})
    h._apply_usage({"final": True, "tok_in": 10, "tok_out": 5, "total_cost_usd": 0.5})
    assert h.totals.cost_reported == pytest.approx(3.5)


def test_a_share_of_zero_is_refused_rather_than_uncapping_a_pass(tmp_cfg):
    """Zero is falsy everywhere downstream, so it would leave that pass with no
    cap at all — the opposite of what writing 0 looks like it asks for."""
    from atelier.harnesses import HarnessError, load_harness

    root = tmp_cfg.harnesses_dir / "deslopify"
    manifest = root / "harness.toml"
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(original.replace("verifier         = 0.18",
                                         "verifier         = 0.0"),
                        encoding="utf-8")
    try:
        with pytest.raises(HarnessError, match="loop.share must be positive"):
            load_harness(root)
    finally:
        manifest.write_text(original, encoding="utf-8")


# --- who a reading is for, and what a reading owes ---------------------------

def test_a_reader_profile_reaches_every_pass(tmp_cfg, reading_problem):
    """What the reader said they know is in front of the writer, the check and
    the revision alike — and absent when there is nothing to say."""
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    profile = "Knows well: manifolds.\nDoes not know: the h-cobordism theorem."
    plan = plan_for(tmp_cfg, reading_problem, harness, reader_profile=profile)
    assert "<reader_profile>\n" + profile in plan.passes[0].prompt
    assert plan.passes[0].prompt.index("<reader_profile>") \
        < plan.passes[0].prompt.index("<your_task>")
    auditor = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    reviser = plan_next_pass(tmp_cfg, plan, role_name="balanced-reviser",
                             round_no=1, index=3)
    assert profile in auditor.prompt and profile in reviser.prompt
    saved = json.loads((plan.run_dir / "config.json").read_text())
    assert saved["spec"]["reader_profile"] == profile

    plain = plan_for(tmp_cfg, reading_problem, harness)
    assert "<reader_profile>" not in plain.passes[0].prompt


def test_a_method_may_owe_only_its_guide_and_state_its_own_task(tmp_cfg, reading_problem):
    """Not every reading is a companion to one paper: a method that declares
    `contract = "guide"` is told to finish its guide, not seven sections."""
    base = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    harness = replace(base, contract="guide", loop=replace(base.loop, enabled=False),
                      default_task="Read `{document}` and compare what it says.")
    plan = plan_for(tmp_cfg, reading_problem, harness)
    prompt = plan.passes[0].prompt
    assert "complete, as your method describes it" in prompt
    assert "seven sections" not in prompt
    assert "Read `progress/math.tex` and compare what it says." in prompt
    hooks = json.loads((plan.passes[0].dir / "settings.json").read_text())["hooks"]
    assert "Stop" not in hooks

    # The companion contract is the default and also has no reading Stop hook.
    plain = plan_for(tmp_cfg, reading_problem, base)
    hooks = json.loads((plain.passes[0].dir / "settings.json").read_text())["hooks"]
    assert "Stop" not in hooks
    assert "selected profile's section order" in plain.passes[0].prompt


def test_the_guide_contract_owes_a_guide_and_notes_and_nothing_else(tmp_cfg,
                                                                    reading_problem):
    from pathlib import Path
    import subprocess
    import sys

    reading = reading_problem.progress_dir / "reading"
    for name in ("graph.json", "map.mmd", "map.pdf", "companion.md", "companion.pdf"):
        (reading / name).unlink(missing_ok=True)
    (reading / "guide.md").write_text("# Two papers\n\nHow they meet.\n", encoding="utf-8")
    (reading_problem.progress_dir / "_session_notes.md").write_text("Done.\n")
    run_dir = tmp_cfg.runs_dir / "hook-guide-contract"
    run_dir.mkdir(parents=True)
    hook = Path(loopmod.__file__).parent / "hooks" / "check_reading.py"

    def stop(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(hook), "--problem", str(reading_problem.root),
             "--run", str(run_dir), "--cwd", str(reading_problem.root), *extra],
            input="{}", capture_output=True, text=True, timeout=20)

    assert stop("--contract", "guide").returncode == 0
    # The same guide under the companion contract is far from finished.
    assert stop().returncode == 2
    (reading / "guide.md").write_text("   \n", encoding="utf-8")
    refused = stop("--contract", "guide")
    assert refused.returncode == 2 and "progress/reading/guide.md" in refused.stderr


def test_a_contract_must_be_one_the_lab_knows(tmp_path):
    import shutil

    from atelier.harnesses import HarnessError, load_harness

    root = tmp_path / "h"
    root.mkdir()
    (root / "SKILL.md").write_text("Read it.\n")
    (root / "harness.toml").write_text(
        '[harness]\nid = "h"\nkind = "read"\ncontract = "everything"\n')
    with pytest.raises(HarnessError, match="contract"):
        load_harness(root)
    (root / "harness.toml").write_text('[harness]\nid = "h"\nkind = "read"\n')
    assert load_harness(root).contract == "companion"
    shutil.rmtree(root)


# --- a different AI for some passes (RunSpec.role_agents) ------------------

def test_the_check_can_run_on_another_provider(tmp_cfg, reading_problem):
    """The draft on Claude, the audit on ChatGPT, the revision on Claude again:
    each pass is compiled for its own CLI, and the record names each pass's
    AI. One pass has no per-token price: the money cap binds on the others,
    and time on all three."""
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    plan = plan_for(tmp_cfg, reading_problem, harness, max_usd=2.0, max_minutes=20,
                    role_agents={"auditor": {"account": "codex", "model": "gpt-5.6-terra",
                                             "effort": "medium"}})
    agents = plan.extra["agents"]
    assert agents["draft"] == {"provider": "claude", "account": "default",
                               "model": "claude-haiku-4-5", "effort": "low"}
    assert agents["auditor"] == {"provider": "codex", "account": "codex",
                                 "model": "gpt-5.6-terra", "effort": "medium"}
    # Left out, the revision is the run's own AI, at the role's own effort.
    reviser = harness.roles["balanced-reviser"]
    assert agents["balanced-reviser"]["model"] == "claude-haiku-4-5"
    assert agents["balanced-reviser"]["effort"] == (reviser.effort or "low")
    # The money cap still binds on the passes with a price; time on them all.
    assert plan.budget_usd == 2.0 and plan.budget_minutes == 20

    record = json.loads((plan.run_dir / "config.json").read_text())
    assert record["resolved"]["agents"] == agents
    assert plan.first.provider == "claude" and plan.first.model == "claude-haiku-4-5"

    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    assert audit.provider == "codex" and audit.account == "codex"
    assert audit.model == "gpt-5.6-terra" and audit.effort == "medium"
    # Unpriced passes use time; priced passes share the overall money limit.
    assert audit.budget_usd is None and audit.budget_minutes
    assert plan.first.budget_usd == pytest.approx(2.0)
    assert "exec" in audit.argv and audit.argv[audit.argv.index("--model") + 1] == "gpt-5.6-terra"
    assert "CODEX_HOME" in audit.env
    # Codex takes no system prompt: the method travels in the prompt itself.
    assert "<method>" in audit.prompt

    revise = plan_next_pass(tmp_cfg, plan, role_name="balanced-reviser", round_no=1, index=3)
    assert revise.provider == "claude" and revise.model == "claude-haiku-4-5"
    assert revise.budget_usd == plan.first.budget_usd == pytest.approx(2.0)
    assert "--append-system-prompt" in revise.argv


def test_a_run_wholly_on_unpriced_models_is_held_to_time(tmp_cfg, reading_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    plan = plan_for(tmp_cfg, reading_problem, harness, max_usd=2.0, max_minutes=None,
                    role_agents={"draft": {"account": "codex", "model": "gpt-5.6-terra"}})
    assert plan.budget_usd is None and plan.budget_minutes


def test_a_run_with_one_ai_plans_as_it_always_did(tmp_cfg, reading_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    plan = plan_for(tmp_cfg, reading_problem, harness, max_usd=2.0, max_minutes=20)
    assert plan.budget_usd == 2.0
    assert {a["model"] for a in plan.extra["agents"].values()} == {"claude-haiku-4-5"}
    audit = plan_next_pass(tmp_cfg, plan, role_name="auditor", round_no=1, index=2)
    auditor = harness.roles["auditor"]
    assert audit.provider == "claude" and audit.effort == (auditor.effort or "low")


def test_the_first_pass_can_be_given_another_ai_too(tmp_cfg, reading_problem):
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-expert")
    plan = plan_for(tmp_cfg, reading_problem, harness,
                    role_agents={"draft": {"model": "claude-opus-5"}})
    assert plan.model == "claude-opus-5" and plan.first.model == "claude-opus-5"
    assert plan.extra["agents"]["auditor"]["model"] == "claude-opus-5"
    assert plan.spec.provider == "claude"
