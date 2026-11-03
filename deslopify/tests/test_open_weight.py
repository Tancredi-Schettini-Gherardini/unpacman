"""Open-weight models: on this Mac with Ollama, or through OpenRouter (2026-10-08).

Nothing here reaches Ollama or OpenRouter: their answers are stood in for.
A real reading through a local Ollama was run by hand (DESIGN.md §22).
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from atelier.config import Account, PriceTable, resolve_secret
from deslopify.app import agents, openweight, ready
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, upload)

CATALOGUE = [
    {"id": "deepseek/deepseek-v4-pro", "name": "DeepSeek: DeepSeek V4 Pro", "created": 300,
     "hugging_face_id": "deepseek-ai/DeepSeek-V4-Pro", "context_length": 1_048_576,
     "supported_parameters": ["tools", "temperature"],
     "pricing": {"prompt": "0.00000029", "completion": "0.00000057",
                 "input_cache_read": "0.000000029"}},
    {"id": "deepseek/deepseek-v4-flash", "name": "DeepSeek: DeepSeek V4 Flash", "created": 200,
     "hugging_face_id": "deepseek-ai/x", "context_length": 1_048_576,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.00000001", "completion": "0.0000012"}},
    {"id": "deepseek/deepseek-v3.2", "name": "DeepSeek: DeepSeek V3.2", "created": 100,
     "hugging_face_id": "deepseek-ai/y", "context_length": 163_840,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.00000026", "completion": "0.00000042"}},
    # Left out: free variant, no tools, no published weights, too short, unknown family.
    {"id": "qwen/qwen3-30b-a3b:free", "hugging_face_id": "Qwen/q", "context_length": 262_144,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "qwen/qwen3-14b", "hugging_face_id": "Qwen/Qwen3-14B", "context_length": 131_072,
     "supported_parameters": ["temperature"], "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}},
    {"id": "openai/gpt-5.6", "hugging_face_id": "", "context_length": 400_000,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.000001", "completion": "0.00001"}},
    {"id": "meta-llama/llama-3.1-8b-instruct", "hugging_face_id": "meta/x", "context_length": 32_768,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.00000005", "completion": "0.00000008"}},
    {"id": "acme/great-model", "hugging_face_id": "acme/g", "context_length": 200_000,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.0000001", "completion": "0.0000001"}},
    {"id": "openai/gpt-oss-20b", "name": "OpenAI: gpt-oss-20b", "created": 50,
     "hugging_face_id": "openai/gpt-oss-20b", "context_length": 131_072,
     "supported_parameters": ["tools"], "pricing": {"prompt": "0.00000002", "completion": "0.00000009"}},
]


def test_the_suggestions_are_the_newest_open_models_that_can_read():
    found = openweight.suggested(CATALOGUE)
    # A model holding a million tokens is asked for with its whole window
    # (openweight.LONG_WINDOW); the shorter one as it is.
    assert [m.id for m in found] == ["deepseek/deepseek-v4-pro[1m]",
                                     "deepseek/deepseek-v4-flash[1m]", "openai/gpt-oss-20b"]
    pro, _, oss = found
    assert pro.name == "DeepSeek V4 Pro" and not pro.small
    assert oss.small                                   # 20 billion parameters
    assert pro.price.input == pytest.approx(0.29) and pro.price.output == pytest.approx(0.57)
    assert pro.price.cache_read_mult == pytest.approx(0.1)


def test_learned_prices_make_a_money_limit_bind():
    table = PriceTable([])
    assert table.lookup("deepseek/deepseek-v4-pro") is None
    table.add([m.price for m in openweight.suggested(CATALOGUE)])
    assert table.cost("deepseek/deepseek-v4-pro", tok_in=1_000_000) == pytest.approx(0.29)
    # Learned again (the same day): replaced, not doubled.
    table.add([m.price for m in openweight.suggested(CATALOGUE)])
    assert len(table._by_model["deepseek/deepseek-v4-pro"]) == 1


def test_ollamas_models_are_those_that_can_use_tools(monkeypatch):
    answers = {
        "/api/version": {"version": "0.40.1"},
        "/api/tags": {"models": [
            {"model": "qwen3:30b", "size": 18_600_000_000,
             "details": {"parameter_size": "30.5B", "context_length": 262_144},
             "capabilities": ["completion", "tools", "thinking"]},
            {"model": "gemma2:2b", "size": 1_600_000_000, "details": {"parameter_size": "2.6B"},
             "capabilities": ["completion"]},
            # unpacman's own long-window copy is not offered again.
            {"model": "unpacman/qwen3:30b", "capabilities": ["completion", "tools"]},
        ]},
    }
    monkeypatch.setattr(openweight, "_get_json",
                        lambda url, timeout, local=False: answers[url.split("11434")[1]])
    status = openweight.ollama_status("http://127.0.0.1:11434")
    assert status["running"] and status["version"] == "0.40.1"
    assert [m.id for m in status["models"]] == ["qwen3:30b"]
    assert [m.id for m in status["other"]] == ["gemma2:2b"]
    qwen = status["models"][0]
    assert qwen.free and not qwen.small and qwen.note == "30.5B, 18.6 GB"


def test_ollama_not_running_is_said_so(monkeypatch):
    def refuse(*a, **k):
        raise ConnectionRefusedError
    monkeypatch.setattr(openweight, "_get_json", refuse)
    monkeypatch.setattr(openweight, "ollama_installed", lambda: False)
    assert openweight.ollama_status() == {"installed": False, "running": False, "version": "",
                                          "models": [], "other": []}


@pytest.mark.parametrize("host, url", [
    ("", "http://127.0.0.1:11434"), ("127.0.0.1:11435", "http://127.0.0.1:11435"),
    ("0.0.0.0", "http://127.0.0.1:11434"), ("http://box:9000", "http://box:9000")])
def test_ollama_is_looked_for_where_its_own_setting_says(monkeypatch, host, url):
    monkeypatch.setenv("OLLAMA_HOST", host)
    assert openweight.ollama_url() == url


def test_a_key_is_kept_where_only_its_owner_can_read_it(app_cfg):
    with pytest.raises(openweight.OpenError):
        openweight.save_key(app_cfg, "my password")
    assert not openweight.has_key(app_cfg)
    key = "sk-or-v1-" + "a1b2c3d4" * 8
    openweight.save_key(app_cfg, f"  {key}\n")
    path = openweight.key_path(app_cfg)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert openweight.key_hint(app_cfg) == f"…{key[-4:]}"
    account = openweight.accounts(app_cfg)["openrouter"]
    assert resolve_secret(account.key_ref) == key
    openweight.forget_key(app_cfg)
    assert not openweight.has_key(app_cfg) and resolve_secret(account.key_ref) is None


def test_the_reader_always_has_the_open_weight_accounts(app_cfg):
    cfg = openweight.with_accounts(app_cfg)
    assert {a.id for a in cfg.accounts.values() if a.provider == "open"} == {"ollama", "openrouter",
                                                                              "deepseek"}
    # A configuration with open-weight accounts of its own keeps them.
    own = replace(app_cfg, accounts={**app_cfg.accounts, "box": Account(
        id="box", provider="open", auth="local", base_url="http://box:11434")})
    assert openweight.with_accounts(own) is own


def test_open_weight_accounts_are_offered_only_with_models(app_cfg, monkeypatch):
    from atelier.harnesses import get_harness

    monkeypatch.setattr(agents, "installed", lambda provider: True)
    cfg = openweight.with_accounts(app_cfg)
    harness = get_harness(cfg.harnesses_dir, "deslopify-expert")
    assert harness.supports("open")                 # it takes Claude, so open-weight too
    models = {"ollama": [openweight.Model(id="qwen3:30b", name="qwen3:30b", free=True)],
              "openrouter": []}
    agents.remember_open(models)
    offers = agents.offered(cfg, harness, open_models=models)
    assert [o.account for o in offers if o.provider == "open"] == ["ollama"]
    menu = agents.menu(cfg, harness, offers, agents.default_agent(cfg, harness, offers),
                       pinned=False, max_usd=1.0, max_minutes=10)
    [local] = [g for g in menu["groups"] if g["account"] == "ollama"]
    assert local["models"][0]["free"] is True and local["models"][0]["priced"] is False
    assert local["models"][0]["cap"] == {"usd": None, "minutes": 10}
    assert agents.full_name("open", "unpacman/qwen3:30b") == "qwen3:30b"


def test_readiness_says_what_an_open_weight_account_lacks(app_cfg, monkeypatch):
    cfg = openweight.with_accounts(app_cfg)
    accounts = openweight.accounts(cfg)
    monkeypatch.setattr(openweight, "ollama_status", lambda base=None: {
        "installed": True, "running": False, "version": "", "models": [], "other": []})
    off = ready.open_status(accounts["ollama"], "/usr/local/bin/claude")
    assert not off.signed_in and "not running" in off.note
    nokey = ready.open_status(accounts["openrouter"], "/usr/local/bin/claude")
    assert not nokey.signed_in and "no key" in nokey.note
    openweight.save_key(cfg, "sk-or-v1-" + "f" * 40)
    keyed = ready.open_status(accounts["openrouter"], "/usr/local/bin/claude")
    assert keyed.signed_in
    # Without Claude Code, which runs them, nothing open-weight can read.
    assert not ready.open_status(accounts["openrouter"], None).signed_in
    r = ready.Readiness(ais=[off, keyed], tex=ready.Tex(), pdf="PDFium", checked=0)
    assert r.blocking("open", "ollama", latex=False) == [off.note]
    assert r.blocking("open", "openrouter", latex=False) == []


def test_a_run_on_an_open_weight_model_is_claude_code_pointed_elsewhere(tmp_cfg, tmp_path):
    """The invocation: Claude Code itself, with the host's address and key, a
    private config folder, every model it might ask for set to the one chosen,
    its own cost cap left out — and Anthropic's prices dropped from its report."""
    from atelier.providers import get_adapter
    from atelier.spec import RunSpec

    key = tmp_path / "key"
    key.write_text("sk-or-v1-secret\n")
    account = Account(id="openrouter", provider="open", auth="api_key",
                      base_url="https://openrouter.ai/api", key_ref=f"file:{key}")
    adapter = get_adapter("open")
    harness = SimpleNamespace(tools=SimpleNamespace(none=False, allow=[], deny=[]),
                              permissions=SimpleNamespace(mode="acceptEdits", network=False), kind="read")
    (tmp_path / "settings.json").write_text("{}")
    argv, env = adapter.build_invocation(
        cfg=tmp_cfg, plan_dir=tmp_path, cwd=tmp_path, spec=RunSpec(problem="p", harness="h"),
        harness=harness, model="deepseek/deepseek-v4-pro", effort="low", account=account,
        system_append_path=None, settings_path=tmp_path / "settings.json",
        agents_path=None, plugin_dir=None, problem_root=tmp_path, budget_usd=3.0)
    assert argv[argv.index("--model") + 1] == "deepseek/deepseek-v4-pro"
    assert "--max-budget-usd" not in argv
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    # Not the key itself, which any program of the user could read in Claude
    # Code's environment: the command that prints it, which Claude Code runs.
    assert "ANTHROPIC_AUTH_TOKEN" not in env and env["ANTHROPIC_API_KEY"] == ""
    assert "sk-or-v1-secret" not in json.dumps(env) + json.dumps(argv)
    assert json.loads((tmp_path / "settings.json").read_text()) == {
        "apiKeyHelper": f"/bin/cat {shlex.quote(str(key))}"}
    assert env["CLAUDE_CODE_API_KEY_HELPER_TTL_MS"] == str(24 * 3600 * 1000)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_cfg.root / "var" / "open" / "openrouter")
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "deepseek/deepseek-v4-pro"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    [usage] = [e for e in adapter.parse_event({"type": "result", "total_cost_usd": 4.2,
                                               "usage": {"input_tokens": 10}}, "R")
               if e.type == "usage"]
    assert usage.payload["total_cost_usd"] is None


def test_a_key_kept_in_a_file_is_fetched_by_claude_code_itself(tmp_cfg, tmp_path, monkeypatch):
    """A key in Claude Code's environment is there for as long as it runs, and
    macOS lets any program of the user — a command of another reading, from
    inside its sandbox — read that (the other reviewer's finding 1,
    2026-11-02). So a key kept in a file is left out, and Claude Code is given
    a command that prints it; every other case is as it was."""
    from atelier.providers.openweight import (LOCAL_TOKEN, helper_needed, key_helper,
                                              open_env)

    folder = tmp_path / "it's a folder"               # a space and a quote: quoted
    folder.mkdir()
    key = folder / "key"
    key.write_text("sk-or-v1-secret\n")
    account = Account(id="openrouter", provider="open", auth="api_key",
                      base_url="https://openrouter.ai/api", key_ref=f"file:{key}")
    env = open_env(tmp_cfg, account, "m", {"ANTHROPIC_AUTH_TOKEN": "ambient"})
    assert "ANTHROPIC_AUTH_TOKEN" not in env and "sk-or-v1-secret" not in json.dumps(env)
    command, path = helper_needed(env, account)
    assert (command, path) == key_helper(account) and path == key
    printed = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)
    assert printed.stdout.strip() == "sk-or-v1-secret"

    # No key in its file yet: as before — a stand-in token, and nothing to fetch.
    key.write_text("")
    env = open_env(tmp_cfg, account, "m", {})
    assert env["ANTHROPIC_AUTH_TOKEN"] == LOCAL_TOKEN and helper_needed(env, account) is None
    assert "CLAUDE_CODE_API_KEY_HELPER_TTL_MS" not in env
    # A key named by an environment variable is in an environment already.
    monkeypatch.setenv("UNPACMAN_TEST_KEY", "sk-or-v1-from-env")
    named = replace(account, key_ref="env:UNPACMAN_TEST_KEY")
    env = open_env(tmp_cfg, named, "m", {})
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-v1-from-env"
    assert key_helper(named) is None and helper_needed(env, named) is None
    # Ollama needs none.
    local = Account(id="ollama", provider="open", auth="local", base_url="http://127.0.0.1:11434")
    env = open_env(tmp_cfg, local, "m", {})
    assert env["ANTHROPIC_AUTH_TOKEN"] == LOCAL_TOKEN and helper_needed(env, local) is None


async def test_an_explanation_by_an_open_weight_model_fetches_its_key_itself(
        tmp_cfg, tmp_path, monkeypatch):
    """explain.call: the key in no environment and no command line — the
    command that prints it given as a setting, and its file the one thing more
    unpacman's own sandbox lets Claude Code read."""
    from atelier.providers.claude import ClaudeAdapter
    from deslopify.app import explain

    key = tmp_path / "keys" / "openrouter"
    key.parent.mkdir()
    key.write_text("sk-or-v1-secret\n")
    account = Account(id="openrouter", provider="open", auth="api_key",
                      base_url="https://openrouter.ai/api", key_ref=f"file:{key}")
    cfg = replace(tmp_cfg, accounts={**tmp_cfg.accounts, "openrouter": account})
    seen = tmp_path / "seen.json"
    fake = tmp_path / "fake-claude"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "sys.stdin.read()\n"
        f"open({str(seen)!r}, 'w').write(json.dumps({{'argv': sys.argv, 'env': dict(os.environ)}}))\n"
        "print(json.dumps({'type': 'stream_event', 'event': {'type': 'content_block_delta',"
        " 'delta': {'type': 'text_delta', 'text': 'Done.'}}}), flush=True)\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,"
        " 'result': 'Done.', 'usage': {}}), flush=True)\n")
    fake.chmod(0o755)
    monkeypatch.setattr(ClaudeAdapter, "executable", staticmethod(lambda: str(fake)))
    sandboxed = {}

    def sandbox(argv, **kwargs):
        sandboxed.update(kwargs)
        return argv

    monkeypatch.setattr("atelier.access.sandbox_command", sandbox)
    agent = agents.Agent(account="openrouter", provider="open", model="qwen/qwen3-max",
                         effort="low")
    got = [x async for x in explain.call(cfg, agent, "system", "question")]
    assert got[-1][0] == "answer" and got[-1][1].text == "Done."
    ran = json.loads(seen.read_text())
    assert "sk-or-v1-secret" not in json.dumps(ran)
    assert "ANTHROPIC_AUTH_TOKEN" not in ran["env"]
    settings = json.loads(ran["argv"][ran["argv"].index("--settings") + 1])
    assert settings == {"apiKeyHelper": f"/bin/cat {shlex.quote(str(key))}"}
    assert key in sandboxed["read"]


@needs_pdftotext
def test_a_paper_too_long_for_a_local_model_is_refused_before_it_starts(client, launched,
                                                                       monkeypatch):
    st = client.app.state.deslopify
    slug = upload(client, paper_pdf()).json()["slug"]
    model = openweight.Model(id="qwen3:8b", name="qwen3:8b", free=True, small=True)
    monkeypatch.setattr(st.discovery, "models", lambda **k: {"ollama": [model], "openrouter": []})
    st.discovery._last = {"ollama": [model], "openrouter": []}
    monkeypatch.setattr(openweight, "long_context", lambda m, base=None: ("unpacman/qwen3:8b", 2_000))
    runs = st.cfg.runs_dir
    before = set(runs.iterdir()) if runs.is_dir() else set()
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "ollama", "model": "qwen3:8b"})
    assert r.status_code == 400 and "cannot hold the method" in r.json()["detail"]
    assert "plan" not in launched
    # The run planned to measure it is gone again.
    assert (set(runs.iterdir()) if runs.is_dir() else set()) == before
    # Room enough: the reading runs on the long-window copy.
    monkeypatch.setattr(openweight, "long_context", lambda m, base=None: ("unpacman/qwen3:8b", 131_072))
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "ollama", "model": "qwen3:8b"})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert plan.model == "unpacman/qwen3:8b" and plan.spec.provider == "open"
    assert plan.budget_usd is None                   # on this Mac: held to time


def test_a_literature_search_is_not_offered_to_open_weight_models(client):
    st = client.app.state.deslopify
    st.discovery._last = {"ollama": [openweight.Model(id="qwen3:30b", name="qwen3:30b")],
                          "openrouter": []}
    groups = client.get("/api/agents?level=search").json()["groups"]
    assert all(g["provider"] != "open" for g in groups)


def test_a_key_is_checked_before_it_is_kept(client, app_cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(openweight, "check_key", lambda key: seen.append(key) or {
        "label": "mine", "usage": 1.5, "limit": None, "free_tier": False})
    for bad in ({"key": "not a key"}, {"key": ""}, {}, {"key": 7}):
        r = client.put("/api/open/key", json=bad)
        assert r.status_code == 400 and seen == []   # never sent anywhere
    key = "sk-or-v1-" + "0" * 40
    r = client.put("/api/open/key", json={"key": key})
    assert r.status_code == 200 and r.json()["hint"] == "…0000" and seen == [key]
    status = client.get("/api/open").json()
    assert status["openrouter"]["key"] is True and key not in json.dumps(status)
    assert client.delete("/api/open/key").json() == {"ok": True}
    assert client.get("/api/open").json()["openrouter"]["key"] is False


def test_a_refused_key_is_not_kept(client, monkeypatch):
    def refuse(key):
        raise openweight.OpenError("OpenRouter does not recognise that key.")
    monkeypatch.setattr(openweight, "check_key", refuse)
    r = client.put("/api/open/key", json={"key": "sk-or-v1-" + "9" * 40})
    assert r.status_code == 400 and "does not recognise" in r.json()["detail"]
    assert client.get("/api/open").json()["openrouter"]["key"] is False


def test_a_model_is_downloaded_from_the_page_one_at_a_time(client, monkeypatch):
    import threading

    release = threading.Event()

    def run(self, base=None):
        self.total, self.completed, self.status = 100, 40, "pulling"
        release.wait(5)
        self.state = "done"
    monkeypatch.setattr(openweight.Pull, "run", run)
    for bad in ("", "../etc", "Model With Spaces", "a" * 200):
        assert client.post("/api/open/pull", json={"model": bad}).status_code == 400
    started = client.post("/api/open/pull", json={"model": "gpt-oss:20b"}).json()
    assert started["model"] == "gpt-oss:20b" and started["state"] == "running"
    assert client.post("/api/open/pull", json={"model": "qwen3:8b"}).status_code == 409
    assert client.get("/api/open/pull").json()["completed"] == 40
    release.set()
    for _ in range(50):
        if client.get("/api/open/pull").json()["state"] == "done":
            break
        threading.Event().wait(0.05)
    assert client.get("/api/open/pull").json()["state"] == "done"


def test_a_download_follows_ollamas_progress(monkeypatch):
    lines = [b'{"status":"pulling manifest"}\n',
             b'{"status":"pulling abc","digest":"abc","total":1000,"completed":250}\n',
             b'{"status":"pulling abc","digest":"abc","total":1000,"completed":1000}\n',
             b'{"status":"success"}\n']

    class Answer:
        def __enter__(self):
            return iter(lines)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(openweight, "_opener", lambda: type("O", (), {
        "open": lambda self, request, timeout: Answer()})())
    pull = openweight.Pull(model="gpt-oss:20b")
    pull.run("http://127.0.0.1:11434")
    assert pull.state == "done" and pull.total == 1000 and pull.completed == 1000
    lines[:] = [b'{"error":"pull model manifest: file does not exist"}\n']
    failed = openweight.Pull(model="nonesuch:1b")
    failed.run("http://127.0.0.1:11434")
    assert failed.state == "failed" and "does not exist" in failed.error
