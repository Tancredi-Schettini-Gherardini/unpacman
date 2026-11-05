"""The first run's suggestion — highlight, right-click, ask — is for someone new
to unpacman only, appears once their first reading has started, and stays until
they have asked (firstask.py; 2026-10-20, 2026-10-23)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from deslopify.app import firstask
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, upload)


@pytest.fixture
def cfg(tmp_cfg, monkeypatch):
    papers = tmp_cfg.root / "papers"
    papers.mkdir()
    monkeypatch.delenv("UNPACMAN_VAR", raising=False)
    return replace(tmp_cfg, problems_dir=papers)


def paper(cfg, slug, asked=None):
    root = cfg.problems_dir / slug
    (root / ".atelier").mkdir(parents=True)
    (root / "deslopify.json").write_text("{}")
    if asked is not None:
        (root / ".atelier" / "explanations.json").write_text(json.dumps(asked))
    return root


def test_a_new_copy_shows_it_and_keeps_saying_so(cfg):
    assert firstask.state(cfg) == "pending" and firstask.pending(cfg)
    kept = json.loads((cfg.root / "var" / "unpacman-first-ask.json").read_text())
    assert kept["state"] == "pending"
    # Decided once: the first paper, and its first reading, change nothing.
    paper(cfg, "first-paper")
    assert firstask.pending(cfg)


def test_asking_once_ends_it_for_good(cfg):
    assert firstask.pending(cfg)
    firstask.asked(cfg)
    assert firstask.state(cfg) == "done"
    firstask.asked(cfg)
    assert firstask.state(cfg) == "done"


def test_a_reader_who_has_asked_before_is_never_shown_it(cfg):
    # As explain.save_item keeps answers.
    paper(cfg, "read-before", asked={"version": 1, "items": [
        {"key": "expert|m|residues", "markdown": "It means this."}]})
    assert firstask.state(cfg) == "done"


def test_a_copy_in_use_before_this_existed_is_past_its_first_run(cfg):
    paper(cfg, "read-before")
    paper(cfg, "answers-since-deleted", asked={"version": 1, "items": []})
    paper(cfg, "something-else-there", asked={"k": "v"})
    assert firstask.state(cfg) == "skipped"
    # Asking later is recorded all the same.
    firstask.asked(cfg)
    assert firstask.state(cfg) == "done"


def test_only_papers_count_and_a_damaged_record_is_decided_again(cfg):
    (cfg.problems_dir / "not-a-paper").mkdir()
    assert firstask.state(cfg) == "pending"
    firstask.store(cfg).write_text("not json")
    assert firstask.state(cfg) == "pending"
    firstask.store(cfg).write_text(json.dumps({"state": "something-else"}))
    assert firstask.state(cfg) == "pending"


def test_a_test_server_keeps_it_in_its_own_var(cfg, tmp_path, monkeypatch):
    own = tmp_path / "test-server-var"
    monkeypatch.setenv("UNPACMAN_VAR", str(own))
    firstask.asked(cfg)
    assert json.loads((own / "unpacman-first-ask.json").read_text())["state"] == "done"
    assert not (cfg.root / "var" / "unpacman-first-ask.json").exists()


def test_it_waits_for_the_first_reading_to_start(cfg):
    assert firstask.pending(cfg) and not firstask.started(cfg)
    firstask.begun(cfg)
    assert firstask.pending(cfg) and firstask.started(cfg)
    # Kept beside the state, for the next start of the server.
    kept = json.loads(firstask.store(cfg).read_text())
    assert kept["state"] == "pending" and kept["started"] is True
    firstask.begun(cfg)
    assert firstask.started(cfg)
    firstask.asked(cfg)
    assert firstask.state(cfg) == "done" and not firstask.pending(cfg)
    firstask.begun(cfg)
    assert firstask.state(cfg) == "done"


def test_a_reading_by_someone_not_new_changes_nothing(cfg):
    paper(cfg, "read-before")
    assert firstask.state(cfg) == "skipped"
    firstask.begun(cfg)
    assert firstask.state(cfg) == "skipped" and not firstask.started(cfg)


@needs_pdftotext
def test_a_reading_started_shows_it_and_a_search_or_a_refusal_does_not(client, launched):
    assert client.get("/api/first-ask").json() == {"pending": True, "started": False}
    search = client.post("/api/searches", json={"query": "Preprints on exotic spheres"}).json()
    r = client.post(f"/api/papers/{search['slug']}/read", json={"level": "search"})
    assert r.status_code == 200, r.text
    assert client.get("/api/first-ask").json()["started"] is False
    slug = upload(client, paper_pdf()).json()["slug"]
    assert client.post(f"/api/papers/{slug}/read", json={"level": "wizard"}).status_code == 400
    assert client.get("/api/first-ask").json()["started"] is False
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert r.status_code == 200, r.text
    assert client.get("/api/first-ask").json() == {"pending": True, "started": True}
