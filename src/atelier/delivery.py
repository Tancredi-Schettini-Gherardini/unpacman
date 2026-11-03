"""Provider-independent delivery checks and companion exports."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

REVIEW_REVISION_MINUTES = 10.0
REVIEW_EXTRA_MINUTES = 5.0
REVISION_EXTRA_MINUTES = 5.0
LONG_REVIEW_REVISION_MINUTES = 20.0
SHORT_RUN_MAX_MINUTES = 20.0
LONG_RUN_MIN_MINUTES = 60.0
DIAGRAM_FILES = ("graph.json", "map.mmd", "map.pdf")


def diagram_required(reading: Path, requested: bool) -> bool:
    if not requested:
        return False
    try:
        papers = json.loads((reading / "paper-profile.json").read_text())["papers"]
        return len(papers) == 1 and papers[0]["profile"] == "pure"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def diagram_problems(reading: Path, requested: bool,
                     document: str | None = None) -> list[str]:
    """The optional diagram is owed only for a recorded pure-mathematics paper."""
    if not requested:
        return []
    try:
        papers = json.loads((reading / "paper-profile.json").read_text())["papers"]
        if len(papers) != 1 or papers[0]["profile"] not in {"pure", "applied"}:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ["Missing or invalid paper-profile.json for the requested diagram."]
    if papers[0]["profile"] == "applied":
        return (["Remove diagram files: the selected paper profile is applied."]
                if any((reading / name).exists() for name in DIAGRAM_FILES) else [])

    from deslopify.graph import Graph, GraphError, validate

    try:
        graph = Graph.load(reading / "graph.json")
    except GraphError as exc:
        return [f"The requested diagram has no usable graph.json: {exc}"]
    problems = [str(p) for p in validate(graph, document) if p.severity == "error"]
    for name in DIAGRAM_FILES[1:]:
        path = reading / name
        if not path.is_file() or not path.stat().st_size:
            problems.append(f"Missing diagram output: {name}")
        elif name.endswith(".pdf") and not path.read_bytes().startswith(b"%PDF-"):
            problems.append("Invalid diagram PDF: map.pdf")
        elif path.stat().st_mtime_ns < (reading / "graph.json").stat().st_mtime_ns:
            problems.append(f"Regenerate {name}: it is older than graph.json.")
    return problems


def time_targets(total: float | None, roles: list[str]) -> dict[str, float]:
    """Reserve half of short runs, ten minutes of medium runs, twenty of long ones."""
    if not total:
        return {}
    if len(roles) == 1:
        return {roles[0]: total}
    checking = (total / 2 if total <= SHORT_RUN_MAX_MINUTES else
                REVIEW_REVISION_MINUTES if total < LONG_RUN_MIN_MINUTES else
                LONG_REVIEW_REVISION_MINUTES)
    weights = [1.5 if role in ("auditor", "verifier", "critic") else 1.0
               for role in roles[1:]]
    return {roles[0]: total - checking, **{
        role: checking * weight / sum(weights)
        for role, weight in zip(roles[1:], weights, strict=True)}}


def reading_stage_deadline(total: float | None, roles: list[str], index: int,
                           started: float, *, review: bool = False,
                           review_extra_used: float = 0.0) -> float | None:
    """Elapsed-minute deadline, unchanged by retries of the same stage.

    Drafting preserves the checking reserve. Subsequent stages
    divide the time actually left, so an early handoff benefits checking and
    an auditor cannot consume the reviser's entire allowance.
    """
    if not total or len(roles) < 2 or not 0 <= index < len(roles) - 1:
        return None
    targets = time_targets(total, roles)
    if index == 0:
        return targets[roles[0]]
    used = min(REVIEW_EXTRA_MINUTES, max(0.0, review_extra_used))
    remaining = sum(targets[r] for r in roles[index:])
    # Discount review grace already spent before dividing the planned time
    # left. It must not consume the reviser's original allocation.
    planned_started = max(0.0, started - used)
    deadline = started + max(0.0, total - planned_started) * targets[roles[index]] / remaining
    return deadline + (REVIEW_EXTRA_MINUTES - used if review else 0.0)


def stage_time_available(total: float | None, roles: list[str], index: int,
                         elapsed: float, *, revision: bool = False,
                         review: bool = False,
                         review_extra_used: float = 0.0) -> float | None:
    """Stage minutes, shared by the initial instructions and the live clock."""
    if not total:
        return None
    remaining = max(0.0, reading_run_limit(
        total, revision=revision, review=review, review_extra_used=review_extra_used) - elapsed)
    deadline = reading_stage_deadline(total, roles, index, elapsed,
                                      review=review, review_extra_used=review_extra_used)
    return max(0.0, deadline - elapsed) if deadline is not None else remaining


def reading_run_limit(total: float | None, *, revision: bool = False,
                      review: bool = False, review_extra_used: float = 0.0) -> float | None:
    """Review and revision have separate five-minute allowances; no unused grace transfers."""
    if total is None:
        return None
    used = min(REVIEW_EXTRA_MINUTES, max(0.0, review_extra_used))
    extra = (used + REVISION_EXTRA_MINUTES if revision else
             REVIEW_EXTRA_MINUTES if review else used)
    return total + extra


LATEX_GUIDANCE = r"""The companion's mathematics must compile with the exporter's
standard LaTeX/AMS packages. The exporter also defines \Sha. Source-paper custom
macros are not automatically available: expand them into correctly defined
standard commands, preserving their meaning, or use explicit notation and
define it in words. Never guess a macro's meaning or omit a formula to make a
PDF compile. Check every formula, including subscripts and superscripts. Repair
export errors in guide.md, not only companion.tex, which is regenerated.
Compilation checks rendering, not mathematical correctness.
"""

PROGRESSIVE_GUIDE = """\
After reading the complete source, prioritize a complete first version of
progress/reading/guide.md. Write every required section at a useful initial
depth, including limitations and a bibliography, before expanding explanations,
adding extra illustrations, or extending literature research. Empty headings
and placeholders do not count as completed sections. Save section by section;
do not hold the entire guide for one final write. Save research sources and
unresolved questions in progress/_session_notes.md as you work.
Follow the session's enforced stage deadlines, including completion retries.
With independent checking, drafting gets T/2 minutes for T at most 20 minutes,
T-10 minutes for 20 < T < 60, and T-20 minutes for T at least 60.
Independent review may use up to 5 extra minutes beyond its planned allocation,
and revision has its own allowance of up to 5 extra minutes. Unused review grace
does not transfer to revision; the whole run can exceed T by at most 10 minutes.
Check time_remaining regularly; at 90%
of your stage allocation, stop expanding and finish saving. Hand off a useful saved
draft promptly so checking and repair can run. If sections remain unfinished,
list them explicitly for the reviewer and reviser. Never invent evidence to
finish. Do not wait for a live reminder.
The application generates the companion exports from the saved guide.
"""


def guide_problems(reading: Path, contract: str) -> list[str]:
    """Check that a readable guide was saved; leave its content to review."""
    problems: list[str] = []
    guide = reading / "guide.md"
    if not guide.is_file() or not guide.stat().st_size:
        return ["Missing or empty guide: guide.md"]
    text = guide.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return ["Missing or empty guide: guide.md"]
    if contract == "companion":
        sections = re.split(r"(?m)^##\s+(.+)\n", text)
        for i in range(1, len(sections), 2):
            if not sections[i + 1].strip():
                problems.append(f"Empty section: {sections[i]}")
    return problems


def export_problems(reading: Path, contract: str) -> list[str]:
    if contract != "companion":
        return []
    error = export_error_path(reading)
    problems = [error.read_text(encoding="utf-8").strip()] if error.is_file() else []
    for ext in ("md", "tex", "pdf"):
        path = reading / f"companion.{ext}"
        if not path.is_file() or not path.stat().st_size:
            problems.append(f"Missing export: companion.{ext}")
        elif ext == "pdf":
            with path.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    problems.append("The companion PDF is invalid.")
    return problems


def export_error_path(reading: Path) -> Path:
    # Outside the reading lane: an auditor's file restoration must not restore
    # an obsolete compiler error over the controller's latest diagnosis.
    return reading.parent / "_companion_export_error.txt"


def delivery_report(reading: Path, contract: str, *, completed: bool,
                    issues: list[str] | None = None) -> dict:
    """Distinguish missing content from a complete text with unfinished checks."""
    guide = reading / "guide.md"
    has_guide = guide.is_file() and bool(guide.read_text(encoding="utf-8", errors="replace").strip())
    missing = guide_problems(reading, contract)
    other = list(dict.fromkeys([*(issues or []), *export_problems(reading, contract)]))
    status = ("failed" if not has_guide else "incomplete" if missing else
              "completed" if completed and not other else "review_unfinished")
    labels = {"failed": "No draft saved", "incomplete": "Incomplete draft",
              "completed": "Completed", "review_unfinished": "Draft — review unfinished"}
    return {"status": status, "label": labels[status], "missing": missing,
            "issues": other, "can_continue": has_guide and status != "completed"}


def copy_reading_work(runs: Path, parent_id: str, problem: str, destination: Path) -> None:
    """Continue from saved files in a new isolated run, without provider secrets."""
    from .access import check_job_tree
    from .handover import copy_without_links

    if not re.fullmatch(r"[A-Za-z0-9_-]+", parent_id):
        raise ValueError("Invalid reading identifier.")
    parent = runs / parent_id
    try:
        config = json.loads((parent / "config.json").read_text())
        result = json.loads((parent / "result.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("The saved reading is unavailable or has not finished.") from exc
    if config.get("resolved", {}).get("problem") != problem:
        raise ValueError("That reading does not belong to this paper.")
    if result.get("state") not in ("done", "incomplete", "failed", "cancelled"):
        raise ValueError("Stop the reading before continuing it.")
    workspace = parent / "workspace"
    if not workspace.is_dir():
        raise ValueError("This older reading has no saved workspace to continue.")
    archived = parent / "reading"
    check_job_tree(archived)

    def draft_in(saved: Path) -> None:
        guide = saved / "guide.md"
        if not guide.is_file() or not guide.read_text(encoding="utf-8", errors="replace").strip():
            raise ValueError("There is no saved draft to continue.")

    if archived.is_dir():
        draft_in(archived)                     # the run's own record, the controller's
    # A program the earlier reading's AI started may still be changing its
    # working folder: copy it without ever following a link (refusing one,
    # as the check before copying always did), and look only at the copy.
    try:
        copy_without_links(workspace, destination)
        reading = destination / "progress" / "reading"
        if archived.is_dir():
            reading.mkdir(parents=True, exist_ok=True)
            copy_without_links(archived, reading, into_existing=True)
        else:
            draft_in(reading)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    work = destination / "work"
    work.mkdir(exist_ok=True)
    prior = work / "previous-review" / parent_id
    prior.mkdir(parents=True, exist_ok=True)
    notes = work / "continuation-notes.md"
    if notes.is_file():
        shutil.copy2(notes, prior / "earlier-continuation-notes.md")
    (work / "continuation-notes.md").write_text(
        (parent / "notes.md").read_text(encoding="utf-8", errors="replace")
        if (parent / "notes.md").is_file() else "No session notes were saved.\n",
        encoding="utf-8")
    # Older runs did not persist diagnostics in the workspace. Carry their
    # actual export failure forward too, not just the missing-PDF symptom.
    report = parent / "delivery.json"
    if report.is_file() and not export_error_path(reading).is_file():
        try:
            issues = json.loads(report.read_text()).get("issues", [])
            errors = [p for p in issues if isinstance(p, str)
                      and p.startswith("Companion export failed:")]
            if errors:
                export_error_path(reading).write_text("\n".join(errors), encoding="utf-8")
        except (ValueError, AttributeError, TypeError):
            pass
    # An old review must never be mistaken for output from a new audit that
    # failed. Keep it as research, outside the new pipeline's result paths.
    for pattern in ("review-*.json", "dispositions-*.json", "clarity-review-*.md", "review-log.md"):
        for path in reading.glob(pattern):
            shutil.move(str(path), str(prior / path.name))


def export_fingerprint(reading: Path, audience: str) -> str:
    digest = hashlib.sha256(audience.encode())
    for path in [reading / "guide.md", *sorted((reading / "figures").rglob("*"))]:
        if path.is_file():
            digest.update(str(path.relative_to(reading)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_source_scope_notice(reading: Path, scope_file: Path) -> None:
    """Keep a visible partial-source notice in the guide and its PDF export."""
    if not scope_file.is_file() or not (reading / "guide.md").is_file():
        return
    scope = json.loads(scope_file.read_text(encoding="utf-8"))
    omitted = scope.get("omitted") or []
    if not omitted:
        return
    guide = reading / "guide.md"
    text = guide.read_text(encoding="utf-8")
    notice = ("**Source limit.** The source text was too long for the reader AI. "
              "This companion is based only on the beginning of this paper; "
              "later parts were not read.\n\n")
    targets: list[tuple[int, int, str]] = []
    if scope.get("collection"):
        papers = list(re.finditer(r"^## Paper (\d+):[^\n]*$", text, re.MULTILINE))
        for index in scope.get("omitted_indices") or []:
            match = next((p for p in papers if int(p.group(1)) == index), None)
            if match is None:
                continue
            end = next((p.start() for p in papers if p.start() > match.start()), len(text))
            section = re.search(r"^### (?:\d+[.)] )?Main (?:results|findings)\s*$",
                                text[match.end():end], re.MULTILINE)
            start = match.end() + section.end() if section else match.end()
            targets.append((start, end, notice))
    else:
        section = re.search(r"^## (?:\d+[.)] )?Main (?:results|findings)\s*$",
                            text, re.MULTILINE)
        heading = re.search(r"^# [^\n]+$", text, re.MULTILINE)
        start = section.end() if section else heading.end() if heading else 0
        targets.append((start, len(text), notice))
    if not targets:
        names = ", ".join(str(x) for x in omitted)
        targets = [(0, len(text), f"**Source limit ({names}).** " + notice)]
    changed = False
    for start, end, body in sorted(targets, reverse=True):
        segment = text[start:end].lower()
        if "source text was too long" in segment and "beginning" in segment:
            continue
        text = text[:start] + "\n\n" + body + text[start:]
        changed = True
    if changed:
        guide.write_text(text, encoding="utf-8")


def export_companion(reading: Path, audience: str) -> None:
    from .access import check_job_tree
    from deslopify.summary_pdf import write_companion

    check_job_tree(reading)
    write_companion(reading / "guide.md", reading / "companion.md",
                    reading / "companion.tex", reading / "companion.pdf",
                    audience=audience)


def restore_source(document: Path, run_dir: Path) -> bool:
    """Restore the saved input before resumption or delivery, including single passes."""
    original = run_dir / "document.orig"
    if original.is_file() and (not document.is_file()
                             or document.read_bytes() != original.read_bytes()):
        shutil.copy2(original, document)
        return True
    return False
