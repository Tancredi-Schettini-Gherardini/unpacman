"""The critique loop: draft, then read adversarially, then revise.

A companion to a hard document is better for having been read by someone who did
not write it. That is the whole of the idea, and it is a good one. The way it
usually gets built is not.

Ask a model to judge a piece of writing and it judges the writing — clarity,
organisation, confidence, flow. Those are exactly the qualities machine-written
mathematics already has in excess, and a revision aimed at them returns a guide
that reads better and claims the same things, now with a reviewer's endorsement
attached. Run that loop on a document full of holes and it does not merely fail
to help: it manufactures the one artefact this harness exists to prevent, and
launders it.

So the mechanism here is built around three refusals.

**A finding must be locatable.** `where` resolves against a node, an edge, a
heading of the guide, a line of the document or an adjudication — or it is not
a finding. "The middle drags" cannot be fixed, cannot be refused on grounds, and
cannot be shown to have been dealt with; it can only be complied with.

**A critic may not supply prose.** Whatever it writes gets pasted, and the round
becomes the critic drafting the guide with an extra step — its errors now
carrying the authority of review. It states the obligation; someone who has read
the document decides how to discharge it.

**A pass may write only its own files.** Everything else is restored from the
snapshot taken before it ran. Without that, the critic edits the guide directly
and the loop quietly collapses into two drafts.

What is left is measured rather than trusted. Between rounds the graph is
compared with the graph before it, and changes to what the companion *claims* —
a node, an edge, a quotation, a verdict — are counted apart from changes to how
it reads. A round that raised findings and moved nothing structural polished,
and the record says so in those words.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .harnesses import Harness, Role
from .delivery import DIAGRAM_FILES, diagram_problems, diagram_required

DRAFT = "draft"

# Lanes are enforced over `progress/reading/` and nothing else — see
# `restore_outside_lane`. That is where the companion lives and what the roles'
# `writes` patterns describe; the document itself is protected separately, by
# hash and by restore. A pass that writes somewhere else entirely is not
# silently undone: it shows up in the session's file changes and in git, where a
# human will see it, which is the right place for something nobody planned for.


# --------------------------------------------------------------------------
# what happened in one pass
# --------------------------------------------------------------------------


@dataclass(slots=True)
class PassOutcome:
    """The controller's reading of a finished pass."""
    index: int
    role: str
    round: int
    notes: list[str] = field(default_factory=list)      # for the run record
    problems: list[str] = field(default_factory=list)   # contract failures
    findings: int = 0
    blocking: int = 0
    carried: int = 0                 # findings at or above the continue threshold
    diff: dict[str, Any] | None = None
    summary: str = ""
    stop: str = ""                   # non-empty ends the loop, and says why
    completed: bool = False
    continued_from: str = ""
    repair_tasks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "role": self.role, "round": self.round,
            "notes": self.notes, "problems": self.problems,
            "findings": self.findings, "blocking": self.blocking,
            "carried": self.carried, "diff": self.diff,
            "summary": self.summary, "stop": self.stop, "completed": self.completed,
            "continued_from": self.continued_from, "repair_tasks": self.repair_tasks,
        }


@dataclass(slots=True)
class LoopState:
    """Everything the controller remembers between passes of one run."""
    harness: Harness
    cwd: Path
    run_dir: Path
    document: Path
    rounds: int
    continue_at: str
    outcomes: list[PassOutcome] = field(default_factory=list)
    finished: str = ""               # why the loop ended
    completed: bool = False
    next_role: str = ""              # role of the pass to run next
    next_round: int = 0

    @property
    def store(self) -> Path:
        return self.run_dir / "loop"

    @property
    def reading(self) -> Path:
        return self.cwd / "progress" / "reading"

    def snapshot_dir(self, round_no: int) -> Path:
        return self.store / f"round-{round_no}"

    def pending(self) -> tuple[str, int] | None:
        """The role and round of the next pass, or None if the loop is done."""
        if self.finished or not self.next_role:
            return None
        return self.next_role, self.next_round

    def carried_at(self, round_no: int) -> int:
        """How many findings the critique of `round_no` asked to be acted on."""
        for outcome in self.outcomes:
            if outcome.round == round_no and outcome.role == self.harness.loop.critic:
                return outcome.carried
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "rounds_configured": self.rounds,
            "continue_at": self.continue_at,
            "finished": self.finished,
            "completed": self.completed,
            "passes": [o.as_dict() for o in self.outcomes],
        }


def start(harness: Harness, cwd: Path, run_dir: Path, document: Path) -> LoopState:
    state = LoopState(
        harness=harness, cwd=cwd, run_dir=run_dir, document=document,
        rounds=harness.loop.rounds, continue_at=harness.loop.continue_at,
    )
    state.store.mkdir(parents=True, exist_ok=True)
    return state


def inherit_draft(state: LoopState, parent_run_id: str) -> None:
    state.outcomes.append(PassOutcome(index=1, role=DRAFT, round=0, completed=True,
        summary="Continued the saved complete draft.", continued_from=parent_run_id))
    take_snapshot(state, 0)
    take_lane_snapshot(state)
    _write_state(state)


# --------------------------------------------------------------------------
# keeping a pass inside its own lane
# --------------------------------------------------------------------------


def _copy_reading(state: LoopState, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    if state.reading.is_dir():
        shutil.copytree(state.reading, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)


def take_snapshot(state: LoopState, round_no: int) -> None:
    """The companion as one round left it. Used to measure the next round."""
    _copy_reading(state, state.snapshot_dir(round_no))


def take_lane_snapshot(state: LoopState) -> None:
    """The companion as the pass about to run will find it.

    Distinct from the round snapshot, and the distinction was not obvious: a
    round snapshot predates the critique, so using it as the reviser's lane
    would have deleted the review file the reviser exists to answer — the
    critique would vanish at exactly the moment it was to be acted on.
    """
    _copy_reading(state, state.store / "lane")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _may_write(rel: str, writes: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in writes)


def restore_outside_lane(state: LoopState, writes: list[str],
                         snapshot: Path) -> list[str]:
    """Undo everything a pass changed that was not its to change.

    The critic is told not to touch the guide. Telling is not enough — not
    because a model disobeys, but because editing a file it is holding open is
    the most natural thing in the world once it has an opinion about it. The
    snapshot makes the instruction true rather than merely stated.
    """
    if not snapshot.is_dir():
        return []
    undone: list[str] = []

    for original in sorted(snapshot.rglob("*")):
        if not original.is_file():
            continue
        rel_in_reading = original.relative_to(snapshot).as_posix()
        live = state.reading / rel_in_reading
        rel = _relative(live, state.cwd)
        if _may_write(rel, writes):
            continue
        try:
            if not live.is_file():
                live.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, live)
                undone.append(f"{rel} (deleted; restored)")
            elif live.read_bytes() != original.read_bytes():
                shutil.copy2(original, live)
                undone.append(f"{rel} (modified; restored)")
        except OSError as exc:
            undone.append(f"{rel} (could not be restored: {exc})")

    if state.reading.is_dir():
        for live in sorted(state.reading.rglob("*")):
            if not live.is_file():
                continue
            rel_in_reading = live.relative_to(state.reading).as_posix()
            if (snapshot / rel_in_reading).is_file():
                continue                      # handled above
            rel = _relative(live, state.cwd)
            if _may_write(rel, writes):
                continue
            try:
                live.unlink()
                undone.append(f"{rel} (created; removed)")
            except OSError as exc:
                undone.append(f"{rel} (could not be removed: {exc})")
    return undone


def restore_document(state: LoopState) -> str:
    """Put the document back if a pass changed it. Returns a note, or ''.

    The controller restores the source after any pass that changed it, so
    subsequent passes review the same document that will be delivered.
    """
    original = state.run_dir / "document.orig"
    if not original.is_file() or not state.document.is_file():
        return ""
    try:
        if state.document.read_bytes() == original.read_bytes():
            return ""
        shutil.copy2(original, state.document)
    except OSError as exc:
        return f"the document was modified and could not be restored: {exc}"
    return (f"{state.document.name} was modified during this pass and has been "
            f"restored — a reading session must leave the document exactly as it "
            f"found it, and every quotation is checked against the original")


def stash_notes(state: LoopState, index: int) -> None:
    """Move this pass's session notes aside so the next pass starts clean.

    They are put back, concatenated, before the lab book is written: one entry
    per run, with each pass's account of itself inside it.
    """
    notes = state.cwd / "progress" / "_session_notes.md"
    if not notes.is_file():
        return
    text = notes.read_text(encoding="utf-8", errors="replace").strip()
    if text:
        (state.store / f"notes-{index}.md").write_text(text + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        notes.unlink()


def collect_notes(state: LoopState, labels: dict[int, str]) -> None:
    """Reassemble every pass's notes into the file the lab book reads."""
    notes = state.cwd / "progress" / "_session_notes.md"
    parts: list[str] = []
    for path in sorted(state.store.glob("notes-*.md"),
                       key=lambda p: int(p.stem.split("-")[1])):
        index = int(path.stem.split("-")[1])
        body = path.read_text(encoding="utf-8", errors="replace").strip()
        if body:
            parts.append(f"### {labels.get(index, f'pass {index}')}\n\n{body}")
    if notes.is_file():
        body = notes.read_text(encoding="utf-8", errors="replace").strip()
        if body:
            last = max(labels) if labels else 0
            parts.append(f"### {labels.get(last, f'pass {last}')}\n\n{body}")
    if parts:
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# reading the artefacts
# --------------------------------------------------------------------------


def review_path(state: LoopState, round_no: int) -> Path:
    return state.reading / f"review-{round_no}.json"


def dispositions_path(state: LoopState, round_no: int) -> Path:
    return state.reading / f"dispositions-{round_no}.json"


def _document_text(state: LoopState) -> str | None:
    excerpt = state.run_dir / "source-excerpt.txt"
    if excerpt.is_file():
        return excerpt.read_text(encoding="utf-8", errors="replace")
    if state.document.is_file():
        from deslopify.source import SourceError, read_source
        try:
            return read_source(state.document)
        except SourceError:
            return None
    return None


def _guide_text(state: LoopState) -> str | None:
    guide = state.reading / "guide.md"
    if guide.is_file():
        return guide.read_text(encoding="utf-8", errors="replace")
    return None


def _load_graph(path: Path):
    from deslopify.graph import Graph, GraphError

    try:
        return Graph.load(path), ""
    except GraphError as exc:
        return None, str(exc)


def _next_pipeline_role(state: LoopState, current: str) -> str:
    sequence = state.harness.loop.sequence
    if current == DRAFT:
        return sequence[0] if sequence else ""
    try:
        at = sequence.index(current)
    except ValueError:
        return ""
    return sequence[at + 1] if at + 1 < len(sequence) else ""


# --------------------------------------------------------------------------
# the decision after each pass
# --------------------------------------------------------------------------


def assess(state: LoopState, index: int, role_name: str, round_no: int,
           role: Role | None, *, ok: bool,
           writes: list[str] | None = None) -> PassOutcome:
    """Inspect what a pass left behind and decide whether the loop goes on.

    `writes` is the pass's lane with its round already substituted in. It comes
    from the compiled pass rather than from the role, because the role's pattern
    is a template: a role that owned `review-*.json` would have had the second
    round's critique free to edit the first round's review.
    """
    outcome = PassOutcome(index=index, role=role_name, round=round_no)

    if (note := restore_document(state)):
        outcome.notes.append(note)

    # A non-draft pass may write only its own files; the snapshot taken at the
    # end of the previous round is what everything else is restored from.
    if role is not None and role_name != DRAFT:
        lane = writes if writes is not None else [
            w.replace("{round}", str(round_no)) for w in role.writes]
        for undone in restore_outside_lane(state, lane, state.store / "lane"):
            outcome.notes.append(f"restored outside this pass's lane: {undone}")

    stash_notes(state, index)

    if not ok:
        outcome.stop = "the pass did not complete, so there is nothing to review"
    elif role_name == DRAFT:
        _assess_draft(state, outcome)
    elif role_name == "graph-builder":
        _assess_graph_builder(state, outcome)
    elif role_name == "verifier-blind":
        _assess_verifier_blind(state, outcome)
    elif role_name == state.harness.loop.critic:
        if role_name == "auditor":
            # A failed audit-time PDF build is itself something the reviser can
            # repair.  Keep the persistent Markdown/LaTeX requirements here,
            # but reserve the hard PDF gate for the final balanced revision.
            _assess_balanced_artifacts(
                state, outcome, require_pdf=False)
            # Export or optional-map defects are repair obligations, not a
            # reason to discard an otherwise usable independent audit.
            outcome.repair_tasks.extend(outcome.problems)
            outcome.problems.clear()
            if outcome.stop:
                outcome.repair_tasks.append(outcome.stop)
                outcome.stop = ""
        if not outcome.stop:
            _assess_critique(state, outcome)
    else:
        if role_name == "balanced-reviser":
            _assess_balanced_artifacts(state, outcome)
        if not outcome.stop:
            _assess_revision(state, outcome)

    outcome.completed = ok and not outcome.problems and (not outcome.stop or state.completed)
    state.outcomes.append(outcome)
    if outcome.stop:
        state.finished = outcome.stop
        state.next_role, state.next_round = "", 0
    elif state.pending():
        # Freeze what the next pass will find, so that whatever it changes
        # outside its own files can be put back exactly.
        take_lane_snapshot(state)
    _write_state(state)
    return outcome


def _assess_draft(state: LoopState, outcome: PassOutcome) -> None:
    if state.harness.loop.sequence:
        guide = state.reading / "guide.md"
        if not guide.is_file() or not guide.read_text(
                encoding="utf-8", errors="replace").strip():
            outcome.stop = "the guide-writer produced no guide for the next independent pass"
            return
        from .delivery import guide_problems
        outcome.problems.extend(guide_problems(state.reading, state.harness.contract))
        # A new guide must not be paired with derived files left by an earlier
        # run. Historical copies already live under runs/; clear only generated
        # reading artefacts so the independent stages must create fresh ones.
        stale_names = {
            "summary.md", "summary.pdf", "graph.json", "map.mmd", "map.pdf",
            "companion.md", "companion.tex", "companion.pdf", "review-log.md",
        }
        requested = bool(state.harness.metadata.get("include_proof_map"))
        if diagram_required(state.reading, requested):
            stale_names.difference_update(DIAGRAM_FILES)
        for path in state.reading.iterdir():
            if (path.name in stale_names
                    or path.match("clarity-review-*.md")
                    or path.match("review-*.json")
                    or path.match("dispositions-*.json")):
                with contextlib.suppress(OSError):
                    path.unlink()
        outcome.problems.extend(diagram_problems(state.reading, requested, _document_text(state)))
        take_snapshot(state, 0)
        outcome.summary = ("a draft was saved; unfinished output will be checked and completed"
                           if outcome.problems else "the reader-first guide was written")
        outcome.repair_tasks.extend(outcome.problems)
        state.next_role = _next_pipeline_role(state, DRAFT)
        state.next_round = 1
        return

    summary_path = state.reading / "summary.md"
    if not summary_path.is_file() or not summary_path.read_text(
            encoding="utf-8", errors="replace").strip():
        outcome.stop = ("the draft produced no summary, so its reading of the "
                        "paper was not recorded before graph construction")
        return
    summary_pdf = state.reading / "summary.pdf"
    if not summary_pdf.is_file() or not summary_pdf.read_bytes().startswith(b"%PDF-"):
        outcome.stop = ("the draft produced no valid summary.pdf before graph "
                        "construction")
        return
    graph_path = state.reading / "graph.json"
    if not graph_path.is_file():
        outcome.stop = ("the draft produced no graph, so there is nothing for a "
                        "second reader to check against the document")
        return
    graph, error = _load_graph(graph_path)
    if graph is None:
        outcome.problems.append(f"graph.json could not be read: {error}")
        outcome.stop = "the draft's graph could not be read, so the loop cannot continue"
        return
    summary_text = summary_path.read_text(encoding="utf-8", errors="replace")
    guide_path = state.reading / "guide.md"
    if not guide_path.is_file():
        outcome.stop = "the draft produced no guide, so there is nothing to review"
        return
    pdf = state.reading / "map.pdf"
    if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        outcome.stop = ("the draft produced no valid map.pdf, so the graph is not "
                        "directly viewable")
        return
    companion = state.reading / "companion.md"
    expected = (summary_text.strip() + "\n\n---\n\n"
                + guide_path.read_text(encoding="utf-8", errors="replace").strip()
                + "\n")
    if not companion.is_file() or companion.read_text(
            encoding="utf-8", errors="replace") != expected:
        outcome.stop = ("the draft produced no current companion.md containing "
                        "the summary followed by the guide")
        return
    companion_pdf = state.reading / "companion.pdf"
    if not companion_pdf.is_file() or not companion_pdf.read_bytes().startswith(b"%PDF-"):
        outcome.stop = ("the draft produced no valid companion.pdf containing "
                        "the summary and guide")
        return

    take_snapshot(state, 0)
    outcome.summary = (f"{len(graph.nodes)} nodes, {len(graph.edges)} edges, "
                       f"{len(graph.of_type('!'))} gap(s) recorded")
    state.next_role, state.next_round = state.harness.loop.critic, 1


def _assess_graph_builder(state: LoopState, outcome: PassOutcome) -> None:
    from deslopify.graph import validate

    graph_path = state.reading / "graph.json"
    graph, error = _load_graph(graph_path)
    if graph is None:
        outcome.problems.append(f"graph.json could not be read: {error}")
        outcome.stop = "the graph-builder produced no usable graph"
        return
    errors = [p for p in validate(graph, _document_text(state))
              if p.severity == "error"]
    if errors:
        outcome.problems.extend(str(p) for p in errors)
        outcome.stop = "the graph-builder's graph did not validate"
        return
    for name in ("map.mmd", "map.pdf", "companion.md", "companion.tex",
                 "companion.pdf"):
        path = state.reading / name
        if not path.is_file() or (name.endswith(".pdf")
                                  and not path.read_bytes().startswith(b"%PDF-")):
            outcome.stop = f"the graph-builder produced no valid {name}"
            return
    guide = state.reading / "guide.md"
    expected = guide.read_text(encoding="utf-8", errors="replace").strip() + "\n"
    if (state.reading / "companion.md").read_text(
            encoding="utf-8", errors="replace") != expected:
        outcome.stop = "companion.md is not the current guide"
        return
    take_snapshot(state, 0)
    outcome.summary = (f"{len(graph.nodes)} nodes, {len(graph.edges)} edges, "
                       f"{len(graph.of_type('!'))} gap(s) recorded")
    state.next_role = _next_pipeline_role(state, "graph-builder")
    state.next_round = 1


def _assess_verifier_blind(state: LoopState, outcome: PassOutcome) -> None:
    path = state.reading / f"clarity-review-{outcome.round}.md"
    if not path.is_file() or not path.read_text(
            encoding="utf-8", errors="replace").strip():
        outcome.stop = "the blind verifier recorded no clarity review"
        return
    outcome.summary = "the guide and graph were tested without consulting the paper"
    state.next_role = _next_pipeline_role(state, "verifier-blind")
    state.next_round = outcome.round


def _assess_balanced_artifacts(state: LoopState, outcome: PassOutcome,
                               *, require_pdf: bool = True) -> None:
    """The combined audit/revision must leave a directly readable companion.

    The five-pass thorough pipeline gets this guarantee from graph-builder and
    reviser.  Balanced has no mandatory graph pass, so enforce the same reader
    contract explicitly here. Diagram requirements follow the request and the
    recorded paper profile, independently of the pass's write permissions.
    """
    guide = state.reading / "guide.md"
    expected = guide.read_text(encoding="utf-8", errors="replace").strip() + "\n"
    companion = state.reading / "companion.md"
    if state.harness.kind == "read":
        from deslopify.summary_pdf import dated_guide
        expected = dated_guide(guide) + "\n"
    if not companion.is_file() or companion.read_text(
            encoding="utf-8", errors="replace") != expected:
        outcome.stop = "the balanced pass produced no current companion.md"
        return
    tex = state.reading / "companion.tex"
    if not tex.is_file():
        outcome.stop = "the balanced pass produced no valid companion.tex"
        return
    pdf = state.reading / "companion.pdf"
    valid_pdf = pdf.is_file() and pdf.read_bytes().startswith(b"%PDF-")
    if not valid_pdf:
        if require_pdf:
            outcome.stop = "the balanced pass produced no valid companion.pdf"
            return
        outcome.problems.append(
            "the auditor produced no valid companion.pdf; continuing to the "
            "reviser so the recorded compilation failure can be repaired")

    problems = diagram_problems(state.reading,
                                bool(state.harness.metadata.get("include_proof_map")),
                                _document_text(state))
    if problems:
        outcome.problems.extend(problems)
        outcome.stop = "the requested diagram is unfinished"


def _assess_critique(state: LoopState, outcome: PassOutcome) -> None:
    from deslopify.graph import GraphError
    from deslopify.review import Review

    path = review_path(state, outcome.round)
    if not path.is_file():
        outcome.stop = (f"the critique pass wrote no {path.name}, so there is "
                        f"nothing for a revision to answer")
        return
    try:
        review = Review.load(path)
    except GraphError as exc:
        outcome.problems.append(f"{path.name} could not be read: {exc}")
        outcome.stop = "the critique could not be read, so the loop stops here"
        return

    # The independent reviewer judges the guide. Reading the JSON is necessary
    # to pass its findings to the reviser, but no separate rule-based checker
    # grades quotations, wording, or the review's form.
    outcome.findings = len(review.findings)
    outcome.blocking = len(review.at_least("blocking"))
    outcome.carried = len(review.at_least(state.continue_at))
    outcome.summary = review.summary.strip()

    if state.harness.kind == "read" and state.harness.loop.sequence:
        from .delivery import export_problems, guide_problems
        outcome.repair_tasks.extend(guide_problems(state.reading, state.harness.contract))
        outcome.repair_tasks.extend(export_problems(state.reading, state.harness.contract))
    if outcome.carried == 0 and not outcome.repair_tasks and not outcome.problems:
        state.completed = not outcome.problems
        outcome.stop = (
            f"the second reading raised nothing at {state.continue_at} or worse"
            + (f" ({outcome.findings} minor finding(s) recorded)"
               if outcome.findings else "")
            + " — the companion is as good as this loop can make it")
        return
    state.next_role = _next_pipeline_role(state, state.harness.loop.critic) \
        if state.harness.loop.sequence else state.harness.loop.reviser
    state.next_round = outcome.round


def _assess_revision(state: LoopState, outcome: PassOutcome) -> None:
    from deslopify.compare import diff_graphs, diff_text, summarise
    from deslopify.graph import Graph, GraphError
    from deslopify.review import Dispositions, Review, validate_dispositions

    round_no = outcome.round
    review_file = review_path(state, round_no)
    disp_file = dispositions_path(state, round_no)
    review = dispositions = None

    if not disp_file.is_file():
        outcome.problems.append(
            f"{disp_file.name} was never written — every finding must be recorded "
            f"as fixed or rejected, and silence is how an inconvenient one goes away")
    elif review_file.is_file():
        try:
            review, dispositions = Review.load(review_file), Dispositions.load(disp_file)
            graph, _ = _load_graph(state.reading / "graph.json")
            # The companion as the critique found it, so that a fix may name the
            # node, edge or section it removed.
            lane = state.store / "lane"
            before, _ = _load_graph(lane / "graph.json") \
                if (lane / "graph.json").is_file() else (None, "")
            before_guide = (lane / "guide.md").read_text(encoding="utf-8",
                                                         errors="replace") \
                if (lane / "guide.md").is_file() else None
            for p in validate_dispositions(review, dispositions, graph=graph,
                                           guide=_guide_text(state),
                                           document=_document_text(state),
                                           before=before, before_guide=before_guide):
                if p.severity == "error":
                    outcome.problems.append(str(p))
            fixed = sum(1 for d in dispositions.items if d.disposition == "fixed")
            rejected = sum(1 for d in dispositions.items if d.disposition == "rejected")
            outcome.summary = f"{fixed} finding(s) fixed, {rejected} rejected"
        except GraphError as exc:
            outcome.problems.append(f"the dispositions could not be read: {exc}")

    before_dir = state.snapshot_dir(round_no - 1)
    before_graph = before_dir / "graph.json"
    after_graph = state.reading / "graph.json"
    if before_graph.is_file() and after_graph.is_file():
        try:
            gd = diff_graphs(Graph.load(before_graph), Graph.load(after_graph))
            td = diff_text(
                (before_dir / "guide.md").read_text(encoding="utf-8", errors="replace")
                if (before_dir / "guide.md").is_file() else "",
                _guide_text(state) or "")
            outcome.diff = {"graph": gd.as_dict(), "guide": td.as_dict(),
                            "summary": summarise(gd, td)}
            _check_claimed_fixes(outcome, dispositions, gd)
        except GraphError as exc:
            outcome.problems.append(f"the graph could not be compared: {exc}")

    take_snapshot(state, round_no)

    # Written now, not only at the end of the run. The next round's critique is
    # asked to check whether a finding recorded as *fixed* is actually visible
    # where its disposition says — and it reads that from this file. Left until
    # finalize, the file does not exist while the round that needs it is running,
    # and the instruction is asking for something the pass cannot do.
    try:
        write_review_log(state, current=outcome)
    except Exception:                       # noqa: BLE001 - a record, never a blocker
        pass

    if state.harness.loop.sequence:
        if state.harness.kind == "read":
            from .delivery import guide_problems
            outcome.problems.extend(guide_problems(state.reading, state.harness.contract))
        next_role = _next_pipeline_role(state, state.harness.loop.reviser)
        if next_role:
            state.next_role, state.next_round = next_role, round_no
        else:
            outcome.stop = "the guide-first verification pipeline completed"
            state.completed = not outcome.problems
        return
    if round_no >= state.rounds:
        outcome.stop = f"{state.rounds} round(s) of review completed"
        state.completed = not outcome.problems
        return
    state.next_role, state.next_round = state.harness.loop.critic, round_no + 1


def _check_claimed_fixes(outcome: PassOutcome, dispositions, graph_diff) -> None:
    """A fix claimed in the graph that the graph does not show.

    This is the loop's own integrity check, and it took a live run to get right.
    The first version flagged any round that left the graph alone, which is
    wrong: a finding about what the reader is *told* is fixed in the guide and
    nowhere else, and calling that "polishing" cries wolf on the ordinary case.

    What is not ordinary is a disposition that names a location in the graph
    while the graph is byte-identical. That is a claim and its contradiction,
    both on the record, and it is the one way a revision can appear to have done
    its work without doing any: the prose around a wrong node is rewritten, and
    `why`, `uses`, `without` and `between` go on answering exactly as before.
    """
    if dispositions is None or graph_diff.structural:
        return
    claimed = [d.id for d in dispositions.items
               if d.disposition == "fixed"
               and d.where.strip().startswith(("graph:", "appeal:"))]
    if claimed:
        outcome.problems.append(
            f"{len(claimed)} finding(s) are recorded as fixed at a location in the "
            f"graph ({', '.join(claimed[:6])}), but no node, edge, quotation or "
            f"verdict changed this round. A fix that reached only the prose leaves "
            f"every query the companion exists for answering as it did before.")
    else:
        outcome.notes.append(
            "the graph is unchanged: this round's work went into the guide, which "
            "is what a finding about the reader's experience calls for")


def _write_state(state: LoopState) -> None:
    try:
        (state.run_dir / "loop.json").write_text(
            json.dumps(state.as_dict(), indent=2), encoding="utf-8")
    except OSError:
        pass


def finish(state: LoopState, reason: str = "") -> None:
    if reason and not state.finished:
        state.finished = reason
    _write_state(state)


# --------------------------------------------------------------------------
# the record a human reads
# --------------------------------------------------------------------------


def write_review_log(state: LoopState, current: PassOutcome | None = None) -> Path | None:
    """What the second reader objected to, and what was done about it.

    This is written by the app from the two JSON files, not by a model, for the
    same reason the lab book is: an account of what happened is worth having
    from something that was not a participant. It is also, on its own, the most
    useful page in the companion for a referee — it is the only place that says
    where this reading was contested and where it held.
    """
    from deslopify.graph import GraphError
    from deslopify.review import Dispositions, Review

    outcomes = state.outcomes + ([current] if current is not None else [])
    if not outcomes:
        return None

    lines = [
        "# What review found",
        "",
        "*Written by Atelier, not by the session. Each round below is one "
        "independent reading of the document against the companion, and every "
        "finding it raised is recorded here with what was done about it — "
        "including the ones that were refused.*",
        "",
    ]
    any_round = False

    for round_no in range(1, state.rounds + 1):
        review_file = review_path(state, round_no)
        if not review_file.is_file():
            continue
        try:
            review = Review.load(review_file)
        except GraphError:
            continue
        any_round = True

        answers: dict[str, Any] = {}
        disp_file = dispositions_path(state, round_no)
        if disp_file.is_file():
            with contextlib.suppress(GraphError):
                answers = {d.id: d for d in Dispositions.load(disp_file).items}

        blocking = len(review.at_least("blocking"))
        lines += [
            f"## Round {round_no}",
            "",
            f"{len(review.findings)} finding(s)"
            + (f", {blocking} of them blocking" if blocking else "")
            + (" — nothing raised" if not review.findings else ""),
            "",
        ]
        if review.summary.strip():
            lines += [f"> {review.summary.strip()}", ""]

        for f in review.findings:
            answer = answers.get(f.id)
            verdict = (f"**{answer.disposition}** — {answer.note}"
                       if answer else
                       "**not answered** — this finding was raised and never "
                       "recorded as fixed or rejected")
            lines += [
                f"### {f.id} · {f.severity} · {f.kind} · `{f.where}`",
                "",
                f.problem.strip(),
                "",
            ]
            if f.quote.strip():
                lines += ["> " + f.quote.strip().replace("\n", " "), ""]
            lines += [f"*Asked for:* {f.fix.strip()}", "", verdict]
            if answer and answer.where.strip():
                lines[-1] += f"  (`{answer.where.strip()}`)"
            lines.append("")

        for outcome in outcomes:
            if outcome.round == round_no and outcome.diff:
                lines += [f"*What changed:* {outcome.diff['summary']}", ""]

    if not any_round:
        return None

    if state.finished:
        lines += ["---", "", f"*The loop ended because {state.finished}.*", ""]

    path = state.reading / "review-log.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        return None
    return path
