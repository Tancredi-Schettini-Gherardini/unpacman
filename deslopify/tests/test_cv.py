"""The reader's mathematical background — their "mathematical CV" (cv.py).

Kept on this computer, written for by every reading of a paper or of several
(unless left out of one), and given to the questions before a Non-expert
reading and to explanations. No AI is called: readings are recorded rather
than run (`launched`), and the questions and explanations are stood in for.
"""

from __future__ import annotations

import json

import pytest

from deslopify.app import cv, explain, questions
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, paper_with, upload)
from test_explain import ask, fake_call as fake_explanation
from test_kinds import fake_call, question_set, run_for

MINE = "Differential topologist: smooth manifolds, surgery. Little analysis; no physics."


@pytest.fixture(autouse=True)
def own_var(monkeypatch):
    # The test's own var/, never one a test server names.
    monkeypatch.delenv("UNPACMAN_VAR", raising=False)


def test_a_background_is_kept_tidied_and_taken_back(client, app_cfg):
    assert client.get("/api/cv").json() == {"text": "", "updated": None, "max": cv.MAX_CHARS}
    r = client.put("/api/cv", json={"text": "  Topologist.\r\nSurgery.   \n\n\n\nNo analysis.  "})
    assert r.status_code == 200 and r.json()["text"] == "Topologist.\nSurgery.\n\nNo analysis."
    assert client.get("/api/cv").json()["text"] == "Topologist.\nSurgery.\n\nNo analysis."
    assert (app_cfg.root / "var" / "unpacman-cv.json").is_file()
    # Emptied is forgotten; and so is deleted.
    assert client.put("/api/cv", json={"text": "   "}).json()["text"] == ""
    assert not (app_cfg.root / "var" / "unpacman-cv.json").exists()
    client.put("/api/cv", json={"text": MINE})
    assert client.delete("/api/cv").json()["text"] == ""
    assert client.get("/api/cv").json()["text"] == ""


def test_a_background_too_long_is_refused_and_the_kept_one_stays(client):
    client.put("/api/cv", json={"text": MINE})
    r = client.put("/api/cv", json={"text": "x" * (cv.MAX_CHARS + 1)})
    assert r.status_code == 400 and "longer than unpacman keeps" in r.json()["detail"]
    assert client.get("/api/cv").json()["text"] == MINE
    assert client.put("/api/cv", json={"text": 3}).status_code == 400


def test_a_test_server_keeps_it_in_a_var_of_its_own(app_cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("UNPACMAN_VAR", str(tmp_path / "elsewhere"))
    cv.save(app_cfg, MINE)
    assert (tmp_path / "elsewhere" / "unpacman-cv.json").is_file()
    assert not (app_cfg.root / "var" / "unpacman-cv.json").exists()


@needs_pdftotext
def test_a_cv_file_is_read_for_the_reader_to_look_over_and_not_kept(client):
    pdf = paper_pdf("Curriculum vitae of A. Topologist")
    r = client.post("/api/cv/file", files={"file": ("cv.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["text"].startswith("Curriculum vitae of A. Topologist") and got["cut"] is False
    assert "  " not in got["text"]                          # the layout's padding undone
    assert client.get("/api/cv").json()["text"] == ""       # looked over first, then kept

    r = client.post("/api/cv/file", files={"file": ("cv.txt", b"One\r\n\r\n\r\nTwo   three\n", "text/plain")})
    assert r.json()["text"] == "One\n\nTwo three"
    long = ("A line of a long CV.\n" * 1200).encode()
    got = client.post("/api/cv/file", files={"file": ("cv.md", long, "text/markdown")}).json()
    assert got["cut"] is True and len(got["text"]) <= cv.MAX_CHARS and got["text"].endswith("CV.")

    for name, data, why in (("cv.docx", b"PK..", "PDF or a plain text"),
                            ("cv.pdf", b"not a pdf", "not a PDF"),
                            ("cv.txt", b"   \n", "No text could be read")):
        r = client.post("/api/cv/file", files={"file": (name, data, "application/octet-stream")})
        assert r.status_code == 400 and why in r.json()["detail"], (name, r.text)


@needs_pdftotext
def test_every_reading_of_a_paper_is_written_for_it_unless_left_out(client, launched, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    client.put("/api/cv", json={"text": MINE})
    r = client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    profile = plan.spec.reader_profile
    assert MINE in profile and "decide how much to explain" in profile   # harness/reader-cv.md
    assert "<reader_profile>" in plan.passes[0].prompt and MINE in plan.passes[0].prompt
    record = json.loads((plan.run_dir / "reader-profile.json").read_text())
    assert record == {"cv": MINE}

    # Left out of one reading: nothing of it is sent, or kept with the run.
    client.post(f"/api/papers/{slug}/read", json={"level": "general", "use_cv": False})
    plan = launched["plan"]
    assert plan.spec.reader_profile == ""
    assert not (plan.run_dir / "reader-profile.json").exists()


@needs_pdftotext
def test_the_background_comes_first_then_the_answers(client, launched, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    client.put("/api/cv", json={"text": MINE})
    monkeypatch.setattr(explain, "call", fake_call({}, [json.dumps(question_set())]))
    made = client.post(f"/api/papers/{slug}/questions", json={}).json()
    client.post(f"/api/papers/{slug}/read", json={
        "level": "general", "profile": {"key": made["key"], "answers": {"q1": "no"}}})
    profile = launched["plan"].spec.reader_profile
    assert profile.index(MINE) < profile.index("The reader's answers")
    record = json.loads((launched["plan"].run_dir / "reader-profile.json").read_text())
    assert record["cv"] == MINE and record["answered"] == 1


@needs_pdftotext
def test_several_papers_are_written_for_it_and_a_search_is_not(client, launched):
    one = upload(client, paper_pdf("On sums of even integers")).json()["slug"]
    two = upload(client, paper_pdf("On products of odd integers", 500)).json()["slug"]
    client.put("/api/cv", json={"text": MINE})
    together = client.post("/api/collections", json={"papers": [one, two]}).json()["slug"]
    assert client.post(f"/api/papers/{together}/read",
                       json={"level": "collection-expert"}).status_code == 200
    assert MINE in launched["plan"].spec.reader_profile
    search = client.post("/api/searches", json={"query": "exotic spheres"}).json()["slug"]
    assert client.post(f"/api/papers/{search}/read", json={"level": "search"}).status_code == 200
    assert launched["plan"].spec.reader_profile == ""


@needs_pdftotext
def test_the_readers_own_version_of_how_to_use_it_is_the_one_used(client, launched):
    slug = upload(client, paper_pdf()).json()["slug"]
    client.put("/api/cv", json={"text": MINE})
    original = client.get("/api/method/file", params={"path": "harness/reader-cv.md"}).json()
    assert original["title"] == "Using your background"
    client.put("/api/method/file", json={"path": "harness/reader-cv.md",
                                          "text": original["text"] + "\nALWAYS USE THEIR FIELD.\n"})
    client.post(f"/api/papers/{slug}/read", json={"level": "expert"})
    assert "ALWAYS USE THEIR FIELD." in launched["plan"].spec.reader_profile


@needs_pdftotext
def test_the_questions_are_chosen_for_it(client, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, [json.dumps(question_set())]))
    plain = client.post(f"/api/papers/{slug}/questions", json={}).json()
    assert "<reader_background>" not in counter["system"]
    client.put("/api/cv", json={"text": MINE})
    mine = client.post(f"/api/papers/{slug}/questions", json={}).json()
    assert "<reader_background>" in counter["system"] and MINE in counter["system"]
    # Questions for one background are not the questions for another...
    assert mine["key"] != plain["key"] and mine["cached"] is False
    # ...and, left out, it is the questions without it, already kept.
    again = client.post(f"/api/papers/{slug}/questions", json={"use_cv": False}).json()
    assert again["key"] == plain["key"] and again["cached"] is True


@needs_pdftotext
def test_explanations_are_pitched_to_it_and_kept_apart(client, app_cfg, monkeypatch):
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_explanation(counter))
    slug, _ = paper_with(app_cfg, client)
    ask(client, slug)
    assert "<reader_background>" not in counter["system"]
    client.put("/api/cv", json={"text": MINE})
    _, events = ask(client, slug)
    assert events[-1]["type"] == "done" and not events[-1].get("cached")
    assert MINE in counter["system"] and counter["calls"] == 2
    # The same question again, for the same background: kept, and free.
    _, events = ask(client, slug)
    assert events[-1].get("cached") is True and counter["calls"] == 2


def test_the_background_sits_in_what_stays_the_same_between_questions(app_cfg):
    paper = type("P", (), {"title": "T", "kind": "paper"})()
    system = explain.system_prompt(paper, ["one", "two"], "GUIDE", reader="WHO THEY ARE")
    assert system.index("WHO THEY ARE") < system.index("[page 1]") < system.index("GUIDE")
    assert "<reader_background>" not in explain.system_prompt(paper, ["one"], None)


@needs_pdftotext
def test_a_companion_says_it_was_written_for_the_background(client, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    reading = run_for(st, app_cfg, slug, "C1", "expert", "deslopify-expert",
                      guide="# Guide\n\n## Main results\n\nIt holds.\n")
    (reading.parent / "reader-profile.json").write_text(json.dumps({"cv": MINE}))
    doc = client.get(f"/api/papers/{slug}/companion?level=expert&run=C1").json()
    assert doc["by"]["profile"] == {"asked": 0, "answered": 0, "cv": True}
    answers = client.get(f"/api/papers/{slug}/answers?run=C1").json()
    assert answers["cv"] == MINE and answers["questions"] == []


@needs_pdftotext
def test_a_report_never_carries_it(client):
    client.put("/api/cv", json={"text": MINE})
    assert "Differential topologist" not in client.get("/api/report").text
