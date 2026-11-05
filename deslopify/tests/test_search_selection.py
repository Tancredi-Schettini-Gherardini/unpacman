"""Search PDFs can be collected without starting runs or changing uploads."""
import pytest

from deslopify.app import fetch, library
from test_app import (  # noqa: F401
    app_cfg, client, launched, needs_pdftotext, paper_pdf, upload,
)
from test_kinds import run_for


def search_results(client, app_cfg, results, *, run="S1", query="papers on parity"):
    slug = client.post("/api/searches", json={"query": query}).json()["slug"]
    run_for(client.app.state.deslopify, app_cfg, slug, run, "search", "deslopify-search",
            guide="# Search\n\n## Found\n\nSome papers.\n", results=results)
    return slug


@needs_pdftotext
def test_fetch_selected_reuses_uploaded_papers_and_forms_collection_without_run(
        client, app_cfg, launched, monkeypatch):
    first_data, second_data = paper_pdf("First paper"), paper_pdf("Second paper")
    first = upload(client, first_data).json()
    slug = search_results(client, app_cfg, [
        {"title": "First paper", "arxiv": "2609.01234v1"},
        {"title": "Second paper", "arxiv": "2609.01235v2"}])
    asked = []

    def download(url):
        asked.append(url)
        return first_data if "01234" in url else second_data

    monkeypatch.setattr(fetch, "download_pdf", download)
    got = []
    for i in range(2):
        response = client.post(f"/api/papers/{slug}/fetch",
                               json={"run": "S1", "index": i, "reuse": True})
        assert response.status_code == 200, response.text
        got.append(response.json())
    assert got[0]["slug"] == first["slug"] and got[0]["existing"] is True
    assert len(asked) == 2
    again = client.post(f"/api/papers/{slug}/fetch",
                        json={"run": "S1", "index": 0, "reuse": True})
    assert again.json()["slug"] == first["slug"] and len(asked) == 2
    together = client.post("/api/collections", json={"papers": [p["slug"] for p in got]})
    assert together.status_code == 200, together.text
    assert together.json()["kind"] == "collection"
    assert len(together.json()["members"]) == 2
    assert client.post("/api/collections", json={"papers": [p["slug"] for p in got]}) \
        .json()["existing"] is True
    assert launched == {}
    assert len([p for p in library.list_papers(app_cfg) if p.kind == "paper"]) == 2
    assert client.get("/static/search-papers.js").status_code == 200


@needs_pdftotext
def test_reuse_is_opt_in_exact_url_and_checks_search_membership(client, app_cfg, monkeypatch):
    slug = search_results(client, app_cfg, [
        {"title": "Version one", "arxiv": "2609.01234v1"},
        {"title": "Version two", "arxiv": "2609.01234v2"},
        {"title": "Closed paper", "doi": "10.1000/xyz"}])
    asked = []

    def download(url):
        asked.append(url)
        return paper_pdf(url)

    monkeypatch.setattr(fetch, "download_pdf", download)
    endpoint = f"/api/papers/{slug}/fetch"
    individual = client.post(endpoint, json={"run": "S1", "index": 0}).json()
    assert client.post(endpoint, json={"run": "S1", "index": 0, "reuse": True}) \
        .json()["slug"] == individual["slug"]
    assert len(asked) == 1
    # The old button still fetches afresh; a different arXiv version is not reused.
    client.post(endpoint, json={"run": "S1", "index": 0})
    version_two = client.post(endpoint, json={"run": "S1", "index": 1, "reuse": True})
    assert len(asked) == 3 and version_two.json()["slug"] != individual["slug"]
    assert client.post(endpoint, json={"run": "NOPE", "index": 0, "reuse": True}).status_code == 404
    assert client.post(endpoint, json={"run": "S1", "index": 9, "reuse": True}).status_code == 404
    assert client.post(endpoint, json={"run": "S1", "index": 2, "reuse": True}).status_code == 400
    assert len(asked) == 3
    # Another completed search can reuse the same imported PDF too.
    other = search_results(client, app_cfg, [{"title": "Same", "arxiv": "2609.01234v1"}],
                           run="S2", query="another question")
    assert client.post(f"/api/papers/{other}/fetch", json={"run": "S2", "index": 0, "reuse": True}) \
        .json()["slug"] == individual["slug"]
    assert len(asked) == 3


@needs_pdftotext
def test_failed_download_can_retry_and_does_not_change_other_papers(client, app_cfg, monkeypatch):
    prior = upload(client, paper_pdf("Already here")).json()
    slug = search_results(client, app_cfg, [{"title": "New", "arxiv": "2609.01234v1"}])

    def fail(url):
        raise fetch.FetchError("Could not download the PDF")

    monkeypatch.setattr(fetch, "download_pdf", fail)
    endpoint = f"/api/papers/{slug}/fetch"
    body = {"run": "S1", "index": 0, "reuse": True}
    assert client.post(endpoint, json=body).status_code == 502
    assert len(library.list_papers(app_cfg)) == 2  # the existing paper and search
    monkeypatch.setattr(fetch, "download_pdf", lambda url: paper_pdf("New"))
    assert client.post(endpoint, json=body).status_code == 200
    assert client.get(f"/api/papers/{prior['slug']}").status_code == 200


@needs_pdftotext
def test_reuse_ignores_missing_source_file(client, app_cfg):
    uploaded = upload(client, paper_pdf()).json()
    paper = next(p for p in library.list_papers(app_cfg) if p.slug == uploaded["slug"])
    url = "https://arxiv.org/pdf/2609.01234v1"
    library.remember_download(paper, url)
    assert library.find_by_download_url(app_cfg, url).slug == paper.slug
    paper.source.unlink()
    assert library.find_by_download_url(app_cfg, url) is None


def test_search_selection_ui():
    from pathlib import Path
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the search selection UI tests")
    script = Path(__file__).parent / "web" / "search-papers.test.mjs"
    result = subprocess.run([node, "--experimental-vm-modules", "--test", str(script)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
