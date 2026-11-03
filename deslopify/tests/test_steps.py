"""A different AI for each step of a reading (2026-10-08).

The first reading, the check and the revision may each run on any AI this
computer offers — Claude for one, ChatGPT for another. Readings are recorded,
not run (`launched`); finished runs are made up on disk.
"""

from __future__ import annotations

import json

import pytest

from deslopify.app import agents
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, isolated_run, launched, needs_pdftotext, paper_pdf, upload)


def slugged(client) -> str:
    return upload(client, paper_pdf()).json()["slug"]


def test_the_menu_names_the_steps_a_reading_has(client):
    steps = client.get("/api/agents?level=expert").json()["steps"]
    assert [s["slot"] for s in steps] == ["draft", "check", "revision"]
    assert steps[1]["title"] == "The check"
    # A literature search remains one pass; a collection now has the same
    # independent check and conditional revision as a single paper.
    assert client.get("/api/agents?level=search").json()["steps"] == []
    for level in ("collection-expert", "collection-nonexpert"):
        assert [s["slot"] for s in client.get(f"/api/agents?level={level}").json()["steps"]] \
            == ["draft", "check", "revision"]


def test_the_steps_of_each_harness(app_cfg):
    from atelier.harnesses import get_harness

    balanced = get_harness(app_cfg.harnesses_dir, "deslopify-expert")
    assert agents.step_roles(balanced) == {
        "draft": ["draft"], "check": ["auditor"], "revision": ["balanced-reviser"]}
    thorough = get_harness(app_cfg.harnesses_dir, "deslopify")
    roles = agents.step_roles(thorough)
    assert roles["revision"] == [thorough.loop.reviser]
    assert "graph-builder" in roles["check"] and "draft" not in roles["check"]


@needs_pdftotext
def test_the_check_on_chatgpt_and_the_rest_on_claude(client, launched):
    slug = slugged(client)
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "default", "model": "claude-sonnet-5", "effort": "high",
        "steps": {"check": {"account": "codex", "model": "gpt-5.5", "effort": "medium"}}})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert plan.spec.role_agents == {"auditor": {"provider": "codex", "account": "codex",
                                                 "model": "gpt-5.5", "effort": "medium"}}
    assert plan.extra["agents"]["auditor"]["model"] == "gpt-5.5"
    assert plan.extra["agents"]["balanced-reviser"]["model"] == "claude-sonnet-5"
    # One step has no per-token price: the money limit binds on the others, the
    # time limit on all of them.
    assert plan.budget_usd == 0.5 and plan.budget_minutes


@needs_pdftotext
def test_a_step_given_the_readings_own_ai_changes_nothing(client, launched):
    slug = slugged(client)
    client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "default", "model": "claude-haiku-4-5",
        "steps": {"check": {"account": "default", "model": "claude-haiku-4-5"},
                  "revision": None}})
    assert launched["plan"].spec.role_agents == {}
    assert launched["plan"].budget_usd == 0.5


@needs_pdftotext
def test_an_effort_of_its_own_is_the_steps(client, launched):
    slug = slugged(client)
    client.post(f"/api/papers/{slug}/read", json={
        "level": "general", "account": "default", "model": "claude-haiku-4-5",
        "steps": {"revision": {"account": "default", "model": "claude-haiku-4-5",
                               "effort": "max"}}})
    assert launched["plan"].spec.role_agents == {
        "balanced-reviser": {"provider": "claude", "account": "default",
                             "model": "claude-haiku-4-5", "effort": "max"}}


@needs_pdftotext
def test_a_step_on_an_ai_that_cannot_run_is_refused_before_anything_starts(client, launched):
    slug = slugged(client)
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "default", "model": "claude-haiku-4-5",
        "steps": {"revision": {"account": "codex", "model": "claude-opus-5"}}})
    assert r.status_code == 400 and r.json()["detail"].startswith("The revision:")
    assert "plan" not in launched


@needs_pdftotext
def test_every_ai_a_reading_uses_must_be_signed_in(client, launched):
    slug = slugged(client)
    client.ready.signed_in = {"claude"}                  # ChatGPT is not
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "account": "default", "model": "claude-haiku-4-5",
        "steps": {"check": {"account": "codex", "model": "gpt-5.5"}}})
    assert r.status_code == 409 and "nothing has been spent" in r.json()["detail"]
    assert "plan" not in launched


@needs_pdftotext
def test_a_reading_by_several_ais_says_which_did_what(client, app_cfg):
    slug = slugged(client)
    st = client.app.state.deslopify
    where = isolated_run(st, app_cfg, slug, "MIX", "2026-10-08T10:00:00+00:00", "done",
                         "claude-opus-5", level="expert")
    config = app_cfg.runs_dir / "MIX" / "config.json"
    record = json.loads(config.read_text())
    record["resolved"]["agents"] = {
        "draft": {"provider": "claude", "account": "default", "model": "claude-opus-5",
                  "effort": "high"},
        "auditor": {"provider": "codex", "account": "codex", "model": "gpt-5.5",
                    "effort": "high"},
        "balanced-reviser": {"provider": "claude", "account": "default",
                             "model": "claude-opus-5", "effort": "high"}}
    config.write_text(json.dumps(record))
    (where.parent / "loop.json").write_text(json.dumps({"passes": [
        {"role": "draft"}, {"role": "auditor"}, {"role": "balanced-reviser"}]}))

    status = client.get(f"/api/papers/{slug}?level=expert&run=MIX").json()
    named = {s["role"]: s.get("agent", {}).get("name") for s in status["steps"]}
    assert named == {"draft": "Claude Opus 5", "auditor": "GPT-5.5",
                     "balanced-reviser": "Claude Opus 5"}
    assert [a["name"] for a in status["run"]["agents"]] == ["Claude Opus 5", "GPT-5.5"]

    doc = client.get(f"/api/papers/{slug}/companion?level=expert&run=MIX").json()
    assert [(s["slot"], s["agent"]["name"]) for s in doc["by"]["steps"]] == [
        ("draft", "Claude Opus 5"), ("check", "GPT-5.5"), ("revision", "Claude Opus 5")]

    # A revision that never ran (nothing worth revising) is credited to nobody.
    (where.parent / "loop.json").write_text(json.dumps({"passes": [
        {"role": "draft"}, {"role": "auditor"}]}))
    doc = client.get(f"/api/papers/{slug}/companion?level=expert&run=MIX").json()
    assert [s["slot"] for s in doc["by"]["steps"]] == ["draft", "check"]


@needs_pdftotext
def test_a_reading_by_one_ai_is_credited_as_before(client, app_cfg):
    slug = slugged(client)
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "ONE", "2026-10-08T10:00:00+00:00", "done",
                 "claude-opus-5", level="expert")
    status = client.get(f"/api/papers/{slug}?level=expert").json()
    assert "agents" not in status["run"]
    assert all("agent" not in s for s in status["steps"])
    doc = client.get(f"/api/papers/{slug}/companion?level=expert").json()
    assert doc["by"]["steps"] == [] and doc["by"]["agent"]["name"] == "Claude Opus 5"


@pytest.mark.parametrize("asked", ["nonsense", 3, ["check"], {"check": "codex"}])
def test_steps_in_a_shape_the_page_never_sends_are_ignored(asked, app_cfg):
    from atelier.harnesses import get_harness

    from deslopify.app.server import _step_agents

    harness = get_harness(app_cfg.harnesses_dir, "deslopify-expert")
    offers = agents.offered(app_cfg, harness)
    first = agents.Agent(account="default", provider="claude", model="claude-haiku-4-5",
                         effort="low")
    assert _step_agents(harness, offers, first, asked) == ({}, {})
