"""The method a reading follows, as the reader may see it and change it.

The files a reading is made from — each level's skill, each later pass's role,
the manifest that strings them together, the fidelity rule every pass works
under — and the instructions the explanations follow are shown in Advanced
settings, and can be edited there.

An edited file is kept apart from the original, in `method.local/` beside the
lab's own `atelier.local.toml`: on this computer only, never committed. The
originals are never touched, so `git pull` brings in Antoine's changes to them
as it always did, and every edit can be undone by resetting it.

A reading started while any of the reading's files is edited is given the whole
method, copied into its run — `runs/<id>/method/`, the originals with the edits
laid over them — and loaded from there. Every pass of that reading, including
those planned after it has started, reads from that copy. So the run's record
shows what it ran with, and editing a file during a reading cannot change it
half-way. A reading started with nothing edited runs exactly as before.

The explanations' instructions are read for each question, so an edit applies
to the next one; what was answered under other instructions is kept apart
(`explain.Texts.fingerprint`).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from atelier.config import Config
from atelier.harnesses import HarnessError, get_harness, load_harness

from . import explain, library, readings

MAX_BYTES = 256 * 1024

# What each known file is, said for someone deciding whether to open it.
# Shown in this order, under these groups.
GROUPS = ("Readings", "Later passes", "Rules", "Settings", "Explanations",
          "Not used by readings started here")

_KNOWN: dict[str, tuple[str, str, str]] = {
    "harness/SKILL-common.md": (
        "Readings", "Shared companion instructions",
        "Source handling, statement boxes, illustrations, historical research, "
        "and citation standards shared by all paper companions."),
    "harness/SKILL-expert.md": (
        "Readings", "Expert paper classification",
        "Selects the pure or applied Expert instructions after reading the paper."),
    "harness/SKILL-nonexpert.md": (
        "Readings", "Non-expert paper classification",
        "Selects the pure or applied Non-expert instructions after reading the paper."),
    "harness/SKILL-pure-expert.md": (
        "Readings", "Pure mathematics — Expert",
        "The full Expert companion instructions for a pure mathematics paper."),
    "harness/SKILL-applied-expert.md": (
        "Readings", "Applied mathematics — Expert",
        "The full Expert companion instructions for an applied mathematics paper."),
    "harness/SKILL-pure-nonexpert.md": (
        "Readings", "Pure mathematics — Non-expert",
        "The full Non-expert companion instructions for a pure mathematics paper."),
    "harness/SKILL-applied-nonexpert.md": (
        "Readings", "Applied mathematics — Non-expert",
        "The full Non-expert companion instructions for an applied mathematics paper."),
    "harness/questionnaire.md": (
        "Readings", "The Non-expert questions",
        "How the questions asked before a Non-expert reading, of one paper or of "
        "several read together, are chosen: what the reader already knows of "
        "what the paper assumes."),
    "harness/reader-profile.md": (
        "Readings", "Using the answers",
        "What every pass of a Non-expert reading is told about the reader's "
        "answers, and how to use them."),
    "harness/reader-cv.md": (
        "Readings", "Using your background",
        "How every reading, the questions before one, and every explanation use the "
        "mathematical background you give in Advanced settings."),
    "harness/SKILL-multi.md": (
        "Readings", "Several papers together",
        "The shared structure of a collection companion: an optional overview "
        "of how the papers are related and one focused summary for each paper."),
    "harness/SKILL-multi-expert.md": (
        "Readings", "Several papers — Expert",
        "How an Expert collection companion applies the Expert paper method to "
        "every paper in the collection."),
    "harness/SKILL-multi-nonexpert.md": (
        "Readings", "Several papers — Non-expert",
        "How a Non-expert collection companion applies the Non-expert paper method "
        "to every paper in the collection."),
    "harness/SKILL-search.md": (
        "Readings", "The literature search",
        "How a search finds papers on a question, confirms each at its source, "
        "and reports them."),
    "harness/roles/auditor.md": (
        "Later passes", "The check",
        "The second pass: a fresh reader tests the companion on its own, then "
        "against the paper."),
    "harness/roles/balanced-reviser.md": (
        "Later passes", "The revision",
        "The third pass, run when the check finds something worth fixing: it "
        "answers each finding."),
    "harness/roles/proof-map.md": (
        "Readings", "The optional diagram",
        "For requested pure-mathematics diagrams: the writer creates, the auditor checks, and the reviser repairs."),
    "harness/harness.toml": (
        "Settings", "The manifest",
        "Which passes run, in what order, with what budgets, tools and "
        "permissions (TOML). Changes to tools and permissions change what the "
        "AI may do on this computer."),
    "harness/roles/graph-builder.md": (
        GROUPS[-1], "The argument map (five passes)",
        "Builds the map of the argument in the lab's five-pass pipeline."),
    "harness/roles/verifier-blind.md": (
        GROUPS[-1], "The blind check (five passes)",
        "Tests the companion without the paper, in the lab's five-pass pipeline."),
    "harness/roles/verifier.md": (
        GROUPS[-1], "The check against the paper (five passes)",
        "Checks the companion against the paper, in the lab's five-pass pipeline."),
    "harness/roles/reviser.md": (
        GROUPS[-1], "The revision (five passes)",
        "Answers the findings, in the lab's five-pass pipeline."),
    "harness/roles/critic.md": (
        GROUPS[-1], "The critic (two passes)",
        "The critique of the lab's original two-role review loop."),
    "explain/instructions.md": (
        "Explanations", "Rules for every answer",
        "What every explanation, answer and key term keeps to."),
    "explain/brief.md": (
        "Explanations", "A short explanation", "What Explain asks for."),
    "explain/more.md": (
        "Explanations", "A longer explanation", "What Explain more asks for."),
    "explain/ask.md": (
        "Explanations", "A question of your own",
        "How a question you ask about a selection, and its follow-ups, are "
        "answered."),
    "explain/glossary.md": (
        "Explanations", "Key terms",
        "How a companion's key terms are chosen. {reader}, {min}, {max} and "
        "{which} are filled in for each level; write a brace meant literally "
        "twice."),
}

# Where each reading file sits in Advanced settings' list (settings.js): a
# paper's two levels side by side, each first choosing pure or applied
# mathematics and then following that profile's instructions (Antoine's
# split of 2026-10-12), then what a Non-expert reading adds, then several
# papers, a search, and what every reading is given. (section, level, part)
_PLACES: dict[str, tuple[str, str, str]] = {
    "harness/SKILL-common.md": ("reader", "", "shared"),
    "harness/SKILL-expert.md": ("paper", "expert", "router"),
    "harness/SKILL-pure-expert.md": ("paper", "expert", "pure"),
    "harness/SKILL-applied-expert.md": ("paper", "expert", "applied"),
    "harness/SKILL-nonexpert.md": ("paper", "nonexpert", "router"),
    "harness/SKILL-pure-nonexpert.md": ("paper", "nonexpert", "pure"),
    "harness/SKILL-applied-nonexpert.md": ("paper", "nonexpert", "applied"),
    "harness/questionnaire.md": ("paper", "nonexpert", "questions"),
    "harness/reader-profile.md": ("paper", "nonexpert", "answers"),
    "harness/SKILL-multi.md": ("collection", "", "shared"),
    "harness/SKILL-multi-expert.md": ("collection", "expert", ""),
    "harness/SKILL-multi-nonexpert.md": ("collection", "nonexpert", ""),
    "harness/SKILL-search.md": ("search", "", ""),
    "harness/reader-cv.md": ("reader", "", "cv"),
}

# The explanation instructions, by file.
_EXPLAIN = {
    "explain/instructions.md": "instructions",
    "explain/brief.md": "brief",
    "explain/more.md": "more",
    "explain/ask.md": "ask",
    "explain/glossary.md": "glossary",
}

# Words the lab fills in before a file reaches the AI. Removing one is allowed,
# but said: the method stops being told what it stood for.
PLACEHOLDERS = {
    "{skill_directory}": "the run's saved pure and applied skill files",
    "{deslopify}": "the command that runs Deslopify's own tools",
    "{document}": "where the paper is",
    "{lane}": "the companion as the pass found it",
    "{reader_level}": "whether the reading is Expert or Non-expert",
    "{round}": "the number of the round of review",
    "{reader}": "who the reader is, for this level",
    "{min}": "the fewest key terms to choose",
    "{max}": "the most key terms to choose",
    "{which}": "which terms suit this level",
}


class MethodError(ValueError):
    """An edit that cannot be kept, said so that it can be put right."""


@dataclass(frozen=True, slots=True)
class Entry:
    path: str          # where it is kept: "harness/roles/auditor.md"
    name: str          # the file's own name: "roles/auditor.md"
    group: str
    title: str
    about: str
    kind: str          # "markdown" | "toml" | "template"
    # Where it is shown among the readings' files (_PLACES); empty otherwise.
    section: str = ""
    level: str = ""
    part: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "group": self.group,
                "title": self.title, "about": self.about, "kind": self.kind,
                "section": self.section, "level": self.level, "part": self.part}


class Method:
    """The reading method's files and the explanations' instructions, with
    the reader's edits — kept under `root`."""

    def __init__(self, cfg: Config, root: Path) -> None:
        self.cfg = cfg
        self.root = root

    # ------------------------------------------------------------ originals

    def family_root(self) -> Path:
        """The reading harness as it ships: Antoine's files."""
        try:
            return get_harness(self.cfg.harnesses_dir, readings.FAMILY).root.resolve()
        except HarnessError as exc:
            raise MethodError(f"The reading method could not be found: {exc}") from exc

    def _policies(self) -> list[str]:
        """The shared rules the reading's passes work under."""
        try:
            base = get_harness(self.cfg.harnesses_dir, readings.FAMILY)
        except HarnessError:
            return ["fidelity"]
        return list(base.shared_policies) or ["fidelity"]

    def _used_roles(self) -> set[str]:
        """The roles readings started here run: those of the two levels'
        pipelines, and the proof map that can be added to them."""
        used = {"proof-map"}
        for variant in readings.VARIANTS.values():
            try:
                h = load_harness(self.family_root(), variant=variant)
            except (HarnessError, MethodError):
                continue
            if h.loop.enabled:
                used |= {h.loop.critic, h.loop.reviser, *h.loop.sequence}
        return used

    def entries(self) -> list[Entry]:
        root = self.family_root()
        used = self._used_roles()
        out: list[Entry] = []

        def add(path: str, name: str, kind: str, fallback: tuple[str, str, str]) -> None:
            group, title, about = _KNOWN.get(path, fallback)
            section, level, part = _PLACES.get(path, ("", "", ""))
            out.append(Entry(path=path, name=name, group=group, title=title,
                             about=about, kind=kind, section=section, level=level,
                             part=part))

        for skill in sorted(root.glob("*.md")):
            add(f"harness/{skill.name}", skill.name, "markdown",
                ("Readings", skill.stem, "A skill of the reading method."))
        for role in sorted((root / "roles").glob("*.md")):
            stem = role.stem
            fallback = (("Later passes" if stem in used else GROUPS[-1]),
                        stem.replace("-", " ").capitalize(),
                        "A pass of the reading method." if stem in used else
                        "A pass of the lab's five-pass pipeline. Readings started here do "
                        "not run it unless the manifest is changed to.")
            add(f"harness/roles/{role.name}", f"roles/{role.name}", "markdown", fallback)
        for name in self._policies():
            path = f"shared/policy/{name}.md"
            add(path, f"policy/{name}.md", "markdown",
                ("Rules", "Fidelity" if name == "fidelity" else name.capitalize(),
                 "The rule every pass of a reading works under: explain the paper, "
                 "never improve it." if name == "fidelity" else
                 "A shared rule the reading's passes work under."))
        add("harness/harness.toml", "harness.toml", "toml",
            ("Settings", "The manifest", "The reading method's manifest."))
        for path in _EXPLAIN:
            add(path, path.split("/", 1)[1],
                "template" if path.endswith("glossary.md") else "markdown",
                ("Explanations", path, ""))
        order = {g: i for i, g in enumerate(GROUPS)}
        return sorted(out, key=lambda e: order.get(e.group, len(GROUPS)))

    def entry(self, path: str) -> Entry:
        found = next((e for e in self.entries() if e.path == path), None)
        if found is None:
            raise MethodError("There is no such file in the method.")
        return found

    def original(self, path: str) -> str:
        """The file as it ships."""
        self.entry(path)
        if path in _EXPLAIN:
            return getattr(explain.DEFAULT_TEXTS, _EXPLAIN[path])
        if path.startswith("harness/"):
            source = self.family_root() / path.removeprefix("harness/")
        else:
            source = self.cfg.shared_dir / path.removeprefix("shared/")
        return source.read_text(encoding="utf-8")

    # ---------------------------------------------------------------- edits

    def _kept(self, path: str) -> Path:
        return self.root / path

    def edited(self, path: str) -> bool:
        return self._kept(path).is_file()

    def current(self, path: str) -> str:
        """The file as the method has it now: the reader's version, else the
        original."""
        kept = self._kept(path)
        if kept.is_file():
            return kept.read_text(encoding="utf-8")
        return self.original(path)

    def edits(self) -> list[str]:
        """Every edited file, of those the method has."""
        return [e.path for e in self.entries() if self.edited(e.path)]

    def reading_edits(self) -> list[str]:
        """The edited files a reading is made from (not the explanations')."""
        return [p for p in self.edits() if not p.startswith("explain/")]

    def save(self, path: str, text: str) -> list[str]:
        """Keep the reader's version of a file, once it is known to work.

        Returns what is worth saying about it — a placeholder the original uses
        and this version does not. A version identical to the original is no
        edit at all: it is removed, and the file is the original again.
        """
        entry = self.entry(path)
        if not isinstance(text, str) or not text.strip():
            raise MethodError("A file cannot be empty. To go back to the original, reset it.")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise MethodError("That is too long for a method file (256 KB at most).")
        text = text.replace("\r\n", "\n")
        original = self.original(path)
        if text == original:
            self.reset(path)
            return []
        headed = original.startswith("---")
        if headed and not text.startswith("---") and path.startswith("harness/roles/"):
            # A role's header says what its pass may write; without it the
            # pass could write nothing, and its reading would fail half-way.
            raise MethodError(
                "Keep the header at the top of the file — the lines between the two "
                "“---” — as it was: it says which files this pass may write.")
        if path in _EXPLAIN:
            self._check_explain(entry, text)
        else:
            self._check_reading(path, text)
        kept = self._kept(path)
        kept.parent.mkdir(parents=True, exist_ok=True)
        staged = kept.with_name(f".{kept.name}.tmp")
        staged.write_text(text, encoding="utf-8")
        staged.replace(kept)
        lost = [p for p in PLACEHOLDERS if p in original and p not in text]
        said = [f"Your version no longer contains {p}, which the original uses for "
                f"{PLACEHOLDERS[p]}." for p in lost]
        if headed and not text.startswith("---"):
            said.append("Your version no longer starts with the header (the lines between "
                        "the two “---”), so the header is read as part of the text.")
        return said

    def reset(self, path: str) -> None:
        self.entry(path)
        self._kept(path).unlink(missing_ok=True)

    def reset_all(self) -> None:
        for path in self.edits():
            self._kept(path).unlink(missing_ok=True)

    # ------------------------------------------------------------- checking

    def _check_explain(self, entry: Entry, text: str) -> None:
        if entry.kind != "template":
            return
        try:
            text.format_map({"reader": "a reader", "min": 1, "max": 2, "which": ""})
        except (KeyError, ValueError, IndexError) as exc:
            raise MethodError(
                "This file may use {reader}, {min}, {max} and {which}, and nothing else "
                f"in braces; write a brace meant literally twice ({exc}).") from exc

    def _check_reading(self, path: str, text: str) -> None:
        """Load the method with this version in it, as a reading would."""
        with tempfile.TemporaryDirectory(prefix="unpacman-method-") as tmp:
            root = self.materialise(Path(tmp) / "method", trial={path: text})
            check(root)

    # ------------------------------------------------------------ a reading

    def materialise(self, dest: Path, *, trial: dict[str, str] | None = None) -> Path:
        """The whole reading method at `dest`: the originals, with the edits —
        and, to check one before keeping it, `trial` — laid over them.

        Shared rules go in the copy's own `policy/`, where a harness's copy of
        a rule takes the place of the lab's (`atelier.compile.policy_path`).
        """
        source = self.family_root()
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(
            "__pycache__", ".DS_Store", "*.pyc"))
        layered = {p: self.current(p) for p in self.reading_edits()} | (trial or {})
        for path, text in layered.items():
            if path.startswith("harness/"):
                target = dest / path.removeprefix("harness/")
            elif path.startswith("shared/policy/"):
                target = dest / "policy" / path.removeprefix("shared/policy/")
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        (dest / "method.json").write_text(json.dumps({
            "edited": sorted(p for p in layered if not p.startswith("explain/")),
            "from": str(source),
        }, indent=1), encoding="utf-8")
        return dest

    def texts(self) -> explain.Texts:
        """The explanations' instructions as they now are."""
        changes = {field: self.current(path) for path, field in _EXPLAIN.items()
                   if self.edited(path)}
        return replace(explain.DEFAULT_TEXTS, **changes) if changes else explain.DEFAULT_TEXTS


def check(root: Path) -> None:
    """A reading method at `root` that the reader can run, or why not."""
    try:
        with (root / "harness.toml").open("rb") as fh:
            tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise MethodError(f"The manifest is not valid TOML: {exc}") from exc
    try:
        base = load_harness(root)
        if base.kind != "read":
            raise MethodError('The manifest must keep kind = "read": a reading may '
                              "not change the paper it explains.")
        with (root / "harness.toml").open("rb") as fh:
            declared = set((tomllib.load(fh).get("variants") or {}))
        for level, variant in readings.VARIANTS.items():
            # A paper's two levels are required. The others — several papers, a
            # search — are checked where present: a manifest edited before they
            # existed still serves readings of a paper.
            if level not in library.LEVELS and variant not in declared:
                continue
            h = load_harness(root, variant=variant)
            if h.id != readings.harness_id(level):
                raise MethodError(
                    f"The manifest's [variants.{variant}] must keep the id "
                    f"{readings.harness_id(level)!r}: it is how readings at that level "
                    "are found.")
    except HarnessError as exc:
        raise MethodError(str(exc)) from exc


def used_by(run_dir: Path) -> list[str] | None:
    """The edited files a reading ran with, if it ran with any."""
    try:
        data = json.loads((run_dir / "method" / "method.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    edited = data.get("edited") if isinstance(data, dict) else None
    return [str(p) for p in edited] if isinstance(edited, list) else None
