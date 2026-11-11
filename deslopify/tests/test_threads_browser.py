"""The explanations by run, in threads, and whole on hover (Tancredi, 2026-11-11):
real frontend and API, synthetic paper and provider; optional Chromium check."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from deslopify.app import explain
from test_app import app_cfg, client, needs_pdftotext, paper_with  # noqa: F401
from test_explain import fake_call

playwright = pytest.importorskip("playwright.sync_api")
WEB = Path(__file__).parents[1] / "deslopify" / "app" / "web"
LONG = ("A collar is a neighbourhood of the boundary diffeomorphic to the product of the "
        "boundary with a half-open interval, and every smooth manifold with boundary has one, "
        "which is what lets two manifolds be glued along their boundaries.")


@needs_pdftotext
def test_threads_runs_and_the_whole_of_a_long_head(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, text="It means this, plainly."))
    slug, paper = paper_with(app_cfg, client)
    html = f'''<!doctype html><html><head><link rel="stylesheet" href="/app.css"></head>
    <body data-view="paper"><main id="paper"><section class="sec" id="s-main">
    <h2>Main ideas</h2><p id="passage">{LONG}</p>
    </section></main><script type="module">
    import * as notes from '/notes.js';
    window.reading = {{slug:'{slug}',level:'expert',run:'R1'}};
    notes.init({{reading:()=>window.reading,kind:()=> 'paper'}});
    await notes.attach('{slug}'); await notes.setReading(window.reading);
    window.testNotes = notes; window.testReady = true;
    </script></body></html>'''
    errors = []

    def route(request):
        path = urlsplit(request.request.url).path
        if path == "/":
            request.fulfill(content_type="text/html", body=html)
        elif path.startswith("/api/"):
            payload = request.request.post_data_json if request.request.post_data else None
            response = client.request(request.request.method, path, json=payload)
            request.fulfill(status=response.status_code,
                            content_type=response.headers.get("content-type", "application/json"),
                            body=response.content)
        else:
            file = WEB / path.lstrip("/")
            if file.is_file():
                mime = "text/javascript" if file.suffix == ".js" else "text/css"
                request.fulfill(content_type=mime, body=file.read_bytes())
            else:
                request.fulfill(status=404)

    def settled(page):
        playwright.expect(page.locator(".note.pending, .note.streaming")).to_have_count(0)

    def select(page, locator, start, end):
        locator.scroll_into_view_if_needed()
        point = locator.evaluate("""(el, [start, end]) => {
          const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode();
          const range = document.createRange();
          range.setStart(node, start); range.setEnd(node, Math.min(end, node.length));
          const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
          const r = range.getClientRects()[0];
          return {x: r.left + 2, y: r.top + r.height / 2};
        }""", [start, end])
        page.mouse.click(point["x"], point["y"], button="right")

    def uids(page):
        return page.locator(".notes-list > .note").evaluate_all("ns => ns.map(n => n.dataset.uid)")

    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", route)
        page.goto("http://testserver/")
        playwright.expect(page.get_by_role("button", name="Ask a question", exact=True)).to_be_enabled()

        # A long passage: its head is cut, and shown whole on hover.
        select(page, page.locator("#passage"), 0, len(LONG))
        page.get_by_role("menuitem", name="Explain this passage").click()
        settled(page)
        first = page.locator(".notes-list > .note").first
        playwright.expect(first.locator(".note-term")).to_contain_text("…")
        first.locator(".note-term").hover()
        playwright.expect(page.locator("#note-quote")).to_be_visible()
        assert " ".join(page.locator("#note-quote").inner_text().split()) == LONG
        page.keyboard.press("Escape")
        playwright.expect(page.locator("#note-quote")).to_be_hidden()
        page.mouse.move(5, 5)

        # Words of its answer, explained and asked about: its thread.
        n1 = uids(page)[0]
        select(page, first.locator(".note-body"), 0, 8)            # "It means"
        page.get_by_role("menuitem", name="Explain “It means”").click()
        settled(page)
        select(page, first.locator(".note-body"), 3, 13)           # "means this"
        page.get_by_role("menuitem", name="Ask about this…").click()
        page.get_by_role("textbox", name="Your question", exact=True).fill("Which one?")
        page.keyboard.press("Enter")
        settled(page)
        n2, n3 = uids(page)[1:]
        # The same words in N2's answer are N2's own question, under N2.
        select(page, page.locator(f'.note[data-uid="{n2}"] .note-body'), 0, 8)
        page.get_by_role("menuitem", name="Explain “It means”").click()
        settled(page)
        order = uids(page)
        n4 = [u for u in order if u not in (n1, n2, n3)][0]
        assert order == [n1, n2, n4, n3]
        depth = page.locator(".notes-list > .note").evaluate_all(
            "ns => ns.map(n => [n.hasAttribute('data-sub'), n.style.getPropertyValue('--depth')])")
        assert depth == [[False, "0"], [True, "1"], [True, "2"], [True, "1"]]
        # The same words in N1's answer again: N2 opens; nothing is asked.
        calls = counter["calls"]
        select(page, first.locator(".note-body"), 0, 8)
        page.get_by_role("menuitem", name="Explain “It means”").click()
        assert len(uids(page)) == 4 and counter["calls"] == calls

        # Folding the first note folds its thread, and says how many.
        first.locator(".note-term").click()
        for uid in (n2, n3, n4):
            playwright.expect(page.locator(f'.note[data-uid="{uid}"]')).to_be_hidden()
        playwright.expect(first.locator(".note-folded-count")).to_have_text("+3 more in this thread, folded with it")
        first.locator(".note-term").click()
        playwright.expect(page.locator(f'.note[data-uid="{n4}"]')).to_be_visible()

        # Another run on screen: its own notes only; a question there is its own.
        page.evaluate("window.reading = {...window.reading, run: 'R2'}; window.testNotes.setReading(window.reading)")
        playwright.expect(page.locator(".notes")).to_be_hidden()
        page.get_by_role("button", name="Ask a question", exact=True).click()
        page.get_by_role("textbox", name="Your question", exact=True).fill("Asked in the second run")
        page.get_by_role("button", name="Ask", exact=True).click()
        settled(page)
        n5 = uids(page)
        assert len(n5) == 1 and n5[0] not in (n1, n2, n3, n4)
        page.evaluate("window.reading = {...window.reading, run: 'R1'}; window.testNotes.setReading(window.reading)")
        playwright.expect(page.locator(".notes-list > .note:not([hidden])")).to_have_count(4)
        assert uids(page) == [n1, n2, n4, n3]
        page.reload()
        playwright.expect(page.locator(".notes-list > .note")).to_have_count(4)
        assert uids(page) == [n1, n2, n4, n3]
        browser.close()

    from deslopify.app import conversations
    stored = {n["uid"]: n for n in conversations.Store()._load(paper)["notes"]}
    assert {u: stored[u]["reading"]["run"] for u in stored} == {
        n1: "R1", n2: "R1", n3: "R1", n4: "R1", n5[0]: "R2"}
    assert stored[n2]["where"] == {"kind": "note", "note": n1}
    assert stored[n4]["where"] == {"kind": "note", "note": n2}
    assert not errors, errors
