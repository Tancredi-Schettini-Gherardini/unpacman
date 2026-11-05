"""Deleting what has been read: a run, a paper's earlier runs, a paper (removal.py).

Runs are made up on disk and in the database, as the other reader tests make
them; nothing is read and nothing is spent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from deslopify.app import explain, library, removal
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, isolated_run, legacy_run, needs_pdftotext, paper_pdf, upload)


def slugged(client) -> str:
    return upload(client, paper_pdf()).json()["slug"]


def state(client):
    return client.app.state.deslopify


def runs_of(client, slug) -> list[str]:
    return [r["id"] for r in client.get(f"/api/papers/{slug}").json()["runs"]]


def keep_beside(app_cfg, slug, run_id):
    """What unpacman keeps beside the paper for one reading: a companion PDF
    rebuilt with its contents, and its key terms."""
    paper = app_cfg.problems_dir / slug
    kept = paper / ".atelier" / "companions"
    kept.mkdir(parents=True, exist_ok=True)
    (kept / f"{run_id}-abc123.pdf").write_bytes(b"%PDF-1.4 kept")
    store = paper / ".atelier" / "glossary.json"
    data = json.loads(store.read_text()) if store.is_file() else {"version": 1, "readings": {}}
    data["readings"][run_id] = {"terms": [{"term": "x", "markdown": "y"}]}
    data["readings"][f"{run_id}|p:abcd"] = {"terms": []}
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(data))


@needs_pdftotext
def test_one_run_goes_with_what_was_kept_for_it_and_nothing_else(client, app_cfg):
    slug = slugged(client)
    st = state(client)
    isolated_run(st, app_cfg, slug, "2026-10-01T10-00-00Z-aaaa", "2026-10-01T10:00:00+00:00",
                 "done", "claude-haiku-4-5", level="expert")
    isolated_run(st, app_cfg, slug, "2026-10-02T10-00-00Z-bbbb", "2026-10-02T10:00:00+00:00",
                 "done", "claude-haiku-4-5", level="expert")
    st.db.add_usage(run_id="2026-10-01T10-00-00Z-aaaa", agent_label="lead", ts="t",
                    account="default", model="m", tok_in=1, tok_out=1,
                    tok_cache_read=0, tok_cache_write=0, cost=0.1)
    keep_beside(app_cfg, slug, "2026-10-01T10-00-00Z-aaaa")
    keep_beside(app_cfg, slug, "2026-10-02T10-00-00Z-bbbb")

    space = client.get(f"/api/papers/{slug}/space").json()
    old = space["runs"]["2026-10-01T10-00-00Z-aaaa"]
    assert old > 0 and space["earlier"]["runs"] == ["2026-10-01T10-00-00Z-aaaa"]

    r = client.post(f"/api/papers/{slug}/runs/delete",
                    json={"run": "2026-10-01T10-00-00Z-aaaa"})
    assert r.status_code == 200 and r.json()["freed"] == old
    assert runs_of(client, slug) == ["2026-10-02T10-00-00Z-bbbb"]
    assert not (app_cfg.runs_dir / "2026-10-01T10-00-00Z-aaaa").exists()
    assert (app_cfg.runs_dir / "2026-10-02T10-00-00Z-bbbb").is_dir()
    assert not st.db.query("SELECT 1 FROM usage_samples WHERE run_id = ?",
                           ("2026-10-01T10-00-00Z-aaaa",))
    kept = app_cfg.problems_dir / slug / ".atelier" / "companions"
    assert [p.name for p in kept.iterdir()] == ["2026-10-02T10-00-00Z-bbbb-abc123.pdf"]
    terms = explain.load_glossaries(SimpleNamespace(root=app_cfg.problems_dir / slug))
    assert sorted(terms) == ["2026-10-02T10-00-00Z-bbbb", "2026-10-02T10-00-00Z-bbbb|p:abcd"]
    # The paper itself is untouched.
    assert (app_cfg.problems_dir / slug / "progress" / "source.pdf").is_file()


@needs_pdftotext
def test_deleting_the_companion_a_level_opens_with_falls_back_to_the_earlier(client, app_cfg):
    slug = slugged(client)
    st = state(client)
    isolated_run(st, app_cfg, slug, "R-old", "2026-10-01T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="general", guide="# Old\n\nOlder companion.\n")
    isolated_run(st, app_cfg, slug, "R-new", "2026-10-02T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="general", guide="# New\n\nNewer companion.\n")
    assert client.get(f"/api/papers/{slug}").json()["companion_run"] == "R-new"
    assert client.post(f"/api/papers/{slug}/runs/delete",
                       json={"run": "R-new"}).status_code == 200
    status = client.get(f"/api/papers/{slug}").json()
    assert status["companion_run"] == "R-old" and status["readings"]["general"]["state"] == "ready"


@needs_pdftotext
def test_a_run_still_reading_is_never_deleted(client, app_cfg, monkeypatch):
    slug = slugged(client)
    st = state(client)
    isolated_run(st, app_cfg, slug, "R-live", "2026-10-02T10:00:00+00:00", "running",
                 "claude-haiku-4-5", level="expert", archived=False)
    handle = SimpleNamespace(run_id="R-live", plan=SimpleNamespace(
        problem=SimpleNamespace(slug=slug), harness=SimpleNamespace(id="deslopify-expert")),
        state=SimpleNamespace(terminal=False), snapshot=lambda: {})
    monkeypatch.setattr(st.manager, "active", lambda: [handle])
    r = client.post(f"/api/papers/{slug}/runs/delete", json={"run": "R-live"})
    assert r.status_code == 409 and "Stop it first" in r.json()["detail"]
    assert client.delete(f"/api/papers/{slug}").status_code == 409
    assert (app_cfg.runs_dir / "R-live").is_dir()
    assert (app_cfg.problems_dir / slug).is_dir()


@needs_pdftotext
def test_only_this_papers_runs_can_be_deleted_through_it(client, app_cfg):
    first, second = slugged(client), upload(client, paper_pdf("Another title")).json()["slug"]
    st = state(client)
    isolated_run(st, app_cfg, second, "R-theirs", "2026-10-02T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    gone = f"/api/papers/{first}/runs/delete"
    assert client.post(gone, json={"run": "R-theirs"}).status_code == 404
    for sneaky in ("..", "../R-theirs", "R-theirs/..", "", None, 7):
        assert client.post(gone, json={"run": sneaky}).status_code == 404
    assert client.post(gone).status_code == 404
    assert (app_cfg.runs_dir / "R-theirs").is_dir()
    assert {p["slug"] for p in client.get("/api/papers").json()} == {first, second}


def test_a_run_folder_is_only_ever_one_directly_in_the_runs_folder(app_cfg):
    assert removal.run_folder(app_cfg, "2026-10-08T06-56-43Z-8b59") == (
        app_cfg.runs_dir / "2026-10-08T06-56-43Z-8b59").resolve()
    for bad in ("", "..", "../papers", "a/b", "/etc", ".hidden"):
        try:
            removal.run_folder(app_cfg, bad)
        except removal.RemovalError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


@needs_pdftotext
def test_tidying_keeps_the_companion_each_level_opens_with(client, app_cfg):
    slug = slugged(client)
    st = state(client)
    isolated_run(st, app_cfg, slug, "E1", "2026-10-01T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    isolated_run(st, app_cfg, slug, "E2", "2026-10-02T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    isolated_run(st, app_cfg, slug, "E3", "2026-10-03T10:00:00+00:00", "failed",
                 "claude-haiku-4-5", level="expert", guide=None)
    isolated_run(st, app_cfg, slug, "G1", "2026-10-01T11:00:00+00:00", "done",
                 "claude-haiku-4-5", level="general")
    plan = client.get(f"/api/papers/{slug}/space").json()["earlier"]
    assert sorted(plan["runs"]) == ["E1", "E3"] and plan["count"] == 2
    out = client.post(f"/api/papers/{slug}/tidy").json()
    assert out["deleted"] == 2
    assert sorted(runs_of(client, slug)) == ["E2", "G1"]
    status = client.get(f"/api/papers/{slug}").json()
    assert {lv: v["companion"] for lv, v in status["readings"].items()} == {
        "expert": "E2", "general": "G1"}


@needs_pdftotext
def test_a_paper_goes_with_every_run_that_read_it(client, app_cfg):
    slug = slugged(client)
    other = upload(client, paper_pdf("Another title")).json()["slug"]
    st = state(client)
    for rid, level in (("P1", "expert"), ("P2", "general")):
        isolated_run(st, app_cfg, slug, rid, "2026-10-01T10:00:00+00:00", "done",
                     "claude-haiku-4-5", level=level)
    isolated_run(st, app_cfg, other, "O1", "2026-10-01T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    r = client.delete(f"/api/papers/{slug}")
    assert r.status_code == 200 and r.json()["freed"] > 0
    assert not (app_cfg.problems_dir / slug).exists()
    assert not (app_cfg.runs_dir / "P1").exists() and not (app_cfg.runs_dir / "P2").exists()
    assert not st.db.query("SELECT 1 FROM runs WHERE problem = ?", (slug,))
    assert (app_cfg.runs_dir / "O1").is_dir() and runs_of(client, other) == ["O1"]
    assert [p["slug"] for p in client.get("/api/papers").json()] == [other]


@needs_pdftotext
def test_a_reading_from_before_isolation_takes_the_shared_companion_with_it(client, app_cfg):
    """Readings made before 2026-10-02 shared the paper's own progress/reading;
    the one credited with it takes it along, so that no older run is credited
    with files it did not write."""
    slug = slugged(client)
    st = state(client)
    reading = app_cfg.problems_dir / slug / "progress" / "reading"
    reading.mkdir(parents=True)
    (reading / "guide.md").write_text("# Shared\n\nThe shared companion.\n")
    legacy_run(st, app_cfg, slug, "L1", "2026-09-20T10:00:00+00:00", "done",
               "claude-haiku-4-5", drafted=True, level="expert")
    legacy_run(st, app_cfg, slug, "L2", "2026-09-21T10:00:00+00:00", "done",
               "claude-haiku-4-5", drafted=True, level="expert")
    assert client.get(f"/api/papers/{slug}").json()["companion_run"] == "L2"
    assert client.post(f"/api/papers/{slug}/runs/delete",
                       json={"run": "L2"}).status_code == 200
    assert not reading.exists()
    status = client.get(f"/api/papers/{slug}").json()
    assert status["companion"] is False and runs_of(client, slug) == ["L1"]


@needs_pdftotext
def test_every_papers_earlier_runs_at_once(client, app_cfg):
    one, two = slugged(client), upload(client, paper_pdf("Another title")).json()["slug"]
    st = state(client)
    for slug, prefix in ((one, "A"), (two, "B")):
        isolated_run(st, app_cfg, slug, f"{prefix}1", "2026-10-01T10:00:00+00:00", "done",
                     "claude-haiku-4-5", level="expert")
        isolated_run(st, app_cfg, slug, f"{prefix}2", "2026-10-02T10:00:00+00:00", "done",
                     "claude-haiku-4-5", level="expert")
    space = client.get("/api/space").json()
    assert space["papers"] == 2 and space["runs"] == 4 and space["earlier"]["count"] == 2
    assert space["bytes"] > space["earlier"]["bytes"] > 0
    out = client.post("/api/tidy").json()
    assert out["deleted"] == 2 and out["skipped"] == []
    assert runs_of(client, one) == ["A2"] and runs_of(client, two) == ["B2"]
    assert client.get("/api/space").json()["earlier"]["count"] == 0


@needs_pdftotext
def test_a_run_keeps_its_number_when_an_earlier_one_is_deleted(client, app_cfg):
    slug = slugged(client)
    st = state(client)

    def numbers():
        return {r["id"]: r["number"] for r in client.get(f"/api/papers/{slug}").json()["runs"]}

    for i, rid in enumerate(("N1", "N2", "N3"), start=1):
        isolated_run(st, app_cfg, slug, rid, f"2026-10-0{i}T10:00:00+00:00", "done",
                     "claude-haiku-4-5", level="expert")
    assert numbers() == {"N1": 1, "N2": 2, "N3": 3}
    client.post(f"/api/papers/{slug}/runs/delete", json={"run": "N1"})
    assert numbers() == {"N2": 2, "N3": 3}
    # The newest deleted: its number is not given to the next run.
    client.post(f"/api/papers/{slug}/runs/delete", json={"run": "N3"})
    isolated_run(st, app_cfg, slug, "N4", "2026-10-04T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    assert numbers() == {"N2": 2, "N4": 4}


@needs_pdftotext
def test_a_lab_problem_of_the_same_name_keeps_its_runs(client, app_cfg, tmp_path):
    """The database knows a run's folder only by its name. A run of the lab's,
    on a problem that happens to share the paper's name, read another folder —
    still there — and is never deleted from the reader."""
    slug = slugged(client)
    st = state(client)
    isolated_run(st, app_cfg, slug, "MINE", "2026-10-02T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    lab_problem = tmp_path / "problems" / slug
    lab_problem.mkdir(parents=True)
    st.db.upsert_run({"id": "LAB", "problem": slug, "harness": "solo", "provider": "claude",
                      "account": "default", "model": "claude-haiku-4-5", "effort": "low",
                      "mode": "standard", "started_at": "2026-10-03T10:00:00+00:00",
                      "status": "done"})
    lab_run = app_cfg.runs_dir / "LAB"
    lab_run.mkdir(parents=True)
    (lab_run / "config.json").write_text(json.dumps({
        "resolved": {"problem_root": str(lab_problem), "cwd": str(lab_problem)}}))
    assert removal.belongs(app_cfg, library.load_paper(app_cfg, slug), "LAB", "solo") is False
    assert client.delete(f"/api/papers/{slug}").status_code == 200
    assert not (app_cfg.runs_dir / "MINE").exists()
    assert lab_run.is_dir() and st.db.query("SELECT 1 FROM runs WHERE id = 'LAB'")


@needs_pdftotext
def test_a_reading_whose_folder_has_moved_still_belongs(client, app_cfg, tmp_path):
    """A reading made on a copy since deleted (a test, a library moved) names a
    folder that is gone: it is still this paper's, being one of unpacman's."""
    slug = slugged(client)
    st = state(client)
    isolated_run(st, app_cfg, slug, "MOVED", "2026-10-02T10:00:00+00:00", "done",
                 "claude-haiku-4-5", level="expert")
    config = app_cfg.runs_dir / "MOVED" / "config.json"
    record = json.loads(config.read_text())
    record["resolved"]["problem_root"] = str(tmp_path / "gone" / slug)
    config.write_text(json.dumps(record))
    r = client.post(f"/api/papers/{slug}/runs/delete", json={"run": "MOVED"})
    assert r.status_code == 200 and not (app_cfg.runs_dir / "MOVED").exists()
