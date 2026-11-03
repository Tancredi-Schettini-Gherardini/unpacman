"""DeepSeek as an AI of its own, beside Claude and ChatGPT (2026-10-13).

Its own service speaks Anthropic's API, so it reads through Claude Code as the
other open-weight hosts do (openweight.py) — but it runs web search itself,
is paid in advance, and has prices of its own. Nothing here reaches DeepSeek:
its answers are stood in for, and readings are recorded rather than run.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import stat
import urllib.error

import pytest

from atelier.config import resolve_secret
from deslopify.app import agents, explain, openweight, ready
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, upload)

KEY = "sk-" + "0123456789abcdef" * 2
BALANCE = {"is_available": True,
           "balance_infos": [{"currency": "USD", "total_balance": "4.20",
                              "granted_balance": "0.00", "topped_up_balance": "4.20"}]}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # DeepSeek's list of models is never asked for here; and the test's own var/.
    monkeypatch.setattr(openweight, "deepseek_listed", lambda key, timeout=8.0: [])
    monkeypatch.delenv("UNPACMAN_VAR", raising=False)


def keyed(client, monkeypatch, about=None):
    monkeypatch.setattr(openweight, "check_deepseek_key",
                        lambda key: openweight.deepseek_balance(about or BALANCE))
    r = client.put("/api/open/key", json={"host": "deepseek", "key": KEY})
    assert r.status_code == 200, r.text
    return r.json()


def test_deepseek_is_an_account_the_reader_always_has(app_cfg):
    cfg = openweight.with_accounts(app_cfg)
    deepseek = cfg.account("deepseek")
    assert deepseek.provider == "open" and deepseek.label == "DeepSeek"
    assert deepseek.base_url == "https://api.deepseek.com/anthropic"
    assert deepseek.key_ref == f"file:{app_cfg.root / 'var' / 'keys' / 'deepseek'}"
    # Its prices are known before any model is listed: a money limit binds.
    pro = cfg.prices.lookup("deepseek-v4-pro[1m]")
    assert (pro.input, pro.output) == (1.32, 3.96)
    assert pro.cache_read_mult == pytest.approx(0.044 / 1.32) and pro.cache_write_mult == 1.0
    assert cfg.prices.lookup("deepseek-flash[1m]").input == 0.30


def test_its_key_is_checked_for_form_before_it_goes_anywhere(app_cfg):
    assert openweight.looks_like_key(KEY, "deepseek")
    for other in ("sk-or-v1-" + "a" * 40, "sk-ant-api03-" + "b" * 40, "sk-proj-" + "c" * 40,
                  "sk-short", "my password", ""):
        assert not openweight.looks_like_key(other, "deepseek"), other
        with pytest.raises(openweight.OpenError, match="DeepSeek key"):
            openweight.save_key(app_cfg, other, "deepseek")
    openweight.save_key(app_cfg, f"  {KEY}\n", "deepseek")
    path = openweight.key_path(app_cfg, "deepseek")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert openweight.key_hint(app_cfg, "deepseek") == f"…{KEY[-4:]}"
    # Its own file: OpenRouter's is not touched, nor read.
    assert not openweight.has_key(app_cfg)
    openweight.forget_key(app_cfg, "deepseek")
    assert not openweight.has_key(app_cfg, "deepseek")


def test_deepseek_says_what_a_key_may_do(monkeypatch):
    answers = []

    def urlopen(request, timeout):
        answers.append((request.full_url, request.headers.get("Authorization")))
        if "bad" in request.headers.get("Authorization", ""):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        return io.BytesIO(json.dumps(BALANCE).encode())
    monkeypatch.setattr(openweight.urllib.request, "urlopen", urlopen)
    assert openweight.check_deepseek_key(KEY) == {"available": True, "balance": "4.20 USD"}
    assert answers[0] == ("https://api.deepseek.com/user/balance", f"Bearer {KEY}")
    with pytest.raises(openweight.OpenError, match="does not recognise"):
        openweight.check_deepseek_key("sk-bad0000000000000000")
    spent = openweight.deepseek_balance({"is_available": False, "balance_infos": [
        {"currency": "CNY", "total_balance": "0.00"}]})
    assert spent == {"available": False, "balance": "0.00 CNY"}


def test_a_key_is_kept_from_the_page_and_shown_only_by_its_end(client, monkeypatch):
    for bad in ({"host": "deepseek", "key": "sk-or-v1-" + "a" * 40}, {"host": "deepseek"}):
        assert client.put("/api/open/key", json=bad).status_code == 400
    assert client.get("/api/open/deepseek").json() == {"key": False}
    out = keyed(client, monkeypatch)
    assert out["hint"] == f"…{KEY[-4:]}" and out["balance"] == "4.20 USD"
    seen = client.get("/api/open/deepseek").json()
    assert seen == {"key": True, "hint": f"…{KEY[-4:]}", "available": True, "balance": "4.20 USD"}
    status = client.get("/api/open").json()
    assert status["deepseek"]["key"] is True and KEY not in json.dumps(status)
    assert status["openrouter"]["key"] is False          # the other host is not touched
    assert client.delete("/api/open/key?host=deepseek").json() == {"ok": True}
    assert client.get("/api/open/deepseek").json() == {"key": False}


def test_its_models_are_offered_beside_claude_and_chatgpt(client, monkeypatch):
    keyed(client, monkeypatch)
    menu = client.get("/api/agents?level=expert").json()
    [group] = [g for g in menu["groups"] if g["account"] == "deepseek"]
    assert group["label"] == "DeepSeek" and group["provider"] == "open"
    assert [m["name"] for m in group["models"]] == ["DeepSeek V4 Pro", "DeepSeek V4.1 Flash"]
    assert all(m["priced"] for m in group["models"])         # a money limit binds
    assert [e["id"] for e in group["efforts"]] == ["low", "medium", "high", "max"]
    assert group["web"] is True and group["signed_in"] is True
    # And it searches the web, so it is offered for a literature search too.
    search = client.get("/api/agents?level=search").json()
    assert [g["account"] for g in search["groups"] if g["provider"] == "open"] == ["deepseek"]


def test_a_model_deepseek_lists_and_unpacman_does_not_know_is_offered_unpriced():
    found = openweight.deepseek_models(["deepseek-flash", "deepseek-v5-preview", "../evil"])
    assert [m.id for m in found] == ["deepseek-v4-pro[1m]", "deepseek-flash[1m]",
                                     "deepseek-v5-preview[1m]"]
    assert found[2].name == "DeepSeek V5 Preview" and found[2].price is None


@needs_pdftotext
def test_a_reading_on_deepseek_is_claude_code_pointed_at_deepseek(client, launched, monkeypatch):
    keyed(client, monkeypatch)
    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "deepseek", "model": "deepseek-v4-pro[1m]", "effort": "max",
        "max_usd": 2})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert (plan.spec.provider, plan.spec.account, plan.model) == ("open", "deepseek",
                                                                   "deepseek-v4-pro[1m]")
    assert plan.budget_usd == 2                                # priced: the money limit binds
    env = plan.passes[0].env
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    # Fetched by Claude Code itself, never in its environment (openweight.key_helper).
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert KEY not in json.dumps(env) and KEY not in json.dumps(plan.passes[0].argv)
    settings = json.loads((plan.passes[0].dir / "settings.json").read_text())
    key_file = openweight.key_path(client.app.state.deslopify.cfg, "deepseek")
    assert settings["apiKeyHelper"] == f"/bin/cat {shlex.quote(str(key_file))}"
    argv = plan.passes[0].argv
    assert argv[argv.index("--model") + 1] == "deepseek-v4-pro[1m]"
    # Web search is among its tools: DeepSeek runs it.
    assert "WebSearch" in argv[argv.index("--tools") + 1]
    # "xhigh" is not DeepSeek's.
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "general", "account": "deepseek", "model": "deepseek-flash[1m]", "effort": "xhigh"})
    assert r.status_code == 400 and "Effort must be one of" in r.json()["detail"]


def test_a_companion_by_deepseek_is_credited_by_name_and_searched():
    described = agents.describe("open", "deepseek", "deepseek-v4-pro[1m]", "high")
    assert described["name"] == "DeepSeek V4 Pro" and described["web"] is True
    assert described["small"] is False
    # Other open-weight hosts answered from memory, and say so.
    assert agents.describe("open", "openrouter", "qwen/qwen3-max", "high")["web"] is False
    assert agents.describe("claude", "default", "claude-opus-5", "high")["web"] is True


def test_getting_ready_says_what_deepseek_lacks(app_cfg):
    cfg = openweight.with_accounts(app_cfg)
    account = openweight.accounts(cfg)["deepseek"]
    nokey = ready.open_status(account, "/usr/local/bin/claude")
    assert nokey.name == "DeepSeek" and not nokey.signed_in
    assert nokey.note == "DeepSeek has no key yet. Add one under Getting ready."
    openweight.save_key(cfg, KEY, "deepseek")
    assert resolve_secret(account.key_ref) == KEY
    assert ready.open_status(account, "/usr/local/bin/claude").usable
    bare = ready.open_status(account, None)
    assert not bare.signed_in and bare.note.startswith("DeepSeek reads through Claude Code")
    r = ready.Readiness(ais=[nokey], tex=ready.Tex(), pdf="PDFium", checked=0)
    assert r.blocking("open", "deepseek", latex=False) == [nokey.note]


def test_an_empty_balance_is_said_plainly():
    said = ready.plain_failure('API Error: 402 {"error":{"message":"Insufficient Balance"}}')
    assert "no credit left" in said and "platform.deepseek.com" in said


def test_an_explanation_by_deepseek_goes_to_deepseek(app_cfg):
    cfg = openweight.with_accounts(app_cfg)
    openweight.save_key(cfg, KEY, "deepseek")
    env = explain._open_env(cfg, cfg.account("deepseek"), "deepseek-flash[1m]")
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert "ANTHROPIC_AUTH_TOKEN" not in env and env["MAX_THINKING_TOKENS"] == "0"
    assert KEY not in json.dumps(env)
    assert env["ANTHROPIC_MODEL"] == "deepseek-flash[1m]"
    # explain.call gives it the command that fetches the key, and that file.
    from atelier.providers.openweight import helper_needed
    key_file = openweight.key_path(cfg, "deepseek")
    assert helper_needed(env, cfg.account("deepseek")) == (
        f"/bin/cat {shlex.quote(str(key_file))}", key_file)
