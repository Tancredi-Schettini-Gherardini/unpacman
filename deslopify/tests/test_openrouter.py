"""OpenRouter as an AI of its own, beside Claude, ChatGPT and DeepSeek
(2026-10-15): a key of one's own, saved on Getting ready, and Claude's newest
models first among its suggestions — OpenRouter says Claude Code works for
certain only with those — then the open-weight ones offered since 2026-10-08.

Nothing here reaches OpenRouter: its catalogue and its answers about a key are
stood in for (shapes as its API gave them on 2026-10-15), and readings are
recorded rather than run.
"""

from __future__ import annotations

import io
import json
import shlex
import urllib.error

import pytest

from deslopify.app import agents, openweight, ready
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, upload)

KEY = "sk-or-v1-" + "0123456789abcdef" * 4


def anthropic(ident: str, created: int, *, context: int = 1_000_000, prompt: str = "0.000004",
              completion: str = "0.00002", **pricing) -> dict:
    kind, version = ident.split("claude-")[1].split("-", 1)
    return {"id": ident, "name": f"Anthropic: Claude {kind.title()} {version}",
            "created": created, "hugging_face_id": None, "context_length": context,
            "supported_parameters": ["tools", "reasoning"],
            "pricing": {"prompt": prompt, "completion": completion, **pricing}}


CATALOGUE = [
    anthropic("anthropic/claude-opus-5", 100, prompt="0.000005", completion="0.000025"),
    anthropic("anthropic/claude-opus-5.5", 300, input_cache_read="0.0000002",
              input_cache_write="0.000005"),
    # A batch variant, cheaper and not for a live reading; a dated snapshot.
    anthropic("anthropic/claude-opus-5.5:batch", 300, prompt="0.000002"),
    anthropic("anthropic/claude-opus-5.5-20260921", 300),
    anthropic("anthropic/claude-fable-5.1", 200, prompt="0.00001", completion="0.00005"),
    # Listed again after the newer one: the newest *version* is what counts.
    anthropic("anthropic/claude-sonnet-4.6", 500, context=1_000_000),
    anthropic("anthropic/claude-sonnet-5.5", 250, prompt="0.000002", completion="0.00001"),
    # Five times the price above 100,000 tokens of prompt.
    anthropic("anthropic/claude-haiku-5.5", 400, prompt="0.0000001", completion="0.0000005",
              input_cache_read="0.00000001",
              overrides=[{"min_prompt_tokens": 100000, "prompt": "0.0000005",
                          "completion": "0.0000025", "input_cache_read": "0.00000005"}]),
    {"id": "anthropic/claude-haiku-4.5", "context_length": 200_000, "created": 10,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
    # An open-weight model, as before; and a safety classifier, which cannot read.
    {"id": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek: DeepSeek V4.1 Flash", "created": 90,
     "hugging_face_id": "deepseek-ai/v4.1", "context_length": 1_048_576,
     "supported_parameters": ["tools"],
     "pricing": {"prompt": "0.00000002", "completion": "0.00000072"}},
    {"id": "openai/gpt-oss-safeguard-20b", "name": "OpenAI: gpt-oss-safeguard-20b", "created": 95,
     "hugging_face_id": "openai/gpt-oss-safeguard-20b", "context_length": 131_072,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.000000075", "completion": "0.0000003"}},
    {"id": "openai/gpt-oss-120b", "name": "OpenAI: gpt-oss-120b", "created": 80,
     "hugging_face_id": "openai/gpt-oss-120b", "context_length": 131_072,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
]


def keyed(client, monkeypatch, about=None):
    """A key saved through the page, OpenRouter's answer stood in for, and its
    catalogue as above."""
    monkeypatch.setattr(openweight.Discovery, "catalogue", lambda self, fetch=True: CATALOGUE)
    monkeypatch.setattr(openweight, "check_key", lambda key: about or {
        "label": "mine", "usage": 1.5, "limit": 10.0, "remaining": 8.5, "free_tier": False})
    r = client.put("/api/open/key", json={"host": "openrouter", "key": KEY})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------
# what it offers
# --------------------------------------------------------------------------


def test_claudes_newest_of_each_kind_come_first():
    found = openweight.suggested(CATALOGUE)
    assert [m.id for m in found] == [
        "anthropic/claude-opus-5.5[1m]", "anthropic/claude-fable-5.1[1m]",
        "anthropic/claude-sonnet-5.5[1m]", "anthropic/claude-haiku-5.5[1m]",
        "deepseek/deepseek-v4.1-flash[1m]", "openai/gpt-oss-120b"]
    opus, fable, sonnet, haiku = found[:4]
    assert [m.name for m in found[:4]] == ["Claude Opus 5.5", "Claude Fable 5.1",
                                           "Claude Sonnet 5.5", "Claude Haiku 5.5"]
    assert haiku.small and not opus.small
    # Each at its price, from OpenRouter's catalogue: Opus 5.5 reads its cache
    # at a twentieth of its input price.
    assert (opus.price.input, opus.price.output) == (pytest.approx(4.0), pytest.approx(20.0))
    assert opus.price.cache_read_mult == pytest.approx(0.05)
    assert opus.price.cache_write_mult == pytest.approx(1.25)
    # A price that rises with a long prompt is the price a money limit binds at.
    assert (haiku.price.input, haiku.price.output) == (pytest.approx(0.5), pytest.approx(2.5))
    assert haiku.price.cache_read_mult == pytest.approx(0.1)


def test_a_model_that_cannot_read_is_not_offered():
    ids = [m.id for m in openweight.suggested(CATALOGUE)]
    assert not any("safeguard" in i or ":batch" in i or "20260921" in i for i in ids)
    # A kind with nothing that can read is left out, not filled with an older one.
    no_fable = [e for e in CATALOGUE if "fable" not in e["id"]]
    assert [m.id for m in openweight.claude_suggested(no_fable)] == [
        "anthropic/claude-opus-5.5[1m]", "anthropic/claude-sonnet-5.5[1m]",
        "anthropic/claude-haiku-5.5[1m]"]
    assert openweight.suggested([{"nonsense": True}, "not an entry"]) == []


def test_its_models_are_offered_beside_the_others_and_held_to_money(client, monkeypatch):
    keyed(client, monkeypatch)
    menu = client.get("/api/agents?level=expert").json()
    [group] = [g for g in menu["groups"] if g["account"] == "openrouter"]
    assert group["label"] == "OpenRouter" and group["provider"] == "open"
    # Named with where they run: a closed menu shows the model, not its group.
    assert [m["name"] for m in group["models"]][:2] == ["Claude Opus 5.5 (OpenRouter)",
                                                         "Claude Fable 5.1 (OpenRouter)"]
    assert all(m["priced"] for m in group["models"])            # a money limit binds
    assert group["models"][0]["cap"]["usd"] is not None
    # Not known to search the web: not offered for a literature search.
    assert group["web"] is False and group["signed_in"] is True
    search = client.get("/api/agents?level=search").json()
    assert all(g["account"] != "openrouter" for g in search["groups"])


@needs_pdftotext
def test_a_reading_through_openrouter_is_claude_code_pointed_at_it(client, launched, monkeypatch):
    keyed(client, monkeypatch)
    client.get("/api/agents")                     # the menu is drawn first, as on the page
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "openrouter", "model": "anthropic/claude-opus-5.5[1m]",
        "effort": "high", "max_usd": 2})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert (plan.spec.provider, plan.spec.account) == ("open", "openrouter")
    assert plan.budget_usd == 2                               # priced: the money limit binds
    env = plan.passes[0].env
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    # The key is fetched by Claude Code itself from the file it is kept in, and
    # is in no program's environment (the other reviewer's finding 1, 2026-11-02).
    assert "ANTHROPIC_AUTH_TOKEN" not in env and env["ANTHROPIC_API_KEY"] == ""
    assert KEY not in json.dumps(env) and KEY not in json.dumps(plan.passes[0].argv)
    settings = json.loads((plan.passes[0].dir / "settings.json").read_text())
    key_file = openweight.key_path(client.app.state.deslopify.cfg)
    assert settings["apiKeyHelper"] == f"/bin/cat {shlex.quote(str(key_file))}"
    argv = plan.passes[0].argv
    assert argv[argv.index("--model") + 1] == "anthropic/claude-opus-5.5[1m]"
    # Priced as Opus 5.5 at OpenRouter's rates, the "[1m]" notwithstanding.
    price = client.app.state.deslopify.cfg.prices.lookup("anthropic/claude-opus-5.5[1m]")
    assert price is not None and price.input == pytest.approx(4.0)


# --------------------------------------------------------------------------
# the key
# --------------------------------------------------------------------------


def test_openrouter_says_what_a_key_may_do(monkeypatch):
    answers = []

    def urlopen(request, timeout):
        answers.append((request.full_url, request.headers.get("Authorization")))
        if "bad" in request.headers.get("Authorization", ""):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        return io.BytesIO(json.dumps({"data": {
            "label": "sk-or-v1-0a1…", "usage": 1.2345678, "limit": 10, "limit_remaining": 8.7654322,
            "is_free_tier": False, "rate_limit": {"requests": -1}}}).encode())
    monkeypatch.setattr(openweight.urllib.request, "urlopen", urlopen)
    said = openweight.check_key(KEY)
    assert said == {"label": "sk-or-v1-0a1…", "usage": 1.2346, "limit": 10.0,
                    "remaining": 8.7654, "free_tier": False}
    assert answers[0] == ("https://openrouter.ai/api/v1/key", f"Bearer {KEY}")
    with pytest.raises(openweight.OpenError, match="does not recognise"):
        openweight.check_key("sk-or-v1-bad")


def test_the_saved_key_is_shown_by_its_end_with_what_openrouter_says(client, monkeypatch):
    assert client.get("/api/open/openrouter").json() == {"key": False}
    out = keyed(client, monkeypatch)
    assert out["hint"] == f"…{KEY[-4:]}" and out["remaining"] == 8.5
    seen = client.get("/api/open/openrouter").json()
    assert seen["key"] is True and seen["hint"] == f"…{KEY[-4:]}" and KEY not in json.dumps(seen)
    assert (seen["remaining"], seen["free_tier"]) == (8.5, False)
    assert seen["models"][0] == {"id": "anthropic/claude-opus-5.5[1m]",
                                 "name": "Claude Opus 5.5", "small": False}
    # OpenRouter cannot be reached: the key is still shown, with why.
    def down(key):
        raise openweight.OpenError("OpenRouter could not be reached to check the key.")
    monkeypatch.setattr(openweight, "check_key", down)
    seen = client.get("/api/open/openrouter").json()
    assert seen["key"] is True and "could not be reached" in seen["error"]
    assert client.delete("/api/open/key?host=openrouter").json() == {"ok": True}
    assert client.get("/api/open/openrouter").json() == {"key": False}


def test_getting_ready_names_openrouter_and_says_what_it_lacks(app_cfg):
    cfg = openweight.with_accounts(app_cfg)
    account = openweight.accounts(cfg)["openrouter"]
    nokey = ready.open_status(account, "/usr/local/bin/claude")
    assert nokey.name == "OpenRouter" and not nokey.signed_in
    assert nokey.note == "OpenRouter has no key yet. Add one under Getting ready."
    openweight.save_key(cfg, KEY)
    assert ready.open_status(account, "/usr/local/bin/claude").usable
    bare = ready.open_status(account, None)
    assert not bare.signed_in and bare.note.startswith("OpenRouter reads through Claude Code")
    # Ollama is still what it was: open-weight, unnamed.
    assert ready.open_status(openweight.accounts(cfg)["ollama"], None).name == "Open-weight"


def test_no_credit_is_said_plainly_for_either_service():
    for said in ('API Error: 402 {"error":{"message":"Insufficient credits. Add more using '
                 'https://openrouter.ai/settings/credits"}}',
                 'API Error: 402 {"error":{"message":"Insufficient Balance"}}'):
        plain = ready.plain_failure(said)
        assert "no credit left" in plain and "openrouter.ai/settings/credits" in plain
        assert "platform.deepseek.com" in plain


# --------------------------------------------------------------------------
# credit
# --------------------------------------------------------------------------


def test_a_companion_through_openrouter_says_so():
    agents.remember_open({"openrouter": openweight.suggested(CATALOGUE)})
    described = agents.describe("open", "openrouter", "anthropic/claude-opus-5.5[1m]", "high")
    assert described["name"] == "Claude Opus 5.5 through OpenRouter"
    assert described["web"] is False and described["small"] is False
    assert agents.describe("open", "openrouter", "anthropic/claude-haiku-5.5[1m]", "low")["small"]
    # A model no longer suggested still reads as a name, not as an id.
    assert agents.full_name("open", "anthropic/claude-sonnet-9.1[1m]") == "Claude Sonnet 9.1"
    assert agents.full_name("open", "acme/great-model[1m]") == "great-model"
    # DeepSeek's own service is not OpenRouter, and Claude's own plan neither.
    assert "OpenRouter" not in agents.describe("open", "deepseek", "deepseek-v4-pro[1m]", "high")["name"]
    assert agents.describe("claude", "default", "claude-opus-5-5", "high")["name"] == "Claude Opus 5.5"
