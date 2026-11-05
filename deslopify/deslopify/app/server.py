"""The Deslopify reading interface.

A separate application from the Atelier lab, deliberately. The lab is an
instrument for comparing methods: it shows harnesses, providers, budgets,
transcripts and costs, because those are what you compare. A person who wants a
hard paper explained needs none of that, and putting them in front of it is how
a good tool becomes an unusable one.

So this serves one surface: drop a paper in, choose who you are (and, if you
like, which AI reads it), read what comes back. Everything underneath is the ordinary Atelier pipeline — the same
`plan_run`, the same `RunManager`, the same run records on disk — which is what
keeps the two views of the same work honest with each other.

Run it with `python -m deslopify.app`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import re
import secrets
import shutil
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from atelier.compile import next_session_number, plan_run
from atelier.config import Config, load_config
from atelier.db import Database, get_db
from atelier.events import EventHub, get_hub
from atelier.harnesses import Harness, HarnessError, get_harness, load_harness
from atelier.problems import LockHeld, ProblemError, acquire_lock, release_lock
from atelier.render import is_image, render_markdown
from atelier.runner import RunManager, _accounts_of
from atelier.spec import RunSpec, new_run_id
from deslopify.source import SourceError, pdf_page_count

from . import (agents, arxiv, catalog, conversations, cv, explain, fetch, firstask, instance, library,
               method, news, openweight, questions, ready, readings, removal, security,
               updates)
from .library import Paper, artefact_path, create_paper, list_papers, load_paper

HARNESS = readings.FAMILY
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"

_LOCAL_FIGURE = re.compile(
    r'(?P<before><img\b[^>]*\bsrc=")(?P<path>figures/[A-Za-z0-9._/-]+)(?P<after>")')
# Earlier collection guides were told to manufacture fragment anchors for their
# contents list. Safe Markdown deliberately renders raw HTML as text, so those
# empty anchors became visible gibberish. New guides do not write them; remove
# the old, stand-alone form when showing an existing guide as well.
_RAW_EMPTY_ANCHOR_LINE = re.compile(
    r"(?im)^[ \t]*<a\b(?=[^>\n]*\b(?:id|name)\s*=)[^>\n]*>\s*</a\s*>\s*(?:\n|$)")


def _companion_html(markdown: str, slug: str, reading: readings.Reading) -> str:
    """Render Markdown and route copied figures through the selected reading."""
    rendered = render_markdown(_RAW_EMPTY_ANCHOR_LINE.sub("", markdown))
    query = urlencode({
        "level": reading.level,
        **({"run": reading.run_id} if reading.run_id else {}),
    })

    def routed(match: re.Match[str]) -> str:
        path = quote(match.group("path"), safe="/")
        return (f'{match.group("before")}/api/papers/{quote(slug)}/asset/{path}'
                f'?{query}{match.group("after")}')

    return _LOCAL_FIGURE.sub(routed, rendered)


def _vendor_dir() -> Path | None:
    """KaTeX, vendored once for the repository.

    Found by walking up rather than by counting `parents[n]`, so that moving
    this package — which is the whole point of it being self-contained — does
    not silently serve a 404 for the mathematics. A standalone Deslopify takes
    a copy of `web/vendor/katex` with it and this finds that instead.
    """
    here = APP_DIR / "web" / "vendor"
    if here.is_dir():
        return here
    for parent in APP_DIR.parents:
        candidate = parent / "web" / "vendor"
        if candidate.is_dir():
            return candidate
    return None


VENDOR_DIR = _vendor_dir()

# What the page may load (2026-10-30, DESIGN §32): unpacman's own files and
# data, nothing from another website. Text an AI wrote — a companion, an
# answer, a key term — may hold an image whose address is on any website, and
# carries whatever the AI was steered to put in it; the browser fetches none,
# so showing the text tells no one anything. Inline styles stay allowed (KaTeX
# and PDF.js set them; they reach no website once images and fonts cannot);
# PDF.js keeps its worker, its fonts and its pictures (blob:, data:).
PAGE_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data: blob:; font-src 'self' data: blob:; "
               "connect-src 'self'; worker-src 'self' blob:; base-uri 'none'; "
               "form-action 'self'; frame-ancestors 'self'")
# And a link followed to another website does not tell it unpacman's address.
PAGE_HEADERS = {"Content-Security-Policy": PAGE_POLICY, "Referrer-Policy": "same-origin"}
# A figure opened in a tab of its own is shown as itself — never run as
# unpacman's page, which an SVG with a script in it otherwise would be.
ASSET_HEADERS = {"Content-Security-Policy": "sandbox; default-src 'none'; "
                                            "img-src 'self' data:; style-src 'unsafe-inline'",
                 "X-Content-Type-Options": "nosniff"}

MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_CV_BYTES = 20 * 1024 * 1024
MAX_TOGETHER = 12          # papers read together at most
# Beside a run: the questions its reader was shown and their answers.
PROFILE_FILE = "reader-profile.json"

# What each pass is doing, said the way a reader would say it. The order is the
# pipeline's own; roles the harness does not declare simply never appear.
STEP_LABELS: dict[str, tuple[str, str]] = {
    "draft": ("Reading the paper",
              "Working through the source and writing the explanation."),
    "graph-builder": ("Mapping the argument",
                      "Drawing the proof's structure and checking every step "
                      "against the source."),
    "verifier-blind": ("Testing it cold",
                       "A second reader tries to follow the explanation "
                       "without the paper, and notes what does not land."),
    "verifier": ("Checking against the paper",
                 "Taking those questions back to the source to see what is "
                 "unclear, wrong, or genuinely missing."),
    "auditor": ("Checking against the paper",
                "A fresh reader first tests the explanation on its own, then "
                "checks its mathematics against the source."),
    "balanced-reviser": ("Answering the review",
                         "Fixing every supported finding and recording any "
                         "finding rejected on the evidence."),
    "reviser": ("Answering the review",
                "Fixing what was wrong and saying why, where it was not."),
}
# The first pass, for what is not one paper.
DRAFT_LABELS: dict[str, tuple[str, str]] = {
    "collection-expert": ("Writing the collection companion",
                          "Reading each paper, then writing its expert summary and "
                          "bibliography in one navigable companion."),
    "collection-nonexpert": ("Writing the collection companion",
                             "Reading each paper, then writing an accessible summary and "
                             "bibliography in one navigable companion."),
    "search": ("Searching the literature",
               "Looking for papers on the question, checking each one at its source, "
               "and writing what was found."),
}


@dataclass(slots=True)
class AppState:
    cfg: Config
    db: Database
    hub: EventHub
    manager: RunManager
    # What a reading runs on when the page does not say. The page offers the
    # choice beside the two buttons (agents.py); these are its starting point,
    # and set from the command line they outrank what the browser remembers —
    # so a test can still insist on a cheap model.
    model: str | None = None
    effort: str | None = None
    max_usd: float | None = None
    max_minutes: float | None = None
    explainer: explain.Explainer | None = None
    conversations: conversations.Store = field(default_factory=conversations.Store)
    # The questions asked before a Non-expert reading (questions.py).
    questioner: questions.Questioner | None = None
    # The reader's own versions of the method's files (Advanced settings).
    method: method.Method | None = None
    # What this computer still needs, and the jobs installing it (ready.py).
    ready: ready.Checker | None = None
    # Whether a newer version is waiting, and taking it (updates.py).
    updater: updates.Updater | None = None
    # How this server was started, and the handle to stop or restart it.
    control: Control | None = None
    awake: instance.KeepAwake | None = None
    # Open-weight models: Ollama here, OpenRouter's catalogue (openweight.py),
    # and the window of each long-window copy of a local model made so far.
    discovery: openweight.Discovery | None = None
    # What Claude Code and Codex themselves offer (catalog.py): the newest
    # models, whatever the lab's list was written before.
    catalog: catalog.Catalog | None = None
    windows: dict[str, int] = field(default_factory=dict)
    pull: openweight.Pull | None = None          # a model being downloaded
    started: float = field(default_factory=time.time)
    # The last requests that failed or were slow to answer, for the report:
    # what a reader saw go wrong is otherwise gone once the page is closed.
    trouble: deque[str] = field(default_factory=lambda: deque(maxlen=TROUBLE_KEPT))


@dataclass(slots=True)
class Control:
    """Set by `__main__` for a server it runs: the uvicorn server, so the page
    can stop it (Quit) or have it restart as a new version (Update)."""
    server: Any = None
    restart: bool = False
    background: bool = False          # started from the icon, not a Terminal
    port: int | None = None


def state_of(request: Request) -> AppState:
    return request.app.state.deslopify


# --------------------------------------------------------------------------
# progress
# --------------------------------------------------------------------------


def _pipeline(cfg: Config, hid: str = readings.FAMILY,
              run_id: str | None = None) -> list[str]:
    # A harness can evolve.  The run's compiled plan is the authority for an
    # older reading; otherwise changing Expert from Thorough to Balanced would
    # redraw yesterday's five completed passes as today's three.
    if run_id:
        config = _read_json(cfg.runs_dir / run_id / "config.json")
        resolved = config.get("resolved") or {}
        loop = resolved.get("loop") or {}
        recorded = [str(p.get("role")) for p in loop.get("passes", [])
                    if isinstance(p, dict) and p.get("role")]
        if recorded:
            return recorded
        if "loop" in resolved or (hid.startswith("deslopify-multi-") and config):
            return ["draft"]  # earlier collection runs had no review pass
    try:
        harness = get_harness(cfg.harnesses_dir, hid)
    except HarnessError:
        return ["draft"]
    return (["draft", *harness.loop.sequence]
            if harness.loop.enabled and harness.loop.sequence else ["draft"])


def _loop_record(cfg: Config, run_id: str) -> dict[str, Any]:
    path = cfg.runs_dir / run_id / "loop.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _plain_summary(role: str, reading: Path, run_dir: Path) -> str:
    """What a pass accomplished, said to a reader rather than to the lab.

    The controller's own summaries — "6 nodes, 4 edges, 1 gap(s) recorded" —
    are written for someone comparing methods. These are counted from the files
    the pass left behind, and read as sentences. Where a count cannot be taken
    reliably, the sentence says less rather than guessing.
    """
    if role == "draft":
        guide = reading / "guide.md"
        if guide.is_file():
            words = len(guide.read_text(encoding="utf-8", errors="replace").split())
            return f"Wrote a {words:,}-word explanation."
        return ""
    if role == "graph-builder":
        # The graph as this pass left it, before any revision changed it.
        snap = run_dir / "loop" / "round-0" / "graph.json"
        graph = _read_json(snap if snap.is_file() else reading / "graph.json")
        nodes = graph.get("nodes") or []
        gaps = sum(1 for n in nodes if str(n.get("id", "")).startswith("!"))
        steps = len(nodes) - gaps
        if not nodes:
            return ""
        text = f"Mapped the argument in {_plural(steps, 'step')}"
        if gaps:
            text += (f", and marked {_plural(gaps, 'place')} where the paper "
                     f"skips work")
        return text + "."
    if role == "verifier-blind":
        review = next(iter(sorted(reading.glob("clarity-review-*.md"))), None)
        if review is None:
            return ""
        body = review.read_text(encoding="utf-8", errors="replace")
        # Each question is its own sub-heading in practice; if the pass wrote
        # them some other way, report that it finished rather than a wrong count.
        asked = sum(1 for line in body.splitlines() if line.startswith("### "))
        return (f"Came back with {_plural(asked, 'question')} the explanation "
                f"did not answer." if asked else "Recorded what did not land.")
    if role == "verifier":
        review = _read_json(next(iter(sorted(reading.glob("review-*.json"))),
                                 reading / "review-1.json"))
        findings = review.get("findings") or []
        if not findings:
            return "Found nothing that needed fixing." if review else ""
        serious = sum(1 for f in findings if f.get("severity") == "blocking")
        text = f"Found {_plural(len(findings), 'thing')} to fix"
        if serious:
            text += f", {serious} of them serious"
        return text + "."
    if role == "auditor":
        review = _read_json(next(iter(sorted(reading.glob("review-*.json"))),
                                 reading / "review-1.json"))
        findings = review.get("findings") or []
        mapped = (reading / "graph.json").is_file()
        if findings:
            text = f"Checked the explanation and found {_plural(len(findings), 'thing')} to fix"
        else:
            text = "Checked the explanation against the source"
        return text + ("; also checked the diagram." if mapped else ".")
    if role in {"reviser", "balanced-reviser"}:
        answers = _read_json(next(iter(sorted(reading.glob("dispositions-*.json"))),
                                  reading / "dispositions-1.json"))
        items = answers.get("dispositions") or []
        if not items:
            return ""
        fixed = sum(1 for d in items if d.get("disposition") == "fixed")
        refused = sum(1 for d in items if d.get("disposition") == "rejected")
        text = f"Fixed {fixed:,}"
        if refused:
            text += f", and disagreed with {refused:,}, giving reasons"
        return text + "."
    return ""


def _raised(reading: Path) -> int:
    """How many points the latest check raised — the review the page shows."""
    reviews = sorted(reading.glob("review-*.json"))
    findings = _read_json(reviews[-1]).get("findings") if reviews else None
    return len(findings) if isinstance(findings, list) else 0


def _why_skipped(finished: str, raised: int = 0) -> str:
    """Why the pipeline stopped short, said plainly — and truthfully.

    The two common reasons mean opposite things. A check that found nothing to
    answer is good news; a budget that ran out means the companion was never
    revised. Reporting the first when the second happened would tell someone an
    unreviewed guide had been cleared.

    "Raised nothing" is the loop's phrase for nothing at the level that calls
    for a revision (`continue_at`), not for nothing at all: the check may still
    have raised minor points, which stay open. The step above this one counts
    them, so this one must not say there were none.
    """
    reason = finished.lower()
    if "raised nothing" in reason:
        if raised == 1:
            return ("Not needed — the one point the check raised was not serious "
                    "enough to revise for. It is still open, and listed with what "
                    "the check found.")
        if raised:
            return (f"Not needed — none of the {raised:,} points the check raised "
                    "was serious enough to revise for. They are still open, and "
                    "listed with what the check found.")
        return "Not needed — the check found nothing to fix."
    if "budget" in reason:
        return "Skipped — the budget ran out first, so the explanation was not revised."
    if "stopped" in reason:
        return "Not reached — the reading was stopped."
    return "Not reached."


def _steps(cfg: Config, reading: readings.Reading | None,
           live_index: int | None, finished: bool) -> list[dict[str, Any]]:
    """One entry per pass, with what happened to it."""
    run_id = reading.run_id if reading else None
    record = _loop_record(cfg, run_id) if run_id else {}
    done = {p.get("role"): p for p in record.get("passes", [])}
    order = _pipeline(cfg, reading.harness if reading else readings.FAMILY, run_id)
    run_dir = cfg.runs_dir / run_id if run_id else cfg.runs_dir

    # A run of one pass keeps no record of its passes: its draft is done when
    # it finished having written something.
    if not record and order == ["draft"] and finished and reading is not None \
            and reading.guide.is_file() and reading.status == "done":
        done = {"draft": {}}
    # Which AI each step runs on — said only when they are not all the same.
    by_role = _run_agents(cfg, run_id)
    mixed = _mixed(by_role)

    out: list[dict[str, Any]] = []
    for i, role in enumerate(order, start=1):
        title, detail = STEP_LABELS.get(role, (role, ""))
        if role == "draft" and reading is not None and reading.level in DRAFT_LABELS:
            title, detail = DRAFT_LABELS[reading.level]
        past = role in done
        if past:
            state = "done" if done[role].get("completed", True) else "incomplete"
        elif live_index is not None and i == live_index:
            state = "active"
        elif finished:
            state = "skipped"
        else:
            state = "pending"
        entry = {"role": role, "title": title, "detail": detail, "state": state}
        if mixed and role in by_role and state != "skipped":
            entry["agent"] = by_role[role]
        if past and reading is not None:
            entry["summary"] = _plain_summary(role, reading.dir, run_dir)
            if done[role].get("continued_from"):
                entry["summary"] = "Reused the saved complete draft from the previous run."
            if state == "incomplete":
                entry["summary"] = "Incomplete. " + entry["summary"]
                entry["problems"] = done[role].get("problems", [])
        elif state == "skipped":
            entry["summary"] = _why_skipped(record.get("finished", ""),
                                            _raised(reading.dir) if reading else 0)
        out.append(entry)
    return out


def _run_view(st: AppState, reading: readings.Reading | None) -> dict[str, Any] | None:
    """A reading's run, as the progress view shows it."""
    if reading is None or reading.run_id is None:
        return None
    # Every AI the run uses, in the order of its steps, when there is more
    # than one: "Opus 5 + GPT-5.6 Terra".
    by_role = _run_agents(st.cfg, reading.run_id)
    distinct: dict[tuple[str, str], dict[str, Any]] = {}
    for a in by_role.values():
        distinct.setdefault((a["provider"], a["model"]), a)     # as first used
    several = {"agents": list(distinct.values())} if _mixed(by_role) else {}
    live = st.manager.get(reading.run_id) if reading.live else None
    if live is not None and not live.state.terminal:
        snap = live.snapshot()
        return {
            "id": snap["run_id"], "state": snap["state"], "level": reading.level,
            "number": reading.number,
            "elapsed": snap["elapsed"], "cost": snap["totals"]["cost_computed"],
            "pass": snap.get("pass"),
            "started": reading.when,
            "agent": agents.describe(snap.get("provider"), snap.get("account"),
                                     snap.get("model"), snap.get("effort")),
            "running": True,
        } | several
    row = reading.row
    return several | {
        "id": reading.run_id, "state": row.get("status") or reading.status,
        "level": reading.level, "number": reading.number,
        "elapsed": row.get("wall_seconds") or 0, "cost": row.get("cost_computed") or 0,
        "reason": row.get("ended_reason") or "", "error": row.get("error") or "",
        "started": reading.when,
        "agent": agents.describe(row.get("provider"), row.get("account"),
                                 row.get("model"), row.get("effort")),
        "running": False,
        "delivery": reading.delivery,
        "continued_from": (_read_json(st.cfg.runs_dir / reading.run_id / "config.json")
                           .get("spec", {}).get("parent_run_id")),
    }


def _overview(levels: dict[str, readings.Level]) -> dict[str, Any]:
    """Each level at a glance: what the page needs to offer a switch."""
    out: dict[str, Any] = {}
    for level, v in levels.items():
        out[level] = {
            "state": v.state,
            "latest": v.latest.run_id if v.latest else None,
            "companion": (v.companion.run_id or "kept") if v.companion else None,
        }
    return out


def _reading_key(reading: readings.Reading) -> str:
    """The stable public name of a reading, including a pre-run kept guide."""
    return reading.run_id or "kept"


def _named_reading(found: list[readings.Reading], run: str | None) -> readings.Reading | None:
    if not run:
        return None
    if run == "kept":
        return next((reading for reading in found if reading.run_id is None), None)
    return readings.find(found, run)


def _history(st: AppState, found: list[readings.Reading],
             levels: tuple[str, ...] = library.LEVELS) -> list[dict[str, Any]]:
    """Every reading of a paper, newest first, for the paper's run picker."""
    companions = {_reading_key(reading) for reading in readings.with_companion(found)}
    # The companion each level opens with; any other is an earlier one.
    current = {_reading_key(v.companion)
               for v in readings.by_level(found, levels).values()
               if v.companion is not None}
    out: list[dict[str, Any]] = []
    for reading in found:
        view = _run_view(st, reading) or {
            "id": _reading_key(reading), "state": reading.status,
            "level": reading.level, "number": reading.number, "started": reading.when,
            "elapsed": 0, "cost": 0, "agent": None, "running": False,
        }
        out.append(view | {
            "id": _reading_key(reading),
            "companion": _reading_key(reading) in companions,
            "current": _reading_key(reading) in current,
            "profile": _profile_summary(st.cfg, reading.run_id),
            # Read with the reader's own version of the method (method.py).
            "own_method": bool(reading.run_id
                               and method.used_by(st.cfg.runs_dir / reading.run_id)),
        })
    return out


def _status(st: AppState, paper: Paper, level: str | None = None,
            run: str | None = None) -> dict[str, Any]:
    """Where one paper stands: at the level asked for, or the one it opens at."""
    found = readings.all_readings(st.cfg, st.db, st.manager, paper)
    levels = readings.by_level(found, paper.levels)
    if level not in paper.levels:
        level = readings.default_level(found, paper)
        if level not in paper.levels:
            level = paper.levels[0] if found else None
    named = _named_reading(found, run)
    if run and named is None:
        raise HTTPException(404, "That reading does not belong to this paper.")
    view = named or (levels[level].latest if level else None)
    # A run names its own level. Honour it even if a stale or hand-written URL
    # pairs that run with the other level.
    if run and view is not None:
        level = view.level
    companion = levels[level].companion if level else None
    credited = {_reading_key(reading) for reading in readings.with_companion(found)}

    live_index = None
    if view is not None and view.live and (handle := st.manager.get(view.run_id or "")):
        live_index = (handle.snapshot().get("pass") or {}).get("index")
    finished = view is not None and not view.live and view.status in (
        "done", "incomplete", "failed", "cancelled")
    record = _loop_record(st.cfg, view.run_id) if view and view.run_id else {}
    # Why a finished reading left no companion, in words a reader can act on:
    # the provider's own message ("Not logged in · Please run /login") is in
    # the run's event log, not in the reason the loop gives for stopping.
    plain = ""
    if finished and view is not None and view.run_id and _reading_key(view) not in credited:
        row = view.row
        plain = ready.plain_failure(
            str(row.get("error") or ""), str(row.get("ended_reason") or ""),
            str(record.get("finished") or ""),
            *ready.failure_texts(st.cfg.runs_dir / view.run_id))
    shown = paper.to_dict()
    for member in shown.get("members", []):
        # Each is a paper of its own, which may have been removed since.
        member["here"] = (st.cfg.problems_dir / member["slug"] / library.MARKER).is_file()
    return {
        "paper": shown,
        "level": level,
        "readings": _overview(levels),
        "runs": _history(st, found, paper.levels),
        "companion": companion is not None,
        "companion_run": companion.run_id if companion else None,
        "view_companion": view is not None and _reading_key(view) in credited,
        "steps": _steps(st.cfg, view, live_index, finished),
        "finished_because": record.get("finished", ""),
        "plain_failure": plain,
        "run": _run_view(st, view),
    }


# --------------------------------------------------------------------------
# the application
# --------------------------------------------------------------------------


def _ai_in_use(cfg: Config, manager: RunManager, provider: str) -> bool:
    """Whether a reading running now uses this AI's account — the one a sign-in
    of unpacman's own changes (signins.py)."""
    account = cfg.default_account(provider)
    return account is not None and any(account.id in _accounts_of(h.plan)
                                       for h in manager.active())


def create_app(cfg: Config | None = None, *, model: str | None = None,
               effort: str | None = None, max_usd: float | None = None,
               max_minutes: float | None = None,
               method_dir: Path | None = None,
               checker: ready.Checker | None = None,
               updater: updates.Updater | None = None,
               control: Control | None = None,
               control_token: str | None = None,
               trusted_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")) -> FastAPI:
    cfg = cfg or load_config()
    # The two open-weight accounts — on this Mac, and OpenRouter — are always
    # there to be offered, once they have a model to offer (openweight.py).
    cfg = openweight.with_accounts(cfg)
    # The accounts in a dict of the reader's own: a sign-in of unpacman's own
    # changes them in place (signins.py), for everything below at once.
    cfg = replace(cfg, accounts=dict(cfg.accounts))
    # Kept beside atelier.local.toml, and like it never committed.
    edits = method.Method(cfg, method_dir or cfg.root / "method.local")
    db = get_db(cfg.db_path)
    hub = get_hub()
    manager = RunManager(cfg, hub, db)
    checker = checker or ready.Checker(cfg)
    if isinstance(checker, ready.Checker):
        checker.in_use = lambda provider: _ai_in_use(cfg, manager, provider)
    # A copy with an icon checks for new versions by itself; a developer's
    # copy only when asked.
    updater = updater or updates.Updater(cfg.root, auto=instance.app_marker(cfg.root).is_file())
    awake = instance.KeepAwake()

    async def keep_awake() -> None:
        # A laptop left alone falls asleep, and a reading asleep is a reading
        # stopped. Checked every few seconds; costs nothing when idle.
        while True:
            awake.set(bool(manager.active()))
            await asyncio.sleep(5)

    async def tidy_signins() -> None:
        # Sign-ins of unpacman's own that nothing reads with any more — one a
        # reading was using when another account took its place, one left
        # half done when unpacman stopped — signed out and removed once no
        # reading uses that AI (ready.Checker.sweep). Their age keeps clear of
        # a sign-in that another copy of unpacman has under way.
        if not isinstance(checker, ready.Checker):
            return
        while True:
            with contextlib.suppress(Exception):
                await checker.sweep(older_than=ready.SIGN_IN_TIMEOUT + 5 * 60)
            await asyncio.sleep(60)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.reap_orphans()
        # Whether this copy's reader is new, decided the first time this
        # version starts — before any paper of theirs is here (firstask.py).
        with contextlib.suppress(Exception):
            await asyncio.to_thread(firstask.state, app.state.deslopify.cfg)
        tasks = [asyncio.create_task(keep_awake()), asyncio.create_task(updater.watch()),
                 asyncio.create_task(tidy_signins()),
                 # What open-weight models there are, before anyone asks.
                 asyncio.create_task(_refresh_open(app.state.deslopify)),
                 # And which models Claude Code and Codex offer (catalog.py).
                 asyncio.create_task(_refresh_models(app.state.deslopify))]
        yield
        for task in tasks:
            task.cancel()
        for job in checker.jobs.values():
            if job.task and not job.task.done():
                job.task.cancel()
        if app.state.deslopify.pull is not None:
            app.state.deslopify.pull.cancel()
        await manager.shutdown()
        awake.stop()

    app = FastAPI(title="unpacman", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    token = control_token or secrets.token_urlsafe(32)
    app.state.control_token = token
    app.state.deslopify = AppState(cfg=cfg, db=db, hub=hub, manager=manager,
                                   model=model, effort=effort,
                                   max_usd=max_usd, max_minutes=max_minutes,
                                   explainer=explain.Explainer(
                                       cfg, texts=edits.texts,
                                       reader=lambda: _reader_block(cfg, edits)),
                                   questioner=questions.Questioner(cfg),
                                   method=edits, ready=checker, updater=updater,
                                   control=control, awake=awake,
                                   discovery=openweight.Discovery(cfg),
                                   catalog=catalog.Catalog(cfg))
    # An AI installed, signed in, signed in with another account or back with
    # the Mac's may offer other models: asked again when the menu is next
    # drawn (catalog.py).
    if isinstance(getattr(checker, "on_finish", None), list):
        model_lists = app.state.deslopify.catalog
        checker.on_finish.append(lambda job: model_lists.forget() if job.kind.startswith(
            ("install-claude", "install-codex", "signin-", "switch-", "revert-")) else None)
    app.add_middleware(_Trouble, log=app.state.deslopify.trouble)
    app.add_middleware(security.LocalControlSecurity, token=token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(trusted_hosts),
                       www_redirect=False)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"} | PAGE_HEADERS)

    # ---------------------------------------------------------------- papers

    @app.get("/api/papers")
    async def api_papers(request: Request) -> list[dict[str, Any]]:
        st = state_of(request)
        return [p.to_dict() | _library_entry(st, p) for p in list_papers(st.cfg)]

    @app.post("/api/papers")
    async def api_upload(request: Request) -> dict[str, Any]:
        st = state_of(request)
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            raise HTTPException(400, "Choose a PDF to read.")
        name = str(upload.filename)
        if Path(name).suffix.lower() != ".pdf":
            raise HTTPException(400, "unpacman reads PDFs. That file is not one.")
        data = await upload.read(MAX_PDF_BYTES + 1)
        return _add_pdf(st, name, data)

    @app.post("/api/arxiv")
    async def api_arxiv(request: Request) -> dict[str, Any]:
        """A paper named by its arXiv number: its PDF fetched from arXiv and made
        a paper as a dropped one is — or the paper it already is (arxiv.py).
        The page sends what was typed; the address is made here."""
        st = state_of(request)
        body = await _json(request)
        ident = arxiv.identifier(_text(body, "id"))
        if ident is None:
            raise HTTPException(400, "That is not an arXiv number. One looks like 2309.01234 "
                                     "or math/0601001, with a version if you like: "
                                     "2309.01234v2.")
        url = arxiv.pdf_url(ident)
        # A version never changes once posted, so one fetched before is this
        # very paper. A number without one is fetched afresh — arXiv may have a
        # newer version — and found again by its bytes if it has not changed.
        if arxiv.versioned(ident) and (known := library.find_by_download_url(st.cfg, url)):
            return known.to_dict() | {"existing": True} | _library_entry(st, known)
        # arXiv's listing, for the title, is asked while the PDF downloads.
        pdf = asyncio.ensure_future(asyncio.to_thread(fetch.download_pdf, url))
        listed = await asyncio.to_thread(arxiv.listing, ident)
        try:
            data = await pdf
        except fetch.FetchError as exc:
            if listed.known is False:
                raise HTTPException(404, f"arXiv has no paper numbered {ident}"
                                         + (", or no such version of it"
                                            if arxiv.versioned(ident) else "")
                                         + ". Check the number.") from exc
            raise HTTPException(502, f"arXiv:{ident} could not be fetched. {exc}") from exc
        added = _add_pdf(st, arxiv.file_name(ident), data, title=listed.title)
        library.remember_download(_paper(st, added["slug"]), url)
        return added

    @app.post("/api/collections")
    async def api_collection(request: Request) -> dict[str, Any]:
        """Several papers, already uploaded, to be read together."""
        st = state_of(request)
        body = await _json(request)
        slugs = body.get("papers")
        if not isinstance(slugs, list) or not all(isinstance(x, str) for x in slugs):
            raise HTTPException(400, "Name the papers to read together.")
        members = [_paper(st, slug) for slug in dict.fromkeys(slugs)]
        if any(m.kind != "paper" for m in members):
            raise HTTPException(400, "Only papers can be read together.")
        if len(members) > MAX_TOGETHER:
            raise HTTPException(400, f"At most {MAX_TOGETHER} papers can be read together.")
        existing = library.find_by_fingerprint(st.cfg, library.collection_fingerprint(members))
        try:
            together = await asyncio.to_thread(library.create_collection, st.cfg, members)
        except (ProblemError, SourceError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return together.to_dict() | {"existing": existing is not None} \
            | _library_entry(st, together)

    @app.post("/api/searches")
    async def api_search(request: Request) -> dict[str, Any]:
        """A search of the literature on a question: made here, run like a
        reading (POST …/read)."""
        st = state_of(request)
        body = await _json(request)
        query = library.normalise_query(_text(body, "query"))
        try:
            known = library.find_by_fingerprint(st.cfg, library.search_fingerprint(query))
            search = known or library.create_search(st.cfg, query)
        except ProblemError as exc:
            raise HTTPException(400, str(exc)) from exc
        return search.to_dict() | {"existing": known is not None} \
            | _library_entry(st, search)

    @app.post("/api/papers/{slug}/fetch")
    async def api_fetch(request: Request, slug: str) -> dict[str, Any]:
        """Add a paper a search found to the library: its PDF, fetched from the
        address the search gave — named by its place in that search's
        results, never by an address sent from the page."""
        st = state_of(request)
        search = _paper(st, slug)
        if search.kind != "search":
            raise HTTPException(400, "Only a search's papers can be fetched.")
        body = await _json(request)
        found = readings.all_readings(st.cfg, st.db, st.manager, search)
        reading = readings.find(found, _text(body, "run"))
        if reading is None:
            raise HTTPException(404, "That search does not belong to this question.")
        results = library.read_results(reading.dir)
        index = body.get("index")
        if not isinstance(index, int) or not 0 <= index < len(results):
            raise HTTPException(404, "There is no such paper in that search.")
        item = results[index]
        if not item.get("pdf"):
            raise HTTPException(400, "The search found no openly available PDF for that "
                                     "paper. Open its link, save the PDF, and drop it here.")
        # Bulk selection can reuse an imported PDF. Keep the individual Add
        # action's existing behavior: fetch afresh, then deduplicate by bytes.
        if body.get("reuse") is True:
            known = library.find_by_download_url(st.cfg, item["pdf"])
            if known:
                return known.to_dict() | {"existing": True} | _library_entry(st, known)
        try:
            data = await asyncio.to_thread(fetch.download_pdf, item["pdf"])
        except fetch.FetchError as exc:
            raise HTTPException(502, str(exc)) from exc
        name = (item.get("arxiv") or re.sub(r"[^A-Za-z0-9]+", "-", item["title"])[:60]
                or "paper").strip("-") + ".pdf"
        added = _add_pdf(st, name, data, title=item["title"])
        library.remember_download(_paper(st, added["slug"]), item["pdf"])
        return added

    @app.get("/api/papers/{slug}/member/{index}")
    async def api_member(request: Request, slug: str, index: int) -> FileResponse:
        """One of a collection's papers, as it was when they were put together."""
        st = state_of(request)
        together = _paper(st, slug)
        if together.kind != "collection" or not 0 <= index < len(together.members):
            raise HTTPException(404, "There is no such paper here.")
        rel = together.members[index].get("file", "")
        path = (together.root / rel).resolve()
        if not rel.startswith("refs/") or together.root.resolve() not in path.parents \
                or not path.is_file():
            raise HTTPException(404, "That paper's file is missing.")
        return FileResponse(path, media_type="application/pdf",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/api/papers/{slug}")
    async def api_paper(request: Request, slug: str, level: str | None = None,
                        run: str | None = None) -> dict[str, Any]:
        st = state_of(request)
        return _status(st, _paper(st, slug), level, run)

    # ------------------------------------------------------------- deleting
    # Runs pile up: each keeps a copy of its paper and everything it wrote.
    # Deleting one, a paper's earlier ones, or a paper with all of its, is
    # asked for on the page and confirmed there (removal.py).

    @app.delete("/api/papers/{slug}")
    async def api_delete(request: Request, slug: str) -> dict[str, Any]:
        """A paper, with every run that read it."""
        st = state_of(request)
        paper = _paper(st, slug)
        if _live(st, paper):
            raise HTTPException(409, "That paper is being read. Stop it first.")
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        try:
            freed = await asyncio.to_thread(
                removal.delete_paper, st.cfg, st.db, st.manager, paper, found)
        except removal.RemovalError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "freed": freed}

    @app.get("/api/papers/{slug}/space")
    async def api_paper_space(request: Request, slug: str) -> dict[str, Any]:
        """What this paper and each of its runs take on disk, and what deleting
        its earlier runs would free."""
        st = state_of(request)
        paper = _paper(st, slug)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)

        def measure() -> dict[str, Any]:
            each = {_reading_key(r): removal.plan(st.cfg, paper, found, [r]).bytes
                    for r in found}
            spare = removal.plan(st.cfg, paper, found, removal.earlier(st.cfg, paper, found))
            return {"paper": removal.size_of(paper.root), "runs": each,
                    "earlier": spare.describe(),
                    "total": removal.footprint(st.cfg, paper, found)}
        return await asyncio.to_thread(measure)

    @app.post("/api/papers/{slug}/runs/delete")
    async def api_delete_run(request: Request, slug: str) -> dict[str, Any]:
        """One run of a paper, and what it wrote. The run is named in the body,
        never in the address: a browser resolves `..` in an address before
        sending it, and `…/runs/..` would arrive as the paper itself."""
        st = state_of(request)
        paper = _paper(st, slug)
        run = _text(await _json(request), "run")
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        reading = _named_reading(found, run) if run else None
        if reading is None:
            raise HTTPException(404, "That run is not one of this paper's.")
        if reading.live:
            raise HTTPException(409, "That run is still reading. Stop it first, then delete it.")
        try:
            freed = await asyncio.to_thread(
                removal.delete_runs, st.cfg, st.db, st.manager, paper, found, [reading])
        except removal.RemovalError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "freed": freed, "deleted": 1}

    @app.post("/api/papers/{slug}/tidy")
    async def api_tidy_paper(request: Request, slug: str) -> dict[str, Any]:
        """A paper's earlier runs: all but the companion each level opens with,
        and any still running."""
        st = state_of(request)
        paper = _paper(st, slug)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        chosen = removal.earlier(st.cfg, paper, found)
        try:
            freed = await asyncio.to_thread(
                removal.delete_runs, st.cfg, st.db, st.manager, paper, found, chosen)
        except removal.RemovalError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "freed": freed, "deleted": len(chosen)}

    @app.get("/api/space")
    async def api_space(request: Request) -> dict[str, Any]:
        """Every paper at once, for Advanced settings: how much there is, and
        what deleting every paper's earlier runs would free."""
        st = state_of(request)

        def measure() -> dict[str, Any]:
            papers = list_papers(st.cfg)
            total = runs = spare = spare_bytes = 0
            for paper in papers:
                found = readings.all_readings(st.cfg, st.db, st.manager, paper)
                total += removal.footprint(st.cfg, paper, found)
                runs += sum(1 for r in found if r.run_id)
                extra = removal.plan(st.cfg, paper, found, removal.earlier(st.cfg, paper, found))
                spare += len(extra.runs)
                spare_bytes += extra.bytes
            return {"papers": len(papers), "runs": runs, "bytes": total,
                    "earlier": {"count": spare, "bytes": spare_bytes}}
        return await asyncio.to_thread(measure)

    @app.post("/api/tidy")
    async def api_tidy(request: Request) -> dict[str, Any]:
        """Every paper's earlier runs. What each level opens with, and a run
        still going, are kept; a paper whose runs could not all be deleted is
        named in `skipped`."""
        st = state_of(request)
        freed = deleted = 0
        skipped: list[str] = []
        for paper in list_papers(st.cfg):
            found = readings.all_readings(st.cfg, st.db, st.manager, paper)
            chosen = removal.earlier(st.cfg, paper, found)
            if not chosen:
                continue
            try:
                freed += await asyncio.to_thread(
                    removal.delete_runs, st.cfg, st.db, st.manager, paper, found, chosen)
                deleted += len(chosen)
            except removal.RemovalError:
                skipped.append(paper.title)
        return {"ok": True, "freed": freed, "deleted": deleted, "skipped": skipped}

    # ---------------------------------------------------------------- agents

    @app.get("/api/agents")
    async def api_agents(request: Request, level: str = "expert") -> dict[str, Any]:
        st = state_of(request)
        # What open-weight models can run now, and which models Claude Code
        # and Codex offer: looked at here, where the menu is drawn, so that
        # every other request can use the lists at once.
        await _refresh_open(st)
        await _refresh_models(st)
        # Both reader levels use the same Balanced limits.  Advertising the
        # base family's thorough limits here would make the page promise a
        # longer run than the one it actually launches. A search and a joint
        # reading have limits of their own, asked for by level.
        harness = _harness(st, level if level in library.ALL_LEVELS else "expert")
        offers, default = _offers(st, harness)
        assert st.ready is not None
        now = await st.ready.aget()
        # Start on an AI that can actually read: if the usual one is not
        # signed in here and another is, the page starts on that one.
        if default and not now.signed_in(default.provider, default.account):
            usable = next((o for o in offers if now.signed_in(o.provider, o.account)), None)
            if usable is not None:
                default = agents.Agent(
                    account=usable.account, provider=usable.provider,
                    model=usable.default_model,
                    effort=agents.effort_for(usable, harness.defaults.effort
                                             or st.cfg.defaults.effort))
        menu = agents.menu(st.cfg, harness, offers, default,
                           pinned=st.model is not None or st.effort is not None,
                           max_usd=st.max_usd, max_minutes=st.max_minutes)
        for group in menu["groups"]:
            group["signed_in"] = now.signed_in(group["provider"], group["account"])
            # Signed in, but unable to read here: too old for a reading's
            # protections, or installed without a part it needs (ready.py).
            unfit = next((a.outdated for a in now.ais if a.outdated and a.signed_in
                          and a.provider == group["provider"]
                          and a.account == group["account"]), "")
            group["outdated"] = bool(unfit)
            if unfit:
                group["outdated_why"] = ready.codex_trouble(unfit)
            if not group["signed_in"]:
                # In the reader's words: what this AI lacks, and what to do.
                group["not_ready"] = " ".join(now.blocking(
                    group["provider"], group["account"], latex=False))
        menu["ready"] = {"ok": now.ok, "tex": now.tex.ready, "blocking": now.blocking()}
        # The steps a reading at this level has, each of which may be given to
        # another AI; a one-pass search has no separate review step.
        roles = agents.step_roles(harness)
        menu["steps"] = [{"slot": slot, "title": title, "detail": detail}
                         for slot, (title, detail) in agents.STEP_NAMES.items()
                         if slot in roles]
        return menu

    # --------------------------------------------------------- open weights

    @app.get("/api/open")
    async def api_open(request: Request, fresh: bool = False) -> dict[str, Any]:
        """Where open-weight models stand here: Ollama, and OpenRouter's key."""
        st = state_of(request)
        assert st.discovery is not None
        status = await asyncio.to_thread(st.discovery.ollama, fresh=fresh)
        await _refresh_open(st)
        found = st.discovery.snapshot()
        from atelier.providers.claude import ClaudeAdapter

        def model(m: openweight.Model) -> dict[str, Any]:
            return {"id": m.id, "name": m.name, "note": m.note, "small": m.small}
        return {
            "engine": ClaudeAdapter.executable() is not None,
            "ollama": {"installed": status["installed"], "running": status["running"],
                       "version": status["version"],
                       "models": [model(m) for m in status["models"]],
                       "other": [model(m) for m in status["other"]],
                       "address": openweight.ollama_url(),
                       # What fits: a model needs about its size in memory.
                       "memory_gb": openweight.memory_gb(),
                       "suggested": openweight.local_suggestions()},
            "openrouter": {"key": openweight.has_key(st.cfg),
                           "hint": openweight.key_hint(st.cfg),
                           "models": [model(m) for m in found.get(openweight.OPENROUTER, [])]},
            "deepseek": {"key": openweight.has_key(st.cfg, openweight.DEEPSEEK),
                         "hint": openweight.key_hint(st.cfg, openweight.DEEPSEEK),
                         "models": [model(m) for m in found.get(openweight.DEEPSEEK, [])]},
        }

    @app.post("/api/open/pull")
    async def api_open_pull(request: Request) -> dict[str, Any]:
        """Download a model into Ollama, so that a reader need not type
        `ollama pull` in Terminal. One at a time."""
        st = state_of(request)
        model = _text(await _json(request), "model") or ""
        if not openweight.can_pull(model):
            raise HTTPException(400, "That is not the name of a model.")
        if st.pull is not None and st.pull.state == "running":
            raise HTTPException(409, f"{st.pull.model} is still being downloaded.")
        st.pull = openweight.Pull(model=model)
        # Its own thread, not the pool's: quitting unpacman must not wait for
        # gigabytes to arrive (the pool's threads are waited for at exit).
        threading.Thread(target=st.pull.run, name=f"pull:{model}", daemon=True).start()
        return st.pull.to_dict()

    @app.get("/api/open/pull")
    async def api_open_pulling(request: Request) -> dict[str, Any]:
        st = state_of(request)
        if st.pull is not None and st.pull.state == "done" and st.discovery is not None:
            # Found at once, rather than when Ollama's list is next looked at.
            await asyncio.to_thread(st.discovery.ollama, fresh=True)
            await _refresh_open(st)
        return st.pull.to_dict() if st.pull is not None else {"state": "none"}

    @app.delete("/api/open/pull")
    async def api_open_pull_stop(request: Request) -> dict[str, Any]:
        st = state_of(request)
        if st.pull is not None:
            st.pull.cancel()
        return {"ok": True}

    @app.put("/api/open/key")
    async def api_open_key(request: Request) -> dict[str, Any]:
        """Keep a key for a host of models — OpenRouter's, or DeepSeek's —
        once the host has said it is one."""
        st = state_of(request)
        body = await _json(request)
        key = _text(body, "key") or ""
        host = _key_host(body.get("host"))
        try:
            # The format first: nothing that is not a key is sent anywhere.
            if not openweight.looks_like_key(key, host):
                raise openweight.OpenError(openweight.KEY_FORMS[host])
            check = (openweight.check_deepseek_key if host == openweight.DEEPSEEK
                     else openweight.check_key)
            about = await asyncio.to_thread(check, key.strip())
            openweight.save_key(st.cfg, key, host)
        except openweight.OpenError as exc:
            raise HTTPException(400, str(exc)) from exc
        assert st.ready is not None
        st.ready.forget()
        await _refresh_open(st)
        return {"ok": True, "hint": openweight.key_hint(st.cfg, host), **about}

    @app.delete("/api/open/key")
    async def api_open_key_forget(request: Request, host: str | None = None) -> dict[str, Any]:
        st = state_of(request)
        openweight.forget_key(st.cfg, _key_host(host))
        assert st.ready is not None
        st.ready.forget()
        await _refresh_open(st)
        return {"ok": True}

    @app.get("/api/open/deepseek")
    async def api_deepseek(request: Request) -> dict[str, Any]:
        """The saved DeepSeek key, as the page may show it: its last characters,
        whether DeepSeek will take it now, and the balance it has."""
        st = state_of(request)
        if not openweight.has_key(st.cfg, openweight.DEEPSEEK):
            return {"key": False}
        key = openweight.key_path(st.cfg, openweight.DEEPSEEK).read_text(encoding="utf-8").strip()
        out: dict[str, Any] = {"key": True, "hint": openweight.key_hint(st.cfg, openweight.DEEPSEEK)}
        try:
            out |= await asyncio.to_thread(openweight.check_deepseek_key, key)
        except openweight.OpenError as exc:
            out["error"] = str(exc)
        return out

    @app.get("/api/open/openrouter")
    async def api_openrouter(request: Request) -> dict[str, Any]:
        """The saved OpenRouter key, as the page may show it: its last
        characters, what OpenRouter says of it now (what is left of a limit set
        on it; whether its account has ever had credit), and the models it is
        offered for."""
        st = state_of(request)
        if not openweight.has_key(st.cfg):
            return {"key": False}
        key = openweight.key_path(st.cfg).read_text(encoding="utf-8").strip()
        await _refresh_open(st)
        out: dict[str, Any] = {
            "key": True, "hint": openweight.key_hint(st.cfg),
            "models": [{"id": m.id, "name": m.name, "small": m.small}
                       for m in (st.discovery.snapshot() if st.discovery else {}).get(
                           openweight.OPENROUTER, [])]}
        try:
            out |= await asyncio.to_thread(openweight.check_key, key)
        except openweight.OpenError as exc:
            out["error"] = str(exc)
        return out

    # --------------------------------------------------------------- method

    @app.get("/api/method")
    async def api_method(request: Request) -> dict[str, Any]:
        """The method's files, which of them the reader has edited, and where
        those edits are kept."""
        st = state_of(request)
        assert st.method is not None
        try:
            entries = st.method.entries()
        except method.MethodError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"where": str(st.method.root),
                "files": [e.to_dict() | {"edited": st.method.edited(e.path)}
                          for e in entries]}

    @app.get("/api/method/file")
    async def api_method_file(request: Request, path: str) -> dict[str, Any]:
        st = state_of(request)
        assert st.method is not None
        try:
            entry = st.method.entry(path)
            return entry.to_dict() | {"text": st.method.current(path),
                                      "original": st.method.original(path),
                                      "edited": st.method.edited(path)}
        except method.MethodError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.put("/api/method/file")
    async def api_method_save(request: Request) -> dict[str, Any]:
        """Keep the reader's version of one file — refused, and said why, if
        the method would no longer load with it."""
        st = state_of(request)
        assert st.method is not None
        body = await _json(request)
        path, text = body.get("path"), body.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            raise HTTPException(400, "Send a file's path and its text.")
        try:
            warnings = st.method.save(path, text)
        except method.MethodError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"edited": st.method.edited(path), "warnings": warnings}

    @app.delete("/api/method/file")
    async def api_method_reset(request: Request, path: str) -> dict[str, Any]:
        st = state_of(request)
        assert st.method is not None
        try:
            st.method.reset(path)
        except method.MethodError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"edited": False}

    @app.post("/api/method/reset")
    async def api_method_reset_all(request: Request) -> dict[str, Any]:
        st = state_of(request)
        assert st.method is not None
        st.method.reset_all()
        return {"edited": []}

    # --------------------------------------------------------- getting ready

    @app.get("/api/hello")
    async def api_hello(request: Request) -> dict[str, Any]:
        """Who answers here: how the icon and a second start recognise this
        copy's server, and how the page finds the new version after an update."""
        st = state_of(request)
        v = updates.current(st.cfg.root) if st.updater else None
        return {"app": "unpacman", "root": str(st.cfg.root), "pid": os.getpid(),
                "started": st.started, "version": v.to_dict() if v else None,
                "background": bool(st.control and st.control.background)}

    @app.post("/api/session")
    async def api_session(request: Request) -> Response:
        """Exchange the launch secret for a read-only browser session cookie,
        named for this server's port (security.cookie_name)."""
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie(security.cookie_name(request.scope), security.read_cookie(token),
                            httponly=True, samesite="strict", path="/api")
        return response

    @app.get("/api/ready")
    async def api_ready(request: Request, fresh: bool = False) -> dict[str, Any]:
        """What this computer has and still needs, and what is being installed."""
        st = state_of(request)
        assert st.ready is not None
        now = await st.ready.aget(fresh=fresh)
        return now.to_dict() | {"jobs": [j.to_dict() for j in st.ready.jobs.values()]}

    @app.post("/api/ready/{kind}")
    async def api_ready_start(request: Request, kind: str) -> dict[str, Any]:
        """Install Claude Code, Codex or LaTeX, or sign in to one of the AIs."""
        st = state_of(request)
        assert st.ready is not None
        if kind.startswith("install-") and platform.system() != "Darwin":
            raise HTTPException(400, "Installing from this page is for Macs. Install it as "
                                     "the README describes, then come back.")
        if kind in ("switch-claude", "switch-codex", "revert-claude", "revert-codex"):
            # Another account would take over a reading's next steps halfway,
            # and going back signs out the account its step is running on
            # (ready.Checker._switch, _revert): not while a reading uses it.
            provider = kind.split("-", 1)[1]
            if _ai_in_use(st.cfg, st.manager, provider):
                then = ("Change account" if kind.startswith("switch-")
                        else "Go back to this Mac's sign-in")
                raise HTTPException(409, f"A reading is using {ready.PROVIDER_NAMES[provider]} "
                                         f"now. {then} once it has finished.")
        try:
            return st.ready.start(kind).to_dict()
        except ready.Busy as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/ready/job/{ident}")
    async def api_ready_job(request: Request, ident: str) -> dict[str, Any]:
        st = state_of(request)
        assert st.ready is not None
        job = st.ready.job(ident)
        if job is None:
            raise HTTPException(404, "That job is not known here.")
        return job.to_dict()

    @app.post("/api/ready/job/{ident}/code")
    async def api_ready_code(request: Request, ident: str) -> dict[str, Any]:
        """The code Claude's sign-in page shows when it cannot return by itself."""
        st = state_of(request)
        assert st.ready is not None
        job = st.ready.job(ident)
        code = _text(await _json(request), "code")
        if job is None or job.kind not in ("signin-claude", "switch-claude") or not code:
            raise HTTPException(400, "Paste the code the sign-in page shows.")
        if len(code) > 400 or any(c.isspace() for c in code.strip()):
            raise HTTPException(400, "That does not look like the sign-in code.")
        if not await job.send(code):
            raise HTTPException(409, "The sign-in has already finished. Start it again.")
        return job.to_dict()

    @app.post("/api/ready/job/{ident}/cancel")
    async def api_ready_cancel(request: Request, ident: str) -> dict[str, Any]:
        st = state_of(request)
        assert st.ready is not None
        job = st.ready.job(ident)
        if job is None:
            raise HTTPException(404, "That job is not known here.")
        if job.task and not job.task.done():
            job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job.task
        return job.to_dict()

    # --------------------------------------------------------------- updates

    @app.get("/api/update")
    async def api_update(request: Request, check: bool = False) -> dict[str, Any]:
        """This copy's version, and whether a newer one is waiting — fetched
        from GitHub first when `check` is asked."""
        st = state_of(request)
        assert st.updater is not None
        if check:
            return await st.updater.refresh(fetch_first=True)
        return st.updater.status()

    @app.post("/api/update")
    async def api_update_apply(request: Request) -> dict[str, Any]:
        st = state_of(request)
        assert st.updater is not None
        if st.control is None or st.control.server is None:
            raise HTTPException(409, "This unpacman was started in a way that cannot restart "
                                     "itself. Stop it, update with git, and start it again.")
        try:
            st.updater.start(readings_running=len(st.manager.active()))
        except updates.UpdateRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        asyncio.get_running_loop().create_task(_restart_when_ready(st))
        return st.updater.status()

    # ----------------------------------------------------------------- quit

    @app.post("/api/quit")
    async def api_quit(request: Request) -> dict[str, Any]:
        """Stop unpacman. A running reading would stop with it, so that is
        refused unless the page says it was meant."""
        st = state_of(request)
        body = await _json(request)
        running = len(st.manager.active())
        if running and body.get("force") is not True:
            raise HTTPException(409, f"{running} reading{'s are' if running != 1 else ' is'} "
                                     "running, and quitting stops "
                                     f"{'them' if running != 1 else 'it'}.")
        if st.control is None or st.control.server is None:
            raise HTTPException(409, "This unpacman was not started in a way it can stop "
                                     "itself. Stop it where it was started.")
        asyncio.get_running_loop().call_later(0.3, _stop, st.control)
        return {"stopping": True, "background": st.control.background}

    # --------------------------------------------------------------- report

    @app.get("/api/report")
    async def api_report(request: Request, log: int = REPORT_LOG_LINES) -> Response:
        """What someone helping would want to know, to copy into an email.
        Versions and states only: never a paper, its title or its text.
        `log`: how many of the log's last lines to end with (fewer for an
        email written by Send feedback, where space is short)."""
        st = state_of(request)
        text = await asyncio.to_thread(_report, st, max(0, min(log, 200)))
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-store"})

    # ------------------------------------------------ the first run's suggestion

    @app.get("/api/first-ask")
    async def api_first_ask(request: Request) -> dict[str, Any]:
        """Whether the first run's suggestion — select some text, right-click
        it, ask — is still to be shown: until a selection is asked about; and
        whether a reading has been started, which is when it appears."""
        st = state_of(request)
        return {"pending": await asyncio.to_thread(firstask.pending, st.cfg),
                "started": await asyncio.to_thread(firstask.started, st.cfg)}

    # ----------------------------------------------------- new versions, by email

    @app.get("/api/news")
    async def api_news(request: Request) -> dict[str, Any]:
        """Whether this copy has asked to hear about new versions, and the
        address to suggest (news.py) — the reader's own, from their sign-in,
        shown to them and sent nowhere unless they say so."""
        st = state_of(request)
        kept = news.load(st.cfg)
        assert st.ready is not None
        suggested, where = news.suggestion(await st.ready.aget())
        signed = kept.get("answer") == "yes"
        return {"asked": bool(kept.get("asked")), "answer": kept.get("answer"),
                "email": kept.get("email") if signed else None,
                "via": kept.get("via") if signed else None,
                "suggestion": suggested, "from": where, "to": FEEDBACK_TO,
                "form": news.SIGNUP_FORM is not None}

    @app.post("/api/news")
    async def api_news_answer(request: Request) -> dict[str, Any]:
        st = state_of(request)
        body = await _json(request)
        v = updates.current(st.cfg.root)
        try:
            return await asyncio.to_thread(
                news.answer, st.cfg,
                # An empty field is not an answer of "no": it is refused.
                email=None if body.get("answer") == "no" else (_text(body, "email") or ""),
                to=FEEDBACK_TO, version=f"{v.short} ({v.date})" if v else "")
        except news.NewsError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/news")
    async def api_news_stop(request: Request) -> dict[str, Any]:
        st = state_of(request)
        v = updates.current(st.cfg.root)
        return await asyncio.to_thread(news.stop, st.cfg, to=FEEDBACK_TO,
                                       version=f"{v.short} ({v.date})" if v else "")

    @app.get("/api/feedback")
    async def api_feedback() -> dict[str, Any]:
        """Who Send feedback writes to (web/feedback.js)."""
        return {"to": FEEDBACK_TO}

    # ------------------------------------------- the reader's background (cv.py)

    @app.get("/api/cv")
    async def api_cv(request: Request) -> dict[str, Any]:
        """The reader's mathematical background, as kept on this computer."""
        st = state_of(request)
        kept = cv.load(st.cfg)
        return {"text": str(kept.get("text") or ""), "updated": kept.get("updated"),
                "max": cv.MAX_CHARS}

    @app.put("/api/cv")
    async def api_cv_save(request: Request) -> dict[str, Any]:
        st = state_of(request)
        body = await _json(request)
        if not isinstance(body.get("text"), str):
            raise HTTPException(400, "Send the text to keep.")
        try:
            return cv.save(st.cfg, body["text"]) | {"max": cv.MAX_CHARS}
        except cv.CvError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/cv")
    async def api_cv_forget(request: Request) -> dict[str, Any]:
        st = state_of(request)
        cv.forget(st.cfg)
        return {"text": "", "updated": None, "max": cv.MAX_CHARS}

    @app.post("/api/cv/file")
    async def api_cv_file(request: Request) -> dict[str, Any]:
        """The text of a CV file — a PDF, or plain text — for the reader to
        look over in the box before keeping it. Nothing is kept here."""
        st = state_of(request)
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            raise HTTPException(400, "Choose a file.")
        name = str(upload.filename)
        suffix = Path(name).suffix.lower()
        data = await upload.read(MAX_CV_BYTES + 1)
        if len(data) > MAX_CV_BYTES:
            raise HTTPException(413, "That file is larger than 20 MB.")
        if suffix == ".pdf":
            if not data[:1024].lstrip().startswith(b"%PDF-"):
                raise HTTPException(400, "That file is named .pdf but is not a PDF.")
            raw = await asyncio.to_thread(_extract, st, data)
        elif suffix in cv.TEXT_SUFFIXES:
            raw = data.decode("utf-8", errors="replace")
        else:
            raise HTTPException(400, "unpacman reads a CV from a PDF or a plain text file "
                                     "(.txt, .md, .tex). Or paste it into the box.")
        body, cut = cv.from_file_text(raw)
        if not body:
            raise HTTPException(400, "No text could be read from that file. Paste it into "
                                     "the box instead.")
        return {"text": body, "cut": cut, "name": name, "max": cv.MAX_CHARS}

    # ----------------------------------------------------------------- read

    @app.post("/api/papers/{slug}/continue")
    async def api_continue(request: Request, slug: str) -> dict[str, Any]:
        st = state_of(request)
        paper = _paper(st, slug)
        body = await _json(request)
        parent = readings.find(readings.all_readings(st.cfg, st.db, st.manager, paper),
                               _text(body, "run"))
        if parent is None:
            raise HTTPException(404, "That reading does not belong to this paper.")
        if parent.live or not parent.delivery.get("can_continue"):
            raise HTTPException(409, "This reading has no unfinished saved draft to continue.")
        if any(_level_of_handle(h) == parent.level for h in _live(st, paper)):
            raise HTTPException(409, "That paper is already being read at this level.")
        if body.get("max_minutes") in (None, ""):
            raise HTTPException(400, "Choose an additional time allowance before continuing.")
        _cap(body, "max_minutes", agents.MINUTES_RANGE, None,
             "The time cap must be between {:g} and {:g} minutes.")
        parent_dir = st.cfg.runs_dir / parent.run_id
        config = _read_json(parent_dir / "config.json")
        previous, resolved = config.get("spec") or {}, config.get("resolved") or {}
        harness = _harness(st, parent.level)
        choices = resolved.get("agents") or {}
        steps = {}
        for slot, roles in agents.step_roles(harness).items():
            if roles and choices.get(roles[0]):
                steps[slot] = choices[roles[0]] | {"custom": True}
        continued = {
            "account": resolved.get("account"), "model": resolved.get("model"),
            "effort": resolved.get("effort"), "custom": True, "steps": steps,
            "max_usd": previous.get("max_usd"),
            "max_minutes": body["max_minutes"],
            "max_concurrent": body.get("max_concurrent"),
            "include_map": bool(resolved.get("include_proof_map")),
        }
        profile = _read_json(parent_dir / PROFILE_FILE)
        if profile:
            profile["text"] = previous.get("reader_profile") or ""
        run_id = new_run_id()
        own = None
        try:
            if (parent_dir / "method" / "harness.toml").is_file():
                from atelier.access import check_job_tree
                check_job_tree(parent_dir / "method")
                own = st.cfg.runs_dir / run_id / "method"
                shutil.copytree(parent_dir / "method", own)
                harness = load_harness(own, variant=readings.VARIANTS[parent.level])
            return await _start_reading(st, paper, parent.level, continued, harness, run_id,
                                        profile or None, parent=parent,
                                        background=str((profile or {}).get("cv") or ""))
        finally:
            if own is not None and not (own.parent / "config.json").exists():
                shutil.rmtree(own.parent, ignore_errors=True)

    @app.post("/api/papers/{slug}/read")
    async def api_read(request: Request, slug: str) -> dict[str, Any]:
        st = state_of(request)
        paper = _paper(st, slug)
        body = await _json(request)
        level = str(body.get("level") or paper.levels[0])
        if level not in paper.levels:
            raise HTTPException(400, f"level must be one of {paper.levels}")
        # What the reader said they know (questions.py): Non-expert only.
        profile = _profile_from(st, paper, level, body.get("profile"))
        # And who they are, in their own words (cv.py), unless they leave it
        # out of this reading. A search is not written for a reader.
        background = (cv.text(st.cfg) if paper.kind != "search"
                      and body.get("use_cv") is not False else "")

        # One reading per level at a time. An Expert and a Non-expert reading of
        # the same paper run side by side: each works in its own snapshot.
        if any(h for h in _live(st, paper) if _level_of_handle(h) == level):
            raise HTTPException(409, "That paper is already being read at this level.")
        if paper.kind == "search":
            # The question as asked now, with today's date: "the latest" moves.
            try:
                library.set_query(paper, _text(body, "query") or paper.query)
            except ProblemError as exc:
                raise HTTPException(400, str(exc)) from exc

        # A reading started while the method is edited is given its own copy
        # of it, in its run, and loaded from there (method.py). Made before the
        # run is planned, and removed again if the reading does not start.
        run_id = new_run_id()
        own = None
        harness = _harness(st, level)
        assert st.method is not None
        if st.method.reading_edits():
            own = st.cfg.runs_dir / run_id / "method"
            try:
                st.method.materialise(own)
                method.check(own)
                harness = load_harness(own, variant=readings.VARIANTS[level])
            except (method.MethodError, HarnessError, OSError) as exc:
                shutil.rmtree(own.parent, ignore_errors=True)
                raise HTTPException(
                    400, f"Your edited method could not be used: {exc} "
                         "Fix it, or reset it, in Advanced settings.") from exc
        try:
            return await _start_reading(st, paper, level, body, harness, run_id, profile,
                                        background=background)
        except BaseException:
            if own is not None and not (st.cfg.runs_dir / run_id / "config.json").exists():
                shutil.rmtree(own.parent, ignore_errors=True)
            raise

    async def _start_reading(st: AppState, paper: Paper, level: str, body: dict[str, Any],
                             harness: Harness, run_id: str,
                             profile: dict[str, Any] | None = None, *,
                             parent: readings.Reading | None = None,
                             background: str = "") -> dict[str, Any]:
        try:
            library.check_source_page_limit(
                await asyncio.to_thread(library.source_page_count, paper))
        except (ProblemError, SourceError) as exc:
            raise HTTPException(400, str(exc)) from exc
        harness = _reading_harness(harness, include_map=(
            body.get("include_map") is True and paper.kind == "paper"))
        if _asks_open(st, body):
            await _refresh_open(st)
        offers, default = _offers(st, harness)
        try:
            agent = agents.choose(offers, default, account=_text(body, "account"),
                                  model=_text(body, "model"),
                                  effort=_text(body, "effort"),
                                  custom=body.get("custom") is True)
        except agents.Unavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        role_agents, step_agents = _step_agents(harness, offers, agent, body.get("steps"))
        # A model on this Mac reads with its copy that holds a whole paper —
        # and a paper longer than even that is refused here, not mid-reading.
        agent, step_agents, role_agents = await _whole_paper(
            st, paper, agent, step_agents, role_agents)

        # Before anything is spent: a reading that cannot finish — no sign-in,
        # no LaTeX to typeset its companion — is refused here, not after its
        # first pass has been paid for. Every AI the reading will use is asked.
        assert st.ready is not None
        now = await st.ready.aget(fresh=True)
        missing: list[str] = []
        for who in _distinct([agent, *step_agents.values()]):
            # A paper and a collection both finish with a typeset companion. A
            # literature search is the one kind of reading that has no PDF.
            for line in now.blocking(who.provider, who.account,
                                     latex=paper.kind != "search"):
                if line not in missing:
                    missing.append(line)
        if missing:
            raise HTTPException(409, " ".join(missing) + " Open Getting ready, in "
                                "Advanced settings, to put it right; nothing has been spent.")

        # Readiness checks await I/O; another request may have started meanwhile.
        if any(_level_of_handle(h) == level for h in _live(st, paper)):
            raise HTTPException(409, "That paper is already being read at this level.")

        requested_concurrency = _parallel_cap(
            body, st.cfg.limits.max_concurrent_runs)
        active_count = len(st.manager.active())
        if active_count >= requested_concurrency:
            raise HTTPException(
                429, f"Your webpage cap allows {requested_concurrency} parallel "
                f"reading{'s' if requested_concurrency != 1 else ''}; "
                f"{active_count} already running.")

        library.set_level(paper, level)
        slug = paper.slug
        spec = RunSpec(
            run_id=run_id,
            problem=slug, harness=harness.id, mode="continue" if parent else "standard",
            parent_run_id=parent.run_id if parent else None,
            task_kind="files",
            task_files=[paper.source.relative_to(paper.root).as_posix()],
            # The harness variant is what reads at this level; the note says it
            # again for runs older than the variants, and for the lab book.
            notes=f"reader level: {level}",
            # Show the reader's background and answers to every pass. A
            # continuation reuses the assembled profile without adding the
            # saved background a second time.
            reader_profile=(profile["text"] if parent and profile else
                            "\n\n".join(x for x in (
                                _reader_block(st.cfg, st.method, background),
                                profile["text"] if profile else "") if x)),
            provider=agent.provider, account=agent.account,
            model=agent.model, effort=agent.effort,
            # The check or the revision on another AI, if the page asked.
            role_agents=role_agents,
            # Advanced settings may set the caps for this reading; otherwise
            # the server's own (the command line's, or the harness's).
            max_usd=_cap(body, "max_usd", agents.USD_RANGE, st.max_usd,
                         "The money cap must be between ${:g} and ${:g}."),
            max_minutes=_cap(body, "max_minutes", agents.MINUTES_RANGE, st.max_minutes,
                             "The time cap must be between {:g} and {:g} minutes."),
        )
        for who in _distinct([agent, *step_agents.values()]):
            ok, why = st.manager.concurrency_ok(who.account)
            if not ok:
                raise HTTPException(429, why)

        # As the lab does since Antoine's change: the lock is held only while a
        # consistent snapshot of the paper is taken, and the reading then runs
        # inside that private copy, archived to runs/<id>/reading/ at the end.
        session_number = next_session_number(st.cfg, paper.problem)
        try:
            acquire_lock(paper.root, spec.run_id, harness.id)
        except LockHeld as exc:
            # Normally another reading taking its snapshot, which is a moment's
            # work — but a session from the lab may hold it for longer.
            raise HTTPException(409, "This paper is busy — another reading is starting, "
                                     "or a session from the lab is using it. Try again "
                                     "in a moment.") from exc
        try:
            plan = plan_run(st.cfg, paper.problem, harness, spec,
                            session_number=session_number, isolate_reading=True,
                            source_char_limit=_reader_input_limit(
                                st, [agent, *step_agents.values()]))
        except ValueError as exc:
            release_lock(paper.root, spec.run_id)
            shutil.rmtree(st.cfg.runs_dir / spec.run_id, ignore_errors=True)
            raise HTTPException(400, str(exc)) from exc
        except Exception:
            release_lock(paper.root, spec.run_id)
            raise
        release_lock(paper.root, spec.run_id)
        if (why := _too_long(st, plan)):
            shutil.rmtree(plan.run_dir, ignore_errors=True)
            raise HTTPException(400, why)
        if profile or background:
            # The questions as shown and the answers given, and the background
            # the reader gave, beside the run: the companion says it was
            # written for them, and can show them.
            record = {k: v for k, v in (profile or {}).items() if k != "text"}
            if background:
                record["cv"] = background
            (plan.run_dir / PROFILE_FILE).write_text(json.dumps(
                record, indent=1, ensure_ascii=False), encoding="utf-8")
        if profile and not parent:
            questions.remember(paper, profile["key"], profile["questions"],
                               profile["answers"])
        handle = await st.manager.launch(plan, session_number=session_number)
        if paper.kind != "search":
            # A first reading under way: the first run's suggestion can show.
            await asyncio.to_thread(firstask.begun, st.cfg)
        return {"run_id": handle.run_id} | _status(st, paper, level)

    @app.post("/api/papers/{slug}/stop")
    async def api_stop(request: Request, slug: str) -> dict[str, Any]:
        """Stop one reading: the run named, else the one at the level named."""
        st = state_of(request)
        paper = _paper(st, slug)
        body = await _json(request)
        run, level = _text(body, "run"), _text(body, "level")
        for handle in _live(st, paper):
            if (run and handle.run_id == run) or (not run and level
                                                  and _level_of_handle(handle) == level):
                await handle.stop("stopped from the reader")
        return _status(st, paper, level, run)

    # ------------------------------------------------------- the questions

    @app.post("/api/papers/{slug}/questions")
    async def api_questions(request: Request, slug: str) -> dict[str, Any]:
        """The questions asked before a Non-expert reading — of a paper, or of
        several papers read together: kept, or made now by the AI the reading
        will use (questions.py). Takes a minute or so the first time; answers
        with the reader's last answers to start from."""
        st = state_of(request)
        paper = _paper(st, slug)
        level = questions.ASKING_LEVEL.get(paper.kind)
        if level is None:
            raise HTTPException(400, "Questions are asked before a Non-expert reading of "
                                     "a paper, or of several papers read together.")
        body = await _json(request)
        offers, default = _offers(st, _harness(st, level))
        try:
            agent = agents.choose(offers, default, account=_text(body, "account"),
                                  model=_text(body, "model"), effort=_text(body, "effort"),
                                  custom=body.get("custom") is True)
        except agents.Unavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        assert st.ready is not None and st.method is not None and st.questioner is not None
        missing = (await st.ready.aget(fresh=True)).blocking(
            agent.provider, agent.account, latex=False)
        if missing:
            raise HTTPException(409, " ".join(missing) + " Open Getting ready, in "
                                "Advanced settings, to put it right.")
        reader = (_reader_block(st.cfg, st.method) if body.get("use_cv") is not False else "")
        try:
            made = await st.questioner.get(paper, agent,
                                           st.method.current("harness/questionnaire.md"),
                                           reader=reader)
        except questions.QuestionsError as exc:
            raise HTTPException(502, str(exc)) from exc
        return made.to_dict() | {
            "answers": questions.last_answers(paper, made.key, made.questions),
            "names": questions.ANSWER_NAMES,
        }

    @app.get("/api/papers/{slug}/answers")
    async def api_answers(request: Request, slug: str, run: str) -> dict[str, Any]:
        """The questions a reading's reader was shown, and their answers."""
        st = state_of(request)
        paper = _paper(st, slug)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        if readings.find(found, run) is None:
            raise HTTPException(404, "That reading does not belong to this paper.")
        record = _read_json(st.cfg.runs_dir / run / PROFILE_FILE)
        if not record:
            raise HTTPException(404, "That reading was not written for any answers.")
        asked = record.get("questions") or []
        answers = record.get("answers") or {}
        return {"questions": questions.asked(asked, answers), "answers": answers,
                "names": questions.ANSWER_NAMES, "made_by": record.get("made_by") or {},
                "cv": str(record.get("cv") or "")}

    # ------------------------------------------------------------ companion

    @app.get("/api/papers/{slug}/companion")
    async def api_companion(request: Request, slug: str, level: str | None = None,
                            run: str | None = None) -> dict[str, Any]:
        st = state_of(request)
        paper = _paper(st, slug)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        reading = _companion_reading(found, paper, level, run)
        # Collection runs deliberately have a small, single-pass contract:
        # they write their guide, then the application gives that exact guide
        # the same persistent Markdown/LaTeX/PDF treatment as a one-paper
        # companion.  Do it lazily so completed collections from before this
        # change gain a PDF too, rather than requiring a costly re-run.
        if reading is not None and paper.kind == "collection":
            await _ensure_collection_companion(paper, reading)
        companion = library.read_companion(paper, reading.dir) if reading else None
        if companion is None or reading is None:
            raise HTTPException(404, "This paper has not been read yet.")
        levels = readings.by_level(found, paper.levels)
        at = levels.get(reading.level) or readings.Level(reading.level)
        latest, current = at.latest, at.companion
        return {
            "title": companion.title,
            "preamble": (_companion_html(companion.preamble, slug, reading)
                         if companion.preamble else ""),
            "sections": [{"key": s.key, "label": s.label, "heading": s.heading,
                          "tone": s.tone,
                          "html": _companion_html(s.markdown, slug, reading)}
                         for s in companion.sections],
            "missing": companion.missing,
            "files": companion.files,
            "review": library.read_review(reading.dir),
            "has_map": (reading.dir / "graph.json").is_file(),
            "run_id": reading.run_id,
            "level": reading.level,
            # Which of the paper's readings this is, for a paper read more than once.
            "number": reading.number, "runs": len(found), "started": reading.when,
            "by": {"level": reading.level, "agent": agents.describe(
                reading.row.get("provider"), reading.row.get("account"),
                reading.row.get("model"), reading.row.get("effort")),
                   # Each step's AI, when they were not all one.
                   "steps": _credited_steps(st, reading),
                   # The files of the method it ran with that the reader had
                   # edited — None when it ran with the method as it ships.
                   "own_method": (method.used_by(st.cfg.runs_dir / reading.run_id)
                                  if reading.run_id else None),
                   # Written for answers the reader gave (questions.py).
                   "profile": _profile_summary(st.cfg, reading.run_id)},
            # An earlier companion, opened from the list of runs: the one this
            # level opens with now, which is newer.
            "current": (_run_view(st, current)
                        if current is not None and current is not reading else None),
            # The companion this level opens with, and a newer reading that has
            # not replaced it: still running, or stopped before it could. The
            # page says which. (Of an earlier companion, `current` says it.)
            "newer": (_run_view(st, latest)
                      if current is reading and latest is not None and latest is not reading
                      and latest.moment > reading.moment else None),
            "readings": _overview(levels),
            "kind": paper.kind,
            # A search's papers, each with what the page may show (library.read_results).
            **({"results": companion.results} if companion.results is not None else {}),
        }

    # ------------------------------------------------------------ explaining

    @app.get("/api/papers/{slug}/explanations")
    async def api_explanations(request: Request, slug: str) -> dict[str, Any]:
        """What this paper has been asked about, and who answers next."""
        st = state_of(request)
        paper = _paper(st, slug)
        harness = _harness(st)
        offers, default = _offers(st, harness)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        reading = _companion_reading(found, paper, request.query_params.get("level"),
                                     request.query_params.get("run"))
        who = _explainer_default(reading, offers, default)
        pages = await _page_count(paper)
        return {
            "items": [explain.public(i, pages) for i in explain.load_store(paper)],
            "notes": st.conversations.public(paper, pages),
            "default": ({"account": who.account, "model": who.model, "effort": "high"}
                        | ({"custom": True} if who.custom else {})
                        if who else None),
            "pages": pages,
        }

    @app.get("/api/papers/{slug}/notes")
    async def api_notes(request: Request, slug: str) -> dict[str, Any]:
        st = state_of(request)
        paper = _paper(st, slug)
        return {"notes": st.conversations.public(paper, await _page_count(paper))}

    @app.delete("/api/papers/{slug}/notes/{uid}")
    async def api_remove_note(request: Request, slug: str, uid: str) -> dict[str, bool]:
        st = state_of(request)
        st.conversations.remove(_paper(st, slug), uid)
        return {"removed": True}

    @app.post("/api/papers/{slug}/explain")
    async def api_explain(request: Request, slug: str) -> StreamingResponse:
        """Explain a selection, streamed as one JSON object per line.

        `start`, then any number of `delta`, then `done` or `error`. A question
        asked before comes back at once as a single `done`.
        """
        st = state_of(request)
        paper = _paper(st, slug)
        body = await _json(request)
        metadata = body.get("conversation")
        ref = conversations.reference(metadata)
        if ref and (existing := st.conversations.existing(paper, ref)):
            if existing["status"] == "done":
                pages = await _page_count(paper)
                async def kept():
                    yield json.dumps({"type": "done", "cached": True,
                                      **explain.public(existing["answer"], pages)}, ensure_ascii=False) + "\n"
                return StreamingResponse(kept(), media_type="application/x-ndjson")
            body = existing["request"]
        where = _where(body.get("where"))
        # A question about the paper as a whole has no selection (the corner's
        # Ask button, 2026-10-23); anything else is about one.
        whole = where["kind"] == "whole"
        text = "" if whole else _text(body, "text")
        if not text and not (whole and _text(body, "question")):
            raise HTTPException(400, "Ask a question first." if whole
                                else "Select some text to explain.")
        # Asked about a selection: the first run's suggestion has done its work.
        if not whole:
            await asyncio.to_thread(firstask.asked, st.cfg)
        context = body.get("context") if isinstance(body.get("context"), str) else ""
        harness = _harness(st)
        offers, default = _offers(st, harness)
        # The reading being read: its companion is what the explainer is given,
        # and its level sets the register of the answer.
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        reading = _companion_reading(found, paper, _text(body, "level"), _text(body, "run"))
        who = _explainer_default(reading, offers, default)
        try:
            agent = agents.choose(offers, who, account=_text(body, "account"),
                                  model=_text(body, "model"),
                                  effort=_text(body, "effort") or "high",
                                  custom=body.get("custom") is True)
        except agents.Unavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        level = (reading.level if reading else None) or _text(body, "level") \
            or paper.level or "expert"
        question = _text(body, "question") or ""
        depth = "ask" if question else "more" if body.get("depth") == "more" else "brief"
        brief = body.get("brief") if isinstance(body.get("brief"), str) else ""
        ask = explain.Ask(text=text, context=context.strip()[:explain.MAX_CONTEXT],
                          where=where,
                          level=level if level in library.ALL_LEVELS else "expert",
                          guide=reading.guide if reading else None,
                          depth=depth, brief=brief.strip(),
                          ask=question, history=_exchanges(body.get("history")))

        keep = None
        if ref:
            if (ref[1] == "more") != (depth == "more"):
                raise HTTPException(400, "Invalid message kind.")
            if ref[1] not in ("root", "more") and not question:
                raise HTTPException(400, "A follow-up needs a question.")
            saved_request = {
                "text": ask.text, "context": ask.context, "where": ask.where,
                "level": ask.level, "run": _text(body, "run"),
                "question": question, "depth": depth, "brief": ask.brief,
                "history": ask.history, "account": agent.account,
                "model": agent.model, "effort": agent.effort, "custom": agent.custom,
            }
            st.conversations.begin(paper, ref, saved_request, metadata)
            keep = lambda item, error: st.conversations.finish(paper, ref, item, error)

        async def events():
            assert st.explainer is not None
            async for event in st.explainer.explain(
                    paper, ask, agent, keep=keep, identity=(paper.slug, *ref) if ref else None):
                yield event

        # The subscription, including cached/joined answers, belongs to the
        # server. Closing the requesting tab cannot cancel saving its message.
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        listening = True
        if ref:
            async def record():
                try:
                    async for event in events():
                        if listening:
                            queue.put_nowait(event)
                except Exception:                                 # noqa: BLE001
                    error = "The explanation could not be saved. Try again."
                    with contextlib.suppress(Exception):
                        st.conversations.finish(paper, ref, None, error)
                    queue.put_nowait({"type": "error", "message": error})
            assert st.explainer is not None
            task = asyncio.create_task(record())
            st.explainer.tasks.add(task)
            task.add_done_callback(st.explainer.tasks.discard)

        async def lines():
            nonlocal listening
            if ref:
                try:
                    while True:
                        event = await queue.get()
                        yield json.dumps(event, ensure_ascii=False) + "\n"
                        if event["type"] in ("done", "error"):
                            return
                finally:
                    listening = False
            else:
                async for event in events():
                    yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/api/papers/{slug}/glossary")
    async def api_glossary(request: Request, slug: str) -> dict[str, Any]:
        """The key terms of one reading's companion, each with a short
        explanation, for the page to link — kept, or made now (which takes as
        long as one answer of some length: tens of seconds, once)."""
        st = state_of(request)
        paper = _paper(st, slug)
        body = await _json(request)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        reading = _companion_reading(found, paper, _text(body, "level"), _text(body, "run"))
        if reading is None or not reading.guide.is_file():
            raise HTTPException(404, "This paper has no companion to link yet.")
        harness = _harness(st)
        offers, default = _offers(st, harness)
        who = _explainer_default(reading, offers, default)
        try:
            agent = agents.choose(offers, who, account=_text(body, "account"),
                                  model=_text(body, "model"),
                                  effort=_text(body, "effort") or "high",
                                  custom=body.get("custom") is True)
        except agents.Unavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        assert st.explainer is not None
        return await st.explainer.glossary(
            paper, reading.run_id or "kept", reading.guide, reading.level, agent)

    @app.get("/api/papers/{slug}/map")
    async def api_map(request: Request, slug: str, level: str | None = None,
                      run: str | None = None) -> dict[str, Any]:
        st = state_of(request)
        paper = _paper(st, slug)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        reading = _companion_reading(found, paper, level, run)
        drawn = library.read_map(reading.dir) if reading else None
        if drawn is None:
            raise HTTPException(404, "This paper has no map yet.")
        return drawn

    @app.get("/api/papers/{slug}/file/{name}")
    async def api_file(request: Request, slug: str, name: str,
                       level: str | None = None, run: str | None = None) -> Response:
        st = state_of(request)
        paper = _paper(st, slug)
        reading_dir = paper.reading
        reading: readings.Reading | None = None
        if name != "source.pdf":
            found = readings.all_readings(st.cfg, st.db, st.manager, paper)
            reading = _companion_reading(found, paper, level, run)
            if reading is None:
                raise HTTPException(404, "no such file")
            reading_dir = reading.dir
        if name == "companion.pdf" and reading is not None and paper.kind == "collection":
            # Direct downloads need the same backward-compatible lazy build as
            # the companion page; otherwise a bookmarked PDF link would 404
            # until the page had first been opened.
            await _ensure_collection_companion(paper, reading)
        path = artefact_path(paper, reading_dir, name)
        if path is None:
            raise HTTPException(404, "no such file")
        media = {".pdf": "application/pdf", ".json": "application/json",
                 ".tex": "application/x-tex; charset=utf-8"}.get(
            path.suffix.lower(), "text/plain; charset=utf-8")
        headers = {"Cache-Control": "no-cache"}
        if name == "guide.md":
            from deslopify.summary_pdf import dated_guide

            return Response(dated_guide(path) + "\n", media_type=media, headers=headers)
        if name == "companion.pdf" and reading is not None:
            path = await _companion_with_contents(paper, reading, path)
        if path.suffix.lower() == ".tex":
            # A LaTeX source is something to keep, not to look at in a tab —
            # and the one that typesets the PDF offered beside it, contents
            # included, even for a companion written before it had any.
            from deslopify.summary_pdf import generation_date, with_contents

            headers["Content-Disposition"] = f'attachment; filename="{slug}-companion.tex"'
            text = path.read_text(encoding="utf-8", errors="replace")
            return Response(with_contents(text, generated_on=generation_date(reading.guide)),
                            media_type=media, headers=headers)
        return FileResponse(path, media_type=media, headers=headers)

    @app.get("/api/papers/{slug}/asset/{name:path}")
    async def api_reading_asset(request: Request, slug: str, name: str,
                                level: str | None = None,
                                run: str | None = None) -> FileResponse:
        st = state_of(request)
        paper = _paper(st, slug)
        found = readings.all_readings(st.cfg, st.db, st.manager, paper)
        reading = _companion_reading(found, paper, level, run)
        if reading is None or not name.startswith("figures/"):
            raise HTTPException(404, "no such asset")
        root = reading.dir.resolve()
        path = (root / name).resolve()
        try:
            path.relative_to(root / "figures")
        except ValueError:
            raise HTTPException(404, "no such asset") from None
        if not path.is_file() or not is_image(path.suffix):
            raise HTTPException(404, "no such asset")
        return FileResponse(path, headers={"Cache-Control": "no-cache"} | ASSET_HEADERS)

    # ---------------------------------------------------------------- static

    app.mount("/static", _Revalidated(directory=str(WEB_DIR)), name="static")
    if VENDOR_DIR is not None:
        app.mount("/vendor", _Revalidated(directory=str(VENDOR_DIR)), name="vendor")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        import traceback

        return JSONResponse(
            {"detail": f"{type(exc).__name__}: {exc}",
             "trace": traceback.format_exc()[-3000:]}, status_code=500)

    return app


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


class _Revalidated(StaticFiles):
    """The page's own files, asked about again on every load.

    Served without this, a browser may keep last week's scripts by its own
    reckoning; after an update, that is an old page talking to a new server.
    `no-cache` keeps the copy but checks it first, and an unchanged file costs
    a 304.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        if path.endswith(".html"):
            # The page reached as a file is held to the same policy as at "/".
            response.headers.update(PAGE_HEADERS)
        return response


# Requests kept for the report, and how long an answer may take before it is
# remembered as slow. An explanation or a list of questions takes longer than
# this as a matter of course, and is remembered as slow only past the longer.
TROUBLE_KEPT = 30
SLOW_S = 15.0
SLOW_CALL_S = 150.0
_SLOW_CALLS = ("/explain", "/glossary", "/questions", "/fetch", "/arxiv")
_PAPER_IN_PATH = re.compile(r"(/api/papers/)[^/]+")


class _Trouble:
    """Remembers requests that failed (any error from the server; a missing
    paper file, which a reader sees as a paper that will not open) or that
    took long to start answering — one line each, without the paper's name.

    Plain ASGI rather than a FastAPI middleware, so that a streamed answer (an
    explanation arriving word by word) passes through untouched.
    """

    def __init__(self, app: Any, log: deque[str]) -> None:
        self.app = app
        self.log = log

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        path = _PAPER_IN_PATH.sub(r"\1…", str(scope.get("path", "")))
        method = scope.get("method", "")

        def note(status: int | str) -> None:
            took = time.monotonic() - started
            slow = SLOW_CALL_S if path.endswith(_SLOW_CALLS) else SLOW_S
            failed = isinstance(status, str) or status >= 500 or (
                status == 404 and ("/file/" in path or "/asset/" in path))
            if failed or took > slow:
                self.log.append(f"{time.strftime('%H:%M:%S')} {method} {path} → {status}"
                                f" after {took:.1f} s")

        async def watched(message: Any) -> None:
            if message.get("type") == "http.response.start":
                note(int(message.get("status", 0)))
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except Exception as exc:
            note(f"failed ({type(exc).__name__})")
            raise


def _stop(control: Control) -> None:
    if control.server is not None:
        control.server.should_exit = True


async def _restart_when_ready(st: AppState) -> None:
    """Once the update is in place and has started on the side, let the new
    version take over: stop this server, which `__main__` then replaces."""
    assert st.updater is not None and st.control is not None
    while st.updater.state == "updating":
        await asyncio.sleep(0.5)
    if st.updater.restart:
        st.control.restart = True
        await asyncio.sleep(0.5)          # let the page hear "restarting"
        _stop(st.control)


# Where Send feedback writes to: the people who make unpacman. The page writes
# the email in the reader's own email program (web/feedback.js) — unpacman
# itself sends nothing — so this address is all there is to change. The news
# card's "Keep me posted" writes to it too (news.py). unpacman's own address
# since 2026-10-15, at Tancredi's request; his personal one before.
FEEDBACK_TO = "unpacman.support@gmail.com"
REPORT_LOG_LINES = 60


def _report(st: AppState, log_lines: int = REPORT_LOG_LINES) -> str:
    """The text "Copy a report" puts on the clipboard, and Send feedback
    attaches; it ends with the last `log_lines` lines of the log."""
    home = str(Path.home())

    def tidy(text: str) -> str:
        return text.replace(home, "~")

    lines = [f"unpacman report — {time.strftime('%Y-%m-%d %H:%M %Z')}", ""]
    v = updates.current(st.cfg.root)
    lines.append("Version: " + (f"{v.short} ({v.date}), branch {v.branch}"
                                + (f", following {v.upstream}" if v.upstream else "")
                                if v else "not a git checkout"))
    if st.updater is not None:
        u = st.updater.status()
        if u.get("package") is not None:
            pkg = u["package"] or {}
            lines.append(f"Installed from: a package made {pkg.get('made', '(date unknown)')}"
                         + (f", version {str(pkg.get('commit', ''))[:7]}" if pkg.get("commit") else ""))
        lines.append(f"Updates: {'available' if u['available'] else 'none waiting'}"
                     + (f"; behind by {u['behind']}" if u["behind"] else "")
                     + (f"; {u['why_not']}" if u["why_not"] else "")
                     + (f"; last check failed: {u['error']}" if u["error"] else ""))
    lines.append(f"Copy: {tidy(str(st.cfg.root))}"
                 + ("; has an icon" if instance.app_marker(st.cfg.root).is_file() else ""))
    started = ("from its icon" if st.control and st.control.background
               else "from a Terminal" if st.control else "by a test")
    lines.append(f"Running: started {started}, "
                 f"{round((time.time() - st.started) / 60)} min ago, port "
                 f"{st.control.port if st.control else '?'}")
    mac = platform.mac_ver()[0]
    lines.append(f"Computer: {'macOS ' + mac if mac else platform.platform()} "
                 f"({platform.machine()}), Python {sys.version.split()[0]}")
    lines.append("")
    if st.ready is not None:
        lines.append(tidy(ready.describe(st.ready.get()).strip("\n")))
        lines.append("")
    papers = list_papers(st.cfg)
    lines.append(f"Papers: {len(papers)}; readings running now: {len(st.manager.active())}")
    rows = st.db.query(
        "SELECT id, harness, status, provider, model, effort, wall_seconds, "
        "cost_computed, ended_reason, error FROM runs WHERE harness LIKE ? "
        "ORDER BY started_at DESC LIMIT 8", (f"{readings.FAMILY}%",))
    for row in rows:
        plain = ready.plain_failure(str(row["error"] or ""), str(row["ended_reason"] or ""))
        lines.append(
            f"- {row['id']} {row['harness']} {row['status']} {row['model']}/{row['effort']} "
            f"{round((row['wall_seconds'] or 0) / 60, 1)} min ${row['cost_computed'] or 0:.2f}"
            + (f" — {tidy(str(row['ended_reason']))[:160]}" if row["ended_reason"] else "")
            + (f" — {plain}" if plain else ""))
    if st.trouble:
        lines += ["", "Requests that failed or were slow, since unpacman started:"]
        lines += [f"  {line}" for line in st.trouble]
    if log_lines > 0:
        lines += ["", "Log (the last lines):"]
        lines += [tidy(line) for line in instance.log_tail(st.cfg.root, log_lines)] or ["(no log)"]
    return "\n".join(lines) + "\n"


def _live(st: AppState, paper: Paper) -> list[Any]:
    """Every reading of this paper running now."""
    return [h for h in st.manager.active() if h.plan.problem.slug == paper.slug]


def _level_of_handle(handle: Any) -> str:
    return readings.level_of(handle.plan.harness.id, handle.plan.spec.notes or "")


def _companion_reading(found: list[readings.Reading], paper: Paper, level: str | None,
                       run: str | None) -> readings.Reading | None:
    """The reading whose companion is wanted: the one named, if it has one;
    otherwise the newest at the level asked for, or at the level the paper
    opens at."""
    credited = readings.with_companion(found)
    if run:
        if run == "kept":
            return next((r for r in credited if r.run_id is None), None)
        named = readings.find(credited, run)
        if named is not None:
            return named
        return None
    if level not in paper.levels:
        level = readings.default_level(found, paper)
    hit = next((r for r in credited if r.level == level), None)
    return hit or (credited[0] if credited and not level else None)


def _add_pdf(st: AppState, name: str, data: bytes, *, title: str = "") -> dict[str, Any]:
    """A PDF, dropped or fetched, made a paper — or the paper it already is."""
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "That PDF is larger than 100 MB.")
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise HTTPException(400, "That file is named .pdf but is not a PDF.")
    try:
        library.check_source_page_limit(pdf_page_count(data))
    except (ProblemError, SourceError) as exc:
        raise HTTPException(400, str(exc)) from exc

    if (known := library.find_by_fingerprint(st.cfg, library.fingerprint(data))):
        return known.to_dict() | {"existing": True} | _library_entry(st, known)

    # Read it before creating anything. A PDF that cannot be read — a scan,
    # an encrypted file, something pdftotext chokes on — should be refused
    # here, not four minutes into a session, and should leave no trace.
    text = _extract(st, data)
    words = len(text.split())
    # A scanned page yields a handful of words or none; a genuine short note
    # yields hundreds. The threshold only has to tell those apart.
    if words < 120:
        raise HTTPException(
            400, "Almost no text could be extracted from that PDF — it is "
                 "probably a scan of page images. Run OCR on it first, then "
                 "try again.")

    try:
        paper = create_paper(st.cfg, name, data,
                             title=title or library.guess_title_from(data, text, name))
    except ProblemError as exc:
        raise HTTPException(400, str(exc)) from exc
    return paper.to_dict() | {"words": words}


def _profile_from(st: AppState, paper: Paper, level: str, raw: Any) -> dict[str, Any] | None:
    """The reader's answers sent with a Non-expert reading (of a paper, or of
    several papers read together), checked against the questions they were
    given, and the text every pass will be shown. Sent with any other reading,
    they are not used: it asks nothing."""
    if not isinstance(raw, dict) or level != questions.ASKING_LEVEL.get(paper.kind):
        return None
    key = str(raw.get("key") or "")
    hit = questions.kept(paper, key)
    if hit is None:
        raise HTTPException(400, "The questions these answers are for are no longer "
                                 "kept. Answer them again, or start without them.")
    asked = hit["questions"]
    answers = questions.clean_answers(asked, raw.get("answers"))
    assert st.method is not None
    text = questions.profile_text(st.method.current("harness/reader-profile.md"),
                                  asked, answers)
    shown = questions.asked(asked, answers)
    return {"key": key, "questions": asked, "answers": answers, "text": text,
            "asked": len(shown), "answered": len(answers),
            "made_by": hit.get("agent") or {}}


def _profile_summary(cfg: Config, run_id: str | None) -> dict[str, Any] | None:
    """How many questions a reading's reader answered, if it was asked any,
    and whether it was written for the background they gave (cv.py)."""
    if not run_id:
        return None
    record = _read_json(cfg.runs_dir / run_id / PROFILE_FILE)
    if not record:
        return None
    return {"asked": int(record.get("asked") or 0), "answered": int(record.get("answered") or 0),
            "cv": bool(record.get("cv"))}


def _reader_block(cfg: Config, edits: method.Method | None,
                  background: str | None = None) -> str:
    """The reader's background as the AI is shown it — how to use it (the
    method's `harness/reader-cv.md`, the reader's own version if edited), then
    the background itself — or "" when there is none."""
    background = cv.text(cfg) if background is None else background
    if not background:
        return ""
    instructions = cv.FALLBACK
    if edits is not None:
        try:
            instructions = edits.current("harness/reader-cv.md")
        except method.MethodError:
            pass
    return cv.block(instructions, background)


def _library_entry(st: AppState, paper: Paper) -> dict[str, Any]:
    """The newest run's state, plus the levels with completed companions.

    A guide on disk is not the same as a finished reading: the first pass writes
    it, and four more follow. Saying "ready" the moment it appears would hand
    someone the draft as though it had been checked.
    """
    found = readings.all_readings(st.cfg, st.db, st.manager, paper)
    levels = readings.by_level(found, paper.levels)
    latest = found[0] if found else None
    if latest is None:
        state = "unread"
    elif latest.live:
        state = "reading"
    elif latest.delivery.get("status") in ("incomplete", "review_unfinished"):
        state = latest.delivery["status"]
    elif latest.status == "incomplete":
        state = "incomplete"
    elif latest.status in ("done", "kept") and latest in readings.with_companion(found):
        state = "ready"
    else:
        state = "stopped"
    return {"state": state,
            "run_count": len(found),
            "levels": [lv for lv, v in levels.items() if v.state == "ready"],
            "reading_levels": [lv for lv, v in levels.items() if v.state == "reading"]}


def _harness(st: AppState, level: str | None = None) -> Harness:
    """The harness for a level — its own variant, or the family for anything
    else (and for a harness from before the variants existed)."""
    ids = [readings.harness_id(level)] if level in library.ALL_LEVELS else []
    for hid in (*ids, readings.FAMILY):
        try:
            return get_harness(st.cfg.harnesses_dir, hid)
        except HarnessError:
            continue
    raise HTTPException(500, "The reading method could not be loaded.")


def _reading_harness(harness: Harness, *, include_map: bool) -> Harness:
    """Apply reader-only add-ons without multiplying visible harness choices.

    The writer creates the optional diagram for pure papers; later sessions
    check and repair it within the same three-pass pipeline.
    """
    if harness.loop.critic != "auditor" or "proof-map" not in harness.roles:
        return harness
    return replace(harness, metadata=harness.metadata | {"include_proof_map": include_map})


def _step_agents(harness: Harness, offers: list[agents.Offer], first: agents.Agent,
                 asked: Any) -> tuple[dict[str, dict[str, str]], dict[str, agents.Agent]]:
    """The AI the page asked for each later step of a reading, checked like
    the first: what `RunSpec.role_agents` carries, and each step's AI.

    A step not asked for, or asked for with the reading's own AI, runs as every
    pass always has. An effort given for a step is that step's; left out, the
    step keeps the effort its role asks for (the method sets some passes
    higher than the first), or the reading's.
    """
    asked = asked if isinstance(asked, dict) else {}
    roles = agents.step_roles(harness)
    role_agents: dict[str, dict[str, str]] = {}
    chosen: dict[str, agents.Agent] = {}
    for slot in ("check", "revision"):
        want = asked.get(slot)
        if slot not in roles or not isinstance(want, dict):
            continue
        effort = _text(want, "effort")
        try:
            other = agents.choose(offers, first, account=_text(want, "account"),
                                  model=_text(want, "model"), effort=effort,
                                  custom=want.get("custom") is True)
        except agents.Unavailable as exc:
            raise HTTPException(
                400, f"{agents.STEP_NAMES[slot][0]}: {exc}") from exc
        if (other.account, other.model) == (first.account, first.model) and not effort:
            continue
        chosen[slot] = other
        for role in roles[slot]:
            role_agents[role] = {"provider": other.provider, "account": other.account,
                                 "model": other.model, **({"effort": other.effort}
                                                          if effort else {})}
    return role_agents, chosen


async def _whole_paper(st: AppState, paper: Paper, agent: agents.Agent,
                       steps: dict[str, agents.Agent], roles: dict[str, dict[str, str]],
                       ) -> tuple[agents.Agent, dict[str, agents.Agent], dict[str, dict[str, str]]]:
    """Every AI of a reading that runs on this Mac (Ollama) swapped for its
    copy that holds a whole paper (openweight.long_context) — or the reading
    refused, before it starts, if the paper is longer than the model can hold."""
    local = {a.account for a in (agent, *steps.values())
             if a.provider == "open" and (acct := st.cfg.account(a.account)) is not None
             and acct.auth == "local"}
    if not local:
        return agent, steps, roles
    swapped: dict[str, str] = {}

    async def whole(who: agents.Agent) -> agents.Agent:
        if who.account not in local:
            return who
        if who.model not in swapped:
            acct = st.cfg.account(who.account)
            try:
                variant, window = await asyncio.to_thread(
                    openweight.long_context, who.model, acct.base_url if acct else None)
            except openweight.OpenError as exc:
                raise HTTPException(409, f"{exc} Nothing has been spent.") from exc
            swapped[who.model] = variant
            st.windows[variant] = window
        return replace(who, model=swapped[who.model])

    agent = await whole(agent)
    steps = {slot: await whole(a) for slot, a in steps.items()}
    roles = {role: (spec | {"model": swapped[spec["model"]]}
                    if spec.get("account") in local and spec.get("model") in swapped else spec)
             for role, spec in roles.items()}
    return agent, steps, roles


def _too_long(st: AppState, plan: Any) -> str:
    """Why the first request exceeds Codex's size limit or a local model's
    context window, or "" if it fits."""
    first = plan.first
    if first.provider == "codex":
        # Codex rejects a turn/start request above 1,048,576 characters. The
        # request also contains a small wrapper, so leave room beyond the
        # prompt and system append instead of starting a doomed run.
        chars = len(first.prompt) + len(first.system_append)
        if chars > 1_048_576 - 16_384:
            return (f"This reading needs about {chars:,} characters in its first "
                    "request, more than ChatGPT can accept at once. Use a shorter "
                    "source or another AI with a larger input limit. Nothing has "
                    "been spent.")
    needed = openweight.tokens_for(len(first.prompt) + len(first.system_append))
    for spec in (plan.extra.get("agents") or {}).values():
        window = st.windows.get(spec.get("model") or "")
        if window and needed > window:
            name = agents.full_name("open", spec["model"])
            return (f"This paper, with the method, needs about {needed:,} tokens at once, "
                    f"and {name} can hold {window:,}. Choose a model that holds more — "
                    "gpt-oss:20b holds 131,072 — or read it with Claude or ChatGPT. "
                    "Nothing has been spent.")
    return ""


def _reader_input_limit(st: AppState, readers: list[agents.Agent]) -> int | None:
    """Conservative first-request character budget across a reading's AIs."""
    limits: list[int] = []
    for who in readers:
        if who.provider == "codex":
            limits.append(1_048_576 - 16_384)
        elif who.provider == "open":
            window = (st.windows.get(who.model)
                      or agents.OPEN_MODELS.get(who.model, {}).get("context")
                      or openweight.MIN_CONTEXT)
            limits.append(max(1, int((window - 30_000) * openweight.CHARS_PER_TOKEN)))
        elif who.provider == "claude":
            # Claude Code ordinarily assumes a 200k-token context. Leave its
            # tool instructions and subsequent conversation out of the paper.
            limits.append(int((200_000 - 30_000) * openweight.CHARS_PER_TOKEN))
    return min(limits) if limits else None


def _asks_open(st: AppState, body: dict[str, Any]) -> bool:
    """Whether a request names an open-weight account, for itself or a step."""
    named = [body.get("account")]
    steps = body.get("steps")
    if isinstance(steps, dict):
        named += [s.get("account") for s in steps.values() if isinstance(s, dict)]
    return any((a := st.cfg.account(str(n))) is not None and a.provider == "open"
               for n in named if n)


def _distinct(who: list[agents.Agent]) -> list[agents.Agent]:
    """One of each account and model, in order."""
    seen: set[tuple[str, str]] = set()
    out: list[agents.Agent] = []
    for a in who:
        if (a.account, a.model) not in seen:
            seen.add((a.account, a.model))
            out.append(a)
    return out


def _run_agents(cfg: Config, run_id: str | None) -> dict[str, dict[str, Any]]:
    """Which AI ran each pass of a run (`resolved.agents` in its record),
    described for the page; empty for a run from before passes could differ."""
    if not run_id:
        return {}
    resolved = _read_json(cfg.runs_dir / run_id / "config.json").get("resolved") or {}
    planned = resolved.get("agents")
    if not isinstance(planned, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for role, a in planned.items():
        if isinstance(a, dict) and a.get("model"):
            described = agents.describe(a.get("provider"), a.get("account"),
                                        a.get("model"), a.get("effort"))
            if described is not None:
                out[str(role)] = described
    return out


def _credited_steps(st: AppState, reading: readings.Reading) -> list[dict[str, Any]]:
    """Who wrote, checked and revised a companion, step by step — or nothing,
    when one AI did all of it (the credit line then names that one)."""
    by_role = _run_agents(st.cfg, reading.run_id)
    if not _mixed(by_role):
        return []
    try:
        roles = agents.step_roles(_harness(st, reading.level))
    except Exception:                                       # noqa: BLE001
        roles = {}
    # Only the steps that ran: a revision skipped because the check found
    # nothing worth revising was done by nobody.
    ran = {p.get("role") for p in _loop_record(st.cfg, reading.run_id).get("passes", [])}
    out: list[dict[str, Any]] = []
    for slot, (title, _) in agents.STEP_NAMES.items():
        for role in roles.get(slot, ["draft"] if slot == "draft" else []):
            if role in by_role and (not ran or role in ran):
                out.append({"slot": slot, "title": title, "role": role,
                            "agent": by_role[role]})
                break
    return out


def _mixed(by_role: dict[str, dict[str, Any]]) -> bool:
    """Whether a run's passes did not all run on one model."""
    return len({(a.get("provider"), a.get("model")) for a in by_role.values()}) > 1


async def _refresh_open(st: AppState) -> None:
    """Look again at what open-weight models can run (in a thread: it may ask
    Ollama, and fetch OpenRouter's catalogue once a day)."""
    if st.discovery is None:
        return
    with contextlib.suppress(Exception):
        agents.remember_open(await asyncio.to_thread(st.discovery.models))


async def _refresh_models(st: AppState) -> None:
    """Ask Claude Code and Codex which models they offer, where the answer kept
    is old (in a thread: it runs each program once, briefly)."""
    if st.catalog is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(st.catalog.refresh)


def _key_host(raw: Any) -> str:
    """Which host a key is for: DeepSeek when the page says so, else OpenRouter
    (the one there was before DeepSeek, and still the default)."""
    return openweight.DEEPSEEK if raw == openweight.DEEPSEEK else openweight.OPENROUTER


def _offers(st: AppState, harness: Harness) -> tuple[list[agents.Offer], agents.Agent | None]:
    found = st.discovery.snapshot() if st.discovery is not None else {}
    # A literature search lives on web search, which open-weight models do not
    # have here (atelier.providers.openweight): they would answer from memory —
    # except DeepSeek's own service, which runs Claude Code's search itself.
    if harness.id == readings.harness_id("search"):
        found = {host: models for host, models in found.items() if host in openweight.SEARCHES}
    offers = agents.offered(st.cfg, harness, extra=st.model, open_models=found,
                            catalog=st.catalog.snapshot() if st.catalog is not None else None)
    return offers, agents.default_agent(st.cfg, harness, offers,
                                        model=st.model, effort=st.effort)


def _explainer_default(reading: readings.Reading | None, offers: list[agents.Offer],
                       default: agents.Agent | None) -> agents.Agent | None:
    """Who explains when the page does not say: whoever wrote the companion.

    The same AI that read the paper, so an explanation and the companion come
    from one understanding of it, at high effort unless the reader chooses
    otherwise. Before any reading, the default.
    """
    written = reading.row if reading else {}
    for offer in offers:
        if offer.account != written.get("account") or not written.get("model"):
            continue
        model = str(written["model"])
        return agents.Agent(account=offer.account, provider=offer.provider,
                            model=model, effort="high",
                            custom=model not in offer.models)
    return default


# One rebuild at a time for each PDF: a second request for the same file
# waits for the first instead of typesetting it again.
_REBUILDS: dict[str, Any] = {}
_COLLECTION_COMPILATIONS: dict[str, Any] = {}


def _collection_audience(level: str) -> str:
    """The PDF renderer's audience name for either collection edition."""
    return "non-expert" if level == "collection-nonexpert" else "expert"


def _collection_pdf_is_current(reading: readings.Reading, pdf: Path) -> bool:
    """Whether a collection PDF has the current remarks and cleaned anchors."""
    from deslopify.companion_layout import place_source_remarks
    if not pdf.is_file():
        return False
    try:
        guide = reading.guide.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    if not _RAW_EMPTY_ANCHOR_LINE.search(guide) and place_source_remarks(guide) == guide:
        return True
    # The original guide is preserved as the run record.  A clean persistent
    # reader-facing copy tells us its PDF has already been rebuilt from it.
    try:
        companion = (reading.dir / "companion.md").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return False
    return not _RAW_EMPTY_ANCHOR_LINE.search(companion) and place_source_remarks(companion) == companion


async def _ensure_collection_companion(paper: Paper, reading: readings.Reading) -> Path | None:
    """Give a completed collection guide its persistent companion files.

    A collection's reader is intentionally asked for one Markdown guide rather
    than the larger single-paper companion contract.  Rendering that guide here
    preserves the simple contract while making the collection a first-class
    companion in the interface: its Markdown, LaTeX source, and PDF all live
    beside the run that produced it.  The work is lazy for compatibility with
    older runs, and serialized per reading so two browser requests cannot
    compile the same document twice.
    """
    from deslopify.summary_pdf import SummaryPdfError, write_companion

    guide = reading.guide
    pdf = reading.dir / "companion.pdf"
    if _collection_pdf_is_current(reading, pdf) or not guide.is_file():
        return pdf if pdf.is_file() else None

    lock = _COLLECTION_COMPILATIONS.setdefault(str(pdf), asyncio.Lock())
    async with lock:
        if _collection_pdf_is_current(reading, pdf):
            return pdf
        try:
            await asyncio.to_thread(
                write_companion, guide, reading.dir / "companion.md",
                reading.dir / "companion.tex", pdf,
                audience=_collection_audience(reading.level), paper_title=paper.title)
        except (SummaryPdfError, OSError):
            # The guide remains readable even if an individual document has
            # malformed LaTeX or TeX is later removed from the machine.
            return None
    return pdf if pdf.is_file() else None


async def _companion_with_contents(paper: Paper, reading: readings.Reading,
                                   stored: Path) -> Path:
    """The companion's PDF, with current heading styles and contents.

    Companions with the current headings and generation date need no change. An older one
    is typeset again, once, and kept in the paper's `.atelier/` — never written
    into the reading, which is a record of what the run made: from its own
    LaTeX source if it has one (method version 12 on), otherwise from its guide,
    the text its PDF was made from. If that cannot be done (no LaTeX here, a
    source that no longer compiles), the PDF the reading made is served as it is.
    """
    import asyncio
    import hashlib
    from functools import partial

    from deslopify.summary_pdf import (HEADING_FORMAT, SummaryPdfError, companion_from_guide,
                                       generation_date,
                                       rebuild_with_contents)
    from deslopify.companion_layout import place_source_remarks_tex

    tex = reading.dir / "companion.tex"
    try:
        generated_on = generation_date(reading.guide)
        if tex.is_file():
            text = tex.read_text(encoding="utf-8", errors="replace")
            formatted = place_source_remarks_tex(text)
            if HEADING_FORMAT in text and r"\date{Generated on " in text and formatted == text:
                return stored
            build = partial(rebuild_with_contents, tex)
            text = formatted
        else:
            text = reading.guide.read_text(encoding="utf-8", errors="replace")
            build = partial(companion_from_guide, reading.guide, paper_title=paper.title,
                            audience="non-expert" if reading.level == "general" else "expert")
    except OSError:
        return stored
    digest = hashlib.sha256((generated_on + HEADING_FORMAT + text)
                            .encode("utf-8")).hexdigest()[:12]
    out = paper.root / ".atelier" / "companions" / f"{reading.run_id or 'kept'}-{digest}.pdf"
    lock = _REBUILDS.setdefault(str(out), asyncio.Lock())
    async with lock:
        if not out.is_file():
            try:
                await asyncio.to_thread(build, output=out)
            except (SummaryPdfError, OSError):
                return stored
    return out if out.is_file() else stored


async def _page_count(paper: Paper) -> int:
    """The pages an answer may link to (`explain.linkable`)."""
    import asyncio

    try:
        return explain.linkable(paper, await asyncio.to_thread(explain.paper_pages, paper))
    except Exception:                                             # noqa: BLE001
        return 0


def _where(raw: Any) -> dict[str, Any]:
    """Where a selection was made, kept to what the page may say about it —
    or "whole": a question about the paper as a whole, with no selection."""
    raw = raw if isinstance(raw, dict) else {}
    kind = raw.get("kind") if raw.get("kind") in ("paper", "companion", "note", "whole") \
        else "paper"
    where: dict[str, Any] = {"kind": kind}
    page = raw.get("page")
    if isinstance(page, int) and 1 <= page <= 100_000:
        where["page"] = page
    for key, limit in (("section", 40), ("label", 120), ("note", 40)):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            where[key] = value.strip()[:limit]
    return where


def _exchanges(raw: Any) -> list[dict[str, str]]:
    """Keep every well-formed exchange supplied for a follow-up, in full."""
    out: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        q, a = item.get("q"), item.get("a")
        if isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip():
            out.append({"q": q, "a": a})
    return out


def _cap(body: dict[str, Any], key: str, bounds: tuple[float, float],
         fallback: float | None, message: str) -> float | None:
    """A cap the page asked for, if it asked; refused if out of bounds."""
    raw = body.get(key)
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, bool):
        raise HTTPException(400, message.format(*bounds))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, message.format(*bounds)) from None
    if not bounds[0] <= value <= bounds[1]:
        raise HTTPException(400, message.format(*bounds))
    return value


def _parallel_cap(body: dict[str, Any], hard_max: int) -> int:
    """The browser's desired concurrency, bounded by the server maximum."""
    raw = body.get("max_concurrent")
    if raw is None or raw == "":
        return hard_max
    message = (f"The parallel-reading cap must be a whole number between "
               f"1 and {hard_max}.")
    if isinstance(raw, bool):
        raise HTTPException(400, message)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, message) from None
    if not number.is_integer() or not 1 <= number <= hard_max:
        raise HTTPException(400, message)
    return int(number)


def _text(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _paper(st: AppState, slug: str) -> Paper:
    # The lab calls these problems. Nobody reading a paper should meet the word.
    try:
        return load_paper(st.cfg, slug)
    except ProblemError as exc:
        raise HTTPException(404, "There is no such paper here.") from exc


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _extract(st: AppState, data: bytes) -> str:
    """The text layer of an uploaded PDF, from a scratch copy."""
    import tempfile

    from deslopify.source import SourceError, read_source

    with tempfile.TemporaryDirectory(prefix="deslopify-upload-") as tmp:
        scratch = Path(tmp) / "upload.pdf"
        scratch.write_bytes(data)
        try:
            return read_source(scratch)
        except SourceError as exc:
            message = str(exc)
            raise HTTPException(400, message[:1].upper() + message[1:]) from exc
        except Exception as exc:                                  # noqa: BLE001
            raise HTTPException(400, f"That PDF could not be read ({exc}).") from exc


def app_from_env() -> FastAPI:
    """Factory for `--reload`, which re-imports this module on every change.

    A reloading server cannot be handed an app object or keyword arguments, so
    the settings travel in one environment variable written by `__main__`.
    """
    import os
    from dataclasses import replace

    settings = json.loads(os.environ.get("DESLOPIFY_APP_SETTINGS", "{}"))
    cfg = load_config()
    if settings.get("papers"):
        cfg = replace(cfg, problems_dir=Path(settings["papers"]))
    return create_app(cfg, model=settings.get("model"), effort=settings.get("effort"),
                      max_usd=settings.get("max_usd"),
                      max_minutes=settings.get("max_minutes"),
                      control_token=settings.get("control_token"))
