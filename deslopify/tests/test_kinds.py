"""Tests for the 2026-10-07 round: the questions asked before a Non-expert
reading, several papers read together, and a search of the literature.

No provider is ever called and nothing is fetched from the internet: the AI's
answers and the downloads are stood in for, and readings are recorded rather
than run (`launched`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deslopify.app import explain, fetch, library, questions
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, launched, needs_pdftotext, paper_pdf, upload)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def question_set(n: int = 12) -> dict:
    """What an AI might answer: n questions, the second following up the first."""
    items = [{"id": "q1", "kind": "definition", "topic": "Riemannian metric",
              "question": "Do you know the definition of a Riemannian metric?",
              "where": "§2, p. 3", "parent": None, "ask_if": None},
             {"id": "q2", "kind": "definition", "topic": "smooth manifold",
              "question": "Do you know the definition of a smooth manifold?",
              "where": "", "parent": "q1", "ask_if": ["no", "roughly"]}]
    for i in range(3, n + 1):
        items.append({"id": f"q{i}", "kind": "theorem", "topic": f"theorem {i}",
                      "question": f"Are you familiar with theorem {i}?",
                      "where": "", "parent": None, "ask_if": None})
    return {"questions": items}


def fake_call(counter: dict, answers: list[str]):
    """Stands in for `explain.call`, answering each call with the next text."""
    async def call(cfg, agent, system, prompt, **kw):
        counter["calls"] = counter.get("calls", 0) + 1
        counter.setdefault("prompts", []).append(prompt)
        counter["system"] = system
        text = answers[min(counter["calls"], len(answers)) - 1]
        yield "answer", explain.Answer(text, 0.01, {"tok_out": 99})
    return call


def run_for(st, app_cfg, slug, run_id, level, harness, *, status="done",
            guide: str | None = None, results: list | None = None,
            started="2026-10-07T10:00:00+00:00"):
    """A finished single-pass run, as a joint reading or a search leaves it:
    archived, and with no record of passes."""
    st.db.upsert_run({"id": run_id, "problem": slug, "harness": harness,
                      "provider": "claude", "account": "default",
                      "model": "claude-haiku-4-5", "effort": "low", "mode": "standard",
                      "started_at": started, "status": status})
    rd = app_cfg.runs_dir / run_id
    (rd / "reading").mkdir(parents=True)
    (rd / "config.json").write_text(json.dumps({
        "spec": {"notes": f"reader level: {level}"},
        "resolved": {"cwd": str(rd / "workspace"),
                     "problem_root": str(app_cfg.problems_dir / slug)}}))
    if guide is not None:
        (rd / "reading" / "guide.md").write_text(guide, encoding="utf-8")
    if results is not None:
        (rd / "reading" / "results.json").write_text(json.dumps({"results": results}))
    return rd / "reading"


# --------------------------------------------------------------------------
# the questions before a Non-expert reading
# --------------------------------------------------------------------------


def test_questions_are_read_with_their_follow_ups_and_nothing_malformed():
    items = question_set(4)["questions"] + [
        {"id": "q2", "question": "A repeated id."},
        {"id": "q9", "question": "Its parent does not exist.", "parent": "q99"},
        {"id": "", "question": "No id."},
        {"id": "q10", "question": "   "},
        {"id": "q11", "kind": "nonsense", "question": "Do you know A?", "parent": "q2"},
        {"id": "q12", "question": "Do you know B?", "parent": "q11"},
        {"id": "q13", "question": "Four levels deep.", "parent": "q12"},
        {"id": "q14", "question": "Below a dropped one.", "parent": "q13"},
    ]
    text = "Here they are:\n```json\n" + json.dumps({"questions": items}) + "\n```\nDone."
    got = questions.parse(text)
    # q11 follows q2, which follows q1: three levels, the most there may be. So
    # q12 is too deep, and q13 and q14 follow it into nothing.
    assert [q["id"] for q in got] == ["q1", "q2", "q3", "q4", "q11"]
    q11 = next(q for q in got if q["id"] == "q11")
    assert q11["kind"] == "object" and q11["ask_if"] == ["no", "roughly"]
    assert next(q for q in got if q["id"] == "q1")["ask_if"] == []
    assert questions.parse("no JSON here") == []
    # Never more than forty.
    assert len(questions.parse(json.dumps(question_set(55)))) == questions.MAX_QUESTIONS


def test_only_questions_shown_are_answered_and_the_reader_is_described():
    qs = questions.parse(json.dumps(question_set(4)))
    # q2 follows up q1 only when q1 is not known well.
    assert [q["id"] for q in questions.asked(qs, {"q1": "yes"})] == ["q1", "q3", "q4"]
    assert "q2" in [q["id"] for q in questions.asked(qs, {"q1": "no"})]
    # An answer to a question never shown is not an answer.
    assert questions.clean_answers(qs, {"q1": "yes", "q2": "no", "q3": "maybe"}) \
        == {"q1": "yes"}

    answers = questions.clean_answers(qs, {"q1": "no", "q2": "roughly", "q3": "yes"})
    text = questions.profile_text("HOW TO USE THEM", qs, answers)
    assert text.startswith("HOW TO USE THEM")
    assert "4 questions were asked; 3 answered." in text
    do_not, roughly = text.index("They do not know these"), text.index("roughly")
    unanswered, well = text.index("did not answer"), text.index("know these well")
    assert do_not < roughly < unanswered < well
    assert "**smooth manifold** (definition) — asked because they answered “no” " \
           "about Riemannian metric" in text
    assert "**Riemannian metric** (definition, §2, p. 3)" in text


@needs_pdftotext
def test_the_questions_are_made_once_kept_and_asked_again_when_too_few(client, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(
        counter, [json.dumps(question_set(3)), json.dumps(question_set(12))]))
    first = client.post(f"/api/papers/{slug}/questions", json={})
    assert first.status_code == 200, first.text
    made = first.json()
    assert len(made["questions"]) == 12 and counter["calls"] == 2
    assert "only 3 usable" in counter["prompts"][1]
    assert made["cost"] == 0.02 and made["cached"] is False and made["answers"] == {}
    assert made["names"] == {"yes": "Yes", "roughly": "Roughly", "no": "No"}
    # The paper is sent; the method's instructions are what the AI is told.
    assert "Between 10 and 40 questions" in counter["system"]
    assert "<companion>" not in counter["system"]
    again = client.post(f"/api/papers/{slug}/questions", json={}).json()
    assert again["cached"] is True and again["key"] == made["key"] and counter["calls"] == 2


@needs_pdftotext
def test_questions_that_cannot_be_made_say_so(client, monkeypatch):
    slug = upload(client, paper_pdf()).json()["slug"]
    counter: dict = {}
    monkeypatch.setattr(explain, "call", fake_call(counter, ["I would rather not."]))
    r = client.post(f"/api/papers/{slug}/questions", json={})
    assert r.status_code == 502 and "could not be made" in r.json()["detail"]
    client.ready.signed_in = False
    r = client.post(f"/api/papers/{slug}/questions", json={})
    assert r.status_code == 409 and "not signed in" in r.json()["detail"]
    # LaTeX is not needed to ask questions.
    client.ready.signed_in, client.ready.tex = True, False
    monkeypatch.setattr(explain, "call", fake_call(counter, [json.dumps(question_set())]))
    assert client.post(f"/api/papers/{slug}/questions", json={}).status_code == 200


@needs_pdftotext
def test_a_non_expert_reading_carries_the_answers_to_every_pass(client, launched,
                                                                monkeypatch, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    monkeypatch.setattr(explain, "call", fake_call({}, [json.dumps(question_set())]))
    made = client.post(f"/api/papers/{slug}/questions", json={}).json()

    answers = {"q1": "no", "q2": "no", "q3": "yes", "q4": "roughly", "q99": "no"}
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "general", "profile": {"key": made["key"], "answers": answers}})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert plan.harness.id == "deslopify-nonexpert"
    profile = plan.spec.reader_profile
    assert "They do not know these" in profile and "**smooth manifold**" in profile
    assert "set how much is explained" in profile        # harness/reader-profile.md
    assert "<reader_profile>" in plan.passes[0].prompt
    record = json.loads((plan.run_dir / "reader-profile.json").read_text())
    assert record["answered"] == 4 and record["asked"] == 12 and "text" not in record
    assert "q99" not in record["answers"]

    # The next time, the reader starts from their answers.
    again = client.post(f"/api/papers/{slug}/questions", json={}).json()
    assert again["answers"] == {"q1": "no", "q2": "no", "q3": "yes", "q4": "roughly"}

    # Answers sent with an Expert reading are not used: it asks nothing.
    client.post(f"/api/papers/{slug}/read", json={
        "level": "expert", "profile": {"key": made["key"], "answers": answers}})
    assert launched["plan"].spec.reader_profile == ""
    # Answers to questions no longer kept are refused rather than guessed at.
    r = client.post(f"/api/papers/{slug}/read", json={
        "level": "general", "profile": {"key": "gone", "answers": answers}})
    assert r.status_code == 400 and "no longer" in r.json()["detail"]


@needs_pdftotext
def test_a_companion_says_it_was_written_for_the_answers(client, monkeypatch, app_cfg):
    slug = upload(client, paper_pdf()).json()["slug"]
    st = client.app.state.deslopify
    reading = run_for(st, app_cfg, slug, "N1", "general", "deslopify-nonexpert",
                      guide="# Guide\n\n## Main results\n\nIt holds.\n")
    qs = questions.parse(json.dumps(question_set()))
    (reading.parent / "reader-profile.json").write_text(json.dumps({
        "key": "k", "questions": qs, "answers": {"q1": "no", "q2": "yes"},
        "asked": 12, "answered": 2}))
    doc = client.get(f"/api/papers/{slug}/companion?run=N1").json()
    assert doc["by"]["profile"] == {"asked": 12, "answered": 2, "cv": False}
    runs = client.get(f"/api/papers/{slug}").json()["runs"]
    assert runs[0]["profile"] == {"asked": 12, "answered": 2, "cv": False}
    shown = client.get(f"/api/papers/{slug}/answers?run=N1").json()
    assert [q["id"] for q in shown["questions"]][:2] == ["q1", "q2"]
    assert shown["answers"] == {"q1": "no", "q2": "yes"}
    assert client.get(f"/api/papers/{slug}/answers?run=NOPE").status_code == 404


# --------------------------------------------------------------------------
# several papers, read together
# --------------------------------------------------------------------------


@needs_pdftotext
def test_several_papers_become_one_collection_read_together(client, launched, app_cfg):
    a = upload(client, paper_pdf("On sums of even integers")).json()["slug"]
    b = upload(client, paper_pdf("On products of odd integers", words=420)).json()["slug"]
    r = client.post("/api/collections", json={"papers": [a, b]})
    assert r.status_code == 200, r.text
    together = r.json()
    assert together["kind"] == "collection" and together["existing"] is False
    assert [m["slug"] for m in together["members"]] == [a, b]
    # The same papers, in any order, are the same collection.
    again = client.post("/api/collections", json={"papers": [b, a]}).json()
    assert again["slug"] == together["slug"] and again["existing"] is True

    root = app_cfg.problems_dir / together["slug"]
    text = (root / "progress" / "source.md").read_text()
    assert "## Paper 1 of 2: On sums of even integers" in text
    assert "## Paper 2 of 2: On products of odd integers" in text and "[page 1]" in text
    assert sorted(p.name for p in (root / "refs").iterdir()) == [
        f"01-{a}.pdf", f"02-{b}.pdf"]
    pdf = client.get(f"/api/papers/{together['slug']}/member/1")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get(f"/api/papers/{together['slug']}/member/2").status_code == 404
    # A collection has no source.pdf of its own to serve.
    assert client.get(f"/api/papers/{together['slug']}/file/source.pdf").status_code == 404

    # A collection offers the two audiences and will typeset its guide, so it
    # waits for LaTeX just as a one-paper companion does.
    client.ready.tex = False
    r = client.post(f"/api/papers/{together['slug']}/read",
                    json={"level": "collection-expert"})
    assert r.status_code == 409 and "LaTeX" in r.json()["detail"]
    client.ready.tex = True
    r = client.post(f"/api/papers/{together['slug']}/read",
                    json={"level": "collection-expert"})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert plan.harness.id == "deslopify-multi-expert" and plan.harness.contract == "guide"
    assert len(plan.passes) == 1
    assert "Read the papers gathered in `progress/source.md`" in plan.passes[0].prompt
    assert "## The non-negotiable collection shape" in plan.system_append
    assert "only **Main results** (pure) or **Main findings**" in plan.system_append
    assert "### Main results" in plan.system_append
    assert "### Historical context" in plan.system_append
    assert "Do not add **Main ideas**" in plan.system_append
    assert client.post(f"/api/papers/{together['slug']}/read",
                       json={"level": "expert"}).status_code == 400


@needs_pdftotext
def test_collection_source_pdf_page_limit_is_inclusive(client, app_cfg, monkeypatch):
    a = upload(client, paper_pdf("On sums of even integers")).json()["slug"]
    b = upload(client, paper_pdf("On products of odd integers", words=420)).json()["slug"]
    counts = {a: 5_000, b: 5_001}
    original = library.source_page_count
    monkeypatch.setattr(library, "source_page_count",
                        lambda paper: counts.get(paper.slug, original(paper)))

    refused = client.post("/api/collections", json={"papers": [a, b]})
    assert refused.status_code == 400
    assert "10,001" in refused.json()["detail"]
    assert len(list(app_cfg.problems_dir.iterdir())) == 2

    counts[b] = 5_000
    accepted = client.post("/api/collections", json={"papers": [a, b]})
    assert accepted.status_code == 200, accepted.text

    counts[b] = 5_001
    existing = client.post("/api/collections", json={"papers": [a, b]})
    assert existing.status_code == 400


@needs_pdftotext
def test_existing_collection_over_page_limit_cannot_start_reading(
        client, launched, monkeypatch):
    a = upload(client, paper_pdf("On sums of even integers")).json()["slug"]
    b = upload(client, paper_pdf("On products of odd integers", words=420)).json()["slug"]
    slug = client.post("/api/collections", json={"papers": [a, b]}).json()["slug"]
    original = library.source_page_count
    monkeypatch.setattr(library, "source_page_count",
                        lambda paper: 10_001 if paper.slug == slug else original(paper))

    refused = client.post(f"/api/papers/{slug}/read",
                          json={"level": "collection-expert"})
    assert refused.status_code == 400
    assert "10,001" in refused.json()["detail"]
    assert not launched


@needs_pdftotext
def test_a_collection_needs_two_different_papers(client):
    a = upload(client, paper_pdf()).json()["slug"]
    for papers in ([a], [a, a]):
        r = client.post("/api/collections", json={"papers": papers})
        assert r.status_code == 400 and "two different" in r.json()["detail"]
    assert client.post("/api/collections", json={"papers": "x"}).status_code == 400
    assert client.post("/api/collections", json={"papers": [a, "nope"]}).status_code == 404


@needs_pdftotext
def test_an_already_uploaded_paper_can_start_a_new_collection(client):
    data = paper_pdf("A paper already in the library")
    a = upload(client, data).json()
    duplicate = upload(client, data, "renamed-copy.pdf").json()
    b = upload(client, paper_pdf("A newly added paper", words=420)).json()

    assert duplicate["existing"] is True and duplicate["slug"] == a["slug"]
    r = client.post("/api/collections", json={"papers": [duplicate["slug"], b["slug"]]})
    assert r.status_code == 200, r.text
    assert [member["slug"] for member in r.json()["members"]] == [a["slug"], b["slug"]]


@needs_pdftotext
@pytest.mark.parametrize("connected", [False, True])
def test_a_joint_reading_is_shown_in_its_own_sections(client, app_cfg, connected):
    a = upload(client, paper_pdf("On sums of even integers")).json()["slug"]
    b = upload(client, paper_pdf("On products of odd integers", words=420)).json()["slug"]
    slug = client.post("/api/collections", json={"papers": [a, b]}).json()["slug"]
    st = client.app.state.deslopify
    connections = (
        "## How the papers are related\n\n### Parity preserved by operations\n\n"
        "Both papers describe how arithmetic operations preserve parity.\n\n"
        if connected else "")
    run_for(st, app_cfg, slug, "T1", "collection-expert", "deslopify-multi-expert", guide=(
        "# Two papers on parity\n\n" + connections +
        "<a id=\"paper-1-on-sums\"></a>\n\n"
        "## Paper 1: ``On sums''\n\n### Main results\n\nIts theorem.\n\n"
        "### Historical context\n\nA detail.\n\n### Bibliography\n\n- [A] A source.\n\n"
        "## Paper 2: ``On products''\n\n### Main results\n\nIts theorem.\n\n"
        "### Historical context\n\nA detail.\n\n### Bibliography\n\n- [B] Another source.\n"))
    doc = client.get(f"/api/papers/{slug}/companion").json()
    assert "Generated on " in doc["preamble"]
    assert "Generated on " in client.get(f"/api/papers/{slug}/file/guide.md").text
    assert [s["label"] for s in doc["sections"]] == (
        (["How the papers are related"] if connected else []) + ["Paper 1: “On sums”", "Paper 2: “On products”"])
    assert "Historical context" in doc["sections"][-2]["html"]
    assert "&lt;a id=" not in "".join(s["html"] for s in doc["sections"])
    assert doc["missing"] == [] and "source.pdf" not in doc["files"]
    assert doc["level"] == "collection-expert"

    status = client.get(f"/api/papers/{slug}").json()
    # One pass, which finished: done, not "Not reached".
    assert [(s["title"], s["state"]) for s in status["steps"]] == [
        ("Writing the collection companion", "done")]
    [row] = [p for p in client.get("/api/papers").json() if p["slug"] == slug]
    assert row["kind"] == "collection" and row["state"] == "ready"
    assert row["levels"] == ["collection-expert"]


@needs_pdftotext
def test_a_collection_guide_is_typeset_and_offered_as_a_pdf(client, app_cfg, monkeypatch):
    a = upload(client, paper_pdf("On sums of even integers")).json()["slug"]
    b = upload(client, paper_pdf("On products of odd integers", words=420)).json()["slug"]
    slug = client.post("/api/collections", json={"papers": [a, b]}).json()["slug"]
    st = client.app.state.deslopify
    reading = run_for(st, app_cfg, slug, "T2", "collection-nonexpert",
                      "deslopify-multi-nonexpert", guide=(
                          "# Two papers on parity\n\n"
                          "## Contents\n\n1. Paper 1\n\n"
                          "<a id=\"paper-1\"></a>\n\n"
                          "## Paper 1: On sums\n\n### Main results\n\nIts theorem.\n"))
    # A PDF generated before the anchor cleanup is replaced once; its original
    # guide remains the archival run record.
    (reading / "companion.pdf").write_bytes(b"%PDF-1.4 old collection\n%%EOF\n")
    (reading / "companion.md").write_text((reading / "guide.md").read_text(encoding="utf-8"),
                                            encoding="utf-8")
    calls = []

    def fake_write(guide, markdown, tex, pdf, *, audience, paper_title=None):
        calls.append((Path(guide), audience, paper_title))
        Path(markdown).write_text(Path(guide).read_text(encoding="utf-8"), encoding="utf-8")
        # The generated source declares its contents, so the download route
        # can serve this stored PDF without trying to invoke real LaTeX.
        Path(tex).write_text("\\documentclass{article}\n\\tableofcontents\n",
                             encoding="utf-8")
        Path(pdf).write_bytes(b"%PDF-1.4 collection companion\n%%EOF\n")
        return Path(markdown), Path(tex), Path(pdf)

    monkeypatch.setattr("deslopify.summary_pdf.write_companion", fake_write)
    doc = client.get(f"/api/papers/{slug}/companion?run=T2")
    assert doc.status_code == 200, doc.text
    assert {"companion.pdf", "companion.tex"} <= set(doc.json()["files"])
    assert calls == [(reading / "guide.md", "non-expert",
                      "On sums of even integers · On products of odd integers")]

    pdf = client.get(f"/api/papers/{slug}/file/companion.pdf?run=T2")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-1.4 collection")


# --------------------------------------------------------------------------
# a search of the literature
# --------------------------------------------------------------------------


def test_a_search_is_made_once_per_question_and_asks_it_with_the_date(client, launched,
                                                                       app_cfg):
    r = client.post("/api/searches", json={"query": "  Latest arXiv preprints on exotic spheres "})
    assert r.status_code == 200, r.text
    search = r.json()
    assert search["kind"] == "search" and search["existing"] is False
    assert search["query"] == "Latest arXiv preprints on exotic spheres"
    again = client.post("/api/searches",
                        json={"query": "latest arxiv   preprints on EXOTIC spheres"}).json()
    assert again["slug"] == search["slug"] and again["existing"] is True
    assert client.post("/api/searches", json={"query": "x"}).status_code == 400

    source = app_cfg.problems_dir / search["slug"] / "progress" / "source.md"
    assert "Latest arXiv preprints on exotic spheres" in source.read_text()
    assert "Asked on " in source.read_text()

    # A search needs no LaTeX; the question may be changed as it is run again.
    client.ready.tex = False
    r = client.post(f"/api/papers/{search['slug']}/read",
                    json={"level": "search", "query": "Preprints on exotic 7-spheres"})
    assert r.status_code == 200, r.text
    plan = launched["plan"]
    assert plan.harness.id == "deslopify-search" and plan.harness.contract == "guide"
    assert "Preprints on exotic 7-spheres" in plan.passes[0].prompt
    assert "carry out the search it asks for" in plan.passes[0].prompt
    assert client.get(f"/api/papers/{search['slug']}").json()["paper"]["title"] \
        == "Preprints on exotic 7-spheres"


def test_search_results_are_kept_to_what_the_page_shows(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps({"results": [
        {"title": "A paper", "authors": "A. One, B. Two and C. Three", "year": "2026",
         "arxiv": "arXiv:2609.01234v2", "summary": "  It   proves   things. "},
        {"title": "Published", "authors": ["D. Four"], "year": 1958,
         "doi": "https://doi.org/10.2307/1970128", "url": "javascript:alert(1)"},
        {"title": "", "arxiv": "2609.00001"},
        {"title": "Odd ids", "arxiv": "not-an-id", "doi": "10.x", "year": 99999,
         "pdf": "ftp://example.org/x.pdf"},
        "not an object",
    ]}))
    got = library.read_results(tmp_path)
    assert [r["title"] for r in got] == ["A paper", "Published", "Odd ids"]
    first, second, odd = got
    assert first["authors"] == ["A. One", "B. Two", "C. Three"] and first["year"] == 2026
    assert first["arxiv"] == "2609.01234v2" and first["summary"] == "It proves things."
    assert first["url"] == "https://arxiv.org/abs/2609.01234v2"
    assert first["pdf"] == "https://arxiv.org/pdf/2609.01234v2"
    assert second["doi"] == "10.2307/1970128" and second["url"] == "https://doi.org/10.2307/1970128"
    assert second["pdf"] == ""
    assert odd["arxiv"] == odd["doi"] == odd["url"] == odd["pdf"] == "" and odd["year"] is None
    (tmp_path / "results.json").write_text("{ not json")
    assert library.read_results(tmp_path) == []


@needs_pdftotext
def test_a_found_paper_is_fetched_by_its_place_in_the_results(client, app_cfg, monkeypatch):
    slug = client.post("/api/searches", json={"query": "papers on parity"}).json()["slug"]
    st = client.app.state.deslopify
    run_for(st, app_cfg, slug, "S1", "search", "deslopify-search",
            guide="# Parity\n\n## Found\n\n- One paper.\n", results=[
                {"title": "On sums of even integers", "arxiv": "2609.01234"},
                {"title": "Behind a paywall", "doi": "10.1000/xyz"}])
    asked: list[str] = []

    def download(url):
        asked.append(url)
        return paper_pdf()
    monkeypatch.setattr(fetch, "download_pdf", download)

    doc = client.get(f"/api/papers/{slug}/companion").json()
    assert [r["title"] for r in doc["results"]] == ["On sums of even integers",
                                                    "Behind a paywall"]
    assert [s["label"] for s in doc["sections"]] == ["Found"]
    r = client.post(f"/api/papers/{slug}/fetch", json={"run": "S1", "index": 0})
    assert r.status_code == 200, r.text
    paper = r.json()
    assert paper["kind"] == "paper" and paper["title"] == "On sums of even integers"
    assert asked == ["https://arxiv.org/pdf/2609.01234"]
    assert client.post(f"/api/papers/{slug}/fetch",
                       json={"run": "S1", "index": 0}).json()["existing"] is True
    assert client.post(f"/api/papers/{slug}/fetch",
                       json={"run": "S1", "index": 1}).status_code == 400
    assert client.post(f"/api/papers/{slug}/fetch",
                       json={"run": "S1", "index": 7}).status_code == 404
    assert client.post(f"/api/papers/{slug}/fetch",
                       json={"run": "NOPE", "index": 0}).status_code == 404

    def refuse(url):
        raise fetch.FetchError("The link did not lead to a PDF.")
    monkeypatch.setattr(fetch, "download_pdf", refuse)
    st.db.upsert_run({"id": "S2", "problem": slug, "harness": "deslopify-search",
                      "provider": "claude", "account": "default", "model": "m",
                      "effort": "low", "mode": "standard",
                      "started_at": "2026-10-07T11:00:00+00:00", "status": "done"})
    reading = app_cfg.runs_dir / "S2" / "reading"
    reading.mkdir(parents=True)
    (reading / "results.json").write_text(json.dumps(
        {"results": [{"title": "Elsewhere", "pdf": "https://example.org/x.pdf"}]}))
    r = client.post(f"/api/papers/{slug}/fetch", json={"run": "S2", "index": 0})
    assert r.status_code == 502 and "did not lead to a PDF" in r.json()["detail"]


def test_a_fetch_goes_only_to_the_open_internet():
    assert fetch.pdf_address("https://arxiv.org/abs/2609.01234") \
        == "https://arxiv.org/pdf/2609.01234"
    assert fetch.pdf_address("https://example.org/a.pdf") == "https://example.org/a.pdf"
    for url in ("http://127.0.0.1:7720/x.pdf", "http://localhost/x.pdf",
                "file:///etc/passwd", "http://10.0.0.7/x.pdf", "not a url"):
        with pytest.raises(fetch.FetchError):
            fetch.download_pdf(url)



def test_the_new_method_files_are_listed_and_an_older_edited_manifest_still_serves(app_cfg,
                                                                                   tmp_path):
    """The questions, the use of the answers, several papers and the search are
    files of the method like the others. A manifest edited before the last two
    existed still serves readings of a paper: only their own readings need them."""
    import re as _re

    from deslopify.app import method as methodmod

    m = methodmod.Method(app_cfg, tmp_path / "method.local")
    titles = {e.path: e.title for e in m.entries()}
    for path in ("harness/questionnaire.md", "harness/reader-profile.md",
                 "harness/SKILL-multi.md", "harness/SKILL-multi-expert.md",
                 "harness/SKILL-multi-nonexpert.md", "harness/SKILL-search.md"):
        assert path in titles and titles[path] != path.rsplit("/", 1)[1][:-3]
    toml = m.original("harness/harness.toml")
    older = _re.sub(r"\n# Several papers, each.*?(?=\n\[defaults\])", "\n", toml, flags=_re.S)
    assert "[variants.multi-expert]" not in older and "[variants.search]" not in older
    assert "[variants.nonexpert]" in older
    m.save("harness/harness.toml", older)            # checked by loading: accepted
    root = m.materialise(tmp_path / "copy")
    methodmod.check(root)
    broken = older.replace('id          = "deslopify-nonexpert"', 'id          = "other"')
    with pytest.raises(methodmod.MethodError):
        m.save("harness/harness.toml", broken)
