"""Source-document discovery and text extraction for reading harnesses."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path


class SourceError(ValueError):
    """A requested reading source is missing, ambiguous, or unreadable."""


READABLE_SUFFIXES = {".md", ".markdown", ".pdf", ".tex"}
_COLLECTION_PAPER = re.compile(r"^## Paper \d+ of \d+: .*?$", re.MULTILINE)
_OMITTED = ("\n\n[The rest of this paper was omitted because its source text "
            "exceeded the reader AI's input limit.]\n")
# PDFium has process-wide state and cannot be called from two threads at once,
# even when they open different documents. Hold this through document.close().
_PDFIUM_LOCK = threading.Lock()


def _pdfium_text(source: Path) -> str:
    return "".join(page + "\f" for page in pdf_pages(source))


def _bloated_extraction(text: str) -> bool:
    """Spot layout padding by how much repeated horizontal space inflates text."""
    if len(text) < 250_000:
        return False
    compact = re.sub(r"[ \t]{2,}", " ", text)
    return len(compact) < len(text) * 0.6


def select_source(root: Path, selected: list[str] | None = None) -> Path:
    """Choose one source document, preferring an explicitly selected file."""
    explicit: list[Path] = []
    for rel in selected or []:
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise SourceError(f"source path escapes the problem folder: {rel}") from exc
        if candidate.suffix.lower() in READABLE_SUFFIXES:
            explicit.append(candidate)
    if len(explicit) > 1:
        raise SourceError(
            "Deslopify needs one source document; select only one Markdown or PDF file")
    if explicit:
        if not explicit[0].is_file():
            raise SourceError(f"selected source does not exist: {explicit[0]}")
        return explicit[0]

    progress = root / "progress"
    named = [p for p in (progress / "source.md", progress / "source.pdf")
             if p.is_file()]
    if len(named) > 1:
        raise SourceError(
            "both progress/source.md and progress/source.pdf exist; keep one or "
            "select the intended source explicitly")
    if named:
        return named[0]

    legacy = progress / "math.tex"
    if legacy.is_file():
        return legacy
    raise SourceError(
        "no reading source found; select one .md or .pdf file, or create "
        "progress/source.md or progress/source.pdf")


def read_source(path: Path | str) -> str:
    """Return stable text for Markdown/TeX, or extract a PDF's text layer."""
    source = Path(path).expanduser()
    suffix = source.suffix.lower()
    if not source.is_file():
        raise SourceError(f"source document does not exist: {source}")
    if suffix in {".md", ".markdown", ".tex", ".txt"}:
        return source.read_text(encoding="utf-8", errors="replace")
    if suffix != ".pdf":
        raise SourceError(f"unsupported source format {suffix or '(none)'}; use .md or .pdf")

    if engine := shutil.which("pdftotext"):
        command = [engine, "-layout", str(source), "-"]
    elif engine := shutil.which("gs"):
        command = [engine, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE",
                   "-sDEVICE=txtwrite", "-sOutputFile=-", str(source)]
    else:
        # Neither program comes with a Mac, and asking someone who only wants
        # a paper explained to install Poppler is the kind of step that ends
        # the attempt. PDFium is installed with the Python packages.
        text = _pdfium_text(source)
        if not text.replace("\f", "").strip():
            raise SourceError(
                "the PDF contains no extractable text layer; run OCR on it before "
                "using Deslopify")
        return text
    try:
        result = subprocess.run(command, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise SourceError("PDF text extraction timed out") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SourceError(f"could not extract PDF text: {detail or 'extractor failed'}")
    text = result.stdout.decode("utf-8", errors="replace")
    if _bloated_extraction(text):
        # Layout extractors, especially Ghostscript's txtwrite, can pad an
        # ordinary paper with enough spaces to exceed the AI's input limit.
        # Prefer PDFium when it retains the substantive text. If it cannot
        # read this PDF, collapse the padding without dropping any words.
        compact = re.sub(r"[ \t]{2,}", " ", text)
        try:
            alternate = _pdfium_text(source)
        except SourceError:
            alternate = ""
        if alternate.strip() and len("".join(alternate.split())) >= \
                len("".join(compact.split())) * 0.8:
            text = alternate
        else:
            text = compact
    if not text.strip():
        raise SourceError(
            "the PDF contains no extractable text layer; run OCR on it before "
            "using Deslopify")
    return text


def beginning_within(text: str, max_chars: int, *, collection: bool = False,
                     ) -> tuple[str, list[str]]:
    """Fit source text by retaining its beginning, independently per paper.

    The returned labels identify only papers whose text was cut. The omitted
    marker is part of the budget and makes the boundary unambiguous to a pass.
    """
    if len(text) <= max_chars:
        return text, []
    matches = list(_COLLECTION_PAPER.finditer(text)) if collection else []
    if not matches:
        if max_chars <= len(_OMITTED):
            raise SourceError("The reader AI cannot hold even the beginning of this source.")
        return text[:max_chars - len(_OMITTED)] + _OMITTED, ["the paper"]

    preamble = text[:matches[0].start()]
    blocks = [text[m.start():matches[i + 1].start() if i + 1 < len(matches)
                   else len(text)] for i, m in enumerate(matches)]
    labels = [m.group().split(": ", 1)[1] for m in matches]
    available = max_chars - len(preamble) - len(blocks) * len(_OMITTED)
    minimum = max(len(m.group()) + 120 for m in matches)
    if available < len(blocks) * minimum:
        raise SourceError("The reader AI cannot hold the beginning of every paper in this collection.")
    low, high = minimum, max(map(len, blocks))
    while low < high:
        middle = (low + high + 1) // 2
        if sum(min(len(block), middle) for block in blocks) <= available:
            low = middle
        else:
            high = middle - 1
    kept = [block if len(block) <= low else block[:low] + _OMITTED
            for block in blocks]
    return preamble + "".join(kept), [label for block, label in zip(blocks, labels)
                                       if len(block) > low]


def pdf_pages(path: Path | str) -> list[str]:
    """The text of each page of a PDF, read by PDFium — Chrome's PDF engine,
    installed as a Python package (`pypdfium2`), so always present.

    The fallback when neither pdftotext nor Ghostscript is installed. Its text
    is in reading order, close to pdftotext's (measured 2026-10-06 on four
    papers: 94–100% of words in the same order, the difference mostly
    letter-spaced headings, which PDFium joins and pdftotext splits). Pages are
    returned without the form feed that pdftotext puts after each.
    """
    with _PDFIUM_LOCK:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:                 # pragma: no cover - a dependency
            raise SourceError(
                "PDF input requires pdftotext, Ghostscript, or the pypdfium2 package, "
                "and none is available") from exc
        try:
            document = pdfium.PdfDocument(str(path))
        except Exception as exc:                   # PdfiumError: encrypted, damaged
            raise SourceError(f"could not extract PDF text: {exc}") from exc
        try:
            pages: list[str] = []
            for index in range(len(document)):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_bounded()
                    finally:
                        textpage.close()
                finally:
                    page.close()
                pages.append(text.replace("\r\n", "\n").replace("\r", "\n"))
            return pages
        except Exception as exc:
            raise SourceError(f"could not extract PDF text: {exc}") from exc
        finally:
            document.close()


def pdf_page_count(source: Path | str | bytes) -> int:
    """Count PDF pages without extracting their text."""
    with _PDFIUM_LOCK:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:                 # pragma: no cover - a dependency
            raise SourceError("PDF page counting requires the pypdfium2 package") from exc
        try:
            document = pdfium.PdfDocument(
                source if isinstance(source, bytes) else str(source))
            try:
                return len(document)
            finally:
                document.close()
        except Exception as exc:
            raise SourceError(f"could not read PDF page count: {exc}") from exc


def pdf_title(path: Path | str) -> str:
    """The title a PDF's metadata gives, or "" — by PDFium, like `pdf_pages`."""
    with _PDFIUM_LOCK:
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(str(path))
        except Exception:
            return ""
        try:
            return str(document.get_metadata_dict().get("Title") or "").strip()
        except Exception:
            return ""
        finally:
            document.close()
