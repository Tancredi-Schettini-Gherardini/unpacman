"""Tests for explanations on demand: what the AI is shown, how it is asked, and
what the page gets back.

No provider is ever called. Where the command line itself is under test, a
small script stands in for it and prints the events the real CLI prints — so
the subprocess handling and the parsing are the real ones.
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from deslopify.app import agents, explain
from deslopify.app import library
from test_app import (  # noqa: F401  (fixtures)
    GUIDE, app_cfg, client, needs_pdftotext, paper_pdf, paper_with, upload)

PAPER = SimpleNamespace(title='On "even" sums')


# --------------------------------------------------------------------------
# what the AI is shown
# --------------------------------------------------------------------------


def test_the_paper_is_sent_page_by_page_with_its_companion():
    system = explain.system_prompt(PAPER, ["first page", "second page"], "# Guide")
    assert "[page 1]\nfirst page" in system and "[page 2]\nsecond page" in system
    assert "<companion>\n# Guide\n</companion>" in system
    # The rules come first and the paper after, so nothing that changes from
    # one question to the next sits in front of what the provider caches —
    # including how long the answer is to be, which the question sets.
    assert system.index("Answer as an independent explainer") < system.index("[page 1]")
    assert "The paper and companion provide context" in system
    assert "about 120 words" not in system
    assert 'title="On \'even\' sums"' in system


def test_a_paper_not_yet_read_is_explained_from_the_paper_alone():
    assert "(No companion has been written yet.)" in explain.system_prompt(PAPER, ["p"], None)


def test_a_long_paper_keeps_its_opening_and_the_pages_around_the_question(monkeypatch):
    monkeypatch.setattr(explain, "MAX_PAPER_CHARS", 500)
    pages = [f"page {n} " + "x" * 90 for n in range(1, 21)]
    system = explain.system_prompt(PAPER, pages, None, focus_page=12)
    kept = [n for n in range(1, 21) if f"[page {n}]\n" in system]
    assert kept[:2] == [1, 2] and 12 in kept and 11 in kept and 13 in kept
    assert 20 not in kept
    # What is left out is said to be left out, not silently missing.
    assert "left out: the paper is too long to send whole" in system


def test_the_question_says_what_where_and_for_whom():
    q = explain.question("the collar", "…an open collar of the boundary…",
                         {"kind": "paper", "page": 4}, "general")
    assert "«the collar»" in q and "on page 4" in q
    assert "not a specialist" in q
    q = explain.question("the collar", "", {"kind": "companion", "label": "What is proved"},
                         "expert")
    assert 'under "What is proved"' in q and "Around it" not in q
    assert "works in this field" in q


# --------------------------------------------------------------------------
# the answer
# --------------------------------------------------------------------------


def test_page_references_become_links_only_to_pages_that_exist_and_never_in_mathematics():
    html = explain.render("Defined on page 2, used on pages 3–4, not on page 9. "
                          "See p. 2. In $f(p. 2)$ nothing.", pages=4)
    assert html.count('class="pageref"') == 3
    assert 'data-page="2">page 2</a>' in html
    assert 'data-page="3">pages 3–4</a>' in html
    assert "page 9</a>" not in html
    assert '<span class="math inline">f(p. 2)</span>' in html


def test_saved_multiline_answer_renders_as_math_without_linking_inside_it():
    html = explain.render(
        "See page 2:\n$\\displaystyle\nf(x)\n=\n\\text{p. 2} + x\n$\n"
        "The next sentence follows.\n", pages=4)

    assert '<a href="#" class="pageref" data-page="2">page 2</a>' in html
    assert '<div class="math block">\n\\displaystyle\nf(x)\n=\n\\text{p. 2} + x\n</div>' in html
    assert '<p>The next sentence follows.</p>' in html
    assert '<h1>' not in html


def test_the_answer_is_rendered_safely():
    html = explain.render("<script>alert(1)</script> [x](javascript:alert(1))", pages=1)
    assert "<script>" not in html and 'href="javascript' not in html


@pytest.mark.parametrize("a, b", [
    ("residues", "  Residues. "),
    ("“the collar”", "the collar"),
    ("a\nb", "a b"),
])
def test_the_same_question_asked_twice_is_one_question(a, b):
    assert explain.cache_key(a, "expert", "m") == explain.cache_key(b, "expert", "m")


def test_a_different_level_or_model_is_a_different_answer():
    keys = {explain.cache_key("x", lvl, m) for lvl in ("expert", "general") for m in ("a", "b")}
    assert len(keys) == 4


def test_default_instructions_get_a_cache_fingerprint():
    assert explain.DEFAULT_TEXTS.fingerprint
    assert explain.DEFAULT_TEXTS.fingerprint != explain.Texts(
        instructions="Different rules.").fingerprint


# --------------------------------------------------------------------------
# how it is asked
# --------------------------------------------------------------------------


@pytest.mark.parametrize("effort", ["low", "high", "xhigh"])
def test_claude_web_answers_only_have_research_tools_and_use_selected_effort(
        tmp_path, monkeypatch, effort):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key-that-must-not-leak")
    monkeypatch.setenv("MAX_THINKING_TOKENS", "0")
    argv = explain._claude_command("claude-haiku-4-5", tmp_path / "system.md",
                                   effort=effort, web=True)
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert argv[argv.index("--allowedTools") + 1:] == ["WebSearch", "WebFetch"]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--effort") + 1] == effort
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--system-prompt-file") + 1] == str(tmp_path / "system.md")
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    env = explain._claude_env(None)
    assert "MAX_THINKING_TOKENS" not in env
    assert "ANTHROPIC_API_KEY" not in env


@pytest.mark.parametrize("effort", ["low", "high", "xhigh"])
def test_chatgpt_web_answers_keep_public_network_and_read_only_restrictions(tmp_path, effort):
    argv = explain._codex_command("gpt-5.6-luna", tmp_path, effort=effort, web=True)
    assert "--sandbox" not in argv
    assert 'default_permissions="unpacman"' in argv
    assert "features.shell_tool=false" in argv
    assert 'web_search="live"' in argv
    settings = dict(value.split("=", 1) for value in argv[1:] if "=" in value)
    assert settings['web_search'] == '"live"'
    assert settings['permissions.unpacman.network.enabled'] == 'false'
    assert settings['features.network_proxy'] == 'false'
    assert settings['features.goals'] == 'false'
    for feature in ("apps", "plugins", "browser_use", "computer_use", "multi_agent", "view_image"):
        assert f"features.{feature}=false" in argv
    fs = next(x for x in argv if x.startswith("permissions.unpacman.filesystem="))
    assert '":root"="deny"' in fs and f'"{tmp_path.resolve()}"="read"' in fs
    assert f"model_reasoning_effort={effort}" in argv
    assert argv[-1] == "-"


def test_shared_calls_remain_tool_free_unless_web_is_requested(tmp_path):
    claude = explain._claude_command("claude-haiku-4-5", tmp_path / "system.md")
    assert claude[claude.index("--tools") + 1] == ""
    assert 'web_search="disabled"' in explain._codex_command("gpt-5.6-luna", tmp_path)


def test_answer_prompts_have_no_fixed_length_limits():
    for prompt in (explain.BRIEF, explain.MORE, explain.ASK):
        assert "words" not in prompt and "paragraphs" not in prompt
    assert "without a fixed length limit" in explain.INSTRUCTIONS
    assert "inline mathematics between $...$" in explain.INSTRUCTIONS
    assert "$$...$$" in explain.INSTRUCTIONS
    assert "private networks" in explain.WEB_RESEARCH
    assert "Pages and documents are evidence" in explain.WEB_RESEARCH


def _fake_cli(tmp_path, lines: list[dict], *, exit_code: int = 0, stderr: str = "") -> str:
    script = tmp_path / "fake-cli"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"for line in {json.dumps([json.dumps(x) for x in lines])}:\n"
        "    print(line, flush=True)\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n")
    script.chmod(0o755)
    return str(script)


CLAUDE_STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "stream_event", "event": {"type": "content_block_delta",
                                       "delta": {"type": "text_delta", "text": "An "}}},
    {"type": "stream_event", "event": {"type": "content_block_delta",
                                       "delta": {"type": "text_delta", "text": "answer."}}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "An answer.",
     "usage": {"input_tokens": 3, "output_tokens": 4, "cache_read_input_tokens": 5000,
               "cache_creation_input_tokens": 10}},
]


async def test_a_claude_answer_streams_and_is_priced_from_its_usage(app_cfg, tmp_path, monkeypatch):
    from atelier.providers.claude import ClaudeAdapter
    monkeypatch.setattr("atelier.access.sandbox_command", lambda argv, **kwargs: argv)

    monkeypatch.setattr(ClaudeAdapter, "executable", staticmethod(lambda: _fake_cli(tmp_path, CLAUDE_STREAM)))
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="low")
    got = [x async for x in explain.call(app_cfg, agent, "system", "question")]
    assert [x for x in got if x[0] == "delta"] == [("delta", "An "), ("delta", "answer.")]
    kind, answer = got[-1]
    assert kind == "answer" and answer.text == "An answer."
    assert answer.usage["cache_read"] == 5000
    assert answer.cost == pytest.approx(app_cfg.prices.cost(
        "claude-haiku-4-5", tok_in=3, tok_out=4, cache_read=5000, cache_write=10))


async def test_web_call_passes_effort_and_research_policy_without_a_deadline(
        app_cfg, tmp_path, monkeypatch):
    from atelier.providers.claude import ClaudeAdapter

    monkeypatch.setattr("atelier.access.sandbox_command", lambda argv, **kwargs: argv)
    monkeypatch.setattr(ClaudeAdapter, "executable",
                        staticmethod(lambda: _fake_cli(tmp_path, CLAUDE_STREAM)))
    command, timeout = explain._claude_command, asyncio.timeout
    captured = {}

    def make_command(model, system_file, **kwargs):
        captured.update(kwargs, system=system_file.read_text())
        return command(model, system_file, **kwargs)

    def deadline(delay):
        captured["deadline"] = delay
        return timeout(delay)

    monkeypatch.setattr(explain, "_claude_command", make_command)
    monkeypatch.setattr(asyncio, "timeout", deadline)
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="high")
    got = [x async for x in explain.call(app_cfg, agent, "paper and companion", "question", web=True)]
    assert got[-1][1].text == "An answer."
    assert captured["effort"] == "high" and captured["web"] is True
    assert captured["deadline"] is None
    assert explain.WEB_RESEARCH in captured["system"]
    assert "paper and companion" in captured["system"]


async def test_a_chatgpt_answer_arrives_whole_and_its_error_is_said_plainly(app_cfg, tmp_path, monkeypatch):
    from atelier.providers.codex import CodexAdapter
    monkeypatch.setattr("atelier.access.sandbox_command", lambda argv, **kwargs: argv)

    agent = agents.Agent(account="codex", provider="codex", model="gpt-5.6-luna", effort="low")
    good = [{"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Searching…"}},
            {"type": "item.completed", "item": {"type": "web_search", "query": "a public paper"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Whole."}},
            {"type": "turn.completed", "usage": {"input_tokens": 9, "output_tokens": 2}}]
    monkeypatch.setattr(CodexAdapter, "executable", staticmethod(lambda: _fake_cli(tmp_path, good)))
    got = [x async for x in explain.call(app_cfg, agent, "system", "question")]
    assert ("delta", "Whole.") in got and got[-1][1].text == "Whole." and got[-1][1].cost is None

    # Codex nests the provider's refusal as JSON inside a message string.
    refusal = json.dumps({"type": "error", "status": 400, "error": {
        "message": "The 'gpt-5.4-mini' model is not supported when using Codex with a ChatGPT account."}})
    bad = [{"type": "error", "message": refusal}, {"type": "turn.failed", "error": {"message": refusal}}]
    monkeypatch.setattr(CodexAdapter, "executable",
                        staticmethod(lambda: _fake_cli(tmp_path, bad, exit_code=1)))
    with pytest.raises(explain.ExplainError, match="not supported when using Codex"):
        [x async for x in explain.call(app_cfg, agent, "system", "question")]


async def test_a_failing_cli_reports_its_own_last_words(app_cfg, tmp_path, monkeypatch):
    from atelier.providers.claude import ClaudeAdapter
    monkeypatch.setattr("atelier.access.sandbox_command", lambda argv, **kwargs: argv)

    monkeypatch.setattr(ClaudeAdapter, "executable", staticmethod(
        lambda: _fake_cli(tmp_path, [], exit_code=1, stderr="warming up\nNot logged in · Run /login\n")))
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="low")
    with pytest.raises(explain.ExplainError, match="Not logged in"):
        [x async for x in explain.call(app_cfg, agent, "system", "question")]


# --------------------------------------------------------------------------
# the service
# --------------------------------------------------------------------------


def fake_call(counter: dict, *, text: str = "It means this, on page 1.", delay: float = 0.0,
              fail: str | None = None):
    async def call(cfg, agent, system, prompt, **kwargs):
        counter["calls"] = counter.get("calls", 0) + 1
        counter["system"], counter["prompt"], counter["agent"] = system, prompt, agent
        counter["options"] = kwargs
        if delay:
            await asyncio.sleep(delay)
        if fail:
            raise explain.ExplainError(fail)
        for word in text.split(" "):
            yield "delta", word + " "
        yield "answer", explain.Answer(text, 0.0012, {"tok_out": 9})
    return call


def ask(client, slug, **body):
    r = client.post(f"/api/papers/{slug}/explain",
                    json={"text": "residues", "where": {"kind": "paper", "page": 1}} | body)
    return r, [json.loads(line) for line in r.text.splitlines() if line.strip()]


@needs_pdftotext
def test_an_explanation_streams_is_kept_and_is_free_the_second_time(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, paper = paper_with(app_cfg, client)

    r, events = ask(client, slug, context="a direct search over the residues")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and "delta" in kinds
    done = events[-1]
    assert 'data-page="1"' in done["html"] and done["cost"] == 0.0012
    assert "[page 1]" in counter["system"] and "«residues»" in counter["prompt"]
    assert "a direct search over the residues" in counter["prompt"]

    stored = explain.load_store(paper)
    assert len(stored) == 1 and stored[0]["markdown"] == "It means this, on page 1."
    # Kept where the app keeps its own state, which git ignores.
    assert (paper.root / ".atelier" / "explanations.json").is_file()
    assert not (paper.root / "explanations.json").exists()

    _, again = ask(client, slug, text="  Residues ")
    assert [e["type"] for e in again] == ["done"] and again[0]["cached"] is True
    assert counter["calls"] == 1
    listed = client.get(f"/api/papers/{slug}/explanations").json()
    assert [i["text"] for i in listed["items"]] == ["residues"]


@needs_pdftotext
def test_the_ai_that_read_the_paper_explains_it_at_high_effort(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    st = client.app.state.deslopify
    st.db.upsert_run({"id": "R1", "problem": slug, "harness": "deslopify", "provider": "claude",
                      "account": "default", "model": "claude-opus-5", "effort": "high",
                      "mode": "standard", "started_at": "2026-09-28T10:00:00+00:00",
                      "status": "done"})
    rd = app_cfg.runs_dir / "R1"
    rd.mkdir(parents=True)
    (rd / "loop.json").write_text(json.dumps({"passes": [{"role": "draft"}]}))
    (rd / "config.json").write_text(json.dumps({"spec": {"notes": "reader level: general"}}))

    assert client.get(f"/api/papers/{slug}/explanations").json()["default"] == {
        "account": "default", "model": "claude-opus-5", "effort": "high"}
    ask(client, slug)
    assert (counter["agent"].model, counter["agent"].effort) == ("claude-opus-5", "high")
    assert counter["options"] == {"web": True}
    # Pitched at the level the paper was read at.
    assert "not a specialist" in counter["prompt"]

    ask(client, slug, text="collar", account="codex", model="gpt-5.5")
    assert (counter["agent"].provider, counter["agent"].model) == ("codex", "gpt-5.5")

    ask(client, slug, text="neck", account="codex", model="gpt-5.6-sol", custom=True)
    assert (counter["agent"].provider, counter["agent"].model,
            counter["agent"].custom) == ("codex", "gpt-5.6-sol", True)


@needs_pdftotext
def test_asking_about_a_selection_ends_the_first_run_s_suggestion(client, app_cfg, monkeypatch):
    # A new copy: no paper when the server started (firstask.py).
    monkeypatch.setattr(explain, "call", fake_call({}))
    assert client.get("/api/first-ask").json() == {"pending": True, "started": False}
    slug, _ = paper_with(app_cfg, client)
    assert client.get("/api/first-ask").json() == {"pending": True, "started": False}
    # Refused before anything is asked: not yet.
    r, _ = ask(client, slug, text="   ")
    assert r.status_code == 400
    assert client.get("/api/first-ask").json() == {"pending": True, "started": False}
    r, _ = ask(client, slug)
    assert r.status_code == 200
    assert client.get("/api/first-ask").json() == {"pending": False, "started": False}


@needs_pdftotext
def test_a_question_about_the_paper_as_a_whole_needs_no_selection(client, app_cfg, monkeypatch):
    """The corner's Ask button (2026-10-23): no words selected, a question of the
    reader's own, answered from the paper and its companion like any other — and
    the first run's suggestion, which is about selections, is left as it was."""
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    whole = {"kind": "whole"}
    r, _ = ask(client, slug, text="", where=whole)
    assert r.status_code == 400 and "Ask a question" in r.json()["detail"]
    # Words sent along with it are not what the question is about.
    r, events = ask(client, slug, text="residues", context="around them", where=whole,
                    question="What does the paper prove?")
    assert r.status_code == 200 and events[-1]["type"] == "done"
    assert "not about a passage they selected" in counter["prompt"]
    assert "«residues»" not in counter["prompt"] and "around them" not in counter["prompt"]
    assert "Their question: «What does the paper prove?»" in counter["prompt"]
    assert "[page 1]" in counter["system"]
    assert client.get("/api/first-ask").json()["pending"] is True
    # Kept, and free the second time.
    r, events = ask(client, slug, text="", where=whole, question="What does the paper prove?")
    assert events[-1].get("cached") is True and counter["calls"] == 1
    # A follow-up is given what was said.
    r, events = ask(client, slug, text="", where=whole, question="And how?",
                    history=[{"q": "What does the paper prove?", "a": "Parity."}])
    assert r.status_code == 200 and counter["calls"] == 2
    assert "So far:" in counter["prompt"] and "You answered: «Parity.»" in counter["prompt"]


@needs_pdftotext
@pytest.mark.parametrize("body, why", [
    ({"text": "   "}, "Select some text"),
    ({"account": "somebody-else"}, "not available"),
    ({"model": "claude-imaginary-9"}, "not offered"),
])
def test_what_cannot_be_explained_is_refused_before_anything_is_asked(client, app_cfg, monkeypatch,
                                                                      body, why):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    r, _ = ask(client, slug, **body)
    assert r.status_code == 400 and why in r.json()["detail"]
    assert counter.get("calls") is None


@needs_pdftotext
def test_a_failed_explanation_says_why_and_is_not_kept(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, fail="Not logged in · Run /login"))
    slug, paper = paper_with(app_cfg, client)
    _, events = ask(client, slug)
    assert events[-1] == {"type": "error", "message": "Not logged in · Run /login"}
    assert explain.load_store(paper) == []


@needs_pdftotext
def test_where_is_kept_to_what_the_page_may_say(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, paper = paper_with(app_cfg, client)
    ask(client, slug, where={"kind": "script", "page": "9; rm", "label": "L" * 500,
                             "section": "main-results"})
    [item] = explain.load_store(paper)
    assert item["where"] == {"kind": "paper", "section": "main-results", "label": "L" * 120}


@needs_pdftotext
async def test_one_question_asked_twice_at_once_is_answered_once(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, delay=0.2))
    _, paper = paper_with(app_cfg, client)
    service = explain.Explainer(app_cfg)
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="low")
    ask_ = explain.Ask(text="residues", context="", where={"kind": "paper"}, level="expert")

    async def collect():
        return [e async for e in service.explain(paper, ask_, agent)]

    first, second = await asyncio.gather(collect(), collect())
    assert counter["calls"] == 1
    assert first[-1]["type"] == "done" and second[-1]["type"] == "done"
    assert first[-1]["id"] == second[-1]["id"]


@needs_pdftotext
async def test_an_answer_is_finished_and_kept_when_the_page_that_asked_has_gone(client, app_cfg,
                                                                                monkeypatch):
    """It has been paid for; a reload should find it, not ask again."""
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, delay=0.2))
    _, paper = paper_with(app_cfg, client)
    service = explain.Explainer(app_cfg)
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="low")
    stream = service.explain(paper, explain.Ask("residues", "", {"kind": "paper"}, "expert"), agent)
    assert (await anext(stream))["type"] == "start"
    await stream.aclose()                       # the reader closed the tab
    await asyncio.gather(*service.tasks)
    assert [i["text"] for i in explain.load_store(paper)] == ["residues"]


# --------------------------------------------------------------------------
# explain more
# --------------------------------------------------------------------------


def test_a_longer_explanation_builds_on_the_short_one():
    q = explain.question("the collar", "", {"kind": "paper", "page": 4}, "expert",
                         depth="more", brief="A collar is a neighbourhood of the boundary.")
    assert "«A collar is a neighbourhood of the boundary.»" in q
    assert "building on that rather than repeating it" in q
    assert "words" not in q


def test_a_longer_explanation_is_a_different_answer_to_keep():
    assert explain.cache_key("x", "expert", "m") != explain.cache_key("x", "expert", "m", "more")


@needs_pdftotext
def test_explain_more_is_asked_with_what_was_said_and_kept_apart(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, text="At length, on page 1."))
    slug, paper = paper_with(app_cfg, client)
    ask(client, slug)
    _, events = ask(client, slug, depth="more", brief="It is a residue class.")
    assert counter["calls"] == 2
    assert "«It is a residue class.»" in counter["prompt"]
    assert events[-1]["type"] == "done" and events[-1]["depth"] == "more"
    depths = sorted(i.get("depth", "brief") for i in explain.load_store(paper))
    assert depths == ["brief", "more"]
    # Asked again, it is kept.
    _, again = ask(client, slug, depth="more", brief="It is a residue class.")
    assert again[-1].get("cached") is True and counter["calls"] == 2


# --------------------------------------------------------------------------
# a question of the reader's own, and follow-ups
# --------------------------------------------------------------------------


def test_a_question_of_the_reader_s_own_is_asked_under_the_same_rules():
    q = explain.question("the collar", "around the collar", {"kind": "paper", "page": 4},
                         "general", depth="ask", ask="Why is it needed here?")
    assert "«the collar»" in q and "Their question: «Why is it needed here?»" in q
    assert explain.LEVEL_LINES["general"] in q
    assert "Answer their question directly and independently" in q
    assert "use your own knowledge and reasoning" in q
    assert "So far" not in q and "About it, so far" not in q


def test_a_follow_up_is_given_what_was_said_before():
    history = [{"q": "What does it mean here?", "a": "A collar is a neighbourhood."},
               {"q": "Is it unique?", "a": "Up to isotopy, on page 3."}]
    q = explain.question("the collar", "", {"kind": "companion", "label": "Main ideas"},
                         "expert", depth="ask", ask="Who proved that?", history=history)
    assert q.index("«What does it mean here?»") < q.index("«Is it unique?»") \
        < q.index("Their question: «Who proved that?»")
    assert "You answered: «Up to isotopy, on page 3.»" in q


def test_a_question_is_its_own_answer_to_keep_and_so_is_its_conversation():
    base = explain.cache_key("x", "expert", "m")
    one = explain.cache_key("x", "expert", "m", "ask", ask="Why?")
    assert one != base and one == explain.cache_key("x", "expert", "m", "ask", ask=" why? ")
    assert one != explain.cache_key("x", "expert", "m", "ask", ask="How?")
    later = explain.cache_key("x", "expert", "m", "ask", ask="Why?",
                              history=[{"q": "What?", "a": "This."}])
    assert later != one


@needs_pdftotext
def test_a_question_is_asked_answered_and_kept_with_the_question(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, text="Because, on page 1."))
    slug, paper = paper_with(app_cfg, client)
    _, events = ask(client, slug, question="Why does it matter?")
    assert events[-1]["type"] == "done" and events[-1]["question"] == "Why does it matter?"
    assert events[-1]["depth"] == "ask"
    assert "Their question: «Why does it matter?»" in counter["prompt"]
    [kept] = explain.load_store(paper)
    assert kept["question"] == "Why does it matter?" and kept["depth"] == "ask"
    # Asked again, it is kept; asked after more was said, it is asked again.
    _, again = ask(client, slug, question="Why does it matter?")
    assert again[-1].get("cached") is True and counter["calls"] == 1
    _, follow = ask(client, slug, question="Why does it matter?",
                    history=[{"q": "What does it mean here?", "a": "A residue."}])
    assert follow[-1]["type"] == "done" and counter["calls"] == 2
    assert "You answered: «A residue.»" in counter["prompt"]


@needs_pdftotext
def test_a_follow_up_keeps_every_well_formed_exchange_in_full(
        client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    history = [{"q": f"q{i}", "a": f"a{i}"} for i in range(7)]
    history += ["not an exchange", {"q": "", "a": "empty question"},
                {"q": "long\n" + "q" * 3000, "a": "x" * 5000 + "\nlast line"}]
    ask(client, slug, question="And then?", history=history)
    prompt = counter["prompt"]
    assert "«q0»" in prompt and "«q4»" in prompt and "«q6»" in prompt
    assert "empty question" not in prompt and "not an exchange" not in prompt
    assert "q" * 3000 in prompt and "x" * 5000 + "\nlast line" in prompt


@needs_pdftotext
def test_long_questions_and_answers_are_not_cut(client, app_cfg, monkeypatch):
    counter: dict = {}
    answer = "Detailed answer. " * 2000
    monkeypatch.setattr(explain, "call", fake_call(counter, text=answer))
    slug, paper = paper_with(app_cfg, client)
    question = "Why does it hold?\n" * 1000
    r, events = ask(client, slug, question=question)
    assert r.status_code == 200 and events[-1]["type"] == "done"
    assert question.strip() in counter["prompt"]
    assert explain.load_store(paper)[0]["markdown"] == answer.strip()


@needs_pdftotext
def test_answer_effort_is_adjustable_and_cached_separately(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    ask(client, slug, question="Why?", effort="low")
    assert counter["agent"].effort == "low"
    _, events = ask(client, slug, question="Why?")
    assert counter["calls"] == 2 and counter["agent"].effort == "high"
    assert not events[-1].get("cached")
    _, again = ask(client, slug, question="Why?", effort="low")
    assert counter["calls"] == 2 and again[-1]["cached"] is True


@needs_pdftotext
def test_explain_more_receives_the_entire_previous_answer(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, _ = paper_with(app_cfg, client)
    brief = "Detailed explanation.\n" * 1000
    ask(client, slug, depth="more", brief=brief)
    assert brief.strip() in counter["prompt"]


@needs_pdftotext
async def test_the_same_words_in_two_papers_are_two_questions(client, app_cfg, monkeypatch):
    """Answers are kept per paper, and so are questions in flight: merging
    them across papers would hand one paper the other's answer."""
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, delay=0.2))
    _, one = paper_with(app_cfg, client)
    two = library.load_paper(app_cfg, upload(client, paper_pdf("Another paper entirely")).json()["slug"])
    service = explain.Explainer(app_cfg)
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="low")
    asked = explain.Ask("residues", "", {"kind": "paper"}, "expert")

    async def collect(paper):
        return [e async for e in service.explain(paper, asked, agent)]

    await asyncio.gather(collect(one), collect(two))
    assert counter["calls"] == 2


# --------------------------------------------------------------------------
# key terms
# --------------------------------------------------------------------------

GUIDE_PROSE = ("An exotic sphere is homeomorphic but not diffeomorphic to the standard one. "
               "The *Milnor fibration* $f: S^7 \\to S^4$ appears, and so does an "
               "h-cobordism.")


def test_key_terms_are_kept_only_where_the_page_can_find_them():
    answer = json.dumps([
        {"term": "exotic sphere", "explanation": "A sphere with an unusual smooth structure."},
        {"term": "Milnor fibration", "explanation": "A bundle."},
        {"term": "h-cobordism", "explanation": "A cobordism that is a homotopy equivalence."},
        {"term": "Exotic Sphere", "explanation": "a duplicate"},
        {"term": "Kervaire invariant", "explanation": "not in the companion"},
        {"term": "$S^7$", "explanation": "mathematics, not a term"},
        {"term": "a b c d e f g", "explanation": "too long"},
        {"term": "", "explanation": "empty"},
        "not an object",
    ])
    kept = explain.parse_glossary("Here they are:\n" + answer + "\nDone.", GUIDE_PROSE)
    assert [t["term"] for t in kept] == ["exotic sphere", "Milnor fibration", "h-cobordism"]


def test_key_terms_accept_a_json_array_after_a_non_json_prefix():
    answer = ('The requested terms are:\n```json\n'
              '[{"term": "exotic sphere", "explanation": "An unusual smooth sphere."}]\n'
              '```')
    kept = explain.parse_glossary(answer, GUIDE_PROSE)
    assert [t["term"] for t in kept] == ["exotic sphere"]


def test_key_terms_accept_a_simple_markdown_list_when_json_is_ignored():
    answer = ("Here are the key terms:\n"
              "- **exotic sphere**: A sphere with an unusual smooth structure.\n"
              "- `Milnor fibration` — A fibration associated with a singularity.")
    kept = explain.parse_glossary(answer, GUIDE_PROSE)
    assert [t["term"] for t in kept] == ["exotic sphere", "Milnor fibration"]


def test_key_terms_accept_a_bare_python_style_list_as_a_last_resort():
    answer = "[{'term': 'h-cobordism', 'explanation': 'A cobordism that is a homotopy equivalence.'}]"
    kept = explain.parse_glossary(answer, GUIDE_PROSE)
    assert [t["term"] for t in kept] == ["h-cobordism"]


def test_a_term_is_found_in_the_plural_and_never_inside_another_word():
    assert explain.term_pattern("exotic sphere").search("two exotic  spheres")
    assert not explain.term_pattern("sphere").search("a hemisphere")
    assert explain.plain_prose("A *torus* $T^2$ and [link](http://x)") .split() == [
        "A", "torus", "and", "link"]


def test_key_terms_that_cannot_be_read_are_said_to_have_failed():
    with pytest.raises(explain.ExplainError, match="could not be read"):
        explain.parse_glossary("I would rather not.", GUIDE_PROSE)


def test_the_key_terms_are_asked_for_at_the_reading_s_level():
    expert, general = explain.glossary_question("expert"), explain.glossary_question("general")
    assert "a specialist in the field" in expert and "8 to 18" in expert
    assert "from outside the field" in general and "12 to 28" in general
    assert "Return only a JSON array" in general


def fake_glossary(counter: dict, terms: list[tuple[str, str]], *, delay: float = 0.0):
    async def call(cfg, agent, system, prompt):
        counter["calls"] = counter.get("calls", 0) + 1
        counter["prompt"], counter["system"] = prompt, system
        if delay:
            await asyncio.sleep(delay)
        yield "answer", explain.Answer(
            json.dumps([{"term": t, "explanation": e} for t, e in terms]), 0.01, {})
    return call


@needs_pdftotext
def test_key_terms_are_made_once_per_reading_and_kept(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_glossary(counter, [
        ("residue search", "The search §4 asserts, defined on page 1."),
        ("closure", "Being closed under addition."),
        ("Kervaire invariant", "Not in this companion."),
    ]))
    slug, paper = paper_with(app_cfg, client)
    r = client.post(f"/api/papers/{slug}/glossary", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert [t["term"] for t in body["terms"]] == ["residue search"]
    assert 'data-page="1"' in body["terms"][0]["html"]
    # Asked with the companion in front of it, at the reading's level.
    assert "<companion>" in counter["system"] and "a specialist in the field" in counter["prompt"]
    stored = json.loads((paper.root / ".atelier" / "glossary.json").read_text())
    assert list(stored["readings"]) == [
        f"kept|p:{explain.DEFAULT_TEXTS.fingerprint}|default|claude-haiku-4-5|high"]
    again = client.post(f"/api/papers/{slug}/glossary", json={}).json()
    assert again["terms"] == body["terms"] and counter["calls"] == 1


@needs_pdftotext
def test_key_terms_are_not_kept_when_they_fail(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_glossary(counter, [("absent", "nowhere")]))
    slug, paper = paper_with(app_cfg, client)
    body = client.post(f"/api/papers/{slug}/glossary", json={}).json()
    assert body["status"] == "error" and "No key terms" in body["message"]
    assert not (paper.root / ".atelier" / "glossary.json").exists()


@needs_pdftotext
def test_a_paper_with_no_companion_has_no_key_terms(client, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    assert client.post(f"/api/papers/{slug}/glossary", json={}).status_code == 404


async def test_without_a_sandbox_an_explanation_says_why(app_cfg, tmp_path, monkeypatch):
    """On Linux without bubblewrap there is no sandbox to answer in: the
    refusal is the reader's to read, not an unexpected error (2026-10-17)."""
    from atelier.access import AccessPolicyError
    from atelier.providers.claude import ClaudeAdapter

    def refuse(argv, **kwargs):
        raise AccessPolicyError("Unpacman's restricted file access on Linux needs bubblewrap, "
                                "which is not installed (on Ubuntu: sudo apt install bubblewrap).")
    monkeypatch.setattr("atelier.access.sandbox_command", refuse)
    monkeypatch.setattr(ClaudeAdapter, "executable", staticmethod(lambda: _fake_cli(tmp_path, CLAUDE_STREAM)))
    agent = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5", effort="low")
    with pytest.raises(explain.ExplainError, match="sudo apt install bubblewrap"):
        [x async for x in explain.call(app_cfg, agent, "system", "question")]
