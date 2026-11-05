"""Papers named by their arXiv number, fetched from arXiv as if dropped here.

A reader may have a paper's arXiv number rather than its PDF. `identifier`
reads what they might type — `2309.01234`, `2309.01234v2`, `arXiv:2309.01234`,
`math/0601001`, `math.GT/0309136`, an arxiv.org address of either, or an arXiv
DOI — and gives the number arXiv knows the paper by. Its PDF is then fetched
from arXiv by the narrow downloader every fetched paper goes through
(`fetch.download_pdf`), from an address made here — never one sent from the
page — and becomes a paper exactly as a dropped PDF does.

Its title comes from arXiv's own listing (`listing`) when that answers: a title
guessed from the first page, as for a dropped PDF, can be a journal's name, a
title cut short, or one in capitals (all three, on three real arXiv PDFs,
2026-10-28). The listing is asked of one fixed address, through the ordinary
network as a browser would ask it; when it does not answer, the title is
guessed as for any PDF, and nothing else changes.
"""

from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlencode

from . import fetch

API = "https://export.arxiv.org/api/query"
LISTING_TIMEOUT_S = 10
LISTING_MAX_BYTES = 1024 * 1024
MAX_INPUT = 300
_ATOM = "{http://www.w3.org/2005/Atom}"

# Since April 2007 a number is YYMM.NNNN, with five digits after the point
# from 2015. Before, an archive — sometimes with a subject class, which is not
# part of the number and which arXiv's listing does not accept — then YYMMNNN:
# math/0601001, math.GT/0309136, hep-th/9901001. Either may name a version.
_NUMBER = re.compile(
    r"(?:(?P<new>\d{4}\.\d{4,5})"
    r"|(?P<archive>[a-z]+(?:-[a-z]+)?)(?:\.[a-z]+(?:-[a-z]+)?)?/(?P<old>\d{7}))"
    r"(?P<version>v[1-9]\d*)?", re.ASCII | re.IGNORECASE)
_ADDRESS = re.compile(
    r"(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html|format)/"
    r"(?P<rest>[^?#]+?)(?:\.pdf)?/?(?:[?#].*)?", re.IGNORECASE)
_DOI = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?10\.48550/arxiv\.(?P<rest>.+)",
                  re.IGNORECASE)
_PREFIX = re.compile(r"arxiv\s*:?\s*(?P<rest>.+)", re.IGNORECASE)


def identifier(text: str | None) -> str | None:
    """The arXiv number in what a reader typed, as arXiv writes it — or None.

    A version, when given, is kept: `v2` is that version and no other. An
    address is read only when it is arXiv's own: any other site's is not an
    arXiv number, whatever its path says, since nothing but the number itself
    may be left once arXiv's address, its DOI or `arXiv:` is taken off.
    """
    # Quotes, brackets and the punctuation of the sentence it was copied from
    # (no number begins or ends with any of them).
    text = " ".join(str(text or "").split()).strip("\"'()[]<>.,;:")
    if not text or len(text) > MAX_INPUT:
        return None
    rest = text
    for form in (_ADDRESS, _DOI, _PREFIX):
        if (found := form.fullmatch(text)):
            rest = found.group("rest")
            break
    number = _NUMBER.fullmatch(rest.strip())
    if number is None:
        return None
    version = (number.group("version") or "").lower()
    if number.group("new"):
        return number.group("new") + version
    return f"{number.group('archive').lower()}/{number.group('old')}{version}"


def versioned(ident: str) -> bool:
    """Whether a number names one version, which never changes once posted."""
    return re.search(r"v\d+$", ident) is not None


def pdf_url(ident: str) -> str:
    """Where arXiv serves the paper's PDF: the newest version, unless one is named."""
    return f"https://arxiv.org/pdf/{ident}"


def file_name(ident: str) -> str:
    """What the fetched PDF is called here, as a file would be: `2309.01234.pdf`,
    `math-0601001.pdf`."""
    return ident.replace("/", "-") + ".pdf"


@dataclass(frozen=True, slots=True)
class Listing:
    """What arXiv's listing says of a number: `known` is True when it lists a
    paper, False when it says there is none, and None when it could not be
    asked or read. `title` is the listed title, on one line."""
    known: bool | None
    title: str = ""


def listing(ident: str) -> Listing:
    """Ask arXiv's listing about a number. Never raises; blocking."""
    url = f"{API}?{urlencode({'id_list': ident, 'max_results': '1'})}"
    request = urllib.request.Request(url, headers={"User-Agent": fetch.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=LISTING_TIMEOUT_S) as response:
            raw = response.read(LISTING_MAX_BYTES + 1)
    except urllib.error.HTTPError as answer:
        # A number arXiv finds malformed is answered 400, with a feed saying so.
        try:
            raw = answer.read(LISTING_MAX_BYTES + 1) if answer.code == 400 else b""
        except (OSError, http.client.HTTPException):
            raw = b""
        finally:
            answer.close()
        return read_listing(raw) if raw else Listing(known=None)
    except (OSError, ValueError, http.client.HTTPException):
        return Listing(known=None)
    return read_listing(raw)


def read_listing(raw: bytes) -> Listing:
    """The listing's answer (an Atom feed): its paper, an error about the number
    — arXiv's way of saying that no paper has it — or nothing."""
    if len(raw) > LISTING_MAX_BYTES or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        return Listing(known=None)
    try:
        feed = ET.fromstring(raw)
    except ET.ParseError:
        return Listing(known=None)
    if feed.tag != f"{_ATOM}feed":
        return Listing(known=None)
    for entry in feed.iter(f"{_ATOM}entry"):
        where = entry.findtext(f"{_ATOM}id") or ""
        if "/abs/" in where:
            title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
            return Listing(known=True, title=title[:300])
    # No entry ("no paper has that number"), or an error entry about the
    # number itself ("incorrect id format"): either way, arXiv lists none.
    return Listing(known=False)
