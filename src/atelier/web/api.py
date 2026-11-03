"""JSON API and the multiplexed SSE stream."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from .. import events as ev
from ..compile import next_session_number, plan_run
from ..events import Event, EventLog
from ..harnesses import HarnessError, discover as discover_harnesses, get_harness
from ..problems import (
    LockHeld,
    ProblemError,
    acquire_lock,
    create_problem,
    discover as discover_problems,
    list_files,
    load_problem,
    release_lock,
    safe_join,
    walk_files,
)
from ..render import human_size, is_image, is_pdf, render_file
from ..runner import RunState
from ..spec import RunSpec
from .app import state_of
from .security import file_headers

router = APIRouter()


# ==========================================================================
# problems
# ==========================================================================


@router.get("/problems")
async def api_problems(request: Request) -> list[dict[str, Any]]:
    st = state_of(request)
    out = []
    for p in discover_problems(st.cfg.problems_dir):
        state = p.state()
        active = st.manager.for_problem(p.slug)
        row = st.db.one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(cost_computed), 0) AS cost, "
            "MAX(started_at) AS last FROM runs WHERE problem = ?", (p.slug,))
        out.append({
            "slug": p.slug,
            "title": p.meta.title,
            "tags": p.meta.tags,
            "difficulty": p.meta.difficulty,
            "benchmark": p.meta.benchmark,
            "status": state.get("status", "open"),
            "one_line": state.get("one_line", ""),
            "confidence": state.get("confidence", 0.0),
            "has_pdf": p.has_pdf(),
            "sessions": int(row["n"]) if row else 0,
            "total_cost": float(row["cost"]) if row else 0.0,
            "last_session": row["last"] if row else None,
            "active_run": active.run_id if active else None,
            "active_state": str(active.state) if active else None,
            "locked": bool(p.lock()),
        })
    return out


@router.post("/problems")
async def api_create_problem(request: Request) -> dict[str, Any]:
    st = state_of(request)
    content_type = request.headers.get("content-type", "")
    upload_data: bytes | None = None
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        body = {
            "slug": str(form.get("slug") or ""),
            "title": str(form.get("title") or ""),
            "statement": str(form.get("statement") or ""),
        }
        upload = form.get("source")
        if upload is not None and getattr(upload, "filename", ""):
            filename = str(upload.filename)
            if Path(filename).suffix.lower() != ".pdf":
                raise HTTPException(400, "the uploaded source must be a PDF file")
            upload_data = await upload.read(100 * 1024 * 1024 + 1)
            if len(upload_data) > 100 * 1024 * 1024:
                raise HTTPException(413, "the uploaded PDF exceeds the 100 MB limit")
            if b"%PDF-" not in upload_data[:1024]:
                raise HTTPException(400, "the uploaded file is not a valid PDF")
    else:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(400, "expected problem fields or a PDF upload") from exc

    slug = (body.get("slug") or "").strip()
    try:
        p = create_problem(st.cfg.problems_dir, slug,
                           title=body.get("title", ""),
                           statement=body.get("statement", ""))
    except ProblemError as exc:
        raise HTTPException(400, str(exc)) from exc
    source = None
    if upload_data is not None:
        source_path = p.progress_dir / "source.pdf"
        source_path.write_bytes(upload_data)
        source = "progress/source.pdf"
    st.hub.publish(ev.TOPIC_PROBLEMS, Event(type=ev.NOTICE, run_id="",
                                            payload={"created": p.slug}))
    return {"slug": p.slug, "title": p.meta.title, "source": source}


def _run_pdf(runs_dir: Path, run_id: str) -> dict[str, Any] | None:
    """The document this one session produced, if it kept one of its own.

    One-shot sessions are independent by design: each writes into its own run
    directory and none of them touches the problem folder. So each has its own
    PDF, and they are listed per session rather than collapsed into one — which
    is the whole point of running a blind attempt more than once.

    Isolated reading sessions are also independent: their companion belongs to
    that particular reading and is kept under the run directory. Ordinary
    solving sessions still edit the problem's single living document.
    """
    run_dir = runs_dir / str(run_id)
    for name in ("reading/companion.pdf", "math.pdf", "answer.pdf"):
        pdf = run_dir / name
        if pdf.is_file():
            return {"name": name, "size": pdf.stat().st_size}
    return None


@router.get("/problems/{slug}")
async def api_problem(request: Request, slug: str) -> dict[str, Any]:
    st = state_of(request)
    try:
        p = load_problem(st.cfg.problems_dir, slug)
    except ProblemError as exc:
        raise HTTPException(404, str(exc)) from exc

    runs = [dict(r) for r in st.db.query(
        "SELECT * FROM runs WHERE problem = ? ORDER BY started_at DESC LIMIT 200", (slug,))]
    for row in runs:
        row["pdf"] = _run_pdf(st.cfg.runs_dir, row["id"])
    active = st.manager.for_problem(slug)
    lock = p.lock()

    from ..git import log_for_problem

    return {
        "slug": p.slug,
        "title": p.meta.title,
        "meta": {
            "tags": p.meta.tags, "difficulty": p.meta.difficulty,
            "statement": p.meta.statement, "notes": p.meta.notes,
            "benchmark": p.meta.benchmark, "created": p.meta.created,
        },
        "state": p.state(),
        "has_pdf": p.has_pdf(),
        "has_statement": p.statement_path() is not None,
        "files": [asdict(f) for f in list_files(p.root, p.meta)],
        "markdown_files": [f.path for f in walk_files(p.root, p.meta)
                           if f.ext in ("md", "markdown")],
        "source_files": [f.path for f in walk_files(p.root, p.meta)
                         if f.ext in ("md", "markdown", "pdf")],
        "runs": runs,
        "active_run": active.snapshot() if active else None,
        "lock": ({"run_id": lock.run_id, "harness": lock.harness,
                  "started_at": lock.started_at, "pid": lock.pid} if lock else None),
        "git_log": log_for_problem(st.cfg, p, limit=30),
    }


@router.get("/problems/{slug}/files")
async def api_problem_files(request: Request, slug: str,
                            subdir: str = Query("")) -> list[dict[str, Any]]:
    st = state_of(request)
    p = load_problem(st.cfg.problems_dir, slug)
    return [asdict(f) | {"size_human": human_size(f.size)}
            for f in list_files(p.root, p.meta, subdir=subdir)]


@router.get("/problems/{slug}/file")
async def api_problem_file(request: Request, slug: str, path: str = Query(...),
                           raw: bool = Query(False)):
    st = state_of(request)
    p = load_problem(st.cfg.problems_dir, slug)
    try:
        target = safe_join(p.root, path)
    except ProblemError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(404, f"no such file: {path}")

    ext = target.suffix.lower().lstrip(".")
    if raw or is_pdf(ext) or is_image(ext):
        media = "application/pdf" if is_pdf(ext) else None
        # Shown as itself, never run as the lab's own page (security.py).
        return FileResponse(target, media_type=media,
                            headers={"Cache-Control": "no-store"} | file_headers(target))

    rendered = render_file(target)
    from ..problems import classify

    return JSONResponse({
        "path": path, "name": target.name, "ext": ext,
        "kind": rendered["kind"], "html": rendered["html"],
        "classification": classify(path, p.meta),
        "size": target.stat().st_size,
        "modified": target.stat().st_mtime,
    })


@router.post("/problems/{slug}/unlock")
async def api_unlock(request: Request, slug: str) -> dict[str, Any]:
    st = state_of(request)
    p = load_problem(st.cfg.problems_dir, slug)
    if st.manager.for_problem(slug):
        raise HTTPException(409, "a session is genuinely running in this process; "
                                 "stop it instead of force-unlocking")
    released = release_lock(p.root)
    return {"released": released}


@router.post("/problems/{slug}/open")
async def api_open_externally(request: Request, slug: str,
                              body: dict = Body(...)) -> dict[str, Any]:
    """Open a file with the OS default application (Preview, etc.)."""
    st = state_of(request)
    p = load_problem(st.cfg.problems_dir, slug)
    target = safe_join(p.root, body.get("path", ""))
    if not target.exists():
        raise HTTPException(404, "no such file")
    if sys.platform != "darwin":
        raise HTTPException(400, "only supported on macOS")
    subprocess.Popen(["open", str(target)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": str(target)}


@router.post("/problems/{slug}/compile")
async def api_compile(request: Request, slug: str) -> dict[str, Any]:
    st = state_of(request)
    p = load_problem(st.cfg.problems_dir, slug)
    from ..latex import compile_tex

    result = await compile_tex(st.cfg, p.progress_dir)
    return {"ok": result.ok, "errors": result.errors,
            "skipped": result.skipped, "log_tail": result.log_tail}


# ==========================================================================
# harnesses
# ==========================================================================


@router.get("/harnesses")
async def api_harnesses(request: Request) -> dict[str, Any]:
    st = state_of(request)
    found, errors = discover_harnesses(st.cfg.harnesses_dir)
    out = []
    for h in found:
        row = st.db.one(
            "SELECT COUNT(*) AS runs, COALESCE(AVG(cost_computed), 0) AS avg_cost, "
            "COALESCE(AVG(wall_seconds), 0) AS avg_seconds FROM runs WHERE harness = ?",
            (h.id,))
        out.append({
            "id": h.id, "title": h.title, "description": h.description,
            "family": h.family, "variant": h.variant,
            "variant_label": h.variant_label,
            "version": h.version, "runner": h.runner, "providers": h.providers,
            "tags": h.tags, "warnings": h.warnings,
            "agents": [{"name": a.name, "description": a.description}
                       for a in h.agents.values()],
            "defaults": {"model": h.defaults.model, "effort": h.defaults.effort,
                         "account": h.defaults.account},
            "budget": {"max_usd": h.budget.max_usd, "max_minutes": h.budget.max_minutes,
                       "max_tokens_out": h.budget.max_tokens_out,
                       "wrapup_at": h.budget.wrapup_at},
            "loop": ({"rounds": h.loop.rounds, "continue_at": h.loop.continue_at,
                      "passes": h.loop.passes(), "sequence": h.loop.sequence}
                     if h.loop.enabled else None),
            "tools_none": h.tools.none,
            "stats": {"runs": int(row["runs"]) if row else 0,
                      "avg_cost": float(row["avg_cost"]) if row else 0.0,
                      "avg_seconds": float(row["avg_seconds"]) if row else 0.0},
        })
    return {"harnesses": out, "errors": errors}


@router.get("/harnesses/{hid}")
async def api_harness(request: Request, hid: str) -> dict[str, Any]:
    st = state_of(request)
    try:
        h = get_harness(st.cfg.harnesses_dir, hid)
    except HarnessError as exc:
        raise HTTPException(404, str(exc)) from exc
    from ..render import render_markdown

    return {
        "id": h.id, "title": h.title, "description": h.description,
        "family": h.family, "variant": h.variant,
        "variant_label": h.variant_label,
        "version": h.version, "runner": h.runner, "providers": h.providers,
        "tags": h.tags, "warnings": h.warnings,
        "skill_html": render_markdown(h.skill) if h.skill else "",
        "skill_raw": h.skill,
        "default_task": h.default_task,
        "agents": [{"name": a.name, "description": a.description,
                    "model": a.model, "tools": a.tools, "prompt": a.prompt}
                   for a in h.agents.values()],
        "defaults": {"model": h.defaults.model, "effort": h.defaults.effort,
                     "account": h.defaults.account, "provider": h.defaults.provider},
        "budget": {"max_usd": h.budget.max_usd, "max_minutes": h.budget.max_minutes,
                   "max_tokens_out": h.budget.max_tokens_out,
                   "max_turns": h.budget.max_turns,
                   "wrapup_at": h.budget.wrapup_at,
                   "grace_seconds": h.budget.grace_seconds},
        "loop": ({"rounds": h.loop.rounds, "continue_at": h.loop.continue_at,
                  "critic": h.loop.critic, "reviser": h.loop.reviser,
                  "sequence": h.loop.sequence,
                  "passes": h.loop.passes(),
                  "roles": {name: {"title": r.title, "writes": r.writes}
                            for name, r in h.roles.items()}}
                 if h.loop.enabled else None),
        "tools": {"allow": h.tools.allow, "deny": h.tools.deny,
                  "bash_allow": h.tools.bash_allow, "bash_deny": h.tools.bash_deny,
                  "none": h.tools.none},
        "permissions": {"mode": h.permissions.mode, "network": h.permissions.network,
                        "edit_static_files": h.permissions.edit_static_files},
        "context": {"include_statement": h.context.include_statement,
                    "include_strategy": h.context.include_strategy,
                    "include_progress": h.context.include_progress,
                    "include_labbook": h.context.include_labbook,
                    "math_tex": h.context.math_tex},
        "skills": {"shared": h.shared_skills, "local": h.local_skills},
    }


@router.post("/harnesses/{hid}/preview")
async def api_harness_preview(request: Request, hid: str,
                              body: dict = Body(...)) -> dict[str, Any]:
    """Dry-run the compiler: show the exact prompt and argv without launching."""
    st = state_of(request)
    spec = _spec_from_body(st, body, harness=hid)
    problem = load_problem(st.cfg.problems_dir, spec.problem)
    harness = get_harness(st.cfg.harnesses_dir, hid)
    spec.run_id = f"preview-{secrets.token_hex(3)}"
    try:
        plan = plan_run(st.cfg, problem, harness, spec,
                        session_number=next_session_number(st.cfg, problem),
                        isolate_reading=(harness.kind == "read"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    argv = list(plan.argv)
    payload = {
        "prompt": plan.prompt,
        "system_append": plan.system_append,
        "argv": argv,
        "argv_display": _display_argv(argv),
        "cwd": str(plan.cwd),
        "env": plan.redacted_env(),
        "settings": json.loads((plan.run_dir / "settings.json").read_text()),
        "model": plan.model, "effort": plan.effort,
        "budget": {"usd": plan.budget_usd, "minutes": plan.budget_minutes,
                   "tokens_out": plan.budget_tokens_out},
        "prompt_chars": len(plan.prompt),
        "prompt_tokens_estimate": len(plan.prompt) // 4,
    }
    import shutil as _sh

    _sh.rmtree(plan.run_dir, ignore_errors=True)
    return payload


def _display_argv(argv: list[str]) -> str:
    out = []
    for a in argv:
        if len(a) > 160:
            a = a[:157] + "…"
        out.append(f"'{a}'" if (" " in a or "\n" in a or a == "") else a)
    return " ".join(out)


# ==========================================================================
# runs
# ==========================================================================


def _spec_from_body(st, body: dict[str, Any], harness: str | None = None) -> RunSpec:
    data = dict(body)
    if harness:
        data["harness"] = harness
    if not data.get("problem"):
        raise HTTPException(400, "`problem` is required")
    if not data.get("harness"):
        raise HTTPException(400, "`harness` is required")
    if not data.get("provider"):
        # The account decides which program runs. Defaulting to the configured
        # provider here launched Claude with a Codex model id and an error only
        # this codebase could interpret.
        acct = st.cfg.account(str(data.get("account") or ""))
        data["provider"] = acct.provider if acct else st.cfg.defaults.provider
    return RunSpec.from_dict(data)


@router.post("/runs")
async def api_launch(request: Request, body: dict = Body(...)) -> dict[str, Any]:
    st = state_of(request)
    spec = _spec_from_body(st, body)

    try:
        problem = load_problem(st.cfg.problems_dir, spec.problem)
        harness = get_harness(st.cfg.harnesses_dir, spec.harness)
    except (ProblemError, HarnessError) as exc:
        raise HTTPException(404, str(exc)) from exc

    if not harness.supports(spec.provider):
        raise HTTPException(400, (
            f"The '{harness.id}' method does not run on {spec.provider}. "
            f"It works with: {', '.join(harness.providers)}. "
            f"Choose a different method, or an account for one of those."))

    account = st.cfg.account(spec.account or harness.defaults.account) or \
        st.cfg.default_account(spec.provider)
    ok, why = st.manager.concurrency_ok(account.id if account else None)
    if not ok:
        raise HTTPException(429, why)

    session_number = next_session_number(st.cfg, problem)

    isolated_reading = harness.kind == "read" and spec.mode != "oneshot"
    lock_held = False
    if spec.mode != "oneshot":
        try:
            acquire_lock(problem.root, spec.run_id, harness.id)
            lock_held = True
        except LockHeld as exc:
            raise HTTPException(409, str(exc)) from exc

    try:
        plan = plan_run(
            st.cfg, problem, harness, spec, session_number=session_number,
            isolate_reading=isolated_reading)
        # A reader needs the lock only long enough to take one consistent
        # snapshot. Its provider then runs entirely inside that private copy.
        if isolated_reading:
            release_lock(problem.root, spec.run_id)
            lock_held = False
        handle = await st.manager.launch(plan, session_number=session_number)
    except ValueError as exc:
        if lock_held:
            release_lock(problem.root, spec.run_id)
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        if lock_held:
            release_lock(problem.root, spec.run_id)
        raise

    return {"run_id": handle.run_id, "state": str(handle.state),
            "snapshot": handle.snapshot()}


@router.get("/runs")
async def api_runs(request: Request, problem: str | None = None,
                   harness: str | None = None, bench: str | None = None,
                   limit: int = Query(200, le=2000)) -> list[dict[str, Any]]:
    st = state_of(request)
    clauses, params = [], []
    if problem:
        clauses.append("problem = ?")
        params.append(problem)
    if harness:
        clauses.append("harness = ?")
        params.append(harness)
    if bench:
        clauses.append("bench_id = ?")
        params.append(bench)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = st.db.query(
        f"SELECT * FROM runs {where} ORDER BY started_at DESC LIMIT ?",
        [*params, limit])
    live = {h.run_id: h for h in st.manager.active()}
    out = []
    for r in rows:
        d = dict(r)
        if h := live.get(d["id"]):
            d["live"] = h.snapshot()
        out.append(d)
    return out


@router.get("/runs/active")
async def api_active_runs(request: Request) -> list[dict[str, Any]]:
    st = state_of(request)
    return [h.snapshot() for h in st.manager.active()]


@router.get("/runs/{run_id}")
async def api_run(request: Request, run_id: str) -> dict[str, Any]:
    st = state_of(request)
    row = st.db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
    handle = st.manager.get(run_id)
    if row is None and handle is None:
        raise HTTPException(404, f"no such run: {run_id}")

    run_dir = st.cfg.runs_dir / run_id
    files = {}
    for name in ("answer.md", "answer.pdf", "math.pdf", "math.tex", "state.json",
                 "_session_notes.md", "prompt.md", "system-append.md", "notes.md",
                 "usage.json", "result.json", "config.json", "commands.log",
                 "contract_violations.json"):
        p = run_dir / name
        if p.is_file():
            files[name] = p.stat().st_size
    reading = run_dir / "reading"
    if reading.is_dir():
        for p in sorted(reading.rglob("*")):
            if p.is_file():
                files[p.relative_to(run_dir).as_posix()] = p.stat().st_size

    steering = [dict(s) for s in st.db.query(
        "SELECT * FROM steering WHERE run_id = ? ORDER BY ts", (run_id,))]

    return {
        "run": dict(row) if row else None,
        "live": handle.snapshot() if handle else None,
        "files": files,
        "steering": steering,
        "run_dir": str(run_dir),
    }


@router.get("/runs/{run_id}/events")
async def api_run_events(request: Request, run_id: str,
                         after: int = Query(0), limit: int = Query(3000, le=20000),
                         kinds: str | None = None) -> dict[str, Any]:
    st = state_of(request)
    path = st.cfg.runs_dir / run_id / "events.jsonl"
    wanted = set(kinds.split(",")) if kinds else None
    out = []
    for e in EventLog.read(path, after_seq=after, limit=None):
        if wanted and e.type not in wanted:
            continue
        out.append(e.to_dict())
        if len(out) >= limit:
            break
    return {"events": out, "count": len(out)}


@router.get("/runs/{run_id}/file")
async def api_run_file(request: Request, run_id: str, name: str = Query(...)):
    st = state_of(request)
    run_dir = (st.cfg.runs_dir / run_id).resolve()
    target = (run_dir / name).resolve()
    if not str(target).startswith(str(run_dir)) or not target.is_file():
        raise HTTPException(404, "no such file")
    if target.suffix.lower() == ".pdf":
        return FileResponse(target, media_type="application/pdf",
                            headers={"Cache-Control": "no-store"} | file_headers(target))
    if target.suffix.lower() in (".md", ".txt", ".log", ".json", ".jsonl"):
        text = target.read_text(encoding="utf-8", errors="replace")
        if target.suffix.lower() == ".md":
            from ..render import render_markdown

            return JSONResponse({"kind": "markdown", "html": render_markdown(text)})
        return PlainTextResponse(text)
    # Whatever else a session wrote — a page, a drawing — is shown as itself,
    # never run as the lab's own page (security.py).
    return FileResponse(target, headers=file_headers(target))


@router.post("/runs/{run_id}/steer")
async def api_steer(request: Request, run_id: str, body: dict = Body(...)) -> dict[str, Any]:
    st = state_of(request)
    handle = st.manager.get(run_id)
    if handle is None:
        raise HTTPException(404, "run is not active in this process")
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "empty steering message")
    if handle.state.terminal:
        raise HTTPException(409, f"run already finished ({handle.state})")
    delivered = await handle.steer(text, origin=body.get("origin", "user"))
    if not delivered:
        raise HTTPException(409, "the session is no longer accepting input")
    return {"queued": True, "delivered": delivered}


@router.post("/runs/{run_id}/signal")
async def api_signal(request: Request, run_id: str, body: dict = Body(...)) -> dict[str, Any]:
    st = state_of(request)
    handle = st.manager.get(run_id)
    if handle is None:
        raise HTTPException(404, "run is not active in this process")
    action = str(body.get("action", "")).lower()

    match action:
        case "freeze":
            ok = await handle.freeze()
        case "thaw":
            ok = await handle.thaw()
        case "wrapup":
            ok = await handle.wrapup(auto=False)
        case "stop":
            asyncio.create_task(handle.stop(reason=body.get("reason", "stopped by user")))
            ok = True
        case "abandon":
            asyncio.create_task(handle.abandon())
            ok = True
        case _:
            raise HTTPException(400, f"unknown action {action!r}; expected "
                                     "freeze|thaw|wrapup|stop|abandon")
    return {"ok": ok, "state": str(handle.state)}


@router.get("/runs/{run_id}/diff")
async def api_run_diff(request: Request, run_id: str) -> dict[str, Any]:
    st = state_of(request)
    row = st.db.one("SELECT problem, git_commit FROM runs WHERE id = ?", (run_id,))
    if row is None:
        raise HTTPException(404, "no such run")
    from ..git import diff_text

    problem = load_problem(st.cfg.problems_dir, row["problem"])
    sha = row["git_commit"]
    if not sha:
        return {"diff": "", "note": "this session produced no commit"}
    return {"diff": diff_text(st.cfg, problem, f"{sha}^", sha), "commit": sha}


@router.post("/runs/{run_id}/verdict")
async def api_verdict(request: Request, run_id: str, body: dict = Body(...)) -> dict[str, Any]:
    st = state_of(request)
    verdict = body.get("verdict")
    if verdict not in ("solved", "substantial", "marginal", "nothing", "wrong", None):
        raise HTTPException(400, "invalid verdict")
    st.db.execute(
        "INSERT INTO verdicts (run_id, verdict, comment, ts) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(run_id) DO UPDATE SET verdict = excluded.verdict, "
        "comment = excluded.comment, ts = excluded.ts",
        (run_id, verdict, body.get("comment", ""), ev.now_iso()))
    return {"ok": True}


# ==========================================================================
# accounts / usage / doctor
# ==========================================================================


@router.get("/accounts")
async def api_accounts(request: Request) -> list[dict[str, Any]]:
    st = state_of(request)
    from ..ledger import all_health

    return [h.to_dict() for h in all_health(st.cfg, st.db)]


@router.post("/accounts/{account_id}/probe")
async def api_probe_account(request: Request, account_id: str) -> dict[str, Any]:
    st = state_of(request)
    acct = st.cfg.account(account_id)
    if acct is None:
        raise HTTPException(404, "no such account")
    from ..providers import get_adapter

    adapter = get_adapter(acct.provider)
    info = await adapter.probe(st.cfg)
    status = info.accounts.get(account_id, info.error or "unknown")
    st.db.add_account_event(account_id, ev.now_iso(), "probe",
                            {"status": status, "version": info.version})
    return {"account": account_id, "status": status,
            "available": info.available, "version": info.version}


@router.get("/usage")
async def api_usage(request: Request, group: str = Query("day"),
                    days: int = Query(30, le=365)) -> dict[str, Any]:
    st = state_of(request)
    from ..ledger import totals, usage_by

    try:
        rows = usage_by(st.db, group, days=days)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"group": group, "days": days, "rows": rows, "totals": totals(st.db)}


@router.get("/doctor")
async def api_doctor(request: Request, live: bool = Query(False)) -> dict[str, Any]:
    st = state_of(request)
    from ..doctor import run_checks

    return await run_checks(st.cfg, st.db, live=live)


@router.post("/client-error")
async def api_client_error(request: Request, body: dict = Body(...)) -> dict[str, Any]:
    """Client-side errors land in the server log, where they are actually seen."""
    import logging

    logging.getLogger("atelier.client").error(
        "client error: %s at %s:%s\n%s",
        body.get("message"), body.get("source"), body.get("line"),
        str(body.get("stack", ""))[:2000],
    )
    return {"ok": True}


@router.get("/config")
async def api_config(request: Request) -> dict[str, Any]:
    st = state_of(request)
    from ..providers import all_adapters

    return {
        "defaults": {
            "provider": st.cfg.defaults.provider, "model": st.cfg.defaults.model,
            "effort": st.cfg.defaults.effort, "account": st.cfg.defaults.account,
            "harness": st.cfg.defaults.harness,
        },
        "testing": {"model": st.cfg.testing.model, "effort": st.cfg.testing.effort},
        "models": st.cfg.models.all() or st.cfg.prices.models,
        "models_by_provider": {
            name: st.cfg.models.for_provider(name) for name in all_adapters()
        },
        "default_model_by_provider": {
            name: st.cfg.defaults.model_for(name) for name in all_adapters()
        },
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "accounts": [{"id": a.id, "label": a.label, "provider": a.provider,
                      "color": a.color} for a in st.cfg.accounts.values()],
        "providers": {name: {
            "steering": ad.capabilities.steering,
            "resume": ad.capabilities.resume,
            "freeze": ad.capabilities.freeze,
            "effort_levels": list(ad.capabilities.effort_levels),
            "available": ad.executable() is not None,
            "tool_free": ad.capabilities.tool_free,
        } for name, ad in all_adapters().items()},
        "paths": {"problems": str(st.cfg.problems_dir), "runs": str(st.cfg.runs_dir)},
        "startup_report": st.startup_report,
    }


# ==========================================================================
# SSE — one multiplexed stream per browser tab
# ==========================================================================


@router.get("/stream")
async def api_stream(request: Request, topics: str = Query("runs,problems,accounts")):
    st = state_of(request)
    sub_id = secrets.token_hex(8)
    wanted = {t.strip() for t in topics.split(",") if t.strip()}
    sub = await st.hub.subscribe(sub_id, wanted)

    async def gen():
        yield _sse({"type": "hello", "sub_id": sub_id,
                    "topics": sorted(wanted)}, event="hello")
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    topic, event = await asyncio.wait_for(sub.queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse({"topic": topic, **event.to_dict()},
                           event="event", eid=f"{topic}:{event.seq}")
        finally:
            await st.hub.unsubscribe(sub_id)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@router.post("/stream/{sub_id}/topics")
async def api_stream_topics(request: Request, sub_id: str,
                            body: dict = Body(...)) -> dict[str, Any]:
    st = state_of(request)
    topics = {str(t) for t in body.get("topics", [])}
    await st.hub.set_topics(sub_id, topics)
    return {"ok": True, "topics": sorted(topics)}


def _sse(data: dict[str, Any], *, event: str = "message", eid: str | None = None) -> str:
    chunks = []
    if eid:
        chunks.append(f"id: {eid}")
    chunks.append(f"event: {event}")
    chunks.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(chunks) + "\n\n"
