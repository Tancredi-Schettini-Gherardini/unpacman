"""Look at a page of the reader in a real browser.

    uv run --with playwright python deslopify/tools/look.py URL OUT.png [options]

Takes a screenshot, and reports any error the page raised, the console, and the
result of an optional DOM probe. Read the PNG afterwards — *look* at it. On
2026-09-28 looking caught what no probe did: an element reporting `hidden`
while plainly on screen, a map centred on the wrong area, a section list
highlighting the wrong section.

Uses Chrome's standalone headless shell, which composites in software and so
works where headless Brave and a WKWebView driven through osascript could not.
It is Chrome, though, not Safari: for Safari's behaviour use picker.swift, a
compiled WebKit program, which does draw pages here (2026-09-29). Install the
shell once:

    uv run --with playwright python -m playwright install --only-shell chromium

Options:
    --width W --height H     viewport (default 1280x860); try 390x844 for a phone
    --dark                   prefers-color-scheme: dark
    --full                   the whole page, not just the viewport
    --wait MS                settle time after load (default 1200)
    --upload FILE            put FILE on #file, as if chosen in the dialog. It
                             skips the dialog itself, so it cannot catch a bug
                             in opening one — picker.swift can
    --click SELECTOR         click this after loading (and after any upload)
    --scroll SELECTOR        scroll this to the top before the shot
    --element SELECTOR       screenshot only this element
    --eval JS | --eval @FILE a probe whose result is printed; pass it from a file,
                             because zsh mangles `$[`, `=word` and bare globs

Nothing here spends money: it only reads pages from a server you started.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=860)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--eval", default=None)
    ap.add_argument("--wait", type=int, default=1200)
    ap.add_argument("--upload", default=None)
    ap.add_argument("--click", default=None)
    ap.add_argument("--scroll", default=None)
    ap.add_argument("--element", default=None)
    a = ap.parse_args()
    if a.eval and a.eval.startswith("@"):
        a.eval = Path(a.eval[1:]).read_text()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": a.width, "height": a.height},
                                color_scheme="dark" if a.dark else "light",
                                device_scale_factor=2)
        logs: list[str] = []
        errors: list[str] = []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.url} {r.failure}"))
        page.goto(a.url, wait_until="networkidle")
        page.wait_for_timeout(a.wait)
        if a.upload:
            page.set_input_files("#file", a.upload)
            page.wait_for_timeout(2500)
        if a.click:
            page.click(a.click)
            page.wait_for_timeout(2500)
        if a.scroll:
            page.evaluate(f"document.querySelector({a.scroll!r})"
                          f".scrollIntoView({{block: 'start'}})")
            page.wait_for_timeout(600)
        if a.element:
            target = page.locator(a.element).first
            target.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            target.screenshot(path=a.out)
        else:
            page.screenshot(path=a.out, full_page=a.full)
        result = page.evaluate(a.eval) if a.eval else None
        browser.close()

    print(json.dumps({
        "errors": errors,
        "console": [line for line in logs if not line.startswith("debug")][-8:],
        "eval": result,
    }, indent=1, default=str))


if __name__ == "__main__":
    main()
