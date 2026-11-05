"""Drag a PDF onto the reader's home page, the way a person would.

    uv run --with playwright python deslopify/tools/drag.py PDF SELECTOR OUT.png [URL]

SELECTOR is where to drop: `#drop` for the box, or anything else — `.lede`, say
— to check that the whole page accepts a drop, as it promises to. Reports
whether the box lit up while dragging, and what the page showed afterwards.

Setting the file input directly (look.py --upload) does not exercise any of the
drag handling. This does: on 2026-09-28 it showed that dropping outside the box
did nothing at all, and that the page-wide handlers had been piling up.

Note that a successful drop *uploads* the PDF, creating a paper. Delete it
afterwards with `curl -X DELETE <URL>/api/papers/<slug>` if it was only a test.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DROP = """([b64, selector]) => {
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const file = new File([bytes], 'dragged-paper.pdf', { type: 'application/pdf' });
  const dt = new DataTransfer();
  dt.items.add(file);
  const el = document.querySelector(selector);
  for (const type of ['dragenter', 'dragover']) {
    el.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
  }
  const lit = document.querySelector('#drop')?.classList.contains('over') ?? false;
  el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  return lit;
}"""


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    pdf, selector, out = sys.argv[1], sys.argv[2], sys.argv[3]
    url = sys.argv[4] if len(sys.argv) > 4 else "http://127.0.0.1:7720/"
    data = base64.b64encode(Path(pdf).read_bytes()).decode()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 860},
                                device_scale_factor=2)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="networkidle")
        lit = page.evaluate(DROP, [data, selector])
        page.wait_for_timeout(2500)
        shown = page.evaluate("""() => ({
          chosen: document.querySelector('.chosen-name')?.textContent ?? null,
          dropHidden: document.querySelector('#drop')?.hidden ?? null,
          toast: document.querySelector('.toast')?.textContent ?? null,
        })""")
        page.screenshot(path=out)
        browser.close()

    print(json.dumps({"errors": errors, "box lit up while dragging": lit, **shown},
                     indent=1))


if __name__ == "__main__":
    main()
