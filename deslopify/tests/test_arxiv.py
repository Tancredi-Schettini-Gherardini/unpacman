"""A paper named by its arXiv number (2026-10-28): the number read in whatever
form it is typed, the paper fetched from an address made here and made a paper
exactly as a dropped PDF is, its title from arXiv's listing when that answers.

Nothing is fetched from the internet: arXiv's PDF and its listing are stood in
for (`fetch.download_pdf`, `arxiv.listing`, `urllib.request.urlopen`).
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from deslopify.app import arxiv, fetch
from test_app import (  # noqa: F401  (fixtures)
    app_cfg, client, needs_pdftotext, paper_pdf, upload)

# --------------------------------------------------------------------------
# reading the number
# --------------------------------------------------------------------------


@pytest.mark.parametrize("typed, number", [
    ("2309.01234", "2309.01234"),
    ("2309.01234v2", "2309.01234v2"),
    ("0704.0001", "0704.0001"),
    (" arXiv:2309.01234 ", "2309.01234"),
    ("arxiv: 2309.01234V3", "2309.01234v3"),
    ("arXiv 1501.00001", "1501.00001"),
    ("math/0601001", "math/0601001"),
    ("math.GT/0309136", "math/0309136"),                  # the class is not in the number
    ("MATH.gt/0309136v3", "math/0309136v3"),
    ("hep-th/9901001", "hep-th/9901001"),
    ("cond-mat.str-el/0012345", "cond-mat/0012345"),
    ("https://arxiv.org/abs/2309.01234", "2309.01234"),
    ("http://www.arxiv.org/abs/2309.01234v2/", "2309.01234v2"),
    ("arxiv.org/pdf/2309.01234v2.pdf", "2309.01234v2"),
    ("https://export.arxiv.org/abs/math/0211159", "math/0211159"),
    ("https://arxiv.org/abs/math.GT/0309136?context=math", "math/0309136"),
    ("https://arxiv.org/html/2309.01234v1#S2", "2309.01234v1"),
    ("10.48550/arXiv.2309.01234", "2309.01234"),
    ("https://doi.org/10.48550/arXiv.2309.01234", "2309.01234"),
    ("doi:10.48550/arxiv.math/0601001", "math/0601001"),
    ("(2309.01234).", "2309.01234"),
    ("\"arXiv:2309.01234,\"", "2309.01234"),
])
def test_a_number_is_read_in_every_form_a_reader_might_type(typed, number):
    assert arxiv.identifier(typed) == number


@pytest.mark.parametrize("typed", [
    None, "", "   ", "2309.123456", "23091.01234", "math/060100", "math/06010011",
    "2309.01234v0", "2309.01234v", "Perelman entropy", "2309.01234 2310.00001",
    # Another site's address is not an arXiv number, whatever its path says.
    "https://evil.com/abs/2309.01234", "https://arxiv.org.evil.com/abs/2309.01234",
    "evil.com/2309.01234", "arxiv.org/list/math.GT/recent", "file:///etc/passwd",
    "../../etc/passwd", "math/0601001/../x", "x" * 400,
])
def test_what_is_not_an_arxiv_number_is_refused(typed):
    assert arxiv.identifier(typed) is None


def test_a_number_s_version_its_address_and_its_file():
    assert arxiv.versioned("2309.01234v2") and arxiv.versioned("math/0601001v1")
    assert not arxiv.versioned("2309.01234") and not arxiv.versioned("math/0601001")
    assert arxiv.pdf_url("2309.01234v2") == "https://arxiv.org/pdf/2309.01234v2"
    assert arxiv.pdf_url("math/0601001") == "https://arxiv.org/pdf/math/0601001"
    assert arxiv.file_name("math/0601001") == "math-0601001.pdf"
    assert arxiv.file_name("2309.01234v2") == "2309.01234v2.pdf"


# --------------------------------------------------------------------------
# arXiv's listing (shapes as it answered on 2026-10-28)
# --------------------------------------------------------------------------

FEED = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">\n'
        '  <title>arXiv Query</title>\n'
        '  <opensearch:totalResults>{total}</opensearch:totalResults>\n{entries}</feed>\n')
FOUND = FEED.format(total=1, entries=(
    '  <entry>\n    <id>http://arxiv.org/abs/math/0211159v1</id>\n'
    '    <title>The entropy formula for the Ricci flow and its\n      geometric applications'
    '</title>\n    <summary>We present a monotonic expression.</summary>\n  </entry>\n'))
NONE = FEED.format(total=0, entries="")
MALFORMED = FEED.format(total=1, entries=(
    '  <entry>\n    <id>https://arxiv.org/api/errors#incorrect_id_format_for_math/9999999</id>\n'
    '    <title>Error</title>\n    <summary>incorrect id format for math/9999999</summary>\n'
    '  </entry>\n'))


def test_the_listing_is_read_for_its_title_or_for_its_silence():
    found = arxiv.read_listing(FOUND.encode())
    assert found == arxiv.Listing(known=True, title="The entropy formula for the Ricci flow "
                                                     "and its geometric applications")
    assert arxiv.read_listing(NONE.encode()) == arxiv.Listing(known=False)
    assert arxiv.read_listing(MALFORMED.encode()) == arxiv.Listing(known=False)
    # Anything else is no answer at all.
    for raw in (b"", b"<html>Service unavailable</html>", b"<feed", b"not xml",
                b'<!DOCTYPE feed [<!ENTITY a "x">]><feed xmlns="http://www.w3.org/2005/Atom"/>',
                NONE.encode() + b" " * arxiv.LISTING_MAX_BYTES):
        assert arxiv.read_listing(raw) == arxiv.Listing(known=None), raw[:40]


def test_the_listing_never_raises_and_reads_arxiv_s_word_for_a_malformed_number(monkeypatch):
    asked: list[str] = []

    def answer(with_):
        def urlopen(request, timeout):
            asked.append(request.full_url)
            assert request.get_header("User-agent") == fetch.USER_AGENT and timeout <= 10
            if isinstance(with_, Exception):
                raise with_
            return io.BytesIO(with_)
        return urlopen

    monkeypatch.setattr(urllib.request, "urlopen", answer(FOUND.encode()))
    assert arxiv.listing("math/0211159").known is True
    assert asked == ["https://export.arxiv.org/api/query?id_list=math%2F0211159&max_results=1"]
    monkeypatch.setattr(urllib.request, "urlopen", answer(urllib.error.URLError("offline")))
    assert arxiv.listing("math/0211159") == arxiv.Listing(known=None)
    monkeypatch.setattr(urllib.request, "urlopen", answer(TimeoutError("slow")))
    assert arxiv.listing("math/0211159") == arxiv.Listing(known=None)
    # arXiv answers a malformed number 400, with a feed saying so; anything
    # else it answers with an error status says nothing about the paper.
    bad = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(MALFORMED.encode()))
    monkeypatch.setattr(urllib.request, "urlopen", answer(bad))
    assert arxiv.listing("math/9999999") == arxiv.Listing(known=False)
    busy = urllib.error.HTTPError("u", 503, "Unavailable", {}, io.BytesIO(NONE.encode()))
    monkeypatch.setattr(urllib.request, "urlopen", answer(busy))
    assert arxiv.listing("math/0211159") == arxiv.Listing(known=None)


# --------------------------------------------------------------------------
# fetching it, as if it had been dropped
# --------------------------------------------------------------------------


@pytest.fixture
def arxiv_site(monkeypatch):
    """Stands in for arXiv: the PDFs it serves by address, what its listing says
    of each number, and every address and number asked of it."""
    site = {"pdfs": {}, "listed": {}, "asked": [], "listings": []}

    def download(url):
        site["asked"].append(url)
        if url not in site["pdfs"]:
            raise fetch.FetchError("The paper could not be fetched: the site answered "
                                   "404 (Not Found).")
        return site["pdfs"][url]

    def listing(ident):
        site["listings"].append(ident)
        return site["listed"].get(ident, arxiv.Listing(known=None))

    monkeypatch.setattr(fetch, "download_pdf", download)
    monkeypatch.setattr(arxiv, "listing", listing)
    return site


@needs_pdftotext
def test_a_number_is_fetched_and_becomes_a_paper_named_as_arxiv_lists_it(client, app_cfg,
                                                                        arxiv_site):
    url = "https://arxiv.org/pdf/math/0211159"
    arxiv_site["pdfs"][url] = paper_pdf("The entropy formula for the Ricci flow")
    arxiv_site["listed"]["math/0211159"] = arxiv.Listing(
        known=True, title="The entropy formula for the Ricci flow and its geometric applications")
    r = client.post("/api/arxiv", json={"id": "arXiv:math.DG/0211159"})
    assert r.status_code == 200, r.text
    paper = r.json()
    assert paper["kind"] == "paper" and "existing" not in paper
    assert paper["title"] == "The entropy formula for the Ricci flow and its geometric applications"
    assert paper["original_name"] == "math-0211159.pdf" and paper["words"] > 120
    # The address asked is made here, from the number alone.
    assert arxiv_site["asked"] == [url] and arxiv_site["listings"] == ["math/0211159"]
    marker = json.loads((app_cfg.problems_dir / paper["slug"] / "deslopify.json").read_text())
    assert marker["download_urls"] == [url]
    assert paper["slug"] in [p["slug"] for p in client.get("/api/papers").json()]
    assert client.get(f"/api/papers/{paper['slug']}/file/source.pdf").content.startswith(b"%PDF")


@needs_pdftotext
def test_without_arxiv_s_listing_the_title_is_guessed_as_for_a_dropped_pdf(client, arxiv_site):
    data = paper_pdf("On sums of even integers")
    arxiv_site["pdfs"]["https://arxiv.org/pdf/2309.01234"] = data
    by_number = client.post("/api/arxiv", json={"id": "2309.01234"}).json()
    assert by_number["title"] == "On sums of even integers"
    # The same bytes dropped are the same paper, which is what was made.
    dropped = upload(client, data).json()
    assert dropped["existing"] is True and dropped["slug"] == by_number["slug"]


@needs_pdftotext
def test_a_paper_dropped_before_is_the_paper_it_already_is(client, app_cfg, arxiv_site):
    data = paper_pdf("On products of odd integers")
    dropped = upload(client, data).json()
    arxiv_site["pdfs"]["https://arxiv.org/pdf/2309.01234v2"] = data
    again = client.post("/api/arxiv", json={"id": "2309.01234v2"}).json()
    assert again["existing"] is True and again["slug"] == dropped["slug"]
    assert again["title"] == dropped["title"]                    # its name is kept
    marker = json.loads((app_cfg.problems_dir / dropped["slug"] / "deslopify.json").read_text())
    assert marker["download_urls"] == ["https://arxiv.org/pdf/2309.01234v2"]


@needs_pdftotext
def test_a_version_fetched_before_is_not_fetched_again_and_a_bare_number_is(client,
                                                                          arxiv_site):
    data = paper_pdf("On sums of even integers")
    arxiv_site["pdfs"]["https://arxiv.org/pdf/2309.01234v2"] = data
    arxiv_site["pdfs"]["https://arxiv.org/pdf/2309.01234"] = data
    first = client.post("/api/arxiv", json={"id": "2309.01234v2"}).json()
    again = client.post("/api/arxiv", json={"id": "https://arxiv.org/abs/2309.01234v2"}).json()
    assert again["existing"] is True and again["slug"] == first["slug"]
    assert arxiv_site["asked"] == ["https://arxiv.org/pdf/2309.01234v2"]     # once
    # Without a version arXiv may hold a newer one: fetched afresh, and found
    # again by its bytes when they have not changed.
    bare = client.post("/api/arxiv", json={"id": "2309.01234"}).json()
    assert bare["existing"] is True and bare["slug"] == first["slug"]
    assert arxiv_site["asked"][-1] == "https://arxiv.org/pdf/2309.01234"
    # And so every time, though that address is remembered by now.
    client.post("/api/arxiv", json={"id": "arXiv:2309.01234"})
    assert arxiv_site["asked"].count("https://arxiv.org/pdf/2309.01234") == 2


def test_a_number_arxiv_does_not_know_is_said_so(client, arxiv_site):
    arxiv_site["listed"]["2309.99999"] = arxiv.Listing(known=False)
    r = client.post("/api/arxiv", json={"id": "2309.99999"})
    assert r.status_code == 404
    assert r.json()["detail"] == "arXiv has no paper numbered 2309.99999. Check the number."
    arxiv_site["listed"]["math/0211159v9"] = arxiv.Listing(known=False)
    r = client.post("/api/arxiv", json={"id": "math/0211159v9"})
    assert r.status_code == 404 and "or no such version of it" in r.json()["detail"]


def test_a_fetch_that_fails_says_why(client, monkeypatch, arxiv_site):
    def offline(url):
        raise fetch.FetchError("Could not connect securely to arxiv.org. Check your "
                               "network/VPN, or save and upload the PDF manually.")
    monkeypatch.setattr(fetch, "download_pdf", offline)
    r = client.post("/api/arxiv", json={"id": "2309.01234"})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("arXiv:2309.01234 could not be fetched. Could not "
                                         "connect securely to arxiv.org.")


@pytest.mark.parametrize("typed", ["", "Perelman entropy", "https://evil.com/abs/2309.01234",
                                   "http://127.0.0.1:7720/api/papers"])
def test_what_is_not_a_number_is_refused_before_anything_is_fetched(client, arxiv_site, typed):
    r = client.post("/api/arxiv", json={"id": typed})
    assert r.status_code == 400 and "is not an arXiv number" in r.json()["detail"]
    assert arxiv_site["asked"] == [] and arxiv_site["listings"] == []
    assert client.post("/api/arxiv", json={}).status_code == 400
