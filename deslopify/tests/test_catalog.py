"""Which models Claude and ChatGPT offer (catalog.py): asked of their own
programs, so that the menu shows the newest models however old the lab's list
is (2026-10-15: it showed Opus 5 and GPT-5.6 Terra as the newest, a month after
Opus 5.5, Fable 5.1, GPT-6.1 Sol and GPT-6 Astra appeared).

Every program here is a stand-in script, so nothing is installed, signed in to
or spent; what the real ones said is recorded in the answers below.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atelier.config import Account
from deslopify.app import agents, catalog
from test_app import (  # noqa: F401  (fixtures)
    MODELS,
    app_cfg,
    client,
)
from test_ready import script

CLAUDE = Account(id="default", provider="claude", auth="subscription")
CODEX = Account(id="codex", provider="codex", auth="subscription")

# What Claude Code 2.1.296 answered on 2026-10-15, asked as catalog.ask_claude
# asks it (non-essential traffic off): one line, the newest of each kind.
HANDSHAKE = json.dumps({"type": "control_response", "response": {
    "subtype": "success", "request_id": "unpacman-models", "response": {
        "models": [
            {"value": "default", "resolvedModel": "claude-opus-5-5",
             "displayName": "Default (recommended)"},
            {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus"},
            {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1",
             "displayName": "Fable"},
            {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet"},
            {"value": "haiku", "resolvedModel": "claude-haiku-5-5", "displayName": "Haiku"}],
        "account": {}, "commands": []}}})
NEWEST_CLAUDE = ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-5-5"]


def catalogue(*slugs: str, hidden: tuple[str, ...] = (), at: datetime | None = None) -> dict:
    """A Codex catalogue in the form Codex keeps it, `slugs` in priority order."""
    models = [{"slug": s, "display_name": s.upper(), "visibility": "list", "priority": i + 1}
              for i, s in enumerate(slugs)]
    models += [{"slug": s, "visibility": "hide", "priority": 50} for s in hidden]
    out = {"models": models}
    if at is not None:
        out["fetched_at"] = at.isoformat().replace("+00:00", "Z")
    return out


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------


def test_claude_codes_answer_is_read_for_the_newest_model_of_each_kind():
    noise = "\n".join(['{"type": "system", "subtype": "init"}', "not json", HANDSHAKE,
                       json.dumps({"type": "control_response", "response": {"response": {
                           "models": [{"value": "x", "resolvedModel": "gpt-5"},
                                      {"value": "y", "resolvedModel": "claude-$(rm -rf)"},
                                      "not a model"]}}})])
    assert catalog.claude_models(noise) == NEWEST_CLAUDE
    assert catalog.claude_models("") == []


def test_claude_code_is_asked_without_a_message_and_without_ambient_keys(
        tmp_path, monkeypatch):
    seen = tmp_path / "seen"
    seen.mkdir()
    exe = script(tmp_path / "bin" / "claude", f"""printf '%s\\n' "$@" > {seen}/argv
env > {seen}/env
pwd > {seen}/cwd
IFS= read -r line
printf '%s\\n' "$line" > {seen}/stdin
case "$line" in *'"initialize"'*) echo '{HANDSHAKE}' ;; esac
cat > /dev/null""")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-for-this")
    assert catalog.ask_claude(str(exe), CLAUDE) == NEWEST_CLAUDE

    argv = (seen / "argv").read_text().splitlines()
    assert argv[:1] == ["-p"] and "--no-session-persistence" in argv
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--setting-sources") + 1] == ""
    sent = json.loads((seen / "stdin").read_text())
    assert sent["type"] == "control_request" and sent["request"] == {"subtype": "initialize"}
    env = (seen / "env").read_text()
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1" in env and "DISABLE_AUTOUPDATER=1" in env
    assert "sk-ant-not-for-this" not in env
    # Asked in a folder of its own, which is gone afterwards.
    assert not Path((seen / "cwd").read_text().strip()).exists()


def test_a_claude_code_that_cannot_say_leaves_nothing(tmp_path):
    silent = script(tmp_path / "silent" / "claude", "cat > /dev/null; exit 1")
    assert catalog.ask_claude(str(silent), CLAUDE) == []
    assert catalog.ask_claude(str(tmp_path / "missing" / "claude"), CLAUDE) == []


def test_an_account_of_its_own_is_asked_with_its_own_settings(tmp_path):
    seen = tmp_path / "env"
    exe = script(tmp_path / "claude", f"env > {seen}; cat > /dev/null")
    other = Account(id="work", provider="claude", auth="subscription",
                    config_dir=tmp_path / "work-login")
    catalog.ask_claude(str(exe), other)
    assert f"CLAUDE_CONFIG_DIR={tmp_path / 'work-login'}" in seen.read_text()


# --------------------------------------------------------------------------
# Codex
# --------------------------------------------------------------------------


def test_a_codex_catalogue_is_read_for_what_can_be_chosen_in_its_order():
    data = catalogue("gpt-6.1-sol", "gpt-6-astra", hidden=("codex-auto-review",))
    data["models"].insert(0, {"slug": "gpt-6-luna", "visibility": "list", "priority": 4})
    data["models"].append({"slug": "Not A Slug!", "visibility": "list", "priority": 0})
    data["models"].append({"slug": "gpt-6.1-sol", "visibility": "list", "priority": 9})
    assert catalog.codex_models(data) == ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"]
    assert catalog.codex_models({"models": "nonsense"}) == []
    assert catalog.codex_models(None) == []


def test_the_accounts_own_recent_list_is_what_codex_offers(app_cfg, tmp_path):
    """The list Codex's service gave the account: the freshest of the two
    places it may be, and no model the account cannot use."""
    now = datetime(2026, 10, 15, 16, 0, tzinfo=UTC)
    home = tmp_path / "codex-login"
    account = replace(CODEX, config_dir=home)
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(catalogue(
        "gpt-6.1-sol", "gpt-6-astra", at=now - timedelta(hours=2))))
    ours = app_cfg.root / "var" / "codex" / "codex"
    ours.mkdir(parents=True)
    (ours / "models_cache.json").write_text(json.dumps(catalogue(
        "gpt-5.6-terra", at=now - timedelta(days=4))))
    never = str(tmp_path / "no-codex")            # not asked: the lists are enough
    assert catalog.ask_codex(never, account, app_cfg, now=now.timestamp()) == \
        ["gpt-6.1-sol", "gpt-6-astra"]
    # The other place, when it is the fresher.
    (ours / "models_cache.json").write_text(json.dumps(catalogue(
        "gpt-7-nova", at=now - timedelta(minutes=5))))
    assert catalog.ask_codex(never, account, app_cfg, now=now.timestamp()) == ["gpt-7-nova"]


def test_an_old_list_gives_way_to_the_one_built_into_codex(app_cfg, tmp_path):
    now = datetime(2026, 10, 15, 16, 0, tzinfo=UTC)
    home = tmp_path / "codex-login"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(catalogue(
        "gpt-5.5", at=now - timedelta(days=30))))
    seen = tmp_path / "seen"
    exe = script(tmp_path / "bin" / "codex", f"""printf '%s ' "$@" > {seen}
echo "HOME=$HOME CODEX_HOME=$CODEX_HOME" >> {seen}
case "$1 $2 $3" in
  "debug models --bundled") echo '{json.dumps(catalogue("gpt-6.1-sol", "gpt-6-luna"))}' ;;
  *) exit 2 ;;
esac""")
    account = replace(CODEX, config_dir=home)
    assert catalog.ask_codex(str(exe), account, app_cfg, now=now.timestamp()) == \
        ["gpt-6.1-sol", "gpt-6-luna"]
    said = seen.read_text()
    # Offline, and nowhere near the real sign-in.
    assert said.startswith("debug models --bundled")
    assert str(home) not in said and str(Path.home() / ".codex") not in said
    # A Codex too old to have a catalogue, or one that answers nonsense.
    script(tmp_path / "bin" / "codex", "exit 2")
    assert catalog.ask_codex(str(exe), account, app_cfg, now=now.timestamp()) == []
    script(tmp_path / "bin" / "codex", "echo 'not json'")
    assert catalog.ask_codex(str(exe), account, app_cfg, now=now.timestamp()) == []


# --------------------------------------------------------------------------
# asked once, and again when the program changes
# --------------------------------------------------------------------------


def test_each_program_is_asked_once_until_it_changes(app_cfg, monkeypatch, tmp_path):
    asked: list[str] = []

    def ask(provider, exe, account, cfg):
        asked.append(provider)
        if provider == "codex":
            raise RuntimeError("a program that misbehaves")
        return list(NEWEST_CLAUDE)

    identity = {"claude": ("claude", 1, 1), "codex": ("codex", 1, 1)}
    monkeypatch.setattr(catalog, "_executable", lambda provider: f"/bin/{provider}")
    monkeypatch.setattr(catalog, "_identity", lambda exe: identity[Path(exe).name])
    now = [1_000_000.0]
    kept = catalog.Catalog(app_cfg, ask=ask, clock=lambda: now[0])
    assert kept.refresh() == {"default": NEWEST_CLAUDE}     # Codex could not say
    assert sorted(asked) == ["claude", "codex"]
    kept.refresh()
    assert len(asked) == 2                                   # kept, not asked again
    identity["claude"] = ("claude", 2, 2)                    # an update
    kept.refresh()
    assert asked[-1] == "claude" and len(asked) == 3
    # And after some hours, both again.
    now[0] += catalog.TTL + 1
    kept.refresh()
    assert len(asked) == 5 and kept.snapshot() == {"default": NEWEST_CLAUDE}


def test_a_program_not_installed_is_not_asked(app_cfg, monkeypatch):
    monkeypatch.setattr(catalog, "_executable", lambda provider: None)
    kept = catalog.Catalog(app_cfg, ask=lambda *a: pytest.fail("asked"))
    assert kept.refresh() == {}


# --------------------------------------------------------------------------
# the menu
# --------------------------------------------------------------------------


def _harness(app_cfg):
    from atelier.harnesses import get_harness

    return get_harness(app_cfg.harnesses_dir, "solo")


def test_claudes_newest_come_first_and_the_lists_older_ones_stay(app_cfg, monkeypatch):
    monkeypatch.setattr(agents, "installed", lambda provider: True)
    offers = agents.offered(app_cfg, _harness(app_cfg), catalog={"default": NEWEST_CLAUDE})
    claude = next(o for o in offers if o.provider == "claude")
    assert list(claude.models) == [*NEWEST_CLAUDE, "claude-opus-5", "claude-sonnet-5",
                                   "claude-haiku-4-5"]
    # What runs when nobody chooses is what ran before: the configured default.
    assert claude.default_model == app_cfg.defaults.model_for("claude")


def test_chatgpts_models_are_those_its_account_can_use(app_cfg, monkeypatch):
    """Codex lists every model the account can use: the lab's list is replaced,
    so a model the account cannot use (GPT-5.4 mini on a ChatGPT plan) is not
    offered — and a Claude id is never offered to ChatGPT."""
    monkeypatch.setattr(agents, "installed", lambda provider: True)
    offers = agents.offered(app_cfg, _harness(app_cfg), catalog={
        "codex": ["gpt-6.1-sol", "gpt-6-astra", "claude-opus-5-5"]})
    codex = next(o for o in offers if o.provider == "codex")
    assert list(codex.models) == ["gpt-6.1-sol", "gpt-6-astra"]
    # The configured default is not among them: the first, the program's own first.
    assert codex.default_model == "gpt-6.1-sol"
    # Nothing said: the configuration's, as before.
    plain = agents.offered(app_cfg, _harness(app_cfg))
    assert list(next(o for o in plain if o.provider == "codex").models) == list(MODELS[1][1])


def test_the_menu_names_the_newest_models(client):
    """End to end: what the programs say is what the page is offered, by name,
    and a reading can be started on it."""
    st = client.app.state.deslopify
    st.catalog = catalog.Catalog(st.cfg, ask=lambda provider, exe, account, cfg: {
        "claude": NEWEST_CLAUDE, "codex": ["gpt-6.1-sol", "gpt-6-astra"]}[provider])
    import deslopify.app.catalog as module
    from unittest import mock
    with mock.patch.object(module, "_executable", lambda provider: f"/bin/{provider}"):
        menu = client.get("/api/agents").json()
    groups = {g["provider"]: g for g in menu["groups"]}
    claude = [(m["id"], m["name"], m["priced"]) for m in groups["claude"]["models"]]
    assert claude[:4] == [("claude-opus-5-5", "Claude Opus 5.5", False),
                          ("claude-fable-5-1", "Claude Fable 5.1", False),
                          ("claude-sonnet-5-5", "Claude Sonnet 5.5", False),
                          ("claude-haiku-5-5", "Claude Haiku 5.5", False)]
    assert [(m["id"], m["name"]) for m in groups["codex"]["models"]] == \
        [("gpt-6.1-sol", "GPT-6.1 Sol"), ("gpt-6-astra", "GPT-6 Astra")]
    # The test's price table knows none of the new Claude models: held to time,
    # and said so — never priced as an older model whose name theirs begins with.
    assert groups["claude"]["models"][0]["cap"]["usd"] is None


def test_a_new_sign_in_is_asked_again(client):
    """Another account may be offered other models: when Getting ready has
    signed one in, the menu asks the programs again (server.create_app)."""
    from types import SimpleNamespace

    from deslopify.app import ready
    from deslopify.app.server import create_app

    # The test client's Getting ready is a stub: a real one, wired as the
    # server wires it (its jobs are not run here; the app is never started).
    checker = ready.Checker(client.app.state.deslopify.cfg)
    app = create_app(client.app.state.deslopify.cfg, checker=checker,
                     trusted_hosts=("testserver",))
    lists = app.state.deslopify.catalog
    lists._found["codex"] = (0.0, None, ["gpt-6.1-sol"])
    for listener in checker.on_finish:
        listener(SimpleNamespace(kind="install-latex"))
    assert lists.snapshot() == {"codex": ["gpt-6.1-sol"]}        # LaTeX: nothing to forget
    for listener in checker.on_finish:
        listener(SimpleNamespace(kind="switch-codex"))
    assert lists.snapshot() == {}
    # And back to the Mac's own sign-in, which may be another account again.
    lists._found["codex"] = (0.0, None, ["gpt-6.1-sol"])
    for listener in checker.on_finish:
        listener(SimpleNamespace(kind="revert-codex"))
    assert lists.snapshot() == {}


def test_an_own_chatgpt_sign_in_lists_only_its_own_models(tmp_cfg, tmp_path):
    """The folder the lab runs Codex in is the account id's, shared with the
    Mac's own sign-in: its list is not that of unpacman's own account."""
    from deslopify.app import signins

    mac = replace(tmp_cfg.accounts["codex"], config_dir=tmp_path / "mac-codex")
    assert catalog.codex_homes(tmp_cfg, mac) == [
        tmp_path / "mac-codex", tmp_cfg.root / "var" / "codex" / "codex"]
    own = replace(mac, config_dir=signins.root(tmp_cfg) / "codex-0123abcd")
    assert catalog.codex_homes(tmp_cfg, own) == [signins.root(tmp_cfg) / "codex-0123abcd"]
