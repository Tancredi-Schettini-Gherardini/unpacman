from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from atelier.db import reset_db
from atelier.web.app import create_app


@pytest.fixture(autouse=True)
def _fresh_web_db():
    reset_db()
    yield
    reset_db()


@pytest.mark.asyncio
async def test_create_problem_accepts_a_pdf_upload(tmp_cfg):
    app = create_app(tmp_cfg)
    transport = httpx.ASGITransport(app=app)
    pdf = b"%PDF-1.4\n% upload fixture\n"

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/problems",
            data={
                "slug": "uploaded-paper",
                "title": "Uploaded Paper",
                "statement": "Read the attached paper.",
            },
            files={"source": ("paper.pdf", pdf, "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["source"] == "progress/source.pdf"
    source = tmp_cfg.problems_dir / "uploaded-paper" / "progress" / "source.pdf"
    assert source.read_bytes() == pdf


@pytest.mark.asyncio
async def test_create_problem_rejects_a_non_pdf_upload(tmp_cfg):
    app = create_app(tmp_cfg)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/problems",
            data={"slug": "not-a-pdf", "title": "Wrong file"},
            files={"source": ("notes.txt", b"plain text", "text/plain")},
        )

    assert response.status_code == 400
    assert "must be a PDF" in response.json()["detail"]
    assert not (tmp_cfg.problems_dir / "not-a-pdf").exists()


@pytest.mark.asyncio
async def test_create_problem_keeps_json_clients_working(tmp_cfg):
    app = create_app(tmp_cfg)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/problems",
            json={"slug": "json-problem", "title": "JSON Problem", "statement": "S"},
        )

    assert response.status_code == 200
    assert response.json()["source"] is None
    assert (tmp_cfg.problems_dir / "json-problem" / "problem.md").read_text() == "S"


@pytest.mark.asyncio
async def test_reader_modes_are_separate_harness_choices_in_the_web_api(tmp_cfg):
    app = create_app(tmp_cfg)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/harnesses")
        expert = await client.get("/api/harnesses/deslopify-expert")
        nonexpert = await client.get("/api/harnesses/deslopify-nonexpert")

    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()["harnesses"]}
    ids = set(rows)
    assert {"deslopify-expert", "deslopify-nonexpert"} <= ids
    assert "deslopify" not in ids
    assert rows["deslopify-expert"]["family"] == "deslopify"
    assert rows["deslopify-expert"]["variant"] == "expert"
    assert rows["deslopify-expert"]["variant_label"] == "Expert"
    assert rows["deslopify-nonexpert"]["family"] == "deslopify"
    assert rows["deslopify-nonexpert"]["variant"] == "nonexpert"
    assert rows["deslopify-nonexpert"]["variant_label"] == "Non-expert"
    # Audience instructions now live in the pure/applied profiles, selected by
    # these short routers rather than duplicated in the entry-point skill.
    assert "SKILL-pure-expert.md" in expert.json()["skill_raw"]
    assert "SKILL-applied-expert.md" in expert.json()["skill_raw"]
    assert "SKILL-pure-nonexpert.md" in nonexpert.json()["skill_raw"]
    assert "SKILL-applied-nonexpert.md" in nonexpert.json()["skill_raw"]


def test_launch_window_has_a_reader_level_selector_for_harness_variants():
    source = (Path(__file__).parents[1] / "web" / "js" / "launch.js").read_text(
        encoding="utf-8")

    assert "Reader level" in source
    assert "x.family === harness.family" in source
    assert "harnessSel.value = readerLevelSel.value" in source


@pytest.mark.asyncio
async def test_two_reading_runs_can_launch_on_the_same_problem(
        tmp_cfg, sample_problem, monkeypatch):
    launched = []

    async def fake_launch(_manager, plan, session_number=1):
        launched.append(plan)
        return type("Launched", (), {
            "run_id": plan.spec.run_id,
            "state": "running",
            "snapshot": lambda self: {"run_id": plan.spec.run_id},
        })()

    monkeypatch.setattr("atelier.runner.RunManager.launch", fake_launch)
    app = create_app(tmp_cfg)
    transport = httpx.ASGITransport(app=app)
    body = {
        "problem": sample_problem.slug,
        "harness": "deslopify-expert",
        "mode": "standard",
        "account": "default",
        "model": "claude-haiku-4-5",
        "effort": "low",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/runs", json=body)
        second = await client.post("/api/runs", json=body)

    assert first.status_code == second.status_code == 200
    assert len(launched) == 2
    assert launched[0].cwd != launched[1].cwd
    assert all(plan.cwd == plan.run_dir / "workspace" for plan in launched)
    assert not (sample_problem.root / ".atelier" / "lock.json").exists()


@pytest.mark.asyncio
async def test_two_mutating_runs_on_the_same_problem_remain_exclusive(
        tmp_cfg, sample_problem, monkeypatch):
    async def fake_launch(_manager, plan, session_number=1):
        return type("Launched", (), {
            "run_id": plan.spec.run_id,
            "state": "running",
            "snapshot": lambda self: {"run_id": plan.spec.run_id},
        })()

    monkeypatch.setattr("atelier.runner.RunManager.launch", fake_launch)
    app = create_app(tmp_cfg)
    transport = httpx.ASGITransport(app=app)
    body = {
        "problem": sample_problem.slug,
        "harness": "solo",
        "mode": "standard",
        "account": "default",
        "model": "claude-haiku-4-5",
        "effort": "low",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/runs", json=body)
        second = await client.post("/api/runs", json=body)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "locked by run" in second.json()["detail"]


def test_problem_page_keeps_new_session_available_while_a_run_is_live():
    source = (Path(__file__).parents[1] / "web" / "js" / "views" /
              "problems.js").read_text(encoding="utf-8")

    live_block = source[source.index("const actions"):source.index("if (data.lock")]
    assert "\u25c9 Open live session" in live_block
    assert "\u25b6 New session" in live_block
    assert "else" not in live_block
