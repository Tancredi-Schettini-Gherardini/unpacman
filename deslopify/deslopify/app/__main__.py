"""Start the reading interface: `python -m deslopify.app`.

Three ways in, one server:

    python -m deslopify.app            from a Terminal; Control+C stops it
    python -m deslopify.app --app      as the Unpacman icon does: in the
                                       background, then the browser
    python -m deslopify.app --check    what this computer still needs

On Windows, unpacman runs inside WSL (Ubuntu), and its Start-menu shortcut
runs the first of these in a window of its own (wsl.py, WINDOWS.md).

A server already running for this copy is reused rather than started twice:
its page is opened instead.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="deslopify.app",
        description="Read a paper: drop in a PDF and get a companion back.")
    ap.add_argument("--host", default="127.0.0.1")
    # Deliberately not Atelier's port. The lab and the reader are different
    # tools for different moments and should be able to run side by side.
    # Unset, 7720 is used if it is free and the next free port if not.
    ap.add_argument("--port", type=int, default=None,
                    help="port to listen on (default: 7720, or the next free one)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument(
        "--papers", type=Path, default=None,
        help="where uploaded papers are kept (default: <atelier>/papers)")
    # The page lets the reader choose the AI. These set where that choice
    # starts, and outrank whatever the browser remembers choosing last time.
    ap.add_argument("--model", default=None,
                    help="model the page starts on (default: the harness's own)")
    ap.add_argument("--effort", default=None,
                    help="effort the page starts on (default: the harness's own)")
    ap.add_argument("--max-usd", type=float, default=None,
                    help="cap per reading, all passes together")
    ap.add_argument("--max-minutes", type=float, default=None,
                    help="wall-clock cap per reading, all passes together")
    ap.add_argument("--reload", action="store_true",
                    help="restart on code changes (development). A reading in "
                         "progress is stopped by a reload.")
    ap.add_argument("--app", action="store_true",
                    help="open unpacman as its icon does: start it in the background "
                         "if it is not running, then open it in the browser")
    ap.add_argument("--check", action="store_true",
                    help="say what this computer still needs before a reading can run")
    ap.add_argument("--make-app", action="store_true",
                    help="put an Unpacman icon for this copy in ~/Applications — or, "
                         "inside WSL on Windows, a shortcut in the Start menu")
    ap.add_argument("--make-package", action="store_true",
                    help="make a zip of the committed `mac` branch of a clone of the "
                         "public repository, which installs without a GitHub account")
    ap.add_argument("--from", dest="package_from", type=Path, default=None,
                    help="the repository --make-package packages (default: this copy)")
    ap.add_argument("--windows", action="store_true",
                    help="with --make-package: the Windows zip, of the `windows` branch")
    ap.add_argument("--out", type=Path, default=None,
                    help="where --make-package puts the zip (default: <atelier>/var/packages)")
    # For the icon's background server, the updater, and tests.
    ap.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--apps-dir", type=Path, default=None, help=argparse.SUPPRESS)
    return ap


def _forwarded(args: argparse.Namespace) -> list[str]:
    """The options a background server is given, as they were given here."""
    out: list[str] = ["--host", args.host]
    for flag, value in (("--papers", args.papers), ("--model", args.model),
                        ("--effort", args.effort), ("--max-usd", args.max_usd),
                        ("--max-minutes", args.max_minutes)):
        if value is not None:
            out += [flag, str(value)]
    return out


def _self_check() -> int:
    """Load everything a server loads, against throwaway folders, and stop.
    The updater runs this in the new version before the old one gives way."""
    import tempfile

    from atelier.config import load_config
    from atelier.harnesses import get_harness

    from . import instance, mac, ready, updates  # noqa: F401
    from .readings import FAMILY
    from .server import WEB_DIR, create_app

    cfg = load_config()
    get_harness(cfg.harnesses_dir, FAMILY)
    with tempfile.TemporaryDirectory(prefix="unpacman-self-check-") as raw:
        tmp = Path(raw)
        (tmp / "papers").mkdir()
        trial = replace(cfg, problems_dir=tmp / "papers", runs_dir=tmp / "runs",
                        db_path=tmp / "atelier.db")
        create_app(trial, method_dir=tmp / "method.local")
    for page in ("index.html", "app.js", "app.css"):
        if not (WEB_DIR / page).is_file():
            print(f"missing {page}", file=sys.stderr)
            return 1
    print("ok")
    return 0


def _open_when_up(url: str, server: object) -> None:
    from .instance import open_browser

    def wait_then_open() -> None:
        deadline = time.time() + 60
        while time.time() < deadline and not getattr(server, "started", False):
            time.sleep(0.1)
        open_browser(url)
    threading.Thread(target=wait_then_open, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)

    from . import instance, ready

    # Started from the icon, nothing is on PATH but the system's folders.
    ready.prepare_environment()

    if args.self_check:
        return _self_check()

    from atelier.config import load_config

    cfg = load_config()
    root = cfg.root

    if args.make_app and ready.platform_name() == "wsl":
        # On a Windows computer: a Start-menu shortcut, made from inside WSL.
        from .wsl import WindowsAppError, make_shortcut

        try:
            link = make_shortcut(root)
        except WindowsAppError as exc:
            print(f"\n  The Unpacman shortcut could not be made: {exc}\n", file=sys.stderr)
            return 1
        print(f"\n  The Unpacman shortcut is at {link}")
        print("  Open it from the Start menu (type unpacman), or pin it to the taskbar.\n")
        return 0

    if args.make_app:
        from .mac import MacAppError, make_app

        try:
            target = make_app(root, args.apps_dir)
        except MacAppError as exc:
            print(f"\n  The Unpacman icon could not be made: {exc}\n", file=sys.stderr)
            return 1
        print(f"\n  The Unpacman icon is at {target}")
        print("  Open it from Launchpad or Spotlight, or drag it to the Dock.\n")
        return 0

    if args.make_package:
        from .package import (BRANCH, WINDOWS_BRANCH, PackageError, make_package,
                              make_windows_package, on_github)

        source = (args.package_from or root).expanduser()
        branch = WINDOWS_BRANCH if args.windows else BRANCH
        make = make_windows_package if args.windows else make_package
        try:
            made = make(source, args.out or root / "var" / "packages")
        except PackageError as exc:
            print(f"\n  No package was made: {exc}\n", file=sys.stderr)
            return 1
        print(f"\n  The package is {made} ({made.stat().st_size / 1e6:.1f} MB).")
        print(f"  It holds the committed `{branch}` branch; send it as it is.")
        if on_github(source, branch) is False:
            print(f"  Note: this version of `{branch}` is not on GitHub yet. Push it, so that")
            print("  a copy installed from the package finds it there.")
        print()
        return 0

    if args.check:
        checker = ready.Checker(cfg)
        checker.auto_trial = False
        now = checker.get(fresh=True)
        if now.tex.found and not now.tex.missing:
            error = ready.trial_typeset(now.tex.bindir)
            now.tex.trial, now.tex.trial_error = ("failed", error) if error else ("ok", "")
        print(ready.describe(now))
        return 0 if now.ok else 1

    if args.host not in ("127.0.0.1", "localhost"):
        print("unpacman only listens on this computer (127.0.0.1 or localhost).",
              file=sys.stderr)
        return 2

    if args.app:
        url = instance.find_running(root)
        if url and "#auth=" not in url:
            print("The running unpacman predates this security update. Restart it after "
                  "its current readings finish to activate the protections.", file=sys.stderr)
        if url is None:
            port = instance.choose_port(args.host, args.port or instance.DEFAULT_PORT)
            try:
                url = instance.start_background(root, host=args.host, port=port,
                                                extra=_forwarded(args))
            except RuntimeError as exc:
                instance.alert("unpacman could not start", str(exc))
                return 1
        if not args.no_browser:
            instance.open_browser(url)
        return 0

    import uvicorn

    from .server import Control, create_app

    # Papers are kept apart from the problems directory on purpose. That folder
    # holds open research, and a paper someone dropped in to read is not a
    # research problem — mixing them would put uploads in the lab's problem
    # list and someone's unsolved conjecture in the reader's library.
    papers = (args.papers or root / "papers").expanduser().resolve()
    papers.mkdir(parents=True, exist_ok=True)
    cfg = replace(cfg, problems_dir=papers)

    if args.port is None:
        # Opened twice — from a Terminal while the icon's copy runs, say: the
        # page of the one already running, rather than a second server on the
        # same papers.
        running = instance.find_running(root)
        if running and not args.reload:
            if "#auth=" not in running:
                print("The running unpacman predates this security update. Restart it "
                      "after its current readings finish.", file=sys.stderr)
            print(f"\n  unpacman is already running → {running}\n")
            if not args.no_browser:
                instance.open_browser(running)
            return 0
        port = instance.choose_port(args.host)
    else:
        # An explicit-port duplicate must not replace the live server's
        # authentication record before uvicorn discovers the occupied port.
        if not instance.port_free(args.host, args.port):
            running = instance.find_running(root)
            if running and not args.reload:
                print(f"\n  unpacman is already running → {running}\n")
                if not args.no_browser:
                    instance.open_browser(running)
                return 0
            print(f"Port {args.port} is already in use; the running server was "
                  "left untouched.", file=sys.stderr)
            return 2
        port = args.port

    url = f"http://{args.host}:{port}/"
    token = os.environ.pop("DESLOPIFY_APP_CONTROL_TOKEN", None) or secrets.token_urlsafe(32)
    launch_url = f"{url}#auth={token}"
    print(f"\n  unpacman → {url if args.background else launch_url}")
    print(f"  papers in  {papers}\n")
    if args.model or args.effort:
        print(f"  starts on  {args.model or 'the default model'}"
              + (f" ({args.effort})" if args.effort else "") + "\n")
    if not args.background and ready.platform_name() == "wsl":
        # Opened from its Start-menu shortcut, this window is unpacman (wsl.py).
        if sys.stdout.isatty():
            sys.stdout.write("\033]0;unpacman\007")
        print("  This window is unpacman: keep it open while you use it — minimise it.")
        print("  Closing it stops unpacman, and any reading it is doing. To stop it, use")
        print("  Quit in Advanced settings › This computer, or press Control+C here.\n")
    sys.stdout.flush()

    if args.reload:
        import json

        os.environ["DESLOPIFY_APP_SETTINGS"] = json.dumps({
            "papers": str(papers), "model": args.model, "effort": args.effort,
            "max_usd": args.max_usd, "max_minutes": args.max_minutes,
            "control_token": token})
        if not args.no_browser:
            threading.Thread(target=lambda: (time.sleep(1.2), instance.open_browser(launch_url)),
                             daemon=True).start()
        instance.write_record(root, url=url, port=port, background=args.background,
                              token=token)
        try:
            uvicorn.run("deslopify.app.server:app_from_env", factory=True, reload=True,
                        reload_dirs=[str(Path(__file__).resolve().parent)],
                        host=args.host, port=port, log_level="warning",
                        access_log=False)
        finally:
            instance.clear_record(root)
        return 0

    control = Control(background=args.background, port=port)
    app = create_app(cfg, model=args.model, effort=args.effort,
                     max_usd=args.max_usd, max_minutes=args.max_minutes,
                     control=control, control_token=token)
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port,
                                           log_level="warning", access_log=False,
                                           workers=1))
    control.server = server
    if not args.no_browser:
        _open_when_up(launch_url, server)
    instance.write_record(root, url=url, port=port, background=args.background,
                          token=token)
    try:
        server.run()
    finally:
        instance.clear_record(root)

    if control.restart:
        # The updater has put the new version in place and checked that it
        # starts: become it, on the same port, so the open page finds it.
        keep: list[str] = []
        skip = False
        for item in argv:
            if skip:
                skip = False
                continue
            if item in ("--port", "--no-browser"):
                skip = item == "--port"
                continue
            if item.startswith("--port="):
                continue
            keep.append(item)
        sys.stdout.flush()
        sys.stderr.flush()
        os.environ["DESLOPIFY_APP_CONTROL_TOKEN"] = token
        os.execv(sys.executable, [sys.executable, "-m", "deslopify.app", *keep,
                                  "--port", str(port), "--no-browser"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
