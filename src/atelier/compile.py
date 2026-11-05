"""Compile a harness + problem + RunSpec into a concrete, reproducible invocation.

Everything the provider needs is materialised under `runs/<id>/`, and nothing
harness-specific is ever written into the problem folder. Two consequences that
matter:

  * a run is hermetic — `--setting-sources ''` means it never inherits your
    personal ~/.claude settings, so benchmark comparisons are meaningful;
  * a run is reproducible — argv.json plus the run directory is enough to
    re-create the exact invocation months later.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config import Account, Config
from .delivery import (
    LATEX_GUIDANCE, PROGRESSIVE_GUIDE,
    DIAGRAM_FILES, diagram_problems, diagram_required,
    export_problems, guide_problems, reading_run_limit, REVIEW_EXTRA_MINUTES, REVISION_EXTRA_MINUTES,
    stage_time_available, time_targets,
)
from .harnesses import Harness
from .labbook import read_entries
from .problems import Problem, read_state, snapshot_static
from .spec import RunSpec

# --------------------------------------------------------------------------
# the compiled plan
# --------------------------------------------------------------------------


def _redact(env: dict[str, str]) -> dict[str, str]:
    return {k: ("<redacted>" if ("KEY" in k or "TOKEN" in k or "SECRET" in k) else v)
            for k, v in env.items()}


@dataclass(slots=True)
class PassPlan:
    """One provider invocation. Most runs have exactly one.

    A run with a critique loop has several, each a separate process with its own
    context. That separation is the point rather than an implementation detail:
    a reviewer who can see the drafter's reasoning is checking it for internal
    consistency, not against the document.
    """
    index: int                   # 1-based
    role: str                    # "draft", or a role named by the harness
    label: str                   # "draft", "critique 1", "revision 1"
    round: int                   # 0 for the draft
    dir: Path                    # where this pass's artefacts are written
    prompt: str
    system_append: str
    argv: list[str]
    env: dict[str, str]
    budget_usd: float | None = None
    budget_minutes: float | None = None
    budget_tokens_out: int | None = None
    budget_turns: int | None = None
    writes: list[str] = field(default_factory=list)
    # Who runs this pass. Most runs use one AI throughout; a run may give a
    # pass of its loop to another (RunSpec.role_agents), even another provider.
    provider: str = ""
    account: str | None = None
    model: str = ""
    effort: str = ""

    def redacted_env(self) -> dict[str, str]:
        return _redact(self.env)


@dataclass(slots=True)
class RunPlan:
    spec: RunSpec
    problem: Problem
    harness: Harness
    account: Account | None
    run_dir: Path
    cwd: Path
    passes: list[PassPlan]
    model: str
    effort: str
    budget_usd: float | None
    budget_minutes: float | None
    budget_tokens_out: int | None
    budget_turns: int | None
    stdin_stream: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    # The first pass is what every existing caller means by "the invocation":
    # `atelier preview`, the web preview, and the tests all want the draft. The
    # runner asks for the pass it is about to start instead.
    @property
    def first(self) -> PassPlan:
        return self.passes[0]

    @property
    def argv(self) -> list[str]:
        return self.first.argv

    @property
    def env(self) -> dict[str, str]:
        return self.first.env

    @property
    def prompt(self) -> str:
        return self.first.prompt

    @property
    def system_append(self) -> str:
        return self.first.system_append

    def redacted_env(self) -> dict[str, str]:
        return _redact(self.env)


# --------------------------------------------------------------------------
# prompt assembly
# --------------------------------------------------------------------------


def _fence(tag: str, body: str, **attrs: str) -> str:
    """Wrap a block in a named tag. Attributes go on the open tag only."""
    rendered = "".join(f' {k}="{str(v).replace(chr(34), "")}"' for k, v in attrs.items())
    return f"\n<{tag}{rendered}>\n{body.rstrip()}\n</{tag}>\n"


def _tex_outline(text: str) -> str:
    """Section headings plus theorem-environment openers — a cheap table of contents."""
    keep: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("\\section", "\\subsection", "\\subsubsection")) or \
           s.startswith(("\\begin{theorem", "\\begin{lemma", "\\begin{proposition",
                         "\\begin{corollary", "\\begin{conjecture", "\\begin{definition")):
            keep.append(s)
    return "\n".join(keep) if keep else "(math.tex has no sections yet)"


def _render_state(state: dict[str, Any]) -> str:
    lines = [f"status: {state.get('status', 'open')}",
             f"confidence: {state.get('confidence', 0.0)}",
             f"summary: {state.get('one_line', '') or '(none recorded)'}"]

    def block(key: str, label: str, fmt) -> None:
        items = state.get(key) or []
        if not items:
            return
        lines.append(f"\n{label}:")
        for it in items[:40]:
            lines.append(f"  - {fmt(it)}")

    block("established", "Established (verified)",
          lambda i: f"[{i.get('id', '?')}] {i.get('statement', i)}" if isinstance(i, dict) else str(i))
    block("conjectures", "Conjectures (not verified)",
          lambda i: f"[{i.get('id', '?')}] {i.get('statement', i)}" if isinstance(i, dict) else str(i))
    block("blockers", "Blockers", str)
    block("next_steps", "Next steps", str)
    block("dead_ends", "Dead ends (do not repeat)",
          lambda i: f"{i.get('approach', i)} — {i.get('why', '')}" if isinstance(i, dict) else str(i))
    return "\n".join(lines)


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def build_prompt(cfg: Config, problem: Problem, harness: Harness, spec: RunSpec,
                 *, session_number: int, budget_line: str,
                 provider_caps: Any = None, document: Path | None = None,
                 document_text: str = "", document_label: str = "",
                 isolated_workspace: bool = False) -> str:
    ctx = harness.context
    oneshot = spec.mode == "oneshot"
    parts: list[str] = []

    # --- 1. orientation -------------------------------------------------
    writable = "progress/**, work/**"
    static = f"{problem.meta.statement}, strategy/**, refs/**"
    if isolated_workspace:
        working_directory = "an isolated copy of the current problem"
    elif oneshot:
        working_directory = "an isolated copy of the problem inputs"
    else:
        working_directory = "the problem folder"
    orient = [
        f"Problem: {problem.meta.title}",
        f"Session: #{session_number} on this problem"
        + ("  (ONE-SHOT: you have no access to any previous session's work)" if oneshot else ""),
        f"Working directory: {working_directory}",
        f"You may create and edit: {writable}",
        f"Read-only (the human's specification): {static}",
        budget_line,
    ]
    if spec.allow_static_edits:
        orient.append("NOTE: static-file protection is DISABLED for this session; you may "
                      "revise the problem statement if the mathematics demands it.")
    parts.append(_fence("session", "\n".join(orient)))

    # --- 2. context -----------------------------------------------------
    if ctx.include_statement and (sp := problem.statement_path()):
        text = sp.read_text(encoding="utf-8", errors="replace")
        if ctx.max_statement_chars and len(text) > ctx.max_statement_chars:
            text = text[: ctx.max_statement_chars] + \
                f"\n\n[...truncated at {ctx.max_statement_chars} characters. " \
                f"The full text is at {sp.name}; read it directly if you need more.]"
        parts.append(_fence("problem_statement", text))

    if ctx.include_strategy:
        for p in problem.strategy_paths():
            parts.append(_fence("strategy", p.read_text(encoding="utf-8", errors="replace"),
                                file=p.name))

    if not oneshot and ctx.include_progress:
        if ctx.include_state:
            parts.append(_fence("current_state", _render_state(read_state(problem.root))))

        n = ctx.labbook_count()
        if n != 0:
            entries = read_entries(problem.progress_dir / "labbook.md")
            chosen = entries if n is None else entries[-n:]
            if chosen:
                body = "\n\n---\n\n".join(e.text.strip() for e in chosen)
                parts.append(_fence("recent_lab_book_entries", body))

        if harness.kind == "read" and document is not None:
            parts.append(_fence(
                "source_document", document_text,
                file=document_label or _relative_to(document, problem.root),
                format=document.suffix.lower().lstrip(".")))
        elif ctx.math_tex != "none":
            mp = problem.progress_dir / "math.tex"
            if mp.is_file():
                raw = mp.read_text(encoding="utf-8", errors="replace")
                body = raw if ctx.math_tex == "full" else _tex_outline(raw)
                parts.append(_fence("math_tex", body, mode=ctx.math_tex))

    # Who it is for. After the material and before the task, so that the task
    # is read with the reader in mind.
    if spec.reader_profile.strip():
        parts.append(_fence("reader_profile", spec.reader_profile.strip()))

    # --- 3. task --------------------------------------------------------
    task = ""
    if spec.task_kind == "text" and spec.task_text.strip():
        task = spec.task_text.strip()
    elif spec.task_kind == "files" and spec.task_files:
        chunks = []
        for rel in spec.task_files:
            fp = problem.root / rel
            if document is not None and fp.resolve() == document.resolve():
                continue
            if fp.is_file():
                chunks.append(f"### {rel}\n\n{fp.read_text(encoding='utf-8', errors='replace')}")
        # A reading method may say what its task is (the skill's
        # `default_task`): one that is not a companion to one paper — several
        # papers together, a search — would otherwise be told to build one.
        where = _relative_to(document, problem.root) if document is not None else ""
        task = ((harness.default_task.replace("{document}", document_label or where)
                 if harness.default_task else
                 f"Read `{where}` and build the reading companion.")
                if harness.kind == "read" and document is not None else
                "\n\n".join(chunks))
    if not task:
        task = harness.default_task.replace(
            "{document}", _relative_to(document, problem.root) if document is not None
            else "the document") or (
            "Continue the work on this problem. Choose the most promising open "
            "direction from the current state, and make concrete progress on it."
        )
    parts.append(_fence("your_task", task))

    # --- 4. wrap-up protocol -------------------------------------------
    if harness.tools.none:
        # Claude can be given no tools at all (`--tools ""`). Codex cannot: the
        # least it will accept is a read-only sandbox, where it can still read
        # files and run commands. Saying "you have no tools" there would be
        # false, and a control condition that misdescribes itself is worse than
        # no control at all — so ask for the same behaviour and record that it
        # is asked for rather than enforced.
        enforced = provider_caps is None or provider_caps.tool_free
        parts.append(_fence("output", TOOL_FREE_ENFORCED if enforced else TOOL_FREE_REQUESTED))
    elif harness.kind == "read":
        protocol = READING_PROTOCOL if harness.contract == "companion" else GUIDE_PROTOCOL
        parts.append(_fence(
            "before_you_finish",
            protocol.replace("{deslopify}", deslopify_command())))
    else:
        parts.append(_fence("before_you_finish", WRAPUP_PROTOCOL))

    return "\n".join(parts).strip() + "\n"


TOOL_FREE_ENFORCED = ("Respond directly with your mathematics. You have no "
                      "tools and no file access in this session.")

TOOL_FREE_REQUESTED = (
    "Respond directly with your mathematics, in this one reply.\n\n"
    "Do not read any files, run any commands, or use any tool, even though the "
    "sandbox would permit reading. This is a control condition: the point is to "
    "measure what you can do unaided, so using a tool would invalidate the "
    "comparison rather than help."
)

READING_PROTOCOL = """\
Before you end this first session, save both of the following. The controller
checks that the guide was saved and sends incomplete work to later passes.

1. `progress/reading/guide.md` — a complete reader-first companion following
   the shared standards and selected profile's section order. Include the
   boxed source-and-scope remark and the required bibliography. Integrate
   supporting inputs and evidence with the proof or validation stages that use
   them. Follow the shared rules for historical research, citation style,
   provisional connections, and material search-scope limitations.
   Generate a new proof map only when this run explicitly supplies the optional diagram
   instructions and the recorded paper profile is pure mathematics.

2. `progress/_session_notes.md` — what was hardest to explain and where the
   independent auditor should be especially skeptical. Do not include
   token counts, costs, or timings; those are measured automatically.

The document under study must be **byte-for-byte unchanged** when you finish.
The controller restores the original before the next pass if it was changed.

If the document is too broken to explain honestly, say so in the guide and in
your notes. An honest report of an argument that cannot be reconstructed is a
result.
"""


# For a reading method whose contract is only its guide (harnesses.CONTRACTS):
# what that guide holds is the method's business, not the lab's.
GUIDE_PROTOCOL = """\
Before you end this session, save both of the following. The controller checks
that the guide was saved.

1. `progress/reading/guide.md` — complete, as your method describes it.

2. `progress/_session_notes.md` — what was hardest, and what a later session
   should look at first. Do not include token counts, costs, or timings;
   those are measured automatically.

The document under study must be **byte-for-byte unchanged** when you finish.
The controller restores the original before the next pass if it was changed.
"""


WRAPUP_PROTOCOL = """\
Before you end this session you MUST do all three of the following. A stop hook
checks them and will refuse to let the session end if any is missing.

1. `progress/math.tex` — fold everything you established into it. Verified results
   go under "Established results"; anything unproved goes under "Conjectures and
   partial results" marked \\conjectural. Record approaches that failed, and why,
   under "Failed approaches".

2. `progress/state.json` — update it to reflect reality. `one_line` must be a
   single sentence a colleague could read to know exactly where the problem
   stands. `status` is one of: open, partial, solved, disproved, stalled.

3. `progress/_session_notes.md` — write your account of this session for the lab
   book: what you tried, what worked, what did not, and what the next session
   should do first. Be honest about dead ends; that record is worth as much as
   the results. Do not include token counts, costs, or timings — those are
   measured and added automatically.

Do not claim more than you proved. If you are unsure whether a step is rigorous,
say so explicitly rather than smoothing over it."""


# --------------------------------------------------------------------------
# passes of a critique loop
# --------------------------------------------------------------------------


def _quiet(fn, default: str = "") -> str:
    """Analysis is an aid to a prompt, never a reason a run fails to launch."""
    try:
        return fn() or default
    except Exception:                                       # noqa: BLE001
        return default


def _graph_view(reading: Path, *, quotes: bool = True) -> str:
    def build() -> str:
        from deslopify.analysis import analyse
        from deslopify.graph import Graph
        from deslopify.render import to_compact_text

        graph = Graph.load(reading / "graph.json")
        return to_compact_text(graph, analyse(graph), quotes=quotes)

    return _quiet(build)


def _coverage_report(reading: Path) -> str:
    def build() -> str:
        from deslopify.coverage import coverage
        from deslopify.graph import Graph

        graph = Graph.load(reading / "graph.json")
        guide = (reading / "guide.md").read_text(encoding="utf-8", errors="replace")
        result = coverage(graph, guide)
        if not result.observations:
            return ("Nothing outstanding: the guide accounts for every gap, "
                    "hypothesis and crux the graph records.")
        return "\n".join(str(o) for o in result.observations)

    return _quiet(build)


def _appeals_report(document: Path) -> str:
    def build() -> str:
        from deslopify.lint import find_appeals
        from deslopify.source import read_source

        text = read_source(document)
        found = find_appeals(text)
        if not found:
            return "None: every step is argued on the page."
        lines = [f"{len(found)} place(s) where a phrase stands in for an argument:", ""]
        for a in found:
            lines.append(f"  line {a.line:<5} {a.why}")
            lines.append(f"    \u201c{a.text}\u201d")
        return "\n".join(lines)

    return _quiet(build)


def _read(path: Path, limit: int = 0) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if limit and len(text) > limit:
        text = text[:limit] + f"\n\n[...truncated at {limit:,} characters; " \
                              f"read {path.name} directly for the rest.]"
    return text


ROLE_WRAPUP = """\
This pass ends when you have produced:

1. `{expect}` — {output_requirement}

2. `progress/_session_notes.md` — your account of this pass. Every pass writes
   its own; they are collected into one lab-book entry for the session.

Inside `progress/reading/` you may create or modify **only** these paths:

{lane}

Everything else in that directory is restored from a snapshot before the next
pass begins. That is not a punishment — it is what makes the separation of these
passes real rather than a matter of instruction. Editing the guide from a
critique pass is not disobedience; it is simply work that will be discarded.

The document under study must be **byte-for-byte unchanged**. It is checked by
hash, and restored if it is not."""


def build_role_prompt(cfg: Config, problem: Problem, harness: Harness, spec: RunSpec,
                      role: Any, *, cwd: Path, index: int, total: int,
                      round_no: int, budget_line: str, expect: str,
                      document: Path | None = None,
                      source_excerpt: Path | None = None,
                      source_scope: str = "") -> str:
    """The prompt for a critique or revision pass.

    Deliberately not `build_prompt` with a different tail. That prompt orients a
    session towards making progress on a problem; these passes are not working
    on the problem at all, and inheriting its framing is how a reviewer starts
    suggesting mathematics.
    """
    reading = cwd / "progress" / "reading"
    document = document or cwd / "progress" / "math.tex"
    loop = harness.loop
    is_critic = role.name == loop.critic
    parts: list[str] = []

    writes = [w.replace("{round}", str(round_no)) for w in role.writes]
    lane = "\n".join(f"  - `{w}`" for w in writes) or "  - (nothing)"
    parts.append(_fence("session", "\n".join([
        f"Problem: {problem.meta.title}",
        f"Pass {index} of at most {total} in this session: "
        f"{role.title.lower()}, round {round_no} of {loop.rounds}",
        "Working directory: the problem folder",
        f"You may create and edit: {', '.join(writes) or '(nothing)'}",
        "Read-only: the document, the problem statement, and everything else",
        budget_line,
    ])))

    if role.include_document and harness.context.include_statement \
            and (sp := problem.statement_path()):
        parts.append(_fence("problem_statement",
                            _read(sp, harness.context.max_statement_chars)))

    from deslopify.source import read_source

    parts.append(_fence("the_guide_as_it_stands", _read(reading / "guide.md"),
                        file="progress/reading/guide.md"))
    if harness.kind == "read":
        parts.append(_fence("latex_requirements", LATEX_GUIDANCE))
        repairs = guide_problems(reading, harness.contract) + export_problems(reading, harness.contract)
        if repairs:
            parts.append(_fence("unfinished_work", "\n".join(f"- {p}" for p in repairs)
                + "\nThese are explicit repair tasks. The reviewer must assess the saved draft "
                  "and record missing content; the reviser must finish these tasks before "
                  "optional expansion, even if the review lists no other findings. "
                  "Use the source and saved research in work/; never invent evidence."))
    if role.include_document:
        parts.append(_fence("the_document", (_read(source_excerpt) if source_excerpt
                            else _quiet(lambda: read_source(document))),
                            file=_relative_to(document, cwd),
                            format=document.suffix.lower().lstrip(".")))
    if source_scope:
        parts.append(_fence("source_scope", source_scope))
    has_graph = (reading / "graph.json").is_file()
    writes_graph = any(w.endswith("/graph.json") for w in role.writes)
    if role.name != "graph-builder" and (has_graph or writes_graph):
        parts.append(_fence("the_graph_as_it_stands", _graph_view(reading),
                            file="progress/reading/graph.json",
                            note="compact view with quotations; the JSON is on disk"))
        if has_graph:
            parts.append(_fence("mechanical_findings", _coverage_report(reading),
                                note="arithmetic on the two files, so that your attention "
                                     "can go to what only a reader can judge"))
    clarity = reading / f"clarity-review-{round_no}.md"
    if role.name != "verifier-blind" and clarity.is_file():
        parts.append(_fence("blind_clarity_review", _read(clarity),
                            file=f"progress/reading/{clarity.name}"))

    # Who the companion is for: a check measures it against them, and a
    # revision answers for them too.
    if spec.reader_profile.strip():
        parts.append(_fence("reader_profile", spec.reader_profile.strip()))

    if role.name == "graph-builder":
        task = (
            "Read the guide as the proposed explanation, then independently read "
            "the original document and build the grounded argument graph and both "
            "map formats. Generate companion.md and companion.tex, then compile "
            "companion.pdf from that LaTeX source."
        )
    elif role.name == "verifier-blind":
        task = (
            f"Read only the guide and graph. Do not open the original document. "
            f"Record every concrete point that is hard to understand or unclear "
            f"in `progress/reading/clarity-review-{round_no}.md`."
        )
    elif is_critic:
        parts.append(_fence("skipped_work", _appeals_report(source_excerpt or document),
                            note=("every place the document stands a phrase in for "
                                  "an argument; the optional graph records a verdict "
                                  "for each" if writes_graph or has_graph else
                                  "places where the document stands a phrase in for "
                                  "an argument, supplied as an audit checklist")))
        if round_no > 1 and (log := _read(reading / "review-log.md")):
            parts.append(_fence("earlier_rounds", log))
        if role.name == "auditor":
            task = (
                f"Compare the paper's main stated results, definitions, hypotheses, "
                f"formulas, and limitations with the guide first. Then identify "
                f"passages that a reader cannot follow and check whether Historical "
                f"context is reasonably complete. Write concrete, located findings "
                f"and checkable suggestions in `progress/reading/review-{round_no}.json` "
                "for the reviser. Do not edit the guide. You do not need the "
                "writer's skill files or its template to perform this review. "
                "If the draft holds up, report no findings."
            )
        elif role.name == "verifier":
            task = (
                f"Use the blind clarity review as your question list, then read "
                f"the original paper and write `progress/reading/review-{round_no}.json`. "
                "Decide what is unclear, what is unfaithful, what the graph gets "
                "wrong, and what is actually missing from the paper. Suggest precise "
                "obligations for improving both guide and graph, never replacement "
                "prose. Ignore cosmetic style. If both artifacts hold up, raise no "
                "findings."
            )
        else:
            task = (
                f"Read the document and the companion against each other and write "
                f"`progress/reading/review-{round_no}.json`.\n\n"
                "You did not write the companion and you have not seen the reasoning "
                "behind it. Check the paper's main claims against the companion, "
                "then assess whether the explanation follows the selected skill's "
                "clarity, style, structure, and length requirements. Report the "
                "substantive weaknesses so they can be answered on their merits.\n\n"
                "If the companion holds up, say so and raise nothing. That ends the "
                "loop, which is a good outcome and not a failure to contribute."
            )
        if round_no > 1:
            task += (
                "\n\nThis is not the first round. `earlier_rounds` records what was "
                "raised before and how it was answered. Two things follow. Do not "
                "re-raise a finding that was answered, unless the answer was wrong — "
                "and if it was wrong, say so as a new finding. And check the answers "
                "themselves: a finding recorded as *fixed* should be visible at the "
                "location it names. One that was recorded as fixed and was not is "
                "worth more than anything else you will find this round."
            )
    else:
        parts.append(_fence("the_review", _read(reading / f"review-{round_no}.json"),
                            file=f"progress/reading/review-{round_no}.json"))
        graph_clause = (" — the graph if it is about the analysis,"
                        if has_graph or writes_graph else "")
        task = (
            f"Answer every finding in `progress/reading/review-{round_no}.json`.\n\n"
            f"Where a finding is right, change the thing it is about{graph_clause} "
            "the guide if it is about what a reader is "
            "told. Where it is wrong, reject it and say what the critic got wrong; "
            "that costs nothing and is often the correct answer.\n\n"
            f"Record every one, fixed or rejected, in "
            f"`progress/reading/dispositions-{round_no}.json`.\n\n"
            "A revision must change a substantive claim, explanation, locator, gap, "
            "or map obligation; cosmetic rewriting is not the work of this pass."
        )

    parts.append(_fence("your_task", task))
    output_requirement = (
        "save a readable JSON review for the reviser. There is no separate "
        "rule-based grading of its findings."
        if is_critic else
        "check it yourself before you finish; the same check runs on the way "
        "out and will refuse to let the pass end."
    )
    parts.append(_fence("before_you_finish",
                        ROLE_WRAPUP.format(expect=expect, lane=lane,
                                           output_requirement=output_requirement)))
    return "\n".join(parts).strip() + "\n"


# --------------------------------------------------------------------------
# system prompt
# --------------------------------------------------------------------------


def deslopify_command() -> str:
    """How to invoke our own analysis tools from a session's working directory."""
    return f"{_python_executable()} -m deslopify"


def diagram_instructions(harness: Harness, role: str) -> str:
    if not harness.metadata.get("include_proof_map") or "proof-map" not in harness.roles:
        return ""
    task = {
        "draft": ("If the recorded profile is pure, save a complete initial guide, then "
                  "create the requested diagram before optional expansion."),
        harness.loop.critic: ("Check diagram eligibility and, when required, the writer's "
                             "diagram independently against the source and guide. Record "
                             "defects in the review; do not edit diagram files."),
        harness.loop.reviser: ("Repair the required diagram for supported findings or "
                              "missing output, preserving sound nodes and edges. "
                              "Rerender after changes."),
    }.get(role, "")
    return f"{harness.roles['proof-map'].body}\n\nYour task: {task}"


def build_system_append(cfg: Config, problem: Problem, harness: Harness,
                        spec: RunSpec, document: Path | None = None,
                        document_label: str = "") -> str:
    parts: list[str] = []
    if harness.skill:
        # A skill is written once and run on any machine, so it refers to our
        # tooling by placeholder. The session's working directory is the problem
        # folder, which is not a Python project, so the interpreter has to be
        # named outright rather than discovered.
        body = harness.skill.replace("{deslopify}", deslopify_command())
        body = body.replace("{skill_directory}", str(cfg.runs_dir / spec.run_id / "skill-references"))
        if document is not None:
            body = body.replace("{document}", document_label or str(document))
        parts.append(body)

    diagram = diagram_instructions(harness, "draft")
    if diagram:
        parts.append(diagram.replace("{deslopify}", deslopify_command())
                     .replace("{document}", shlex.quote(str(
                         cfg.runs_dir / spec.run_id / "source-excerpt.txt")))
                     .replace("{lane}", str(cfg.runs_dir / spec.run_id / "loop" / "lane")))

    policies = list(harness.shared_policies)
    if not policies:
        # A tool-less control has no files to keep a contract about; sending it
        # one would just be noise it cannot act on.
        if harness.kind == "read":
            # `rigor` governs promoting a claim to established, which a reader
            # never does. `fidelity` governs not changing what the text says.
            policies = ["fidelity"]
        else:
            policies = ["rigor"] if harness.tools.none else ["file-contract", "rigor"]
    for name in policies:
        p = policy_path(cfg, harness, name)
        if p.is_file():
            _, body = _strip_frontmatter(p.read_text(encoding="utf-8"))
            parts.append(body.strip())

    if harness.agents:
        listing = "\n".join(f"- `{a.name}` — {a.description}" for a in harness.agents.values())
        parts.append(
            "# Subagents available to you\n\n"
            f"{listing}\n\n"
            "Delegate with the Task tool. Give each subagent everything it needs in the "
            "prompt: it cannot see this conversation."
        )
    return "\n\n---\n\n".join(p for p in parts if p.strip()).strip()


def build_role_system_append(cfg: Config, harness: Harness, role: Any,
                            *, lane: Path | None = None,
                            document: Path | None = None,
                            skill_directory: Path | None = None) -> str:
    """The method for one pass of a loop, plus the policy the whole run is under.

    The harness's own SKILL.md is deliberately *not* included. It is the method
    for building a companion, and a pass that is reading one adversarially or
    answering findings against it is doing a different job; handing over both
    produces a reviewer that starts redrafting.
    """
    # `{lane}` is the companion as this pass found it. The reviser needs it to
    # run the same check the Stop hook will run: a fix that removed a wrong edge
    # names an edge that no longer exists, and without the earlier state its own
    # check would report an error the hook does not.
    body = role.body.replace("{deslopify}", deslopify_command())
    body = body.replace("{lane}", str(lane) if lane else "")
    body = body.replace("{document}", str(document) if document else "SOURCE_DOCUMENT")
    reader_level = ("non-expert" if "nonexpert" in (harness.variant or "")
                    else "expert")
    body = body.replace("{reader_level}", reader_level)
    parts = [body]
    diagram = diagram_instructions(harness, role.name)
    if diagram:
        parts.append(diagram.replace("{deslopify}", deslopify_command())
                     .replace("{document}", shlex.quote(str(
                         skill_directory.parent / "source-excerpt.txt"
                         if skill_directory else document or "SOURCE_DOCUMENT")))
                     .replace("{lane}", str(lane) if lane else ""))
    if harness.skill_references and skill_directory is not None \
            and role.name != "auditor":
        shared_instructions = (
            f"Read `{skill_directory / 'SKILL-common.md'}` for the shared companion "
            "standards, then the selected profile. "
            if "SKILL-common.md" in harness.skill_references else ""
        )
        parts.append(
            "# Selected companion instructions\n\n"
            + shared_instructions +
            "The draft records each paper's profile and selected skill filename in "
            "`progress/reading/paper-profile.json`. Read that record and the selected "
            f"file under `{skill_directory}`. These are the same saved instructions "
            "the draft used. Consult the other profile only for a genuinely mixed "
            "paper. Use their audience, structure, evidence, and presentation rules "
            "as review or revision criteria; your present role still controls what "
            "you read first, what you may write, and the scope of your work. Do not "
            "restart drafting or repeat the writer's literature search. A blind "
            "pass must still remain blind to the source. After the source-reading "
            "phase, flag a missing or inappropriate selection instead of silently "
            "applying the other profile. Preserve the selected structure when "
            "revising, unless a supported finding requires correcting the profile."
        )
    if (harness.variant or "").startswith("multi-") and role.name == "auditor":
        parts.append(
            "# Collection audit\n\n"
            "This guide covers several papers. Check the main claims and Historical "
            "context for each paper against its own source; do not merge their "
            "evidence or count references only across the collection. An optional "
            "opening section titled How the papers are related must have conceptual "
            "subsections, each "
            "explaining a clear relationship between a naturally related subset "
            "of at least two papers. Flag any manual Contents or final linking "
            "section. Check that every paper starts on a new PDF page. The "
            "controller renders Markdown, TeX, and PDF from the saved guide; "
            "inspect the generated files when available."
        )
    elif (harness.variant or "").startswith("multi-") and skill_directory is not None:
        parts.append(
            "# Collection-specific review and revision\n\n"
            f"Read `{skill_directory / 'collection-method.md'}` in full. It is "
            "the collection method used by the reader. Its format takes "
            "precedence over any single-paper section template or generic "
            "role wording. Preserve the optional opening section titled "
            "How the papers are related "
            "only when clearly connected subsets of papers warrant conceptual "
            "subsections; omit a manual Contents or final linking section. "
            "For each paper in order, keep only Main results or Main findings, "
            "Historical context, and Bibliography. Every paper starts on a new "
            "PDF page. Check each paper against its own source, profile, "
            "claims, and bibliography; do not merge their evidence or count "
            "references only across the collection. The reviewer is independent "
            "of the writer and records findings for each affected paper. The "
            "reviser changes only what supported findings or delivery defects "
            "require. The controller renders Markdown, TeX, and PDF from the "
            "saved guide, so the role's generic manual export command is not "
            "required. Inspect the generated files when available and flag "
            "rendering defects. Role-specific read order and write lanes still "
            "apply."
        )
    policies = list(harness.shared_policies) or (
        ["fidelity"] if harness.kind == "read" else ["rigor"])
    for name in policies:
        path = policy_path(cfg, harness, name)
        if path.is_file():
            _, body = _strip_frontmatter(path.read_text(encoding="utf-8"))
            parts.append(body.strip())
    return "\n\n---\n\n".join(p for p in parts if p.strip()).strip()


def policy_path(cfg: Config, harness: Harness, name: str) -> Path:
    """A shared policy, or the harness's own copy of it if it carries one.

    A harness may ship `policy/<name>.md` beside its skill: that copy is used
    for its runs in place of `shared/policy/<name>.md`. The reader uses this
    for a reading whose method someone has edited, which is copied whole into
    the run — so every pass of that run, including those planned later, reads
    the same rules.
    """
    own = harness.root / "policy" / f"{name}.md"
    return own if own.is_file() else cfg.shared_dir / "policy" / f"{name}.md"


def _strip_frontmatter(text: str) -> tuple[dict[str, str], str]:
    from .harnesses import parse_frontmatter

    return parse_frontmatter(text)


# --------------------------------------------------------------------------
# settings.json (permissions + hooks)
# --------------------------------------------------------------------------


def build_settings(cfg: Config, problem: Problem, harness: Harness, spec: RunSpec,
                   run_dir: Path, cwd: Path, *, role: str = "",
                   round_no: int = 0, expect: str = "",
                   validate: str = "") -> dict[str, Any]:
    allow: list[str] = []
    deny: list[str] = []

    for cmd in harness.tools.bash_allow:
        allow.append(f"Bash({cmd})")

    if harness.kind == "read":
        # The harness is told to check its own work; refusing it permission to
        # do so would make the instruction a formality.
        allow.append(f"Bash({_python_executable()} -m deslopify:*)")
    for cmd in harness.tools.bash_deny:
        deny.append(f"Bash({cmd})")

    # Always-on guardrails, regardless of harness. These protect the repository
    # and the machine, not the mathematics.
    for cmd in ("git push:*", "git reset --hard:*", "git clean:*", "rm -rf /*", "sudo:*"):
        rule = f"Bash({cmd})"
        if rule not in deny:
            deny.append(rule)

    if not harness.permissions.network:
        deny.extend(["WebSearch", "WebFetch"])

    hooks: dict[str, list[dict[str, Any]]] = {}
    hook_dir = Path(__file__).resolve().parent / "hooks"
    python = _python_executable()

    # The hooks operate on whatever root actually holds progress/ for THIS
    # session: the problem folder normally, the isolated workspace in one-shot.
    # Pointing them at the real problem during a one-shot run made the Stop hook
    # demand files in a directory the agent could not even see.
    hook_root = cwd

    def hook_cmd(script: str, *args: str) -> str:
        parts = [_q(python), _q(str(hook_dir / script)),
                 "--problem", _q(str(hook_root)), "--run", _q(str(run_dir)),
                 "--cwd", _q(str(cwd)), *args]
        return " ".join(parts)

    if not (harness.permissions.edit_static_files or spec.allow_static_edits):
        hooks.setdefault("PreToolUse", []).append({
            "matcher": "Write|Edit|NotebookEdit|MultiEdit",
            "hooks": [{"type": "command", "command": hook_cmd("guard_static.py"),
                       "timeout": 15}],
        })

    if (harness.kind != "read"
            and harness.hooks.get("post_write_tex", "builtin:compile_latex")
            == "builtin:compile_latex"):
        hooks.setdefault("PostToolUse", []).append({
            "matcher": "Write|Edit|MultiEdit",
            "hooks": [{"type": "command", "command": hook_cmd("compile_tex.py"),
                       "timeout": cfg.latex.timeout_s + 20}],
        })

    hooks.setdefault("PostToolUse", []).append({
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": hook_cmd("log_command.py"), "timeout": 15}],
    })

    if not harness.tools.none and harness.kind != "read":
        # Solving sessions keep their file-contract Stop hook. Reading passes
        # rely on independent review and the controller's delivery checks.
        extra: list[str] = []
        if role:
            extra += ["--role", _q(role), "--round", str(round_no)]
        if expect:
            extra += ["--expect", _q(expect)]
        if validate:
            extra += ["--validate", validate]
        hooks.setdefault("Stop", []).append({
            "hooks": [{"type": "command", "command": hook_cmd("check_contract.py", *extra),
                       "timeout": 90}],
        })

    settings: dict[str, Any] = {
        "permissions": {
            "allow": allow,
            "deny": deny,
            "defaultMode": spec.permission_mode or harness.permissions.mode,
            # NEVER widen access from an isolated workspace. A one-shot must
            # remain blind to progress/, while concurrent readers must not be
            # able to write through to one shared progress/reading directory.
            "additionalDirectories": (
                [] if (spec.mode == "oneshot" or cwd == problem.root
                       or harness.kind == "read")
                else [str(problem.root)]
            ),
        },
        "hooks": hooks,
        "includeCoAuthoredBy": False,
    }
    return settings


def _q(s: str) -> str:
    return f'"{s}"' if (" " in s or "'" in s) else s


def _python_executable() -> str:
    import sys

    return sys.executable or "python3"


# --------------------------------------------------------------------------
# plugin directory (skills + agents, progressive disclosure)
# --------------------------------------------------------------------------


def build_plugin(cfg: Config, harness: Harness, plugin_dir: Path) -> bool:
    """Materialise a Claude Code plugin holding this harness's skills.

    Returns True when the plugin has content worth loading. Skills are the only
    thing that genuinely benefits from progressive disclosure; the method itself
    goes in the system prompt so it is always in force.
    """
    skills_out = plugin_dir / "skills"
    wrote = False

    for name in harness.shared_skills:
        src = cfg.shared_dir / "skills" / name
        if src.is_dir():
            shutil.copytree(src, skills_out / name, dirs_exist_ok=True)
            wrote = True
    for name in harness.local_skills:
        src = harness.root / "skills" / name
        if src.is_dir():
            shutil.copytree(src, skills_out / name, dirs_exist_ok=True)
            wrote = True

    if wrote:
        manifest_dir = plugin_dir / ".claude-plugin"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "plugin.json").write_text(json.dumps({
            "name": f"atelier-{harness.id}",
            "version": f"{harness.version}.0.0",
            "description": harness.description or f"Atelier harness {harness.id}",
        }, indent=2), encoding="utf-8")
    return wrote


# --------------------------------------------------------------------------
# the compiler
# --------------------------------------------------------------------------


# Default weights for how a run's budget is divided between its passes. The
# draft does the most work by far — it reads the document cold and builds the
# graph — while a critique writes one small file. Normalised over whatever
# sequence a harness actually asks for, so a one-round loop is not left with a
# third of its budget unspent.
DEFAULT_SHARE = {"draft": 0.25, "graph-builder": 0.25,
                 "verifier-blind": 0.10, "verifier": 0.18,
                 "critic": 0.18, "reviser": 0.22}


def pass_sequence(harness: Harness) -> list[tuple[str, str, int]]:
    """(role, label, round) for every pass a run of this harness may have."""
    out = [("draft", "draft", 0)]
    if harness.loop.enabled:
        if harness.loop.sequence:
            for role_name in harness.loop.sequence:
                role = harness.roles.get(role_name)
                out.append((role_name, role.title.lower() if role else role_name, 1))
            return out
        for round_no in range(1, harness.loop.rounds + 1):
            out.append((harness.loop.critic, f"critique {round_no}", round_no))
            out.append((harness.loop.reviser, f"revision {round_no}", round_no))
    return out


def pass_budgets(harness: Harness, budget_usd: float | None,
                 budget_minutes: float | None,
                 budget_tokens_out: int | None = None,
                 budget_turns: int | None = None, *,
                 priced: list[bool] | None = None) -> list[tuple]:
    """How much of the run's budget each pass may use before it wraps up.

    The run-level cap, plus a reading reviser's allowance, is the stop. These
    decide when an individual pass is told to finish, so that a draft cannot
    quietly consume the whole session
    and leave nothing for the reading that was the point of enabling the loop.

    Every cap divides, not only the two a harness happens to set. A token or
    turn ceiling left whole would have let the draft spend all of it and starve
    exactly the passes the division exists to protect.

    `priced` says, pass by pass, whether its AI has a price
    (`RunSpec.role_agents` may give passes different ones). Money is divided
    only among those that can spend it — a pass with no price gets none, and
    its share is not left idle while a priced pass is cut short.
    """
    sequence = pass_sequence(harness)
    if harness.kind == "read":
        # Provider caps cover the run; the controller enforces stage handoffs.
        return [(budget_usd if priced is None or priced[i] else None,
                 reading_run_limit(budget_minutes, revision=role == harness.loop.reviser,
                                   review=role == harness.loop.critic),
                 budget_tokens_out, budget_turns)
                for i, (role, _, _) in enumerate(sequence)]
    if len(sequence) == 1:
        return [(budget_usd, budget_minutes, budget_tokens_out, budget_turns)]

    loop = harness.loop
    def weight(role: str) -> float:
        if role in loop.share:
            return max(0.0, loop.share[role])
        return DEFAULT_SHARE.get(
            role, DEFAULT_SHARE["critic"] if role == loop.critic
            else DEFAULT_SHARE["reviser"])

    weights = [weight(role) for role, _, _ in sequence]
    total = sum(weights) or 1.0
    can_pay = priced if priced and len(priced) == len(weights) and any(priced) \
        else [True] * len(weights)
    paying = sum(w for w, p in zip(weights, can_pay, strict=True) if p) or 1.0
    return [((budget_usd * w / paying) if budget_usd and p else None,
             (budget_minutes * w / total) if budget_minutes else None,
             int(budget_tokens_out * w / total) if budget_tokens_out else None,
             max(1, int(budget_turns * w / total)) if budget_turns else None)
            for w, p in zip(weights, can_pay, strict=True)]


def priced_passes(cfg: Config, harness: Harness, agents: dict[str, Any]) -> list[bool]:
    """Whether each pass of `pass_sequence(harness)` runs on a priced model."""
    return [cfg.prices.lookup((agents.get(role) or {}).get("model") or "") is not None
            for role, _, _ in pass_sequence(harness)]


def _compile_pass(cfg: Config, *, problem: Problem, harness: Harness, spec: RunSpec,
                  account: Account | None, provider: str, model: str, effort: str,
                  run_dir: Path, cwd: Path, index: int, role: str, label: str,
                  round_no: int, prompt: str, system_append: str,
                  budget_usd: float | None, budget_minutes: float | None,
                  budget_tokens_out: int | None, budget_turns: int | None,
                  writes: list[str], agents_path: Path | None,
                  plugin_dir: Path | None, expect: str = "",
                  validate: str = "", completion_session_id: str = "",
                  completion_attempt: int = 1) -> PassPlan:
    """Materialise one provider invocation.

    The first pass writes its artefacts at the root of the run directory, where
    every existing reader of a run expects to find them. Later passes get a
    `pass-N/` of their own, which also keeps their transcripts, session ids and
    last-message files from colliding.
    """
    from .providers import get_adapter

    invocation_dir = run_dir if index == 1 else run_dir / f"pass-{index}"
    completion_dir = "writer-completion" if role == "draft" and completion_attempt == 1 \
        else f"completion-{completion_attempt}"
    pass_dir = invocation_dir / completion_dir if completion_session_id else invocation_dir
    pass_dir.mkdir(parents=True, exist_ok=True)
    adapter = get_adapter(provider)

    if system_append and not adapter.capabilities.system_prompt:
        prompt = f"{_fence('method', system_append)}\n\n{prompt}"

    if harness.kind == "read":
        from .access import reading_policy
        from .time_remaining import state_path

        command = (f"{shlex.quote(_python_executable())} -m atelier.time_remaining "
                   f"--state {shlex.quote(str(state_path(run_dir)))}")
        prompt = _fence("time_remaining", (
            f"Run `{command}` now, after substantial tool calls, after saving each "
            "section, and before starting new research. It reports this stage's "
            "time allocation and the remaining whole-run time. At 90% of this "
            "stage's allocation, `wrap_up` becomes true: stop new research and expansion, "
            "finish saving required files and session notes, and return promptly. "
            "Record unresolved gaps honestly. Stage handoffs are enforced by the "
            "runner so later checking retains its time. The clock "
            "keeps the original allocation across completion retries. "
            "If the command reports `available: false`, keep following the time "
            "budget in these instructions; do not assume you have unlimited time."
        )) + "\n\n" + prompt
        policy = _fence("access_policy", reading_policy(
            cwd, None if role == "auditor" else run_dir / "skill-references",
            network=harness.permissions.network))
        prompt = policy + "\n\n" + prompt
        # Claude receives the method as a system append; the same policy must
        # explicitly constrain that method, including its research demands.
        system_append += "\n\n" + policy

    (pass_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    if system_append:
        (pass_dir / "system-append.md").write_text(system_append, encoding="utf-8")

    settings = build_settings(cfg, problem, harness, spec, run_dir, cwd,
                              role=(role if index > 1 or validate else ""),
                              round_no=round_no,
                              expect=expect, validate=validate)
    if harness.kind == "read":
        from .access import runtime_paths
        settings["sandbox"] = {
            "enabled": True, "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False,
            "excludedCommands": [],
            "filesystem": {
                "denyRead": ["/"],
                "allowRead": [str(p) for p in runtime_paths()] +
                             [str(cwd), str(run_dir / "loop")] +
                             ([str(run_dir / "source-excerpt.txt")]
                              if harness.metadata.get("include_proof_map") else []) +
                             ([] if role == "auditor" else
                              [str(run_dir / "skill-references")]),
                "allowWrite": [str(cwd)],
            },
            "network": {"allowedDomains": ["*"] if harness.permissions.network else [],
                        "allowLocalBinding": False, "allowUnixSockets": []},
        }
        settings["permissions"]["defaultMode"] = "dontAsk"
    (pass_dir / "settings.json").write_text(json.dumps(settings, indent=2),
                                            encoding="utf-8")

    # Every pass is a fresh session. Reusing the id would either be refused by
    # the CLI or silently resume the previous pass — and a critic that can see
    # the drafter's reasoning is checking it for internal consistency rather
    # than against the document, which is the one thing it is here not to do.
    pass_spec = spec if index == 1 else replace(
        spec, provider_session_id=str(uuid.uuid4()), resume_session_id=None)
    if completion_session_id:
        pass_spec = replace(pass_spec, resume_session_id=completion_session_id)

    adapter_problem_root = (
        cwd if harness.kind == "read" and cwd != problem.root else problem.root)
    argv, env = adapter.build_invocation(
        cfg=cfg, plan_dir=invocation_dir, cwd=cwd, spec=pass_spec, harness=harness,
        model=model, effort=effort, account=account,
        system_append_path=(pass_dir / "system-append.md") if system_append else None,
        settings_path=pass_dir / "settings.json",
        agents_path=agents_path, plugin_dir=plugin_dir,
        problem_root=adapter_problem_root, budget_usd=budget_usd,
    )
    if completion_session_id and "--output-last-message" in argv:
        argv[argv.index("--output-last-message") + 1] = str(pass_dir / "last-message.txt")
    if harness.kind == "read":
        from .access import protect_provider
        argv, env = protect_provider(
            argv, env, cwd=cwd, state=invocation_dir / "provider-state",
            readable=[run_dir],
            provider=provider, tools=True)

    (pass_dir / "argv.json").write_text(json.dumps(argv, indent=2), encoding="utf-8")
    (pass_dir / "env.json").write_text(json.dumps(_redact(env), indent=2),
                                       encoding="utf-8")
    return PassPlan(index=index, role=role, label=label, round=round_no,
                    dir=pass_dir, prompt=prompt, system_append=system_append,
                    argv=argv, env=env, budget_usd=budget_usd,
                    budget_minutes=budget_minutes,
                    budget_tokens_out=budget_tokens_out, budget_turns=budget_turns,
                    writes=list(writes), provider=provider,
                    account=account.id if account else None, model=model,
                    effort=effort)


def reading_budget_line(total: float | None, roles: list[str], role: str,
                        remaining: float | None = None, usd: float | None = None,
                        *, continuation: bool = False, revision: bool = False,
                        review: bool = False, review_extra_used: float = 0.0) -> str:
    spending = f" Shared run spending limit: ${usd:.2f}." if usd else ""
    if not total:
        return "All stages share the run budget; there is no separate stage deadline. Save progressively." + spending
    remaining = total if remaining is None else remaining
    target = stage_time_available(total, roles, roles.index(role), total - remaining,
                                  revision=revision, review=review,
                                  review_extra_used=review_extra_used)
    reserve = ""
    if len(roles) > 1:
        draft = time_targets(total, roles)[roles[0]]
        reserve = (f"Drafting stops at {draft:g} min elapsed, reserving {total - draft:g} min "
                   "for review and revision. Drafting receives 50% of T for T at most "
                   "20 min, T-10 min for 20 < T < 60, and T-20 min for T at least 60. "
                   "Unused drafting time goes to checking. ")
    kind = "continuation" if continuation else "run"
    if revision or review:
        limit = reading_run_limit(total, revision=revision, review=review,
                                  review_extra_used=review_extra_used)
        stage = "revision" if revision else "review"
        extra = (REVISION_EXTRA_MINUTES if revision else
                 max(0.0, REVIEW_EXTRA_MINUTES - review_extra_used))
        timing = (f"Overall planned {kind} time: {total:g} min; {stage} may use up to "
                  f"{extra:g} additional min beyond its planned allocation, ending by "
                  f"{limit:g} min elapsed. Approximately {max(0.0, remaining + limit - total):g} "
                  f"min remain for {stage}. ")
    else:
        timing = (f"Overall {kind} time limit: {total:g} min; "
                  f"approximately {max(0.0, remaining):g} min remain. ")
    if len(roles) > 1:
        timing += (f"Review and revision each have their own extra allowance: "
                   f"{REVIEW_EXTRA_MINUTES:g} min for review and {REVISION_EXTRA_MINUTES:g} min "
                   "for revision. Unused review grace does not transfer to revision. ")
    return (timing
            + reserve + f"Time available for this stage: about {target:g} min. "
            "Stage handoffs are enforced, without extra drafting grace time or a reset on retries. "
            "At 90% of this stage allocation, stop research and expansion and finish saving. "
            "Save useful output early and update it progressively, including review and revision records. "
            "This timing policy overrides any flexible-timing guidance in the method. "
            "Check time_remaining; do not wait for a live reminder." + spending)


def plan_writer_completion(cfg: Config, plan: RunPlan, session_id: str,
                           problems: list[str], *, stage: PassPlan | None = None,
                           attempt: int = 1) -> PassPlan:
    """Resume an unfinished writer/reviser in its existing private provider state.

    Its original run budget and accounting remain in force. This is not an
    additional review pass and never shares the writer's session with a critic.
    """
    draft = stage or plan.first
    prompt = (
        "Finish and save progress/reading/guide.md now, using the source and "
        "evidence already gathered in this conversation and working directory. "
        "Begin no new web research, downloads, or optional illustration work. "
        "Save sections as you write them. Preserve required sections and citations; "
        "state remaining limitations honestly. Never fabricate evidence. "
        "Finish progress/_session_notes.md. Leave the source unchanged. "
        "The application will generate the exports. The stage's current "
        "time allowance and the shared spending limit still apply. "
        "Prioritize the outstanding required work.\n\n"
        "Delivery checks still outstanding:\n" + "\n".join(f"- {p}" for p in problems)
    )
    if draft.role != "draft":
        prompt += (f"\nFinish progress/reading/dispositions-{draft.round}.json as well. "
                   "Keep every valid correction already made and respect this revision's write lane.")
    return _compile_pass(
        cfg, problem=plan.problem, harness=plan.harness, spec=plan.spec,
        account=cfg.account(draft.account) if draft.account else plan.account,
        provider=draft.provider or plan.spec.provider, model=draft.model or plan.model,
        effort=draft.effort or plan.effort, run_dir=plan.run_dir, cwd=plan.cwd,
        index=draft.index, role=draft.role,
        label=f"{draft.label.removesuffix(' completion')} completion", round_no=draft.round,
        prompt=prompt, system_append=draft.system_append,
        budget_usd=draft.budget_usd, budget_minutes=draft.budget_minutes,
        budget_tokens_out=draft.budget_tokens_out, budget_turns=draft.budget_turns,
        writes=draft.writes, agents_path=None, plugin_dir=None,
        expect=("progress/reading/guide.md" if draft.role == "draft" else
                f"progress/reading/dispositions-{draft.round}.json"),
        validate=("draft" if plan.harness.loop.enabled else "guide") if draft.role == "draft" else "dispose",
        completion_session_id=session_id, completion_attempt=attempt)


def role_agent(cfg: Config, base: tuple[str, Account | None, str, str],
               override: dict[str, str] | None) -> tuple[str, Account | None, str, str]:
    """(provider, account, model, effort) for one role: the run's own `base`,
    with whatever `override` names in its place.

    Choosing an account chooses a provider, as for the run itself; a provider
    named without a model starts on that provider's default model, never on
    the other provider's model id.
    """
    provider, account, model, effort = base
    if not override:
        return base
    named = cfg.account(override.get("account")) if override.get("account") else None
    new_provider = override.get("provider") or (named.provider if named else provider)
    if named is None or named.provider != new_provider:
        named = account if (account and account.provider == new_provider) \
            else cfg.default_account(new_provider)
    new_model = override.get("model") or (
        model if new_provider == provider else cfg.defaults.model_for(new_provider))
    return new_provider, named, new_model, override.get("effort") or effort


def plan_next_pass(cfg: Config, plan: RunPlan, *, role_name: str, round_no: int,
                   index: int, remaining_minutes: float | None = None,
                   files: Path | None = None) -> PassPlan | None:
    """Compile the pass that follows the one just finished.

    It cannot be compiled in advance: its prompt is built from what the previous
    pass left on disk — read from `files` when given: the controller's private
    copy of the working folder between a reading's passes (handover.py), laid
    out as the folder is. The pass itself is compiled for `plan.cwd`, where the
    AI works, as always.
    """
    harness = plan.harness
    role = harness.roles.get(role_name)
    if role is None:
        return None
    if harness.metadata.get("include_proof_map") and role_name == harness.loop.reviser:
        role = replace(role, writes=list(dict.fromkeys([
            *role.writes, *(f"progress/reading/{name}" for name in DIAGRAM_FILES)])))

    is_critic = role_name == harness.loop.critic
    if role_name == "graph-builder":
        expect, validate = "progress/reading/graph.json", "graph"
    elif role_name == "verifier-blind":
        expect, validate = (f"progress/reading/clarity-review-{round_no}.md",
                            "clarity")
    elif is_critic:
        expect, validate = f"progress/reading/review-{round_no}.json", "review-output"
    else:
        expect, validate = (f"progress/reading/dispositions-{round_no}.json",
                            "dispose")

    sequence = pass_sequence(harness)
    budgets = pass_budgets(harness, plan.budget_usd, plan.budget_minutes,
                           plan.budget_tokens_out, plan.budget_turns,
                           priced=priced_passes(cfg, harness, plan.extra.get("agents") or {}))
    usd, minutes, tokens, turns = (budgets[index - 1] if index - 1 < len(budgets)
                                   else (None, None, None, None))

    caps = [f"${usd:.2f}"] if usd else []
    if minutes:
        caps.append(f"{minutes:g} min")
    budget_line = (
        f"Budget for this pass: {' / '.join(caps)}. Save work progressively and "
        "finish within this allocation; do not wait for a live reminder."
        if caps else "Budget: no hard cap set for this pass."
    )
    if harness.kind == "read":
        minutes = reading_run_limit(plan.budget_minutes,
            revision=role_name == harness.loop.reviser, review=is_critic,
            review_extra_used=plan.extra.get("review_extra_minutes", 0.0))
        budget_line = reading_budget_line(plan.budget_minutes,
            [r for r, _, _ in sequence], role_name, remaining_minutes, plan.budget_usd,
            continuation=plan.spec.mode == "continue",
            revision=role_name == harness.loop.reviser, review=is_critic,
            review_extra_used=plan.extra.get("review_extra_minutes", 0.0))

    document = Path(plan.extra["document"]) if plan.extra.get("document") else None
    if files is not None and document is not None and document.is_relative_to(plan.cwd):
        document = files / document.relative_to(plan.cwd)
    prompt = build_role_prompt(
        cfg, plan.problem, harness, plan.spec, role, cwd=files or plan.cwd,
        index=index, total=len(sequence), round_no=round_no,
        budget_line=budget_line, expect=expect,
        document=document,
        source_excerpt=(Path(plan.extra["source_excerpt"])
                        if plan.extra.get("source_excerpt") else None),
        source_scope=plan.extra.get("source_scope", ""))
    if harness.kind == "read" and role_name == harness.loop.reviser:
        record = plan.run_dir / "loop.json"
        if record.is_file():
            outcomes = json.loads(record.read_text(encoding="utf-8")).get("passes", [])
            repairs = outcomes[-1].get("repair_tasks", []) if outcomes else []
            if repairs:
                prompt += _fence("controller_repair_tasks", "\n".join(f"- {p}" for p in repairs))
    role_document = (Path(plan.extra["document"])
                     if plan.extra.get("document") else None)
    system_append = build_role_system_append(
        cfg, harness, role, lane=plan.run_dir / "loop" / "lane",
        document=role_document, skill_directory=plan.run_dir / "skill-references")
    if plan.extra.get("source_scope"):
        system_append += "\n\n" + plan.extra["source_scope"]

    label = next((lab for r, lab, rd in sequence
                  if r == role_name and rd == round_no), role_name)
    # This role's AI, as planned when the run began (`role_agents`); a plan
    # made without one runs the role as every pass ran before there was a
    # choice: the run's own AI, at the role's own effort if it has one.
    chosen = (plan.extra.get("agents") or {}).get(role_name) or {}
    provider = chosen.get("provider") or plan.spec.provider
    account = cfg.account(chosen.get("account")) if chosen.get("account") else plan.account
    if account is not None and account.provider != provider:
        account = cfg.default_account(provider)
    return _compile_pass(
        cfg, problem=plan.problem, harness=harness, spec=plan.spec,
        account=account, provider=provider, model=chosen.get("model") or plan.model,
        effort=chosen.get("effort") or role.effort or plan.effort,
        run_dir=plan.run_dir, cwd=plan.cwd,
        index=index, role=role_name, label=label, round_no=round_no,
        prompt=prompt, system_append=system_append,
        budget_usd=usd, budget_minutes=minutes,
        budget_tokens_out=tokens, budget_turns=turns,
        # A round owns its own files and no others. Left as `review-*.json`,
        # the second critique's lane covered the first round's review — the
        # record it is specifically asked to check the earlier answers against,
        # and the one thing in the loop that must not be quietly editable.
        writes=[w.replace("{round}", str(round_no)) for w in role.writes],
        agents_path=None, plugin_dir=None, expect=expect, validate=validate)


def plan_run(cfg: Config, problem: Problem, harness: Harness, spec: RunSpec,
             *, session_number: int = 1,
             isolate_reading: bool = False,
             source_char_limit: int | None = None) -> RunPlan:
    run_dir = cfg.runs_dir / spec.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if harness.kind == "read":
        from .access import check_job_tree
        check_job_tree(problem.root)

    # A run may ask for fewer rounds of review than the harness defaults to, or
    # none at all. The harness is replaced for this plan rather than mutated:
    # the same object is handed to other runs.
    if harness.loop.enabled:
        rounds = harness.loop.rounds
        if spec.loop_rounds is not None:
            rounds = max(0, int(spec.loop_rounds))
        if spec.mode == "oneshot":
            # A one-shot is a single blind attempt by definition, and that is
            # what it is there to measure. Leaving the loop on would also have
            # given the one pass that runs a fraction of the budget and spent
            # the rest on passes that never happen.
            rounds = 0
        if rounds != harness.loop.rounds:
            harness = replace(harness, loop=replace(
                harness.loop, enabled=rounds > 0, rounds=rounds))

    # -- working directory ------------------------------------------------
    isolated_reading = bool(
        isolate_reading and harness.kind == "read" and spec.mode != "oneshot")
    if spec.mode == "oneshot":
        cwd = run_dir / "workspace"
        snapshot_static(problem, cwd, include_work=spec.include_work_in_oneshot)
    elif isolated_reading:
        from .problems import snapshot_problem

        cwd = run_dir / "workspace"
        if spec.mode == "continue" and spec.parent_run_id:
            from .delivery import copy_reading_work
            copy_reading_work(cfg.runs_dir, spec.parent_run_id, problem.slug, cwd)
        else:
            snapshot_problem(problem, cwd)
    else:
        problem.ensure_scaffold()
        cwd = problem.root

    document: Path | None = None
    document_text = ""
    document_label = ""
    if harness.kind == "read":
        from deslopify.source import read_source, select_source

        original = select_source(problem.root, spec.task_files)
        document_label = _relative_to(original, problem.root)
        if cwd != problem.root:
            document = cwd / document_label
            if spec.mode == "oneshot":
                document.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, document)
        else:
            document = original
        document_text = read_source(document)

    if harness.metadata.get("include_proof_map"):
        reading = cwd / "progress" / "reading"
        if spec.mode != "continue" or not diagram_required(reading, True):
            for name in DIAGRAM_FILES:
                (reading / name).unlink(missing_ok=True)

    # -- model / effort / account ----------------------------------------
    # Choosing an account chooses a provider: picking the Codex account and
    # getting a Claude session would be nonsense, and the env vars of one would
    # be handed to the other. The provider then chooses the default model.
    chosen = cfg.account(spec.account or harness.defaults.account)
    provider = (spec.provider or (chosen.provider if chosen else "")
                or harness.defaults.provider or cfg.defaults.provider)
    account = chosen if chosen and chosen.provider == provider \
        else cfg.default_account(provider)

    model = (spec.model or harness.defaults.model
             or cfg.defaults.model_for(provider))
    effort = spec.effort or harness.defaults.effort or cfg.defaults.effort

    # The first pass may itself be given to another AI; the run is then that
    # AI's, and the other passes start from it.
    if spec.role_agents.get("draft"):
        provider, account, model, effort = role_agent(
            cfg, (provider, account, model, effort), spec.role_agents["draft"])

    # Write the resolution back: everything downstream — the runner picking an
    # adapter, the lab book, the run record — reads it from the spec, and an
    # unresolved provider there means no adapter at all.
    spec.provider = provider
    spec.account = account.id if account else spec.account

    from .providers import get_adapter as _adapter_for

    # -- budgets ----------------------------------------------------------
    b = harness.budget
    budget_usd = spec.max_usd if spec.max_usd is not None else b.max_usd
    budget_min = spec.max_minutes if spec.max_minutes is not None else b.max_minutes
    budget_tok = spec.max_tokens_out if spec.max_tokens_out is not None else b.max_tokens_out
    budget_turns = spec.max_turns if spec.max_turns is not None else b.max_turns

    # Every pass's AI, decided now: each role of the loop runs on the run's own
    # AI unless `role_agents` gives it another. Kept in the plan (for the passes
    # compiled later) and in the run's record (for whoever reads it after).
    agents = {"draft": {"provider": provider, "account": account.id if account else None,
                        "model": model, "effort": effort}}
    for role_name, _, _ in pass_sequence(harness)[1:]:
        if role_name in agents:
            continue
        p2, a2, m2, e2 = role_agent(cfg, (provider, account, model, effort),
                                    spec.role_agents.get(role_name))
        role = harness.roles.get(role_name)
        explicit = (spec.role_agents.get(role_name) or {}).get("effort")
        agents[role_name] = {"provider": p2, "account": a2.id if a2 else None,
                             "model": m2,
                             "effort": explicit or (role.effort if role else "") or e2}

    # A dollar cap can only bind if the model has a price. On a ChatGPT or Claude
    # subscription tokens are not billed per token at all, and an unpriced model
    # reports a cost of zero — so a session given only a dollar cap would run
    # with no limit whatsoever. Fall back to a wall-clock cap rather than let
    # that happen silently. With several AIs the cap still binds on the passes
    # that are priced — the expensive ones, usually — and the unpriced ones are
    # held to time like every pass; only when none is priced is it dropped.
    priced = [cfg.prices.lookup(a["model"]) is not None for a in agents.values()]
    if not any(priced):
        # The dollar cap is dropped rather than displayed: it can never trigger,
        # and a limit that looks real but cannot fire is worse than none.
        budget_usd = None
        if not (budget_min or budget_tok or budget_turns):
            budget_min = b.max_minutes or 30.0
    elif not all(priced) and not (budget_min or budget_tok or budget_turns):
        # The passes with no price have only time to stop them.
        budget_min = b.max_minutes or 30.0

    # Reading stages share provider caps; the controller enforces their handoffs.
    # Other harnesses retain their allocated per-pass budgets.
    draft_usd, draft_min, draft_tok, draft_turns = pass_budgets(
        harness, budget_usd, budget_min, budget_tok, budget_turns,
        priced=priced_passes(cfg, harness, agents))[0]
    looped = harness.loop.enabled

    caps = []
    if (shown_usd := draft_usd if looped else budget_usd):
        caps.append(f"${shown_usd:.2f}")
    if (shown_min := draft_min if looped else budget_min):
        caps.append(f"{shown_min:g} min")
    if (shown_tok := draft_tok if looped else budget_tok):
        caps.append(f"{shown_tok:,} output tokens")
    budget_line = (
        f"Budget{' for this first pass' if looped else ''}: {' / '.join(caps)}. "
        "Save work progressively and finish within this allocation; "
        "do not wait for a live reminder."
        if caps else "Budget: no hard cap set for this session."
    )
    if looped:
        total = f"${budget_usd:.2f}" if budget_usd else f"{budget_min:g} min"
        budget_line += (
            f" This session then continues with further passes that read and "
            f"revise what you produce; {total} covers all of them together.")
    if harness.kind == "read":
        budget_line = reading_budget_line(budget_min,
            [r for r, _, _ in pass_sequence(harness)], "draft", usd=budget_usd,
            continuation=spec.mode == "continue")

    # -- text artefacts ---------------------------------------------------
    # Snapshot the on-demand methods once. Later passes use these files even
    # if the shipped or locally edited method changes while the run is active.
    if harness.skill_references:
        skill_directory = run_dir / "skill-references"
        skill_directory.mkdir(parents=True, exist_ok=True)
        for name, body in harness.skill_references.items():
            body = body.replace("{deslopify}", deslopify_command())
            body = body.replace("{document}", str(document) if document else "SOURCE_DOCUMENT")
            body = body.replace("{skill_directory}", str(skill_directory))
            (skill_directory / name).write_text(body, encoding="utf-8")
        if (harness.variant or "").startswith("multi-"):
            (skill_directory / "collection-method.md").write_text(
                harness.skill.replace("{skill_directory}", str(skill_directory))
                .replace("{document}", str(document) if document else "SOURCE_DOCUMENT"),
                encoding="utf-8")
    prompt = build_prompt(cfg, problem, harness, spec,
                          session_number=session_number, budget_line=budget_line,
                          provider_caps=_adapter_for(provider).capabilities,
                          document=document, document_text=document_text,
                          document_label=document_label,
                          isolated_workspace=isolated_reading)
    if harness.kind == "read":
        prompt += "\n\n" + PROGRESSIVE_GUIDE
        prompt += _fence("latex_requirements", LATEX_GUIDANCE)
        if spec.mode == "continue" and spec.parent_run_id:
            reading = cwd / "progress" / "reading"
            prompt += _fence("continue_saved_draft",
                "Continue the existing guide rather than starting over. The previous run's "
                "workspace, research in work/, figures, and references are preserved here. "
                "Read work/continuation-notes.md and work/previous-review/ first. "
                "Finish missing sections and known corrections before adding anything else; "
                "reuse gathered evidence and avoid repeating literature research. "
                "The selected time allowance is additional time for this continuation.\n\n"
                + "\n".join(f"- {p}" for p in (
                    guide_problems(reading, harness.contract)
                    + export_problems(reading, harness.contract))))
    system_append = build_system_append(
        cfg, problem, harness, spec, document, document_label)

    source_scope = ""
    source_excerpt: Path | None = None
    if harness.kind == "read" and document is not None and document_text:
        # Codex rejects turn/start above this many characters. Other reader
        # models supply their effective limit through the app. Keep room for
        # later review prompts, which also contain the completed companion.
        limit = source_char_limit or (1_048_576 - 16_384 if provider == "codex" else None)
        if limit and len(prompt) + len(system_append) > limit:
            from deslopify.source import SourceError, beginning_within

            # Providers without a separate system channel copy the method
            # into the invocation prompt. The access policy is added later as
            # well, so size against those bytes too.
            reserve = max(16_384, limit // 5,
                          len(system_append) + 16_384)
            budget = len(document_text) - (len(prompt) + len(system_append) - limit) - reserve
            if budget <= 0:
                raise SourceError("The reader AI cannot hold the method and the beginning of this source.")
            excerpt, omitted = beginning_within(
                document_text, budget,
                collection=(harness.variant or "").startswith("multi-"))
            old = _fence("source_document", document_text, file=document_label,
                         format=document.suffix.lower().lstrip("."))
            new = _fence("source_document", excerpt, file=document_label,
                         format=document.suffix.lower().lstrip("."))
            if old not in prompt:
                raise SourceError("Could not locate the source text in the reader's prompt.")
            prompt = prompt.replace(old, new, 1)
            names = ", ".join(omitted)
            source_scope = (
                "The source text exceeded the selected reader AI's input limit. "
                f"Only the beginning was supplied for: {names}. Treat the supplied "
                "text as the complete available evidence for those papers; do not "
                "read later pages from the PDFs or full source file. In the companion, "
                "state plainly within each affected paper's Main results or Main "
                "findings that the source text was too long and this companion is "
                "based only on the beginning of that paper. Do not make claims "
                "about unseen results, proofs, or bibliography. Preserve this notice "
                "through review and revision. Unaffected papers may be read in full."
            )
            prompt += "\n\n" + _fence("source_scope", source_scope)
            system_append += "\n\n" + source_scope
            if len(prompt) + len(system_append) > limit:
                raise SourceError("The reader AI cannot hold the beginning of every source paper.")
            source_excerpt = run_dir / "source-excerpt.txt"
            source_excerpt.write_text(excerpt, encoding="utf-8")
            starts = list(re.finditer(r"^## Paper (\d+) of \d+: ", excerpt, re.MULTILINE))
            omitted_indices = [int(match.group(1)) for i, match in enumerate(starts)
                               if "[The rest of this paper was omitted" in excerpt[
                                   match.start():starts[i + 1].start()
                                   if i + 1 < len(starts) else len(excerpt)]]
            (run_dir / "source-scope.json").write_text(json.dumps({
                "collection": (harness.variant or "").startswith("multi-"),
                "omitted": omitted,
                "omitted_indices": omitted_indices,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    if harness.metadata.get("include_proof_map") and source_excerpt is None:
        source_excerpt = run_dir / "source-excerpt.txt"
        source_excerpt.write_text(document_text, encoding="utf-8")

    # A reading session must leave the document as it found it. Record its
    # hash and a copy now, before a pass can change it; the controller uses
    # the copy to restore the source between passes.
    if harness.kind == "read":
        import hashlib

        if document is not None and document.is_file():
            (run_dir / "document.json").write_text(json.dumps({
                "path": str(document),
                "sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
                "bytes": document.stat().st_size,
            }, indent=2), encoding="utf-8")
            shutil.copy2(document, run_dir / "document.orig")

    agents_json = {name: a.to_cli() for name, a in harness.agents.items()}
    if agents_json:
        (run_dir / "agents.json").write_text(json.dumps(agents_json, indent=2), encoding="utf-8")

    plugin_dir = run_dir / "plugin"
    has_plugin = build_plugin(cfg, harness, plugin_dir)

    # -- provider invocation ---------------------------------------------
    # The draft is pass one. A harness with no loop has no others, and takes
    # exactly the path it always did.
    draft = _compile_pass(
        cfg, problem=problem, harness=harness, spec=spec, account=account,
        provider=provider, model=model, effort=effort, run_dir=run_dir, cwd=cwd,
        index=1, role="draft", label="draft", round_no=0,
        prompt=prompt, system_append=system_append,
        budget_usd=draft_usd if harness.loop.enabled else budget_usd,
        budget_minutes=draft_min if harness.loop.enabled else budget_min,
        budget_tokens_out=draft_tok if harness.loop.enabled else budget_tok,
        budget_turns=draft_turns if harness.loop.enabled else budget_turns,
        writes=[], agents_path=(run_dir / "agents.json") if agents_json else None,
        plugin_dir=plugin_dir if has_plugin else None,
        expect=("progress/reading/guide.md"
                if harness.loop.enabled and harness.loop.sequence else ""),
        validate=("draft"
                  if harness.loop.enabled and harness.loop.sequence else ""),
    )

    extra: dict[str, Any] = {"isolated_reading": isolated_reading, "agents": agents}
    if document is not None:
        extra["document"] = str(document)
    if source_excerpt is not None:
        extra["source_excerpt"] = str(source_excerpt)
        extra["source_scope"] = source_scope

    plan = RunPlan(
        spec=spec, problem=problem, harness=harness, account=account,
        run_dir=run_dir, cwd=cwd, passes=[draft],
        model=model, effort=effort,
        budget_usd=budget_usd, budget_minutes=budget_min,
        budget_tokens_out=budget_tok, budget_turns=budget_turns,
        extra=extra,
    )
    if (spec.mode == "continue" and spec.parent_run_id and harness.loop.sequence
            and not guide_problems(cwd / "progress" / "reading", harness.contract)
            and not diagram_problems(cwd / "progress" / "reading",
                                     bool(harness.metadata.get("include_proof_map")),
                                     source_excerpt.read_text() if source_excerpt else document_text)):
        # A complete text needs checking, not another writing pass. The audit
        # gets a fresh context; the old workspace remains untouched.
        role_name = harness.loop.sequence[0]
        checking = plan_next_pass(cfg, plan, role_name=role_name, round_no=1, index=2,
                                  remaining_minutes=budget_min)
        if checking is not None:
            plan.passes = [checking]
            extra["continued_complete_draft"] = True

    (run_dir / "config.json").write_text(json.dumps({
        "run_id": spec.run_id,
        "spec": spec.to_dict(),
        "resolved": {
            "problem": problem.slug, "problem_root": str(problem.root),
            "document": str(document) if document is not None else None,
            "harness": harness.id, "harness_version": harness.version,
            "runner": harness.runner, "provider": provider,
            "account": account.id if account else None,
            "model": model, "effort": effort, "cwd": str(cwd),
            "include_proof_map": bool(harness.metadata.get("include_proof_map")),
            # Which AI runs each pass; the same throughout unless the run
            # asked otherwise (RunSpec.role_agents).
            "agents": agents,
            "budget": {"usd": budget_usd, "minutes": budget_min,
                       "tokens_out": budget_tok, "turns": budget_turns,
                       "wrapup_at": b.wrapup_at, "grace_seconds": b.grace_seconds},
            "loop": ({"rounds": harness.loop.rounds,
                      "continue_at": harness.loop.continue_at,
                      "passes": [{"role": r, "label": lab, "round": rd}
                                 for r, lab, rd in pass_sequence(harness)]}
                     if harness.loop.enabled else None),
        },
    }, indent=2), encoding="utf-8")

    return plan


def next_session_number(cfg: Config, problem: Problem) -> int:
    from .db import get_db

    row = get_db(cfg.db_path).one(
        "SELECT COUNT(*) AS n FROM runs WHERE problem = ? AND mode != 'oneshot'",
        (problem.slug,),
    )
    return int(row["n"] if row else 0) + 1
