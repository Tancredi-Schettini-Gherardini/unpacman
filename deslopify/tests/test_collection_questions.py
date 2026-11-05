"""The questions before a Non-expert reading of several papers read together
(2026-10-26): asked as before a paper's, from every paper of the set, kept with
the set, and carried to every pass of its reading. A paper is asked exactly as
before. No provider is ever called (`fake_call`), and readings are recorded
rather than run (`launched`).
"""

from __future__ import annotations

import json
from pathlib import Path

from deslopify.app import explain, questions
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, upload)
from test_kinds import fake_call, question_set, run_for

TITLES = ("On sums of even integers", "On products of odd integers")


def collection(client, *titles: str, words: int = 400) -> str:
    slugs = [upload(client, paper_pdf(t, words=words)).json()["slug"] for t in titles]
    r = client.post("/api/collections", json={"papers": slugs})
    assert r.status_code == 200, r.text
    return r.json()["slug"]


@needs_pdftotext
def test_a_collection_is_asked_one_set_of_questions_from_every_paper(client, monkeypatch,
                                                                     app_cfg):
    slug = collection(client, *TITLES)
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, [json.dumps(question_set(12))]))
    r = client.post(f"/api/papers/{slug}/questions", json={})
    assert r.status_code == 200, r.text
    made = r.json()
    assert len(made["questions"]) == 12 and made["cached"] is False
    system = counter["system"]
    # The method's instructions, then what a set is, then every paper.
    assert "Between 10 and 40 questions" in system                # questionnaire.md
    assert system.index("Between 10 and 40") < system.index("Several papers are read together")
    assert "## Paper 1 of 2: On sums of even integers" in system
    assert "## Paper 2 of 2: On products of odd integers" in system
    assert "left out" not in system and "<companion>" not in system
    # Kept with the set, so that asking again is free.
    kept = json.loads((app_cfg.problems_dir / slug / ".atelier" / "questions.json").read_text())
    assert list(kept) == [made["key"]]
    again = client.post(f"/api/papers/{slug}/questions", json={}).json()
    assert again["cached"] is True and again["key"] == made["key"] and counter["calls"] == 1


@needs_pdftotext
def test_a_paper_is_asked_exactly_as_before(client, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, [json.dumps(question_set())]))
    made = client.post(f"/api/papers/{slug}/questions", json={}).json()
    assert "Several papers are read together" not in counter["system"]
    # Kept under the key it always had: questions already kept stay valid.
    st = client.app.state.deslopify
    assert made["key"] == questions.store_key(
        made["agent"]["model"], st.method.current("harness/questionnaire.md"))


def test_every_paper_of_a_long_set_keeps_its_beginning(monkeypatch):
    head = "## Paper {} of 3: {}\n\nFile: `refs/0{}.pdf` · 9 pages\n\n[page 1]\n"
    pages = [head.format(1, "Short", 1) + "a" * 100,
             head.format(2, "Long", 2) + "b" * 5000,
             head.format(3, "Longer", 3) + "c" * 9000]
    monkeypatch.setattr(explain, "MAX_PAPER_CHARS", 20_000)
    assert questions.whole_set(pages) is pages                    # it fits: as it is
    monkeypatch.setattr(explain, "MAX_PAPER_CHARS", 3_000)
    cut = questions.whole_set(pages)
    assert [p.split("\n", 1)[0] for p in cut] == [p.split("\n", 1)[0] for p in pages]
    assert cut[0] == pages[0]                                     # the short one whole
    assert all("[The rest of this paper was omitted" in p for p in cut[1:])
    assert len(cut[1]) == len(cut[2])                             # the long ones alike
    assert sum(map(len, cut)) <= 3_000
    # What an explanation would be given instead: the first papers whole and
    # the last left out — which is what the questions must not do.
    kept = [n for n, _ in explain._select_pages(pages, None)]
    assert kept != [1, 2, 3]
    assert [n for n, _ in explain._select_pages(cut, None)] == [1, 2, 3]


@needs_pdftotext
def test_a_long_set_is_asked_about_every_paper(client, monkeypatch):
    slug = collection(client, *TITLES)
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, [json.dumps(question_set())]))
    monkeypatch.setattr(explain, "MAX_PAPER_CHARS", 2_000)
    assert client.post(f"/api/papers/{slug}/questions", json={}).status_code == 200
    system = counter["system"]
    assert "## Paper 1 of 2: On sums" in system and "## Paper 2 of 2: On products" in system
    assert system.count("[The rest of this paper was omitted") == 2
    assert "left out" not in system


@needs_pdftotext
def test_a_collection_non_expert_reading_carries_the_answers_to_every_pass(
        client, launched, monkeypatch, app_cfg):
    slug = collection(client, *TITLES)
    monkeypatch.setattr(explain, "call", fake_call({}, [json.dumps(question_set())]))
    made = client.post(f"/api/papers/{slug}/questions", json={}).json()

    answers = {"q1": "no", "q2": "no", "q3": "yes", "q4": "roughly", "q99": "no"}
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "collection-nonexpert", "profile": {"key": made["key"], "answers": answers}})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert plan.harness.id == "deslopify-multi-nonexpert"
    profile = plan.spec.reader_profile
    assert "They do not know these" in profile and "**smooth manifold**" in profile
    assert "set how much is explained" in profile                # harness/reader-profile.md
    assert "<reader_profile>" in plan.passes[0].prompt
    record = json.loads((plan.run_dir / "reader-profile.json").read_text())
    assert record["answered"] == 4 and record["asked"] == 12 and "text" not in record
    assert "q99" not in record["answers"]

    # The next time, the reader starts from their answers — kept with the set.
    again = client.post(f"/api/papers/{slug}/questions", json={}).json()
    assert again["answers"] == {"q1": "no", "q2": "no", "q3": "yes", "q4": "roughly"}
    assert (app_cfg.problems_dir / slug / ".atelier" / "reader-answers.json").is_file()

    # An Expert reading of the set asks nothing, so its answers are not used.
    client.post(f"/api/papers/{slug}/read", json={
        "level": "collection-expert", "profile": {"key": made["key"], "answers": answers}})
    assert launched["plan"].harness.id == "deslopify-multi-expert"
    assert launched["plan"].spec.reader_profile == ""
    # Answers to questions no longer kept are refused rather than guessed at.
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "collection-nonexpert", "profile": {"key": "gone", "answers": answers}})
    assert r.status_code == 400 and "no longer" in r.json()["detail"]


def test_a_search_is_asked_nothing(client, launched):
    slug = client.post("/api/searches", json={"query": "exotic spheres"}).json()["slug"]
    r = client.post(f"/api/papers/{slug}/questions", json={})
    assert r.status_code == 400 and "several papers read together" in r.json()["detail"]
    client.post(f"/api/papers/{slug}/read", json={
        "level": "search", "profile": {"key": "any", "answers": {"q1": "no"}}})
    assert launched["plan"].spec.reader_profile == ""


@needs_pdftotext
def test_a_collection_companion_says_it_was_written_for_the_answers(client, monkeypatch,
                                                                   app_cfg):
    slug = collection(client, *TITLES)
    st = client.app.state.deslopify
    reading = run_for(st, app_cfg, slug, "C1", "collection-nonexpert",
                      "deslopify-multi-nonexpert", guide=(
                          "# Two papers on parity\n\n"
                          "## Paper 1: On sums\n\n### Main results\n\nIts theorem.\n"))
    qs = questions.parse(json.dumps(question_set()))
    (reading.parent / "reader-profile.json").write_text(json.dumps({
        "key": "k", "questions": qs, "answers": {"q1": "no", "q2": "yes"},
        "asked": 12, "answered": 2}))

    def fake_write(guide, markdown, tex, pdf, *, audience, paper_title=None):
        Path(markdown).write_text(Path(guide).read_text(encoding="utf-8"), encoding="utf-8")
        Path(tex).write_text("\\documentclass{article}\n", encoding="utf-8")
        Path(pdf).write_bytes(b"%PDF-1.4 collection companion\n%%EOF\n")
        return Path(markdown), Path(tex), Path(pdf)

    monkeypatch.setattr("deslopify.summary_pdf.write_companion", fake_write)
    doc = client.get(f"/api/papers/{slug}/companion?run=C1")
    assert doc.status_code == 200, doc.text
    assert doc.json()["by"]["profile"] == {"asked": 12, "answered": 2, "cv": False}
    runs = client.get(f"/api/papers/{slug}").json()["runs"]
    assert runs[0]["profile"] == {"asked": 12, "answered": 2, "cv": False}
    shown = client.get(f"/api/papers/{slug}/answers?run=C1").json()
    assert [q["id"] for q in shown["questions"]][:2] == ["q1", "q2"]
    assert shown["answers"] == {"q1": "no", "q2": "yes"}
