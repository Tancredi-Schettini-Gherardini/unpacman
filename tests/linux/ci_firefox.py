"""The page in Firefox — the browser most Linux computers come with — on one of
GitHub's Linux machines (deslopify/public/github/linux.yml, 2026-11-05): a
test server of this checkout with unpacman's stand-in AI, a free reading, then
its companion in Firefox: explain a selection, ask a question about the paper,
reload, and the notes are back; light and dark pictures. Nothing is spent.

    uv run --with playwright==1.63.0 python tests/linux/ci_firefox.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "deslopify" / "tests"))
from test_app import paper_pdf  # noqa: E402  (enough text to be read)

PORT, TOKEN = "7811", "ci-firefox-token"
BASE = f"http://127.0.0.1:{PORT}"
PICTURES = Path.cwd() / "firefox-pictures"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        failures.append(name)
    return bool(ok)


def api(method, path, body=None, raw=None, content_type="application/json", timeout=60):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Unpacman-Token": TOKEN, "Content-Type": content_type})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def poll(pg, js, timeout=30.0):
    end = time.time() + timeout
    while True:
        value = pg.evaluate(js)
        if value or time.time() > end:
            return value
        pg.wait_for_timeout(200)


SELECT = """(words) => {
  const host = document.querySelector('#paper');
  const w = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const i = n.nodeValue.indexOf(words);
    if (i < 0) continue;
    n.parentElement.scrollIntoView({ block: 'center' });
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + words.length);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    const b = r.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
  }
  return null;
}"""
DONE = "() => document.querySelectorAll('.notes-list .note.done').length"


# Its data and log in the folder it is run from (on GitHub, the checkout).
WORK = Path.cwd() / "firefox-work"
WORK.mkdir(exist_ok=True)
root = WORK / "root"
log = open(WORK / "server.log", "w")
# The checkout's own Python, and the system's folders only: the stand-in AI is
# a Python script (`#!/usr/bin/env python3`) run inside unpacman's sandbox,
# which rightly hides uv's caches and this checkout's .venv from it.
CLEAN = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_RUN_RECURSION_DEPTH")}
CLEAN["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if sys.platform == "darwin" and Path("/opt/homebrew/bin/python3").exists():
    # Tried on a Mac: its /usr/bin/python3 is a stub the sandbox blocks.
    CLEAN["PATH"] = "/opt/homebrew/bin:" + CLEAN["PATH"]
server = subprocess.Popen([str(HERE / ".venv" / "bin" / "python"), "deslopify/tools/isolated_server.py",
                           str(root), "--standin", "--pretend", "ready", "--token", TOKEN,
                           "--port", PORT, "--model", "claude-haiku-4-5", "--effort", "low"],
                          cwd=HERE, env=CLEAN, stdout=log, stderr=subprocess.STDOUT,
                          start_new_session=True)
try:
    for _ in range(90):
        if api("GET", "/api/hello", timeout=5)[0] == 200:
            break
        time.sleep(1)
    boundary = "unpacmanff"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sums.pdf\""
            f"\r\nContent-Type: application/pdf\r\n\r\n").encode() \
        + paper_pdf() + f"\r\n--{boundary}--\r\n".encode()
    status, paper = api("POST", "/api/papers", raw=body,
                        content_type=f"multipart/form-data; boundary={boundary}")
    check("a paper added", status == 200, str(paper)[:200])
    slug = paper["slug"]
    status, out = api("POST", f"/api/papers/{slug}/read", {"level": "expert", "max_usd": 1,
                                                           "max_minutes": 20})
    check("a reading starts", status == 200, str(out)[:200])
    state = None
    for _ in range(240):
        papers = api("GET", "/api/papers")[1]
        state = next((p.get("state") for p in papers if p.get("slug") == slug), None)
        if state not in (None, "unread", "reading"):
            break
        time.sleep(5)
    check("the reading is ready", state == "ready", str(state))

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.firefox.launch()
        PICTURES.mkdir(exist_ok=True)
        for scheme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=scheme)
            ctx.add_init_script(f"try {{ localStorage.setItem('unpacman.controlToken', {TOKEN!r});"
                                " localStorage.setItem('deslopify.hints', JSON.stringify({off: true, "
                                "seen: {}})); } catch (e) {}")
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(f"{scheme}: {e}"))
            pg.goto(f"{BASE}/#/p/{slug}")
            shown = poll(pg, "() => !!document.querySelector('#paper section.sec')")
            check(f"{scheme}: the companion is shown", bool(shown))
            if scheme == "light" and shown:
                pg.wait_for_timeout(800)
                at = pg.evaluate(SELECT, "worked example")
                check("a selection made", bool(at))
                if at:
                    pg.mouse.click(at["x"], at["y"], button="right")
                    pg.wait_for_timeout(300)
                    items = pg.evaluate("() => [...document.querySelectorAll('.ctx .ctx-item')]"
                                        ".map(i => i.textContent.trim())")
                    check("right-click offers Explain", bool(items) and items[0].startswith("Explain"),
                          str(items))
                    if items:
                        pg.locator(".ctx .ctx-item").first.click()
                        check("the explanation arrives in a note", bool(poll(pg, DONE)))
                pg.locator(".ask-any").click()
                pg.wait_for_timeout(300)
                field = pg.locator(".ctx.asking textarea")
                field.fill("What is the main result?")
                field.press("Enter")
                check("a question about the paper is answered",
                      bool(poll(pg, "() => document.querySelectorAll('.notes-list .note.done')"
                                    ".length >= 2")))
                pg.reload()
                back = poll(pg, "() => document.querySelectorAll('.notes-list .note.done')"
                                ".length >= 2")
                check("after a reload, the notes are back", bool(back))
            pg.wait_for_timeout(600)
            pg.screenshot(path=str(PICTURES / f"firefox-{scheme}.png"))
            ctx.close()
        browser.close()
    real = [e for e in errors if "favicon" not in e]
    check("no page errors", not real, "; ".join(real)[:500])
finally:
    api("POST", "/api/quit", {}, timeout=10)
    try:
        server.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(server.pid, signal.SIGTERM)
    log.close()
    print("--- test server's log (tail):")
    print("\n".join((WORK / "server.log").read_text().splitlines()[-20:]))

print(f"\n{'all ok' if not failures else f'{len(failures)} FAIL: ' + '; '.join(failures)}")
sys.exit(1 if failures else 0)
