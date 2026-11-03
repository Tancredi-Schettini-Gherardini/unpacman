"""Command line: `atelier serve | doctor | run | new | import | init-git | reindex`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atelier",
                                 description="A local harness lab for AI-assisted mathematics")
    ap.add_argument("--root", type=Path, default=None,
                    help="atelier installation directory (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="run the web interface")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.add_argument("--reload", action="store_true")

    p_doc = sub.add_parser("doctor", help="check the environment")
    p_doc.add_argument("--live", action="store_true",
                       help="probe provider CLIs and account auth (slower)")
    p_doc.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run", help="run one session headlessly")
    p_run.add_argument("--problem", required=True)
    p_run.add_argument("--harness", required=True)
    p_run.add_argument("--mode", default="standard",
                       choices=["standard", "oneshot", "continue"])
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--effort", default=None)
    p_run.add_argument("--account", default=None)
    p_run.add_argument("--provider", default=None, choices=["claude", "codex"],
                       help="which AI program to drive (default: from the harness)")
    p_run.add_argument("--task", default=None, help="free-text task")
    p_run.add_argument("--task-file", action="append", default=[],
                       help="problem-relative input file; Deslopify accepts one .md or .pdf")
    p_run.add_argument("--max-usd", type=float, default=None)
    p_run.add_argument("--max-minutes", type=float, default=None)
    p_run.add_argument("--rounds", type=int, default=None,
                       help="rounds of critique, for a harness with a review "
                            "loop (0 turns it off; default: the harness's own)")
    p_run.add_argument("--quiet", action="store_true")

    p_new = sub.add_parser("new", help="scaffold a new problem")
    p_new.add_argument("slug")
    p_new.add_argument("--title", default="")

    p_imp = sub.add_parser("import", help="adopt an existing folder as a problem")
    p_imp.add_argument("path", type=Path)
    p_imp.add_argument("--slug", default=None)
    p_imp.add_argument("--title", default="")
    p_imp.add_argument("--statement", default=None,
                       help="which file is the problem statement")
    p_imp.add_argument("--move", action="store_true",
                       help="move instead of copy")

    sub.add_parser("setup", help="first-time setup: check everything and create what is missing")
    sub.add_parser("init-git", help="initialise the problems directory as a git repo")
    sub.add_parser("reindex", help="rebuild the database from runs/ on disk")

    p_prev = sub.add_parser("preview", help="print the compiled prompt without running")
    p_prev.add_argument("--problem", required=True)
    p_prev.add_argument("--harness", required=True)
    p_prev.add_argument("--mode", default="standard",
                        choices=["standard", "oneshot", "continue"])
    p_prev.add_argument("--task", default=None)
    p_prev.add_argument("--task-file", action="append", default=[],
                        help="problem-relative input file; Deslopify accepts one .md or .pdf")
    p_prev.add_argument("--provider", default=None, choices=["claude", "codex"])
    p_prev.add_argument("--model", default=None)
    p_prev.add_argument("--argv", action="store_true", help="also print the command line")

    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.root)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    match args.cmd:
        case "serve":
            return cmd_serve(cfg, args)
        case "doctor":
            return asyncio.run(cmd_doctor(cfg, args))
        case "run":
            return asyncio.run(cmd_run(cfg, args))
        case "new":
            return cmd_new(cfg, args)
        case "import":
            return cmd_import(cfg, args)
        case "setup":
            return cmd_setup(cfg)
        case "init-git":
            return cmd_init_git(cfg)
        case "reindex":
            return cmd_reindex(cfg)
        case "preview":
            return cmd_preview(cfg, args)
    return 1


# --------------------------------------------------------------------------


def cmd_serve(cfg, args) -> int:
    import secrets

    import uvicorn

    from .web.app import create_app
    from .web.security import allowed_hosts

    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    # A key made for this start (web/security.py): the address printed and
    # opened carries it after `#`, which a browser never sends to a server; a
    # website open in the same browser has neither it nor this computer's name.
    token = secrets.token_urlsafe(32)
    url = f"http://{host}:{port}/#auth={token}"

    if not args.no_browser and cfg.server.open_browser:
        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n  atelier → {url}\n"
          "  (open this address; it changes each time atelier starts)\n")
    app = create_app(cfg, control_token=token, allowed_hosts=allowed_hosts(host))
    uvicorn.run(app, host=host, port=port, log_level="warning",
                access_log=False, workers=1)
    return 0


async def cmd_doctor(cfg, args) -> int:
    from .db import get_db
    from .doctor import format_report, run_checks

    report = await run_checks(cfg, get_db(cfg.db_path), live=args.live)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n  atelier doctor — {cfg.root}\n")
        print(format_report(report))
        print()
    return 0 if report["worst"] != "fail" else 1


async def cmd_run(cfg, args) -> int:
    from .compile import next_session_number, plan_run
    from .db import get_db
    from .events import EventHub
    from .harnesses import get_harness
    from .problems import acquire_lock, load_problem, release_lock
    from .runner import RunManager
    from .spec import RunSpec

    db = get_db(cfg.db_path)
    hub = EventHub()
    manager = RunManager(cfg, hub, db)

    problem = load_problem(cfg.problems_dir, args.problem)
    harness = get_harness(cfg.harnesses_dir, args.harness)

    task_kind = "default"
    if args.task:
        task_kind = "text"
    elif args.task_file:
        task_kind = "files"

    spec = RunSpec(
        problem=args.problem, harness=args.harness, mode=args.mode,
        model=args.model, effort=args.effort, account=args.account,
        provider=args.provider or "",
        task_kind=task_kind, task_text=args.task or "",
        task_files=list(args.task_file),
        max_usd=args.max_usd, max_minutes=args.max_minutes,
        loop_rounds=args.rounds,
    )

    isolated_reading = harness.kind == "read" and spec.mode != "oneshot"
    needs_lock = spec.mode != "oneshot" and not isolated_reading
    lock_held = False
    if spec.mode != "oneshot":
        acquire_lock(problem.root, spec.run_id, harness.id)
        lock_held = True

    n = next_session_number(cfg, problem)
    try:
        plan = plan_run(
            cfg, problem, harness, spec, session_number=n,
            isolate_reading=isolated_reading)
        if isolated_reading:
            release_lock(problem.root, spec.run_id)
            lock_held = False
    except ValueError as exc:
        if lock_held:
            release_lock(problem.root, spec.run_id)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        if lock_held:
            release_lock(problem.root, spec.run_id)
        raise
    print(f"  run {spec.run_id} · {harness.id} on {problem.slug} · "
          f"{plan.model} ({plan.effort})")
    if not args.quiet:
        print(f"  {plan.run_dir}\n")

    try:
        handle = await manager.launch(plan, session_number=n)
    except Exception:
        if needs_lock:
            release_lock(problem.root, spec.run_id)
        raise

    if not args.quiet:
        printer = asyncio.create_task(_print_stream(hub, handle.run_id))
    try:
        await asyncio.wait_for(handle._exit_future, timeout=None)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n  interrupt — stopping gracefully…")
        await handle.stop("interrupted from the terminal")
    finally:
        if not args.quiet:
            printer.cancel()
        if needs_lock:
            release_lock(problem.root, spec.run_id)

    # Terminal state is published before finalize does its work, so wait on the
    # explicit signal — otherwise asyncio.run() cancels the lab-book write.
    try:
        await asyncio.wait_for(handle.finished.wait(), timeout=300)
    except TimeoutError:
        print("  warning: finalize did not complete within 5 minutes", file=sys.stderr)

    t = handle.totals
    print(f"\n  {handle.state} · {handle.ended_reason}")
    money = f"${t.cost_computed:.4f}" if t.priced else "cost not priced"
    print(f"  {money} · {t.tok_in:,} in / {t.tok_out:,} out · "
          f"{t.turns} turns · {t.tool_calls} tool calls · "
          f"{handle.clock.wall / 60:.1f} min")
    return 0 if handle.state == "done" else 1


async def _print_stream(hub, run_id: str) -> None:
    from .events import topic_run

    sub = await hub.subscribe(f"cli-{run_id}", {topic_run(run_id)})
    dim, reset, bold = "\033[2m", "\033[0m", "\033[1m"
    while True:
        _, event = await sub.queue.get()
        p = event.payload
        match event.type:
            case "assistant.text":
                print(f"\n{p.get('text', '').strip()}\n")
            case "tool.use":
                inp = json.dumps(p.get("input", {}))[:120]
                print(f"{dim}  ⚙ {p.get('name')} {inp}{reset}")
            case "notice":
                print(f"{dim}  · {p.get('message')}{reset}")
            case "budget":
                print(f"{bold}  ⚠ budget {p.get('level')}: {p.get('detail')}{reset}")
            case "steer.sent":
                print(f"{bold}  → {p.get('text', '')[:200]}{reset}")
            case "latex":
                ok = "compiled" if p.get("ok") else f"FAILED: {'; '.join(p.get('errors', [])[:2])}"
                print(f"{dim}  · latex {ok}{reset}")
            case "error":
                print(f"\033[31m  ✗ {p.get('message', '')[:400]}{reset}")


def cmd_new(cfg, args) -> int:
    from .problems import ProblemError, create_problem

    try:
        p = create_problem(cfg.problems_dir, args.slug, title=args.title)
    except ProblemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"  created {p.root}")
    return 0


def cmd_import(cfg, args) -> int:
    import shutil

    from .problems import Problem, ProblemMeta, SLUG_RE

    src = args.path.expanduser().resolve()
    if not src.is_dir():
        print(f"error: {src} is not a directory", file=sys.stderr)
        return 1
    slug = args.slug or src.name.lower().replace(" ", "-")
    if not SLUG_RE.match(slug):
        print(f"error: {slug!r} is not a valid slug", file=sys.stderr)
        return 1

    dest = cfg.problems_dir / slug
    if dest.exists() and dest.resolve() != src:
        print(f"error: {dest} already exists", file=sys.stderr)
        return 1

    if dest.resolve() != src:
        if args.move:
            shutil.move(str(src), str(dest))
        else:
            shutil.copytree(src, dest)

    statement = args.statement
    if not statement:
        candidates = sorted(dest.glob("*.md"), key=lambda p: -p.stat().st_size)
        statement = candidates[0].name if candidates else "problem.md"

    meta = ProblemMeta(
        title=args.title or slug.replace("-", " ").replace("_", " ").title(),
        slug=slug, statement=statement,
    )
    if not (dest / "problem.toml").is_file():
        meta.dump(dest)
    problem = Problem(root=dest, meta=ProblemMeta.load(dest))
    problem.ensure_scaffold()
    for sub in ("strategy", "refs"):
        (dest / sub).mkdir(exist_ok=True)

    print(f"  imported {dest}")
    print(f"  statement: {problem.meta.statement}")
    return 0


EXAMPLE_STATEMENT = """\
# Perfect numbers below 100

A positive whole number is **perfect** when it equals the sum of its divisors,
not counting itself. For example 6 is perfect, because its divisors are 1, 2
and 3, and 1 + 2 + 3 = 6.

**The task.** Find every perfect number below 100, and prove that there are no
others.

This is a solved and completely standard piece of mathematics. It is here so
you have something harmless to try a first session on, and so you can see what
Atelier produces. Delete this folder whenever you like.
"""


def cmd_setup(cfg) -> int:
    """First-time setup, written for someone who does not use a terminal often.

    Everything it reports is phrased as a plain sentence, and every problem is
    followed by the exact thing to do about it.
    """
    import shutil

    print()
    print("  Setting up atelier")
    print(f"  Folder: {cfg.root}")
    print()

    ok = True

    # --- the folder your mathematics lives in ---------------------------
    if cfg.problems_dir.is_dir():
        n = sum(1 for p in cfg.problems_dir.iterdir()
                if p.is_dir() and not p.name.startswith("."))
        print(f"  [ok]   Your problems folder exists: {cfg.problems_dir}")
        print(f"         It currently holds {n} problem{'' if n == 1 else 's'}.")
    else:
        cfg.problems_dir.mkdir(parents=True, exist_ok=True)
        print(f"  [made] Created your problems folder: {cfg.problems_dir}")
        print("         Each piece of mathematics you work on gets one folder in here.")

    # --- something to look at on the first screen -----------------------
    existing = [d for d in cfg.problems_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")] \
        if cfg.problems_dir.is_dir() else []
    if not existing:
        from .problems import ProblemError, create_problem

        try:
            example = create_problem(cfg.problems_dir, "example-perfect-numbers",
                                     title="Example: perfect numbers below 100")
            (example.root / "problem.md").write_text(EXAMPLE_STATEMENT, encoding="utf-8")
            print("  [made] Added one small example problem so the screen is not empty,")
            print("         and so you have something safe to try a first session on.")
            print("         Delete it whenever you like.")
        except ProblemError:
            pass                      # not important enough to stop setup

    # --- the program that actually talks to the model -------------------
    claude = shutil.which("claude")
    if claude:
        print(f"  [ok]   Claude Code is installed: {claude}")
    else:
        print("  [note] Claude Code is not installed.")
        print("         You only need it if you want to use Claude rather than")
        print("         ChatGPT. To install it:")
        print("             curl -fsSL https://claude.ai/install.sh | bash")
        print("         Then sign in by running:  claude")

    codex = shutil.which("codex")
    if codex:
        print(f"  [ok]   Codex (ChatGPT) is installed: {codex}")
        print("         Sign in with:  codex login")
    else:
        print("  [note] Codex (ChatGPT) is not installed.")
        print("         You only need it if you want to use ChatGPT rather than")
        print("         Claude. To install it:  brew install --cask codex")

    if not claude and not codex:
        ok = False
        print("  [need] You have neither Claude Code nor Codex, and atelier needs")
        print("         at least one of them to do anything.")

    # --- turning mathematics into a readable PDF ------------------------
    if shutil.which("latexmk"):
        print("  [ok]   LaTeX is installed, so sessions can produce PDFs.")
    else:
        print("  [warn] LaTeX is not installed, so no PDFs will be produced.")
        print("         Sessions in the lab still work, and the mathematics is")
        print("         always saved as plain text either way. The reader")
        print("         (unpacman) does need it: open unpacman, and its Getting")
        print("         ready page installs a small LaTeX with one click.")
        print("         Or, on a Mac:  brew install --cask mactex-no-gui")

    # --- keeping a history of what changed ------------------------------
    if shutil.which("git"):
        from .git import ensure_repo, is_repo

        if is_repo(cfg) or ensure_repo(cfg):
            print("  [ok]   Your work is saved after every session, and any session")
            print("         can be undone. This history stays on your computer.")
        else:
            print("  [warn] Could not start a history for your problems folder.")
            print("         Everything still works; sessions just cannot be undone.")
    else:
        print("  [warn] Git is not installed. Atelier will still work, but it")
        print("         cannot keep a history of changes to your mathematics.")

    print()
    if ok:
        print("  You are ready. Start it with:")
        print("      uv run atelier serve")
        print("  Then open the address it prints, in your browser.")
    else:
        print("  Install the missing item above, then run this again:")
        print("      uv run atelier setup")
    print()
    return 0 if ok else 1


def cmd_init_git(cfg) -> int:
    from .git import ensure_repo, is_repo

    if is_repo(cfg):
        print(f"  {cfg.problems_dir} is already a git repository")
        return 0
    if ensure_repo(cfg):
        print(f"  initialised git repository in {cfg.problems_dir}")
        return 0
    print("  failed to initialise repository", file=sys.stderr)
    return 1


def started_from_id(run_id: str) -> str:
    """The moment a run id begins with (`2026-09-16T22-15-31Z-5678`), spelled as
    the runner spells `started_at`: a rebuilt row must sort among the others,
    and `2026-09-16T22-15-31Z` sorts below a run started earlier that hour."""
    from datetime import datetime

    try:
        return datetime.strptime(run_id[:20], "%Y-%m-%dT%H-%M-%S%z").isoformat()
    except ValueError:
        return run_id


def cmd_reindex(cfg) -> int:
    import json as _json

    from .db import get_db

    db = get_db(cfg.db_path)
    n = 0
    for run_dir in sorted(cfg.runs_dir.glob("*")):
        cfgf = run_dir / "config.json"
        resf = run_dir / "result.json"
        if not cfgf.is_file():
            continue
        try:
            conf = _json.loads(cfgf.read_text())
            resolved = conf.get("resolved", {})
            spec = conf.get("spec", {})
            row = {
                "id": conf.get("run_id", run_dir.name),
                "problem": resolved.get("problem", ""),
                "harness": resolved.get("harness", ""),
                "harness_version": resolved.get("harness_version"),
                "provider": resolved.get("provider", "claude"),
                "account": resolved.get("account"),
                "model": resolved.get("model"),
                "effort": resolved.get("effort"),
                "mode": spec.get("mode", "standard"),
                "provider_session_id": spec.get("provider_session_id"),
                "bench_id": spec.get("bench_id"),
                "started_at": started_from_id(run_dir.name),
                "status": "failed",
            }
            if resf.is_file():
                res = _json.loads(resf.read_text())
                stats = res.get("stats", {})
                row |= {
                    "started_at": res.get("started_at", row["started_at"]),
                    "ended_at": res.get("ended_at"),
                    "status": res.get("state", "failed"),
                    "ended_reason": res.get("ended_reason"),
                    "wall_seconds": stats.get("wall_seconds"),
                    "active_seconds": stats.get("active_seconds"),
                    "turns": stats.get("turns", 0),
                    "tool_calls": stats.get("tool_calls", 0),
                    "tok_in": stats.get("tok_in", 0),
                    "tok_out": stats.get("tok_out", 0),
                    "tok_cache_read": stats.get("tok_cache_read", 0),
                    "tok_cache_write": stats.get("tok_cache_write", 0),
                    "cost_computed": stats.get("cost_computed", 0.0),
                    "cost_reported": stats.get("cost_reported"),
                    "status_before": res.get("status_before"),
                    "status_after": res.get("status_after"),
                    "git_commit": res.get("git_commit"),
                    "error": res.get("error"),
                }
            db.upsert_run(row)
            n += 1
        except Exception as exc:                              # noqa: BLE001
            print(f"  skipped {run_dir.name}: {exc}", file=sys.stderr)
    print(f"  reindexed {n} runs")
    return 0


def cmd_preview(cfg, args) -> int:
    import shutil as _sh

    from .compile import next_session_number, plan_run
    from .harnesses import get_harness
    from .problems import load_problem
    from .spec import RunSpec

    problem = load_problem(cfg.problems_dir, args.problem)
    harness = get_harness(cfg.harnesses_dir, args.harness)
    task_kind = "text" if args.task else ("files" if args.task_file else "default")
    spec = RunSpec(problem=args.problem, harness=args.harness, mode=args.mode,
                   provider=args.provider or "", model=args.model,
                   task_kind=task_kind, task_text=args.task or "",
                   task_files=list(args.task_file))
    spec.run_id = "preview-cli"
    plan = plan_run(cfg, problem, harness, spec,
                    session_number=next_session_number(cfg, problem))
    print("=" * 78)
    print("SYSTEM PROMPT APPEND")
    print("=" * 78)
    print(plan.system_append)
    print()
    print("=" * 78)
    print("OPENING MESSAGE")
    print("=" * 78)
    print(plan.prompt)
    if args.argv:
        print("=" * 78)
        print("ARGV")
        print("=" * 78)
        for a in plan.argv:
            print(f"  {a[:300]}")
    print()
    print(f"  ~{len(plan.prompt) // 4:,} prompt tokens (rough) · "
          f"{plan.model} ({plan.effort}) · cwd {plan.cwd}")
    _sh.rmtree(plan.run_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
