"""The reader's mathematical background — their "mathematical CV" (2026-10-13).

Written once in Advanced settings, in their own words or taken from a CV file:
what they work on, what they know well, what they do not. Every reading
started with it is written for that reader (it travels in the run's reader
profile, beside the answers to a Non-expert reading's questions, so every pass
is shown it); the questions before a Non-expert reading are chosen with it,
and so are explanations of what the reader selects or asks. How it is to be
used is the method's own file, `harness/reader-cv.md`, editable like the rest.

Kept in `var/unpacman-cv.json`, on this computer only. It is sent to the AI a
reader chooses, as the paper is, and to nobody else; the report a reader may
send with Feedback never includes it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atelier.config import Config

from .places import var_dir

# About two pages of a CV, or a long paragraph of one's own: enough to say what
# one knows, and still a small part of what each reading is sent.
MAX_CHARS = 12_000

# The extensions a CV can be read from, besides PDF.
TEXT_SUFFIXES = (".txt", ".md", ".tex", ".text")

# How to use it, should the method's own file (`harness/reader-cv.md`) be
# missing from a harness that predates it.
FALLBACK = ("The reader has described their own mathematical background below. Use it to "
            "decide how much to explain and where — not what is claimed: what lies in their "
            "field, use freely; what lies outside it, explain from what they know. Stay "
            "faithful to the paper.")


class CvError(ValueError):
    """Said so a reader can act on it."""


def store(cfg: Config) -> Path:
    return var_dir(cfg) / "unpacman-cv.json"


def load(cfg: Config) -> dict[str, Any]:
    try:
        data = json.loads(store(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return {}
    return data


def text(cfg: Config) -> str:
    """The background as saved, or "" for none."""
    return str(load(cfg).get("text") or "").strip()


def tidy(raw: str) -> str:
    """Text as a reader would want it kept: Windows line ends and trailing
    spaces gone, no run of more than one blank line."""
    lines = [line.rstrip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def save(cfg: Config, raw: str) -> dict[str, Any]:
    """Keep it — or, empty, forget it."""
    body = tidy(raw)
    if not body:
        forget(cfg)
        return {"text": "", "updated": None}
    if len(body) > MAX_CHARS:
        raise CvError(f"That is longer than unpacman keeps ({len(body):,} characters; at most "
                      f"{MAX_CHARS:,}). Keep what says what you know: your field, what you "
                      "work on, what you know well and what you do not.")
    record = {"text": body, "updated": datetime.now(UTC).isoformat(timespec="seconds")}
    path = store(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    staged.replace(path)
    return record


def forget(cfg: Config) -> None:
    store(cfg).unlink(missing_ok=True)


def from_file_text(raw: str) -> tuple[str, bool]:
    """A CV file's text, to put in the box for the reader to look over before
    keeping it: page layout undone (a PDF's columns come out padded with
    spaces), and cut to fit if it is longer than unpacman keeps. Returns the
    text and whether it was cut."""
    body = raw.replace("\f", "\n\n")
    body = "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in body.split("\n"))
    body = tidy(body)
    if len(body) <= MAX_CHARS:
        return body, False
    cut = body[:MAX_CHARS]
    # At the end of a line, if there is one near the cut.
    end = cut.rfind("\n")
    return (cut[:end] if end > MAX_CHARS * 0.8 else cut).rstrip(), True


def block(instructions: str, background: str) -> str:
    """What the AI is shown: how to use the background
    (`harness/reader-cv.md`), then the background itself."""
    if not background.strip():
        return ""
    return (instructions.strip()
            + "\n\n## The reader's mathematical background, in their own words\n\n"
            + background.strip() + "\n")


def fingerprint(shown: str) -> str:
    """A short hash of what the AI was shown, so that answers given for one
    background are kept apart from those given for another."""
    return hashlib.sha256(shown.encode("utf-8")).hexdigest()[:10] if shown else ""
