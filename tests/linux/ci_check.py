"""Checks run on GitHub's own Linux machines, on unpacman as a person installs
it on a Linux computer (deslopify/public/github/linux.yml, 2026-11-05). Each
step is one subcommand; each line says ok or FAIL, and any FAIL ends the step
with a failure. Nothing here signs in to anything or reaches any AI: the AI of
the readings and explanations is unpacman's own stand-in, and every "install"
is the real one, done the way the page does it.

    python3 ci_check.py installed SHA      what the one line installed
    python3 ci_check.py open               open it as its menu entry does
    python3 ci_check.py ready              Getting ready: protection, then Claude Code, Codex, LaTeX
    python3 ci_check.py reading            a free reading and an explanation (stand-in AI)
    python3 ci_check.py update SHA         an older version, offered and taking this one
    python3 ci_check.py quit               Quit, as Advanced settings does

Standard library only: it runs with the system's own Python 3.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
COPY = HOME / ".unpacman"
PYTHON = COPY / ".venv" / "bin" / "python"
STATE = HOME / ".unpacman-ci.json"          # what one step tells the next
PUBLIC = "https://github.com/Tancredi-Schettini-Gherardini/unpacman.git"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        failures.append(name)
    return bool(ok)


def done() -> None:
    print(f"\n{'all ok' if not failures else f'{len(failures)} FAIL: ' + '; '.join(failures)}")
    sys.exit(1 if failures else 0)


def git(*args: str, cwd: Path = COPY) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=False).stdout.strip()


def state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def remember(**values: object) -> None:
    STATE.write_text(json.dumps(state() | values))


def api(method: str, path: str, body: object = None, *, base: str | None = None,
        token: str | None = None, timeout: float = 60, raw: bytes | None = None,
        content_type: str = "application/json") -> tuple[int, object]:
    s = state()
    base = (base or s["url"]).rstrip("/")
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{base}{path}", data=data, method=method, headers={
        "X-Unpacman-Token": token or s["token"], "Content-Type": content_type})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(text) if text.strip()[:1] in "[{" else text)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(text)
        except ValueError:
            return exc.code, text


def until(what, timeout: float, every: float = 2.0):
    end = time.monotonic() + timeout
    while True:
        value = what()
        if value or time.monotonic() > end:
            return value
        time.sleep(every)


def desktop_entry() -> dict[str, str]:
    share = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
    path = share / "applications" / "unpacman.desktop"
    entry = {"_path": str(path)}
    if path.is_file():
        for line in path.read_text().splitlines()[1:]:
            if "=" in line:
                key, value = line.split("=", 1)
                entry[key] = value
    return entry


# --------------------------------------------------------------------- steps

def installed(sha: str) -> None:
    check("the copy is in ~/.unpacman", (COPY / ".git").is_dir())
    head = git("rev-parse", "HEAD")
    check("at the version just published", head == sha, f"{head[:7]} vs {sha[:7]}")
    check("on the branch linux", git("rev-parse", "--abbrev-ref", "HEAD") == "linux")
    check("following the public repository, its branch linux only",
          git("remote", "get-url", "origin") == PUBLIC
          and git("for-each-ref", "--format=%(refname)", "refs/remotes") == "refs/remotes/origin/linux",
          git("remote", "get-url", "origin"))
    check("its packages installed", PYTHON.is_file())
    check("left clean", git("status", "--porcelain", "--untracked-files=no") == "")
    marker = COPY / "var" / "unpacman-app.json"
    check("marked as installed, so it looks for new versions by itself", marker.is_file())
    entry = desktop_entry()
    check("a menu entry", "Exec" in entry, entry["_path"])
    check("which opens this copy as its icon would",
          entry.get("Exec") == f"{PYTHON} -m deslopify.app --app"
          and entry.get("Path") == str(COPY.resolve()), json.dumps(entry)[:300])
    check("with unpacman's icon", Path(entry.get("Icon", "/nonexistent")).is_file(),
          entry.get("Icon", ""))
    validate = shutil.which("desktop-file-validate")
    if validate:
        out = subprocess.run([validate, entry["_path"]], capture_output=True, text=True)
        check("the menu entry passes desktop-file-validate",
              out.returncode == 0 and not out.stdout.strip(), (out.stdout + out.stderr).strip())
    for tool in ("bwrap", "socat", "rg", "git", "curl", "xz", "perl", "pdftoppm"):
        check(f"{tool} installed", shutil.which(tool) is not None)
    done()


def open_() -> None:
    entry = desktop_entry()
    seen = HOME / ".unpacman-ci-browser"
    stub = HOME / ".unpacman-ci-browser.sh"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$1" >> {seen}\n')
    stub.chmod(0o755)
    seen.unlink(missing_ok=True)
    # As a desktop starts it: the Exec line, in the Path folder — with a browser
    # that writes down the address it is given (Python's webbrowser obeys $BROWSER).
    argv = entry["Exec"].split()
    started = time.monotonic()
    subprocess.run(argv, cwd=entry["Path"], env=os.environ | {"BROWSER": str(stub)},
                   capture_output=True, text=True, timeout=180, check=False)
    url = until(lambda: seen.is_file() and seen.read_text().strip().splitlines()[-1], 60, 0.5)
    check("opened from the menu entry: the browser is given its address",
          bool(url), f"{round(time.monotonic() - started, 1)} s")
    record = COPY / "var" / "unpacman-server.json"
    check("running in the background", record.is_file())
    if not url or not record.is_file():
        done()
    server = json.loads(record.read_text())
    token = re.search(r"#auth=([^&\s]+)", url)
    check("the address carries its key", bool(token) and token.group(1) == server.get("token"))
    remember(url=server["url"], token=server["token"], pid=server.get("pid"))
    status, hello = api("GET", "/api/hello")
    check("it answers", status == 200 and isinstance(hello, dict) and hello.get("app") == "unpacman",
          str(hello)[:200])
    page = api("GET", "/")[1]
    check("the page knows it is on a Linux computer", 'data-platform="linux"' in str(page))
    # Opened again: the same unpacman, not a second one.
    subprocess.run(argv, cwd=entry["Path"], env=os.environ | {"BROWSER": str(stub)},
                   capture_output=True, text=True, timeout=180, check=False)
    servers = [l for l in subprocess.run(["ps", "-eo", "args"], capture_output=True,
                                         text=True).stdout.splitlines()
               if "deslopify.app" in l and "--background" in l]
    check("opened twice, one unpacman", len(servers) == 1, f"{len(servers)} running")
    done()


def ready() -> None:
    status, now = api("GET", "/api/ready?fresh=true", timeout=180)
    check("Getting ready answers", status == 200, str(now)[:200])
    check("on a Linux computer", now.get("platform") == "linux" and now.get("installable") is True,
          str(now.get("platform")))
    protection = now.get("protection") or {}
    check("Protection: the sandbox works here (bubblewrap)",
          protection.get("needed") is True and protection.get("ok") is True, json.dumps(protection))
    for kind, label in (("install-claude", "Claude Code"), ("install-codex", "Codex"),
                        ("install-latex", "LaTeX")):
        status, job = api("POST", f"/api/ready/{kind}", {}, timeout=120)
        if not check(f"{label}: installing starts", status == 200, str(job)[:300]):
            continue
        ident = job["id"]
        final = until(lambda: (lambda j: j if j.get("state") in ("done", "failed") else None)(
            api("GET", f"/api/ready/job/{ident}", timeout=60)[1]), 1500, 5)
        check(f"{label}: installed by the page", bool(final) and final.get("state") == "done",
              (final or {}).get("error", "timed out") if not final or final.get("state") != "done"
              else "")
    status, now = api("GET", "/api/ready?fresh=true", timeout=300)
    ais = {a["provider"]: a for a in now.get("ais", []) if a.get("account") == "default"}
    for provider in ("claude", "codex"):
        a = ais.get(provider, {})
        check(f"{provider} found installed (not signed in: no account here)",
              a.get("installed") is True and not a.get("signed_in"), json.dumps(a)[:300])
    tex = now.get("tex") or {}
    check("LaTeX ready, a test page typeset", not tex.get("missing") and tex.get("found"),
          json.dumps(tex)[:300])
    done()


def _pdf(lines: list[str]) -> bytes:
    """A small, valid PDF with a text layer: the tests' own make_pdf, run by the
    installed copy's Python (this script needs only the system's)."""
    made = subprocess.run(
        [str(PYTHON), "-c", "import sys; sys.path.insert(0, 'deslopify/tests'); "
         "from test_app import make_pdf; sys.stdout.buffer.write(make_pdf(sys.argv[1:]))",
         *lines], cwd=COPY, capture_output=True, check=False)
    if made.returncode != 0:
        raise SystemExit("No PDF could be made: " + made.stderr.decode()[-500:])
    return made.stdout


def reading() -> None:
    # A test server of the installed copy's own, its AI the stand-in.
    root = HOME / "unpacman-ci-reading"
    shutil.rmtree(root, ignore_errors=True)
    port, token = "7801", "ci-reading-token"
    log = open(HOME / "unpacman-ci-reading.log", "w")
    # The system's folders only: the stand-in AI is a Python script
    # (`#!/usr/bin/env python3`) run inside unpacman's sandbox, which hides
    # other Pythons (a runner's tool cache, say) from it.
    env = os.environ | {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
    server = subprocess.Popen([str(PYTHON), "deslopify/tools/isolated_server.py", str(root),
                               "--standin", "--pretend", "ready", "--token", token,
                               "--port", port, "--model", "claude-haiku-4-5", "--effort", "low"],
                              cwd=COPY, env=env, stdout=log, stderr=subprocess.STDOUT,
                              start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    try:
        up = until(lambda: api("GET", "/api/hello", base=base, token=token, timeout=5)[0] == 200
                   if _alive(server) else "dead", 90, 1)
        check("a test server of the installed copy, with the stand-in AI", up is True)
        if up is not True:
            return
        words = ("Let a and b be even integers. Then a + b is even. Proof. Write a = 2m and "
                 "b = 2n; then a + b = 2(m + n), which is even.")
        boundary = "unpacmanci"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"sums.pdf\"\r\nContent-Type: application/pdf\r\n\r\n").encode() \
            + _pdf(["On sums of even integers", "A. Author", "Abstract", words, words]) \
            + f"\r\n--{boundary}--\r\n".encode()
        status, paper = api("POST", "/api/papers", base=base, token=token, raw=body,
                            content_type=f"multipart/form-data; boundary={boundary}")
        if not check("a paper added", status == 200 and "slug" in paper, str(paper)[:300]):
            return
        slug = paper["slug"]
        status, started = api("POST", f"/api/papers/{slug}/read",
                              {"level": "expert", "max_usd": 1, "max_minutes": 20},
                              base=base, token=token)
        if not check("an Expert reading starts", status == 200, str(started)[:300]):
            return

        def finished():
            s, papers = api("GET", "/api/papers", base=base, token=token)
            mine = next((p for p in papers if p.get("slug") == slug), {}) if s == 200 else {}
            # "unread" only until the run is registered; "reading" while it runs.
            return mine if mine.get("state") not in (None, "unread", "reading") else None

        info = until(finished, 1200, 5) or {}
        check("the reading finished, ready to read", info.get("state") == "ready",
              json.dumps(info)[:400])
        runs = sorted((root / "runs").glob("*/reading/companion.pdf"))
        check("its companion written", bool(runs), str(runs[0]) if runs else "")
        if runs:
            pdf = runs[0].read_bytes()
            check("its PDF typeset", pdf.startswith(b"%PDF-") and len(pdf) > 2000,
                  f"{len(pdf)} bytes")
        # An explanation, kept with the paper (saved conversations).
        status, out = api("POST", f"/api/papers/{slug}/explain",
                          {"text": "even integers", "where": {"kind": "paper", "page": 1},
                           "effort": "low", "conversation": {"note": "ci1", "message": "root",
                                                             "key": "k"}},
                          base=base, token=token, timeout=120)
        check("an explanation answered", status == 200 and '"type": "done"' in str(out),
              str(out)[-300:])
        status, notes = api("GET", f"/api/papers/{slug}/notes", base=base, token=token)
        check("and kept with the paper", status == 200 and len(notes.get("notes", [])) == 1
              and notes["notes"][0].get("status") == "done", str(notes)[:300])
    finally:
        api("POST", "/api/quit", {}, base=base, token=token, timeout=10) if _alive(server) else None
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGTERM)
        log.close()
        print("--- test server's log (tail):")
        print("\n".join((HOME / "unpacman-ci-reading.log").read_text().splitlines()[-25:]))
        done()


def _alive(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def update(sha: str) -> None:
    parent = git("rev-parse", "--verify", "--quiet", "HEAD^")
    if not parent:
        print("This is the branch's first version: no older one to update from (skipped).")
        done()
    # An older copy: the version before, as one installed then would be.
    api("POST", "/api/quit", {}, timeout=10)
    until(lambda: not (COPY / "var" / "unpacman-server.json").exists(), 30, 0.5)
    git("reset", "--quiet", "--hard", parent)
    check("an older version in place", git("rev-parse", "HEAD") == parent, parent[:7])
    entry = desktop_entry()
    subprocess.run(entry["Exec"].split(), cwd=entry["Path"],
                   env=os.environ | {"BROWSER": "true"}, capture_output=True, timeout=180)
    record = COPY / "var" / "unpacman-server.json"
    until(record.is_file, 60, 0.5)
    server = json.loads(record.read_text())
    remember(url=server["url"], token=server["token"])
    status, s = api("GET", "/api/update?check=1", timeout=120)
    check("Check for a new version finds it", status == 200 and s.get("available") is True
          and s.get("behind", 0) >= 1, json.dumps(s)[:300])
    before = api("GET", "/api/hello")[1].get("started")
    status, s = api("POST", "/api/update", {}, timeout=60)
    check("Update now starts", status == 200, str(s)[:300])

    def back():
        try:
            st, hello = api("GET", "/api/hello", timeout=5)
        except OSError:
            return None
        return hello if st == 200 and hello.get("started") != before else None

    came = until(back, 600, 2)
    check("unpacman came back", bool(came))
    check("as the version just published", git("rev-parse", "HEAD") == sha,
          git("rev-parse", "--short", "HEAD"))
    s = api("GET", "/api/update")[1]
    check("idle, no error", s.get("state") == "idle" and not s.get("error"), json.dumps(s)[:300])
    check("copy left clean", git("status", "--porcelain", "--untracked-files=no") == "")
    done()


def quit_() -> None:
    status, out = api("POST", "/api/quit", {}, timeout=10)
    check("Quit answers", status == 200, str(out)[:200])
    gone = until(lambda: not [l for l in subprocess.run(["ps", "-eo", "args"], capture_output=True,
                                                        text=True).stdout.splitlines()
                              if "deslopify.app" in l and "--background" in l], 30, 0.5)
    check("unpacman stopped", bool(gone))
    done()


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    steps = {"installed": lambda: installed(sys.argv[2]), "open": open_, "ready": ready,
             "reading": reading, "update": lambda: update(sys.argv[2]), "quit": quit_}
    if what not in steps:
        sys.exit(__doc__)
    steps[what]()
