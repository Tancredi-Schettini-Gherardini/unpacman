"""A reading that stopped before it was archived — unpacman was stopped in the
middle of it — is archived when it is next looked at, from a copy that
follows nothing, and its working folder is never used as it stands (security
check of 2026-11-02): a program the AI left running may still change that
folder, and opening the reading must not make unpacman's own code write or
read through a link there.

Every "outside" file is a dummy in the test's temporary folder; no AI is
called, and the LaTeX run is stood in for (only the files written before it
matter here).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from atelier import handover
from deslopify.app import library, readings
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, needs_pdftotext, paper_pdf, upload)

DUMMY = "# a dummy shell start-up file, made by the test\n"
SECRET = "DUMMY-SECRET outside the reading\n"
GUIDE = ("# Two papers on parity\n\n## Paper 1: On sums\n\n### Main results\n\n"
         "Its theorem.\n")


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "outside"
    folder.mkdir()
    for name in ("dummy-zprofile", "dummy-tex"):
        (folder / name).write_text(DUMMY)
    (folder / "secret.txt").write_text(SECRET)
    return folder


def _collection(client) -> str:
    a = upload(client, paper_pdf("On sums of even integers")).json()["slug"]
    b = upload(client, paper_pdf("On products of odd integers", words=420)).json()["slug"]
    return client.post("/api/collections", json={"papers": [a, b]}).json()["slug"]


def _stopped(st, app_cfg, slug, run_id, *, status="failed",
             harness="deslopify-multi-expert", level="collection-expert"):
    """A reading as unpacman leaves one it was stopped in the middle of:
    marked failed at the next start, its snapshot never archived."""
    st.db.upsert_run({"id": run_id, "problem": slug, "harness": harness,
                      "provider": "claude", "account": "default",
                      "model": "claude-haiku-4-5", "effort": "low", "mode": "standard",
                      "started_at": "2026-11-02T08:00:00+00:00", "status": status})
    rd = app_cfg.runs_dir / run_id
    reading = rd / "workspace" / "progress" / "reading"
    reading.mkdir(parents=True)
    (rd / "config.json").write_text(json.dumps({
        "spec": {"notes": f"reader level: {level}"},
        "resolved": {"cwd": str(rd / "workspace"),
                     "problem_root": str(app_cfg.problems_dir / slug)}}))
    (reading / "guide.md").write_text(GUIDE)
    return rd, reading


def _no_tex(monkeypatch):
    def compile_tex(tex, output):
        output.write_bytes(b"%PDF-1.4 a stand-in PDF\n%%EOF\n")
        return output

    monkeypatch.setattr("deslopify.summary_pdf._compile_tex", compile_tex)


@needs_pdftotext
@pytest.mark.parametrize("asked", ["the collection", "its PDF"])
def test_opening_a_stopped_collection_never_writes_through_its_snapshot(
        client, app_cfg, outside, monkeypatch, asked):
    slug = _collection(client)
    st = client.app.state.deslopify
    rd, reading = _stopped(st, app_cfg, slug, "S1")
    # What a program the AI left running could put there.
    (reading / "companion.md").symlink_to(outside / "dummy-zprofile")
    (reading / "companion.tex").symlink_to(outside / "dummy-tex")
    _no_tex(monkeypatch)
    url = (f"/api/papers/{slug}/companion" if asked == "the collection"
           else f"/api/papers/{slug}/file/companion.pdf?run=S1")
    r = client.get(url)
    assert r.status_code == 200, r.text
    # The files the links name are untouched; the companion was made in the
    # run's archive, from the guide the reading left.
    assert (outside / "dummy-zprofile").read_text() == DUMMY
    assert (outside / "dummy-tex").read_text() == DUMMY
    archive = rd / "reading"
    assert (archive / "guide.md").read_text() == GUIDE
    assert "Two papers on parity" in (archive / "companion.md").read_text()
    assert (archive / "companion.tex").is_file() and (archive / "companion.pdf").is_file()
    assert not any(p.is_symlink() for p in archive.rglob("*"))
    # The links were set aside, themselves, and never followed.
    kept = sorted((rd / handover.QUARANTINE).iterdir())
    assert sorted(p.name.split("-", 1)[1] for p in kept) == ["companion.md", "companion.tex"]
    assert all(p.is_symlink() for p in kept)
    assert sorted(p.name for p in reading.iterdir()) == ["guide.md"]


@needs_pdftotext
def test_a_stopped_reading_never_shows_a_file_its_snapshot_links_to(client, app_cfg, outside):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    rd, reading = _stopped(st, app_cfg, slug, "P1", harness="deslopify-expert", level="expert")
    (reading / "guide.md").unlink()
    (reading / "guide.md").symlink_to(outside / "secret.txt")
    r = client.get(f"/api/papers/{slug}/companion?level=expert")
    assert r.status_code == 404
    assert "DUMMY-SECRET" not in r.text
    assert "DUMMY-SECRET" not in client.get(f"/api/papers/{slug}").text
    assert (outside / "secret.txt").read_text() == SECRET



@needs_pdftotext
def test_a_link_on_the_way_to_a_stopped_reading_is_never_followed(client, app_cfg, outside,
                                                                    tmp_path):
    # Another agent's review, 2026-11-02: a link left in place of progress/,
    # leading to a folder of the user's that holds a reading/ of its own, was
    # followed — what was there copied into the archive, and the link and
    # pipe in it moved out into rejected-files/.
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    rd, reading = _stopped(st, app_cfg, slug, "W1", harness="deslopify-expert", level="expert")
    beyond = tmp_path / "beyond"
    (beyond / "reading").mkdir(parents=True)
    (beyond / "reading" / "guide.md").write_text("# Not this reading's\n\n" + SECRET)
    (beyond / "reading" / "a-link").symlink_to(outside / "secret.txt")
    os.mkfifo(beyond / "reading" / "a-pipe")
    workspace = rd / "workspace"
    (workspace / "progress").rename(workspace / "progress-real")
    (workspace / "progress").symlink_to(beyond, target_is_directory=True)
    paper = library.load_paper(app_cfg, slug)
    [found] = readings.all_readings(st.cfg, st.db, st.manager, paper)
    assert found.dir == rd / "reading" and not found.dir.exists()
    r = client.get(f"/api/papers/{slug}/companion?level=expert")
    assert r.status_code == 404 and "DUMMY-SECRET" not in r.text
    assert "DUMMY-SECRET" not in client.get(f"/api/papers/{slug}").text
    assert not (rd / "reading").exists() and not (rd / handover.QUARANTINE).exists()
    assert sorted(p.name for p in (beyond / "reading").iterdir()) == \
        ["a-link", "a-pipe", "guide.md"]
    assert (outside / "secret.txt").read_text() == SECRET

@needs_pdftotext
def test_a_running_reading_is_shown_from_its_snapshot_and_left_alone(client, app_cfg,
                                                                    monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    rd, reading = _stopped(st, app_cfg, slug, "R1", status="running",
                           harness="deslopify-expert", level="expert")
    handle = SimpleNamespace(run_id="R1", plan=SimpleNamespace(problem=SimpleNamespace(slug=slug)))
    monkeypatch.setattr(st.manager, "active", lambda: [handle])
    [found] = readings.all_readings(st.cfg, st.db, st.manager, library.load_paper(app_cfg, slug))
    assert found.live and found.dir == reading
    assert not (rd / "reading").exists()


@needs_pdftotext
@pytest.mark.parametrize("why", ["this unpacman is finishing it", "it is still marked running"])
def test_a_reading_not_settled_here_is_never_archived_from_its_snapshot(
        client, app_cfg, monkeypatch, why):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    status = "done" if why == "this unpacman is finishing it" else "running"
    rd, reading = _stopped(st, app_cfg, slug, "F1", status=status,
                           harness="deslopify-expert", level="expert")
    if why == "this unpacman is finishing it":
        # It archives its own reading, perhaps at this very moment.
        monkeypatch.setattr(st.manager, "get", lambda run_id: object() if run_id == "F1" else None)
    [found] = readings.all_readings(st.cfg, st.db, st.manager, library.load_paper(app_cfg, slug))
    assert not found.live
    assert found.dir == rd / "reading" and not found.dir.exists()
    assert client.get(f"/api/papers/{slug}/companion?level=expert").status_code == 404
    assert (reading / "guide.md").read_text() == GUIDE


@needs_pdftotext
def test_a_snapshot_that_cannot_be_archived_shows_nothing_and_is_not_tried_again(
        client, app_cfg, outside, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    rd, reading = _stopped(st, app_cfg, slug, "L1", harness="deslopify-expert", level="expert")
    reading.rename(reading.parent / "reading-real")
    reading.symlink_to(outside, target_is_directory=True)
    calls = []
    archive = handover.archive
    monkeypatch.setattr(handover, "archive", lambda *a, **k: calls.append(1) or archive(*a, **k))
    paper = library.load_paper(app_cfg, slug)
    for _ in range(3):
        [found] = readings.all_readings(st.cfg, st.db, st.manager, paper)
    assert calls == [1]
    assert not found.guide.exists() and not (rd / "reading").exists()
    assert client.get(f"/api/papers/{slug}/companion?level=expert").status_code == 404
    assert sorted(p.name for p in outside.iterdir()) == ["dummy-tex", "dummy-zprofile",
                                                         "secret.txt"]


@needs_pdftotext
def test_an_archive_that_fails_never_breaks_the_page(client, app_cfg, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    rd, reading = _stopped(st, app_cfg, slug, "B1", harness="deslopify-expert", level="expert")

    def broken(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(handover, "archive", broken)
    assert client.get(f"/api/papers/{slug}").status_code == 200
    assert client.get("/api/papers").status_code == 200
    assert not (rd / "reading").exists()
