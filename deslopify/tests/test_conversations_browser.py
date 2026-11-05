"""Real frontend and API, synthetic paper and provider; optional Chromium check."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from deslopify.app import conversations, explain
from test_app import app_cfg, client, needs_pdftotext, paper_with  # noqa: F401
from test_explain import fake_call

playwright = pytest.importorskip("playwright.sync_api")
WEB = Path(__file__).parents[1] / "deslopify" / "app" / "web"


@needs_pdftotext
def test_future_chats_reopen_and_continue_across_tabs_and_server_state(client, app_cfg, monkeypatch, tmp_path):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, paper = paper_with(app_cfg, client)
    html = f'''<!doctype html><html><head><link rel="stylesheet" href="/app.css"></head>
    <body data-view="paper"><main id="paper"><section class="sec" id="s-main">
    <h2>Main ideas</h2><p id="passage">A collar is a neighbourhood of the boundary.</p>
    </section></main><script type="module">
    import * as notes from '/notes.js';
    const reading = {{slug:'{slug}',level:'expert',run:null}};
    notes.init({{reading:()=>reading,kind:()=> 'paper'}});
    await notes.attach('{slug}'); await notes.setReading(reading);
    window.testNotes = notes; window.testReady = true;
    </script></body></html>'''
    errors = []
    calls = []
    held = []
    hold_next_answer = False

    def route(request):
        nonlocal hold_next_answer
        path = urlsplit(request.request.url).path
        if path == "/":
            request.fulfill(content_type="text/html", body=html)
        elif path.startswith("/api/"):
            payload = request.request.post_data_json if request.request.post_data else None
            calls.append((request.request.method, path, payload))
            response = client.request(request.request.method, path, json=payload)
            if path.endswith("/explain") and hold_next_answer:
                hold_next_answer = False
                held.append(request)  # the server finished; the old tab has not received it
                return
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

    def open_page(context):
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", route)
        page.goto("http://testserver/")
        playwright.expect(page.get_by_role("button", name="Ask a question", exact=True)).to_be_enabled()
        return page

    def ask(page, question):
        page.get_by_role("button", name="Ask a question", exact=True).click()
        page.get_by_role("textbox", name="Your question", exact=True).fill(question)
        page.get_by_role("button", name="Ask", exact=True).click()
        playwright.expect(page.locator(".note.pending, .note.streaming")).to_have_count(0)

    def follow(note, question):
        note.get_by_role("textbox", name="Your follow-up question", exact=True).fill(question)
        note.get_by_role("button", name="Send", exact=True).click()
        playwright.expect(note.locator(".note-qa").last.locator(".note-qa-body")).to_be_visible()

    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 1000})
        first = open_page(context)
        ask(first, "First question")
        note = first.locator(".note").first
        follow(note, "First follow-up")
        first.get_by_role("combobox", name="Reasoning effort for explanations").select_option("low")
        follow(note, "Second follow-up at low effort")
        ask(first, "Second question")
        playwright.expect(first.locator(".note-term-text")).to_have_text(["First question", "Second question"])
        first.reload()
        playwright.expect(first.locator(".note")).to_have_count(2)
        playwright.expect(first.locator(".note").first.locator(".note-qa")).to_have_count(2)

        # Reopening from the launcher can use a new browser context altogether.
        before = counter["calls"]
        fresh = browser.new_context(viewport={"width": 1400, "height": 1000})
        second = open_page(fresh)
        playwright.expect(second.locator(".note")).to_have_count(2)
        assert counter["calls"] == before
        client.app.state.deslopify.conversations = conversations.Store()
        second.reload()
        playwright.expect(second.locator(".note")).to_have_count(2)
        follow(second.locator(".note").first, "Continue after reopening")
        assert "First question" in counter["prompt"]
        assert "First follow-up" in counter["prompt"]
        assert "Second follow-up at low effort" in counter["prompt"]
        # Refreshing another tab keeps the draft already being typed there.
        draft = first.locator(".note").first.get_by_role("textbox", name="Your follow-up question")
        draft.fill("A draft still being written")
        first.evaluate("window.dispatchEvent(new Event('focus'))")
        playwright.expect(first.locator(".note").first.locator(".note-qa")).to_have_count(3)
        playwright.expect(draft).to_have_value("A draft still being written")
        # The older tab's own subsequent message does not overwrite the new one.
        follow(first.locator(".note").first, "Another tab's follow-up")
        second.reload()
        playwright.expect(second.locator(".note").first.locator(".note-qa")).to_have_count(4)
        second.locator(".note").last.get_by_role("button", name="Remove this note").click()
        playwright.expect(second.locator(".note")).to_have_count(1)
        second.reload()
        playwright.expect(second.locator(".note")).to_have_count(1)

        # Leave and return to a paper in the same tab.
        second.evaluate("window.testNotes.detach()")
        second.evaluate(f"window.testNotes.attach('{slug}')")
        playwright.expect(second.locator(".note").first.locator(".note-qa")).to_have_count(4)
        # Leave while a response is arriving, then return before its old callback runs.
        before = counter["calls"]
        hold_next_answer = True
        second.get_by_role("button", name="Ask a question", exact=True).click()
        second.get_by_role("textbox", name="Your question", exact=True).fill("Leaving during an answer")
        second.get_by_role("button", name="Ask", exact=True).click()
        playwright.expect(second.locator(".note.pending")).to_have_count(1)
        assert len(held) == 1
        second.evaluate("window.testNotes.detach()")
        second.evaluate(f"window.testNotes.attach('{slug}')")
        playwright.expect(second.locator(".note")).to_have_count(2)
        playwright.expect(second.locator(".note.error, .note.pending")).to_have_count(0)
        assert counter["calls"] == before + 1
        second.locator(".note").last.get_by_role("button", name="Remove this note").click()
        playwright.expect(second.locator(".note")).to_have_count(1)
        first.screenshot(path=str(tmp_path / "conversations.png"))
        browser.close()

    sent = [body for method, path, body in calls if path.endswith("/explain")]
    assert [body["effort"] for body in sent[:4]] == ["high", "high", "low", "low"]
    assert len({body["conversation"]["note"] for body in sent}) == 3
    stored = conversations.Store().public(paper, 1)
    assert len(stored) == 1 and len(stored[0]["thread"]) == 4
    assert not errors, errors
