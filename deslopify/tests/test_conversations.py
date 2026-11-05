"""Future chats survive the tab, without importing the old answer cache."""

from __future__ import annotations

import asyncio
import json

import pytest

from deslopify.app import agents, conversations, explain, security
from test_app import app_cfg, client, needs_pdftotext, paper_with  # noqa: F401
from test_explain import ask, fake_call


def target(note="first", message="root", **metadata):
    return {"note": note, "message": message, "key": note, **metadata}


def notes(client, slug):
    response = client.get(f"/api/papers/{slug}/explanations")
    assert response.status_code == 200, response.text
    return response.json()["notes"]


@needs_pdftotext
def test_complete_threads_survive_a_new_store_and_keep_their_context(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, paper = paper_with(app_cfg, client)
    anchor = {"kind": "paper", "page": 1, "find": "residues", "html": "<script>bad</script>"}
    ask(client, slug, conversation=target(anchor=anchor), context="the selected paragraph", effort="high")
    ask(client, slug, conversation=target(message="more"), depth="more", brief="First answer.")
    ask(client, slug, conversation=target(message="follow-1"), question="Why does that follow?",
        history=[{"q": "What does it mean?", "a": "First answer."}], effort="low")
    ask(client, slug, conversation=target("second"), question="A different question")

    st = client.app.state.deslopify
    st.conversations = conversations.Store()  # the browser and server retained no chat state
    restored = notes(client, slug)
    assert [n["uid"] for n in restored] == ["first", "second"]
    first = restored[0]
    assert first["context"] == "the selected paragraph"
    assert first["anchor"] == {"kind": "paper", "page": 1, "find": "residues"}
    assert first["status"] == first["more"]["status"] == first["thread"][0]["status"] == "done"
    assert first["thread"][0]["uid"] == "follow-1"
    assert first["thread"][0]["q"] == "Why does that follow?"
    assert first["request"]["effort"] == "high"
    assert first["thread"][0]["request"]["effort"] == "low"
    assert first["thread"][0]["request"]["history"][0]["a"] == "First answer."
    assert "means this" in first["html"]
    before = counter["calls"]
    ask(client, slug, conversation=target(message="follow-2"), question="What about the next step?",
        history=[{"q": first["thread"][0]["q"], "a": "It means this."}])
    assert counter["calls"] == before + 1
    assert "Why does that follow?" in counter["prompt"]
    stored = (paper.root / conversations.STORE).read_text()
    assert '"markdown"' in stored and '"html"' not in stored


@needs_pdftotext
def test_cached_old_answers_are_not_imported_but_new_cached_questions_are_saved(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    ask(client, slug)
    assert notes(client, slug) == []
    _, events = ask(client, slug, conversation=target())
    assert events[-1]["cached"] and counter["calls"] == 1
    assert len(notes(client, slug)) == 1
    # Reusing the message ID after a settings change is still the same answer.
    ask(client, slug, conversation=target(), text="changed", effort="low")
    assert counter["calls"] == 1
    assert notes(client, slug)[0]["text"] == "residues"


@needs_pdftotext
def test_key_term_notes_keep_their_seed_and_more_answer(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    ask(client, slug, conversation=target(message="more", seed="The $x$ in <script>bad</script>"),
        depth="more", brief="The x in the paper.")
    note = notes(client, slug)[0]
    assert note["text"] == "residues" and note["fromTerm"] is True
    assert note["status"] == note["more"]["status"] == "done"
    assert '<script>' not in note["html"] and 'class="math inline"' in note["html"]


@needs_pdftotext
def test_remove_is_durable_and_a_stale_tab_cannot_recreate_the_note(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    ask(client, slug, conversation=target())
    response = client.delete(f"/api/papers/{slug}/notes/first")
    assert response.status_code == 200
    client.app.state.deslopify.conversations = conversations.Store()
    assert notes(client, slug) == []
    response = client.post(f"/api/papers/{slug}/explain", json={
        "conversation": target(message="later"), "question": "Continue", "where": {"kind": "whole"}})
    assert response.status_code == 409 and counter["calls"] == 1
    response = client.post(f"/api/papers/{slug}/explain", json={
        "conversation": target(), "question": "Old retry", "where": {"kind": "whole"}})
    assert response.status_code == 409 and counter["calls"] == 1


@needs_pdftotext
def test_history_read_and_delete_keep_the_existing_security_boundary(client, app_cfg, monkeypatch):
    monkeypatch.setattr(explain, "call", fake_call({}))
    slug, _ = paper_with(app_cfg, client)
    ask(client, slug, conversation=target())
    url = f"/api/papers/{slug}/notes/first"
    token = client.headers.pop("X-Unpacman-Token")
    assert client.get(f"/api/papers/{slug}/explanations").status_code == 403
    assert client.get(f"/api/papers/{slug}/notes").status_code == 403
    assert client.delete(url).status_code == 403
    client.cookies.set(security.SESSION_COOKIE, security.read_cookie(token))
    assert client.get(f"/api/papers/{slug}/notes").status_code == 200
    assert client.delete(url).status_code == 403   # download cookies never authorize deletion
    client.cookies.clear()
    client.headers["X-Unpacman-Token"] = token
    assert client.delete(url, headers={"Origin": "https://other.example"}).status_code == 403
    assert client.delete(url, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert len(notes(client, slug)) == 1
    response = client.post(f"/api/papers/{slug}/explain", json={
        "conversation": target("../outside"), "text": "x", "where": {"kind": "paper"}})
    assert response.status_code == 400


@needs_pdftotext
def test_persistence_does_not_trim_questions_answers_or_history(client, app_cfg, monkeypatch):
    question = "A long question " * 1000
    answer = "A long answer " * 2000
    history = [{"q": f"Earlier question {i}" * 100, "a": f"Earlier answer {i}" * 200} for i in range(30)]
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, text=answer))
    slug, paper = paper_with(app_cfg, client)
    ask(client, slug, conversation=target(), question=question, history=history)
    client.app.state.deslopify.conversations = conversations.Store()
    note = notes(client, slug)[0]
    assert note["question"] == question.strip()
    assert note["request"]["history"] == history
    data = json.loads((paper.root / conversations.STORE).read_text())
    assert data["notes"][0]["messages"][0]["answer"]["markdown"] == answer.strip()


@needs_pdftotext
async def test_closing_both_subscribers_keeps_each_message_once(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, delay=0.1))
    _, paper = paper_with(app_cfg, client)
    history = conversations.Store()
    service = explain.Explainer(app_cfg)
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="high")
    request = {"text": "x", "context": "", "where": {"kind": "paper"}, "level": "expert",
               "account": agent.account, "model": agent.model, "effort": "high", "custom": False}
    streams = []
    for uid in ("tab-one", "tab-two"):
        ref = (uid, "root")
        history.begin(paper, ref, request, {})
        stream = service.explain(paper, explain.Ask("x", "", {"kind": "paper"}, "expert"), agent,
                                 keep=lambda item, error, ref=ref: history.finish(paper, ref, item, error))
        assert (await anext(stream))["type"] == "start"
        streams.append(stream)
    for stream in streams:
        await stream.aclose()
    await asyncio.gather(*service.tasks)
    restored = conversations.Store().public(paper, 1)
    assert counter["calls"] == 1
    assert [n["uid"] for n in restored] == ["tab-one", "tab-two"]
    assert all(n["status"] == "done" for n in restored)
    # An in-flight answer cannot resurrect a note removed in another tab.
    history.remove(paper, "tab-one")
    history.finish(paper, ("tab-one", "root"), explain.load_store(paper)[0], None)
    assert [n["uid"] for n in history.public(paper, 1)] == ["tab-two"]


@needs_pdftotext
async def test_rejoining_a_message_after_instruction_edits_does_not_start_another_answer(
        client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, delay=0.1))
    _, paper = paper_with(app_cfg, client)
    service = explain.Explainer(app_cfg)
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="high")
    question = explain.Ask("x", "", {"kind": "paper"}, "expert")
    identity = (paper.slug, "first", "root")
    first = service.explain(paper, question, agent, identity=identity)
    assert (await anext(first))["type"] == "start"
    service.texts = lambda: explain.Texts(instructions="Edited while the answer was running.")
    second = service.explain(paper, question, agent, identity=identity)
    assert (await anext(second))["joined"] is True
    await first.aclose()
    await second.aclose()
    await asyncio.gather(*service.tasks)
    assert counter["calls"] == 1 and service.identities == {}


@needs_pdftotext
async def test_disconnecting_during_the_request_still_saves_the_thread(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, delay=0.1))
    slug, paper = paper_with(app_cfg, client)
    body = json.dumps({"text": "residues", "where": {"kind": "paper"},
                       "conversation": target()}).encode()
    disconnected = asyncio.Event()
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            disconnected.set()  # the browser leaves after the first stream event

    await client.app({
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST", "scheme": "http", "path": f"/api/papers/{slug}/explain",
        "query_string": b"", "root_path": "", "http_version": "1.1",
        "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                    (b"x-unpacman-token", client.app.state.control_token.encode())],
    }, receive, send)
    service = client.app.state.deslopify.explainer
    await asyncio.gather(*service.tasks)
    assert counter["calls"] == 1
    assert conversations.Store().public(paper, 1)[0]["status"] == "done"


@needs_pdftotext
def test_restart_marks_incomplete_messages_retryable_without_starting_a_model(client, app_cfg, monkeypatch):
    counter = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, paper = paper_with(app_cfg, client)
    store = client.app.state.deslopify.conversations
    request = {"text": "residues", "context": "", "where": {"kind": "paper"}, "level": "expert",
               "account": "default", "model": "claude-haiku-4-5", "effort": "high", "custom": False}
    store.begin(paper, ("first", "root"), request, {})
    assert notes(client, slug)[0]["status"] == "pending"
    client.app.state.deslopify.conversations = conversations.Store()
    restored = notes(client, slug)[0]
    assert restored["status"] == "error" and "Try again" in restored["error"]
    assert counter == {}
    ask(client, slug, conversation=target())
    assert notes(client, slug)[0]["status"] == "done" and counter["calls"] == 1
