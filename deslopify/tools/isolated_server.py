"""Run the reader on its own papers, runs and database — for testing.

    .venv/bin/python deslopify/tools/isolated_server.py ROOT [--port 7731]
        [--model claude-haiku-4-5] [--effort low] [--max-usd 0.5] [--max-minutes 5]
        [--seed SLUG ...] [--pretend fresh|installed|no-latex|ready] [--standin]
        [--token TOKEN]

ROOT is a scratch folder; `ROOT/papers`, `ROOT/runs`, `ROOT/atelier.db`,
`ROOT/method.local` (edits to the method, from Advanced settings) and
`ROOT/var` (what a reader tells it: a background, keys, the news answer) are
created there. `--seed SLUG` copies one of your real papers, every run that
read it and their database rows into ROOT — read-only on the real side — so a
finished companion can be looked at without spending anything.

Pages need the server's control token, as the real reader's do (security.py):
open the address this prints, which carries it after `#auth=`. It is also
written to `ROOT/control-token`; `--token` fixes it for scripts (the browser
checks in `var/ui-checks/` set it in the page's local storage).

Why not just `python -m deslopify.app --papers ROOT`: the reader and the lab
share `var/atelier.db`, and a second server's start-up marks the other one's
live readings as failed (`RunManager.reap_orphans`). A test server must have a
database of its own.

`--pretend` makes Getting ready (ready.py) report this computer as a new Mac
would — nothing installed (`fresh`), Claude Code installed but not signed in
(`installed`), signed in but without LaTeX (`no-latex`), or everything in place
(`ready`) — and its Install and Sign in buttons run harmless stand-ins that
take a few seconds, so the page can be looked at in every state without
installing or signing in to anything. Without it, the real checks run.

`--standin` answers every AI call with the stand-in programs in
`tools/standin/` instead of Claude Code and Codex: readings, explanations, key
terms and questions all run, take a little time and write their files, and
nothing is spent. Each call is logged to `ROOT/standin.log` (which model ran
which step). `STANDIN_SECONDS` sets how long a pass takes. The stand-ins are
kept ahead of the real programs whatever Getting ready does to PATH, and the
server refuses to start if `claude` or `codex` would still be a real one.

Pass a cheap `--model` and small caps. With none, a reading started through
this server runs the harness default (Claude Opus 5), exactly as the real
reader would. Browser scripts should answer `POST /api/papers/*/read` with
Playwright's `page.route` unless a real run is the point.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path


def seed(cfg, root: Path, slug: str) -> None:
    from atelier.db import Database

    real_papers = cfg.root / "papers"
    src = real_papers / slug
    if not src.is_dir():
        raise SystemExit(f"no paper {slug!r} in {real_papers}")
    dst = root / "papers" / slug
    if not dst.exists():
        shutil.copytree(src, dst)
    real = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    real.row_factory = sqlite3.Row
    rows = [dict(r) for r in real.execute("SELECT * FROM runs WHERE problem = ?", (slug,))]
    db = Database(root / "atelier.db")
    for row in rows:
        db.upsert_run(row)
        run_src = cfg.runs_dir / row["id"]
        run_dst = root / "runs" / row["id"]
        if run_src.is_dir() and not run_dst.exists():
            shutil.copytree(run_src, run_dst, symlinks=True)
            _point_at_copy(run_dst, src, dst, run_src)
    print(f"  seeded {slug}: {len(rows)} run(s)")


def _point_at_copy(run: Path, real_paper: Path, paper: Path, real_run: Path) -> None:
    """A copied run's record names the copy it now belongs to, not the real
    paper and run folders — or the copy would take the real ones for another
    paper of the same name (removal.belongs), and look for its files there."""
    import json

    config = run / "config.json"
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    resolved = data.get("resolved")
    if not isinstance(resolved, dict):
        return
    for key in ("problem_root", "cwd", "document"):
        value = resolved.get(key)
        if isinstance(value, str):
            value = value.replace(str(real_run), str(run)).replace(str(real_paper), str(paper))
            resolved[key] = value
    config.write_text(json.dumps(data, indent=2), encoding="utf-8")


def keep_standin_first(standin: Path) -> None:
    """Getting ready reorders PATH as it measures (`ready.prepare_environment`
    puts a Codex installed from it first) and after an install
    (`ready._prepend_path`). Either puts the real Claude Code and Codex in
    ~/.local/bin ahead of the stand-ins, and a test meant to cost nothing runs
    the real ones (it did, 2026-10-15) — so the stand-ins go back in front
    after each."""
    from deslopify.app import ready

    def first(env: dict[str, str] | None) -> None:
        env = os.environ if env is None else env
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and p != str(standin)]
        env["PATH"] = os.pathsep.join([str(standin), *parts])

    prepare, prepend = ready.prepare_environment, ready._prepend_path

    def prepare_environment(env=None, home=None):
        prepare(env, home)
        first(env)

    def _prepend_path(folder, env=None):
        prepend(folder, env)
        first(env)

    ready.prepare_environment = prepare_environment
    ready._prepend_path = _prepend_path
    ready.prepare_environment()             # as Getting ready will; then check
    for name in ("claude", "codex"):
        found = shutil.which(name)
        if not found or Path(found).parent != standin:
            raise SystemExit(f"--standin: `{name}` would be {found}, not the stand-in — "
                             "refusing to start a test server that could spend.")


class Pretend:
    """Getting ready, as a new Mac would report it; jobs that only pretend."""

    def __init__(self, cfg, start: str) -> None:
        self.cfg = cfg
        self.claude = {"fresh": "none", "installed": "installed"}.get(start, "signed")
        self.codex = "none"
        self.tex = start == "ready"
        self.jobs = {}
        # Whose the Mac's sign-in is; and unpacman's own, once Change account
        # has made one (signins.py) — until it goes back to the Mac's.
        self.who = {"claude": "reader@example.org", "codex": "reader@example.org"}
        self.own: dict[str, str | None] = {"claude": None, "codex": None}

    def get(self, *, fresh: bool = False):
        import time

        from deslopify.app import ready

        def ai(account, provider, level):
            own = self.own.get(provider)
            return ready.SignIn(account=account, provider=provider,
                                installed=level != "none", signed_in=level == "signed",
                                who=(own or self.who.get(provider, "")) if level == "signed" else "",
                                plan="Max" if level == "signed" else "",
                                version="2.1.300 (Claude Code)" if level != "none" else "",
                                own=bool(own), mac_signed_in=bool(own),
                                mac_who=self.who.get(provider, "") if own else "")
        ais = [ai(a.id, a.provider, self.claude if a.provider == "claude" else self.codex)
               for a in self.cfg.accounts.values()]
        tex = (ready.Tex(bindir="/Library/TinyTeX/bin/universal-darwin", label="TinyTeX",
                         ours=True, trial="ok") if self.tex else ready.Tex())
        return ready.Readiness(ais=ais, tex=tex, pdf="PDFium", checked=time.time())

    async def aget(self, *, fresh: bool = False):
        return self.get(fresh=fresh)

    def forget(self, **_):
        pass

    def job(self, ident):
        return self.jobs.get(ident)

    def start(self, kind: str):
        import asyncio
        import secrets

        from deslopify.app import ready

        job = ready.Job(id=secrets.token_hex(4), kind=kind, title=kind)
        self.jobs[job.id] = job

        async def work() -> None:
            steps = {
                "install-claude": ["Downloading Claude Code from Anthropic…",
                                   "Setting up Claude Code…"],
                "install-codex": ["Downloading Codex's installer…",
                                  "Installing Codex: about 135 MB to download, then a moment to set up…"],
                "install-latex": ["Downloading LaTeX (TinyTeX)… 40 of about 70 MB",
                                  "Unpacking LaTeX…",
                                  "Adding the pieces the companion needs (about 100 MB; a few minutes)…",
                                  "Checking that LaTeX can typeset a test page…"],
                "signin-claude": ["A Claude sign-in page is opening in your browser. Sign in there; "
                                  "this page notices when you are done."] * 3,
                "signin-codex": ["A ChatGPT sign-in page is opening in your browser. Sign in "
                                 "there; this page notices when you are done."] * 3,
                "switch-claude": ["A Claude sign-in page is opening in your browser. Sign in "
                                  "there with the other account; this page notices when you "
                                  "are done."] * 3,
                "switch-codex": ["A ChatGPT sign-in page is opening in your browser. Sign in "
                                 "there with the other account; this page notices when you "
                                 "are done."] * 3,
                "revert-claude": [f"Signing unpacman's own account, {self.own['claude']}, out…"],
                "revert-codex": [f"Signing unpacman's own account, {self.own['codex']}, out…"],
            }[kind]
            if kind.startswith(("signin-", "switch-")):
                job.url = "https://example.org/pretend-sign-in"
            try:
                for step in steps:
                    job.say(step)
                    job.lines.append(step)
                    await asyncio.sleep(1.5)
            except asyncio.CancelledError:
                job.finish("cancelled", "Stopped.")
                raise
            provider = kind.split("-", 1)[1]
            if kind.startswith("install-"):
                if provider == "latex":
                    self.tex = True
                else:
                    setattr(self, provider, "installed")
            else:
                setattr(self, provider, "signed")
            program = {"claude": "Claude Code", "codex": "Codex"}.get(provider, "")
            if kind.startswith("switch-"):
                self.own[provider] = "another.reader@example.org"
            elif kind.startswith("revert-"):
                self.own[provider] = None
            job.say({"install-claude": "Claude Code is installed. Next, sign in.",
                     "install-codex": "Codex is installed. Next, sign in.",
                     "install-latex": "LaTeX is ready.",
                     "signin-claude": "Signed in to Claude as reader@example.org.",
                     "signin-codex": "Signed in to ChatGPT as reader@example.org.",
                     "switch-claude": "", "switch-codex": "",
                     "revert-claude": "", "revert-codex": ""}[kind]
                    or (f"unpacman reads with another.reader@example.org from now on. {program} "
                        "elsewhere on this Mac keeps the sign-in it has."
                        if kind.startswith("switch-") else
                        f"unpacman reads with {program}'s sign-in on this Mac again, "
                        f"{self.who[provider]}."))
            job.finish("done")

        job.task = asyncio.get_running_loop().create_task(work())
        return job


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--port", type=int, default=7731)
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--max-usd", type=float, default=None)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--seed", action="append", default=[], metavar="SLUG")
    ap.add_argument("--pretend", choices=("fresh", "installed", "no-latex", "ready"),
                    default=None)
    ap.add_argument("--standin", action="store_true",
                    help="answer every AI call with tools/standin (nothing spent)")
    ap.add_argument("--token", default=None,
                    help="the control token pages must present (default: a new one)")
    args = ap.parse_args()

    import uvicorn

    from atelier.config import load_config
    from deslopify.app.server import Control, create_app

    root = args.root.expanduser().resolve()
    if args.standin:
        # Found first by every lookup of `claude` and `codex` (shutil.which).
        standin = Path(__file__).resolve().parent / "standin"
        os.environ["PATH"] = f"{standin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ.setdefault("STANDIN_LOG", str(root / "standin.log"))
        keep_standin_first(standin)
        print(f"  stand-in AI: {standin} (log: {os.environ['STANDIN_LOG']})")
    (root / "papers").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    # What the reader is told and keeps — a background, keys, a sign-up — in a
    # var/ of its own, never the real one (deslopify/app/places.py).
    os.environ["UNPACMAN_VAR"] = str(root / "var")
    real = load_config()
    for slug in args.seed:
        seed(real, root, slug)
    cfg = replace(real, problems_dir=root / "papers", runs_dir=root / "runs",
                  db_path=root / "atelier.db")
    token = args.token or secrets.token_urlsafe(24)
    fd = os.open(root / "control-token", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
    print(f"  isolated reader → http://127.0.0.1:{args.port}/#auth={token}  ({root})")
    # Quit works here as in the real reader. Update does not: this server's
    # copy is the real checkout, which a test must never move.
    from deslopify.app import updates

    class NoUpdates(updates.Updater):
        def start(self, *, readings_running: int) -> None:
            raise updates.UpdateRefused("A test server never updates: its copy is the "
                                        "real checkout.")

    control = Control(port=args.port)
    app = create_app(cfg, model=args.model, effort=args.effort,
                     max_usd=args.max_usd, max_minutes=args.max_minutes,
                     method_dir=root / "method.local",
                     checker=Pretend(cfg, args.pretend) if args.pretend else None,
                     updater=NoUpdates(real.root, auto=False), control=control,
                     control_token=token)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                           log_level="warning"))
    control.server = server
    server.run()


if __name__ == "__main__":
    main()
