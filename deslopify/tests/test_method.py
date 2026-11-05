"""Tests for the method as the reader may change it: which files are shown, how
an edit is kept and checked, and what a reading started with edits runs.

Nothing here touches the method as it ships: each test's harnesses are copies,
and the reader's edits go to a temporary `method.local/`.
"""

from __future__ import annotations

import json

import pytest

from deslopify.app import explain, method
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, paper_with, upload)


def files(client) -> dict[str, dict]:
    return {f["path"]: f for f in client.get("/api/method").json()["files"]}


def save(client, path: str, text: str):
    return client.put("/api/method/file", json={"path": path, "text": text})


def test_every_file_a_reading_is_made_from_is_shown_and_none_is_edited(client, app_cfg):
    listed = files(client)
    for path in ("harness/SKILL-common.md", "harness/SKILL-expert.md", "harness/SKILL-nonexpert.md",
                 "harness/SKILL-pure-expert.md", "harness/SKILL-applied-expert.md",
                 "harness/SKILL-pure-nonexpert.md", "harness/SKILL-applied-nonexpert.md",
                 "harness/roles/auditor.md", "harness/roles/balanced-reviser.md",
                 "harness/roles/proof-map.md", "harness/harness.toml",
                 "shared/policy/fidelity.md", "explain/instructions.md",
                 "explain/glossary.md"):
        assert path in listed and listed[path]["edited"] is False
    # What readings started here do not run is shown apart, not hidden.
    assert listed["harness/roles/verifier-blind.md"]["group"] == "Not used by readings started here"
    assert listed["harness/roles/auditor.md"]["group"] == "Later passes"
    assert client.get("/api/method").json()["where"] == str(app_cfg.root / "method.local")


def test_a_papers_files_are_placed_by_level_and_by_pure_or_applied(client):
    # Advanced settings shows a paper's two levels side by side, each first
    # deciding pure or applied, then that profile's file (settings.js).
    listed = files(client)
    place = {path: (f["section"], f["level"], f["part"]) for path, f in listed.items()}
    for level in ("expert", "nonexpert"):
        assert place[f"harness/SKILL-{level}.md"] == ("paper", level, "router")
        assert place[f"harness/SKILL-pure-{level}.md"] == ("paper", level, "pure")
        assert place[f"harness/SKILL-applied-{level}.md"] == ("paper", level, "applied")
    assert place["harness/questionnaire.md"] == ("paper", "nonexpert", "questions")
    assert place["harness/SKILL-multi.md"][0] == "collection"
    assert place["harness/SKILL-search.md"][0] == "search"
    # Everything else keeps its group, and has no place among the readings'.
    assert place["harness/roles/auditor.md"] == ("", "", "")
    # A reading file the list does not know yet is still shown, among them.
    assert {f["group"] for p, f in listed.items() if f["section"]} == {"Readings"}


def test_an_edit_is_kept_apart_and_the_original_is_never_touched(client, app_cfg):
    path = "harness/SKILL-expert.md"
    shipped = (app_cfg.harnesses_dir / "deslopify" / "SKILL-expert.md").read_text(encoding="utf-8")
    one = client.get("/api/method/file", params={"path": path}).json()
    assert one["text"] == one["original"] == shipped and one["edited"] is False

    r = save(client, path, shipped + "\nALWAYS GIVE AN EXAMPLE.\n")
    assert r.status_code == 200 and r.json() == {"edited": True, "warnings": []}
    one = client.get("/api/method/file", params={"path": path}).json()
    assert one["text"].endswith("ALWAYS GIVE AN EXAMPLE.\n") and one["original"] == shipped
    assert (app_cfg.root / "method.local" / "harness" / "SKILL-expert.md").is_file()
    assert (app_cfg.harnesses_dir / "deslopify" / "SKILL-expert.md").read_text(
        encoding="utf-8") == shipped

    # Reset is the original again; and a version identical to it is no edit.
    assert client.delete("/api/method/file", params={"path": path}).json() == {"edited": False}
    assert files(client)[path]["edited"] is False
    assert save(client, path, shipped).json() == {"edited": False, "warnings": []}
    assert not (app_cfg.root / "method.local" / "harness" / "SKILL-expert.md").exists()


def test_a_role_must_keep_its_header_and_a_skill_is_warned(client):
    role = "harness/roles/auditor.md"
    original = client.get("/api/method/file", params={"path": role}).json()["original"]
    r = save(client, role, "My note first.\n" + original)
    assert r.status_code == 400 and "header" in r.json()["detail"]
    skill = "harness/SKILL-expert.md"
    original = client.get("/api/method/file", params={"path": skill}).json()["original"]
    r = save(client, skill, "My note first.\n" + original)
    assert r.status_code == 200 and any("header" in w for w in r.json()["warnings"])


def test_removing_a_placeholder_is_allowed_and_said(client):
    path = "harness/roles/auditor.md"
    original = client.get("/api/method/file", params={"path": path}).json()["original"]
    assert "{deslopify}" in original
    r = save(client, path, original.replace("{deslopify}", "deslopify"))
    assert r.status_code == 200
    assert any("{deslopify}" in w for w in r.json()["warnings"])


@pytest.mark.parametrize("path, change, why", [
    ("harness/harness.toml", lambda t: t + "\n[broken\n", "not valid TOML"),
    ("harness/harness.toml", lambda t: t.replace('kind        = "read"', 'kind        = "solve"'),
     'kind = "read"'),
    ("harness/harness.toml", lambda t: t.replace('id          = "deslopify-expert"',
                                                 'id          = "my-expert"'),
     "deslopify-expert"),
    ("harness/roles/auditor.md", lambda t: t.replace("name: auditor", "name: inspector"),
     "auditor"),
    ("harness/SKILL-nonexpert.md", lambda t: "   \n", "cannot be empty"),
    ("explain/glossary.md", lambda t: t + " {readers}", "{reader}"),
])
def test_an_edit_that_would_break_the_method_is_refused_and_not_kept(client, app_cfg,
                                                                     path, change, why):
    original = client.get("/api/method/file", params={"path": path}).json()["original"]
    edited = change(original)
    assert edited != original
    r = save(client, path, edited)
    assert r.status_code == 400 and why in r.json()["detail"]
    assert files(client)[path]["edited"] is False
    assert not (app_cfg.root / "method.local" / path).exists()


def test_only_the_method_s_own_files_can_be_written(client, app_cfg):
    for path in ("../../etc/passwd", "harness/../../atelier.toml", "shared/policy/rigor.md",
                 "harness/roles/nope.md"):
        assert save(client, path, "anything").status_code == 400
        assert client.get("/api/method/file", params={"path": path}).status_code == 404
    assert not (app_cfg.root / "method.local").exists()


# --------------------------------------------------------------------------
# a reading started with edits
# --------------------------------------------------------------------------


@needs_pdftotext
@pytest.mark.parametrize("filename", ["SKILL-common.md", "SKILL-expert.md", "SKILL-pure-expert.md",
                                    "SKILL-applied-expert.md"])
def test_a_reading_started_with_edits_runs_its_own_copy_of_the_method(client, app_cfg,
                                                                      launched, filename):
    from atelier.compile import build_role_system_append

    path = f"harness/{filename}"
    skill = client.get("/api/method/file", params={"path": path}).json()["original"]
    save(client, path, skill + "\nMY OWN STEP.\n")
    rule = client.get("/api/method/file",
                      params={"path": "shared/policy/fidelity.md"}).json()["original"]
    save(client, "shared/policy/fidelity.md", rule + "\nMY OWN RULE.\n")

    slug = upload(client, paper_pdf()).json()["slug"]
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert r.status_code == 200
    plan = launched["plan"]
    copy = app_cfg.runs_dir / plan.spec.run_id / "method"
    assert plan.harness.root == copy and plan.harness.id == "deslopify-expert"
    loaded = (plan.harness.skill if filename == "SKILL-expert.md" else
              (plan.run_dir / "skill-references" / filename).read_text(encoding="utf-8"))
    assert "MY OWN STEP." in loaded
    # The rule is the run's own, for the first pass and the ones after it.
    assert "MY OWN RULE." in plan.system_append
    assert "MY OWN RULE." in build_role_system_append(app_cfg, plan.harness,
                                                      plan.harness.roles["auditor"])
    assert method.used_by(copy.parent) == [path, "shared/policy/fidelity.md"]
    # Editing again later does not reach a reading that has started.
    save(client, path, skill + "\nA LATER STEP.\n")
    assert "A LATER STEP." not in (copy / filename).read_text(encoding="utf-8")


@needs_pdftotext
def test_a_reading_started_with_nothing_edited_runs_as_before(client, app_cfg, launched):
    slug = upload(client, paper_pdf()).json()["slug"]
    # Explanations' instructions are not part of a reading.
    save(client, "explain/brief.md", "Answer in one sentence.")
    assert client.post(f"/api/papers/{slug}/read", json={"level": "general"}).status_code == 200
    plan = launched["plan"]
    assert plan.harness.root.resolve() == (app_cfg.harnesses_dir / "deslopify").resolve()
    assert not (app_cfg.runs_dir / plan.spec.run_id / "method").exists()


@needs_pdftotext
def test_an_edit_broken_outside_the_page_stops_the_reading_and_leaves_nothing(
        client, app_cfg, launched):
    """Kept edits are checked when saved, but the folder is a folder: one
    broken by hand must stop the reading with a reason, not start a broken run."""
    kept = app_cfg.root / "method.local" / "harness" / "harness.toml"
    kept.parent.mkdir(parents=True)
    kept.write_text("[broken\n", encoding="utf-8")
    slug = upload(client, paper_pdf()).json()["slug"]
    before = set(app_cfg.runs_dir.iterdir())
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert r.status_code == 400 and "Your edited method could not be used" in r.json()["detail"]
    assert "plan" not in launched
    assert set(app_cfg.runs_dir.iterdir()) == before


@needs_pdftotext
def test_a_reading_refused_after_its_copy_was_made_leaves_no_copy(client, app_cfg, launched):
    skill = client.get("/api/method/file",
                       params={"path": "harness/SKILL-expert.md"}).json()["original"]
    save(client, "harness/SKILL-expert.md", skill + "\nMY OWN STEP.\n")
    slug = upload(client, paper_pdf()).json()["slug"]
    before = set(app_cfg.runs_dir.iterdir())
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert", "model": "no-such-model"})
    assert r.status_code == 400 and "plan" not in launched
    assert set(app_cfg.runs_dir.iterdir()) == before


@needs_pdftotext
def test_the_companion_says_when_it_was_read_with_an_edited_method(client, app_cfg):
    from test_app import isolated_run

    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    isolated_run(st, app_cfg, slug, "M1", "2026-10-04T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    copy = app_cfg.runs_dir / "M1" / "method"
    copy.mkdir()
    (copy / "method.json").write_text(json.dumps({"edited": ["harness/SKILL-expert.md"]}))
    doc = client.get(f"/api/papers/{slug}/companion?run=M1").json()
    assert doc["by"]["own_method"] == ["harness/SKILL-expert.md"]
    assert client.get(f"/api/papers/{slug}").json()["runs"][0]["own_method"] is True


# --------------------------------------------------------------------------
# the explanations' instructions
# --------------------------------------------------------------------------


@needs_pdftotext
def test_edited_explanation_instructions_apply_to_the_next_question_and_keep_apart(
        client, app_cfg, monkeypatch):
    from test_explain import ask, fake_call

    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter))
    slug, paper = paper_with(app_cfg, client)
    ask(client, slug)
    assert explain.BRIEF in counter["prompt"]

    assert save(client, "explain/brief.md", "Answer in one plain sentence.").status_code == 200
    assert save(client, "explain/instructions.md", "MY OWN RULES.").status_code == 200
    _, events = ask(client, slug)
    # Asked again, not served from what the original instructions answered.
    assert counter["calls"] == 2 and events[-1]["type"] == "done"
    assert "Answer in one plain sentence." in counter["prompt"]
    assert counter["system"].startswith("MY OWN RULES.")
    keys = [i["key"] for i in explain.load_store(paper)]
    assert len(keys) == 2 and len(set(keys)) == 2

    # Reset, the original answer is the one kept from before.
    client.post("/api/method/reset")
    _, again = ask(client, slug)
    assert again[-1].get("cached") is True and counter["calls"] == 2


def test_a_key_terms_template_is_filled_in_for_each_level():
    q = explain.glossary_question("general", "For {reader}: {min} to {max}. {which} {{json}}")
    assert q.startswith("For a mathematician from outside the field: 12 to 28.")
    assert q.endswith("{json}")
    assert explain.glossary_question("expert") == explain.glossary_question(
        "expert", explain.GLOSSARY_TEMPLATE)
