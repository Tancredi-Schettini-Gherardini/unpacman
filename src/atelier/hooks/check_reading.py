#!/usr/bin/env python3
"""Stop: hold a reading session, or one pass of one, to its contract.

A reading session has the opposite obligation to a solving one. It must produce
a graph that validates and a guide a human can read, and it must leave the
document **byte-for-byte as it found it**. The last of those is the one worth
enforcing mechanically: "I did not change it" is exactly the kind of claim a
model will make sincerely and wrongly, so the hash is compared rather than the
promise believed.

A run with a critique loop has several passes, and they owe different things. A
critique pass owes a saved review and owes the companion no edits at all; a
revision pass owes an answer to every finding and the full contract
besides. The pass says which it is on the command line rather than the hook
guessing from whatever files it finds lying around.

Like every hook here it blocks at most twice and then relents, recording the
gap. A session that cannot finish its paperwork should still be allowed to end.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

if __package__:
    from ._util import allow, block, counter, parse_args, read_payload
else:
    from _util import allow, block, counter, parse_args, read_payload

MAX_BLOCKS = 2


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deslopify(*argv: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "deslopify", *argv],
        cwd=Path(__file__).resolve().parent,
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def indent(text: str, lines: int = 20) -> str:
    return "\n".join(f"    {ln}" for ln in text.splitlines()[:lines])


def check_document(run: Path, missing: list[str]) -> None:
    """The document under study must be exactly as the session found it."""
    record = run / "document.json"
    if not record.is_file():
        return
    try:
        noted = json.loads(record.read_text(encoding="utf-8"))
        doc = Path(noted["path"])
    except (OSError, ValueError, KeyError):
        return                        # unreadable record: fail open, never wedge

    if not doc.is_file():
        missing.append(
            f"`{doc}` — the document under study is gone. Restore it; "
            f"a reading session must leave it exactly as it found it.")
    elif sha256(doc) != noted["sha256"]:
        missing.append(
            f"`{doc.name}` — **the document has been modified**. A reading "
            f"session must not change the document it explains, not even to "
            f"fix a typo or close a gap. Restore it to its original contents "
            f"(`git checkout -- {doc}` if it is tracked), then finish. What "
            f"you wanted to change belongs in the guide as a finding.")


def recorded_document(run: Path) -> str | None:
    """The document this run is about, as the app recorded it before the start.

    Supplied to every check explicitly. The graph carries its own `document`
    field, but that field is written by whoever wrote the graph — so a graph
    naming a file that does not exist had its quotations accepted unread.
    """
    record = run / "document.json"
    if not record.is_file():
        return None
    try:
        return str(json.loads(record.read_text(encoding="utf-8"))["path"])
    except (OSError, ValueError, KeyError):
        return None


def check_graph(reading: Path, missing: list[str], document: str | None = None) -> None:
    graph_path = reading / "graph.json"
    if not graph_path.is_file():
        missing.append(
            "`progress/reading/graph.json` — the argument graph. Everything else "
            "is a query against it, so it has to exist before the guide means "
            "anything. See the method for the node and edge vocabulary.")
        return
    args = ["check", str(graph_path)]
    if document:
        args += ["--document", document]
    code, detail = deslopify(*args)
    if code != 0:
        missing.append(
            "`progress/reading/graph.json` does not validate:\n" + indent(detail)
            + "\n  Fix these and run `python -m deslopify check "
            + str(graph_path) + "` yourself until it passes.")


def check_guide(reading: Path, missing: list[str]) -> None:
    guide = reading / "guide.md"
    if not guide.is_file() or not guide.read_text(
            encoding="utf-8", errors="replace").strip():
        missing.append(
            "`progress/reading/guide.md` — the companion a reader actually reads: "
            "the sections required by the selected pure or applied method.")
        return

    text = guide.read_text(encoding="utf-8", errors="replace")
    heading_matches = list(re.finditer(
        r"^\s{0,3}(?P<marks>#{1,6})\s+(?P<title>.+?)\s*#*\s*$",
        text, re.MULTILINE))
    headings = [match.group("title") for match in heading_matches]

    def normalise_heading(value: str) -> str:
        value = re.sub(r"^\s*\d+[.)]?\s*", "", value)
        value = re.sub(r"[*_`]", "", value).strip().lower()
        return re.sub(r"\s+", " ", value)

    found = {normalise_heading(heading) for heading in headings}
    validation = "about the method and experimental validation"
    legacy_validation = "about the validation, experiments, and proofs"
    profile = "applied" if {validation, legacy_validation} & found else "pure"
    try:
        selections = json.loads((reading / "paper-profile.json").read_text())["papers"]
        if len(selections) == 1 and selections[0].get("profile") in {"pure", "applied"}:
            profile = selections[0]["profile"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass  # Older companions have no routing record; their headings still work.
    required = (
        "main findings", "q&a", "historical context",
        validation,
    ) if profile == "applied" else (
        "main results", "main ideas", "about the proofs", "q&a",
        "historical context",
    )
    absent = [heading for heading in required if heading not in found
              and not (heading == "about the proofs" and "about the proof" in found)
              and not (heading == "main findings" and "main results" in found)
              and not (heading == validation and legacy_validation in found)]
    if absent:
        missing.append(
            "`progress/reading/guide.md` is missing required section heading(s): "
            + ", ".join(absent)
            + ". Use the exact headings required by the selected method.")

    if "not a certification of" not in text.lower() \
            or "correctness" not in text.lower() \
            or "responsible for checking the source" not in text.lower():
        missing.append(
            "`progress/reading/guide.md` is missing the short **Notice** about "
            "correctness and the reader's responsibility required near its "
            "beginning.")

    by_name = {
        normalise_heading(match.group("title")): match
        for match in heading_matches
    }
    history = by_name.get("historical context")
    references = by_name.get("references cited in this companion")
    thin = by_name.get("where the document is thin")

    if references is None:
        missing.append(
            "`progress/reading/guide.md` must end with the mandatory "
            "**References cited in this companion** bibliography. "
            "A Citation scope limitation may qualify the account but cannot "
            "replace its sources.")
        return

    if history is None or (thin is not None and thin.start() <= history.end()) \
            or references.start() <= (thin.end() if thin else history.end()) \
            or heading_matches[-1].start() != references.start():
        missing.append(
            "**References cited in this companion** must be the final "
            "bibliography, after the substantive sections.")
        return

    reference_level = len(references.group("marks"))
    references_end = len(text)
    for match in heading_matches:
        if match.start() > references.start() \
                and len(match.group("marks")) <= reference_level:
            references_end = match.start()
            break

    historical_prose = text[history.end():(thin.start() if thin else references.start())]
    bibliography = text[references.end():references_end]
    guide_prose = text[:references.start()] + text[references_end:]
    # Compact alphabetic labels: Mil56, GM74, ABC89, ABC+89. Keeping the
    # pre-year part short is also what prevents bibliography labels from
    # consuming the text column in the generated LaTeX.
    key_pattern = r"[A-Za-z]{2,5}\+?[0-9]{2}[a-z]?"

    def citation_keys(prose: str) -> set[str]:
        keys: set[str] = set()
        for match in re.finditer(r"\[([^\]\n]+)\](?!\()", prose):
            # A bracket may cite several works, with optional locators:
            # [Wil06, pp. 2–5; BS65; Tat66] or [BS65, Tat66]. Appendix
            # locators such as A.10 are not bibliography keys; Markdown link
            # labels are excluded above.
            for item in re.split(r"[;,]", match.group(1)):
                key = item.strip()
                if re.fullmatch(key_pattern, key):
                    keys.add(key)
        return keys

    historical_cited = citation_keys(historical_prose)
    cited = citation_keys(guide_prose)

    entry_pattern = re.compile(
        rf"^\s*[-*]\s+\[({key_pattern})\]\s+(.+?)"
        rf"(?=^\s*[-*]\s+\[{key_pattern}\]\s+|\Z)",
        re.MULTILINE | re.DOTALL)
    entries = entry_pattern.findall(bibliography)
    entry_keys = [key for key, _ in entries]

    if not historical_cited:
        missing.append(
            "Historical context contains no academic in-text citation. Cite "
            "claims with compact mathematical bibliography keys such as `[Mil56]`.")
    if not entries:
        missing.append(
            "**References cited in this companion** contains no bibliography "
            "entry in the required form `- [Abc89] Full entry.`")
        return

    duplicate_keys = sorted({key for key in entry_keys if entry_keys.count(key) > 1})
    if duplicate_keys:
        missing.append(
            "Duplicate bibliography key(s): " + ", ".join(duplicate_keys) + ".")

    defined = set(entry_keys)
    unresolved = sorted(cited - defined)
    unused = sorted(defined - cited)
    if unresolved:
        missing.append(
            "Citation key(s) without a bibliography entry: "
            + ", ".join(unresolved) + ".")
    if unused:
        missing.append(
            "Bibliography entry key(s) not cited in the companion: "
            + ", ".join(unused) + ".")

    incomplete = []
    for key, entry in entries:
        words = re.findall(r"\b\w+\b", entry)
        if not re.search(r"\b(?:18|19|20)\d{2}\b", entry) or len(words) < 8:
            incomplete.append(key)
    if incomplete:
        missing.append(
            "Bibliography entry key(s) appear incomplete (each needs authors, "
            "title, publication information, and year): "
            + ", ".join(sorted(incomplete)) + ".")


def check_guide_written(reading: Path, missing: list[str]) -> None:
    """The guide, whatever its method asks it to hold — for a method whose
    contract is only that (`--contract guide`): several papers read together,
    a search. Its shape is the method's business, not this hook's."""
    guide = reading / "guide.md"
    if not guide.is_file() or not guide.read_text(
            encoding="utf-8", errors="replace").strip():
        missing.append(
            "`progress/reading/guide.md` — what your method asks you to write. "
            "It is missing or empty.")


def check_companion(reading: Path, missing: list[str]) -> None:
    """The reader-facing compiled file is the guide with its generation date."""
    from deslopify.summary_pdf import dated_guide

    guide = reading / "guide.md"
    markdown = reading / "companion.md"
    tex = reading / "companion.tex"
    pdf = reading / "companion.pdf"
    if not guide.is_file():
        return  # their own checks give the more useful error
    expected = dated_guide(guide) + "\n"
    if not markdown.is_file() or markdown.read_text(
            encoding="utf-8", errors="replace") != expected:
        missing.append(
            "`progress/reading/companion.md` — the exact reader-facing copy of "
            "the guide is missing or stale. Generate all forms with "
            "`python -m deslopify companion progress/reading/guide.md --markdown "
            "progress/reading/companion.md --tex progress/reading/companion.tex "
            "--audience expert --pdf "
            "progress/reading/companion.pdf`.")
        return
    if not tex.is_file():
        missing.append(
            "`progress/reading/companion.tex` — the persistent LaTeX source is "
            "missing. Regenerate all companion outputs with the `deslopify "
            "companion` command.")
        return
    tex_text = tex.read_text(encoding="utf-8", errors="replace")
    if ("\\documentclass" not in tex_text or "\\begin{document}" not in tex_text
            or "\\end{document}" not in tex_text):
        missing.append(
            "`progress/reading/companion.tex` is not a complete LaTeX document.")
    if ("Reader's guide to" not in tex_text
            and not re.search(r"Reader['’]s companion to", tex_text)
            or not re.search(r"(?:non-expert|expert) (?:mode|edition)", tex_text)):
        missing.append(
            "`progress/reading/companion.tex` must identify the paper and the "
            "expert or non-expert mode in its title.")
    if tex.stat().st_mtime_ns < markdown.stat().st_mtime_ns:
        missing.append(
            "`progress/reading/companion.tex` is older than `companion.md`. "
            "Regenerate all reader-facing outputs after the last guide edit.")
    if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        missing.append(
            "`progress/reading/companion.pdf` — the directly viewable guide "
            "is missing or invalid. Regenerate it with the "
            "`deslopify companion` command.")
    elif pdf.stat().st_mtime_ns < max(markdown.stat().st_mtime_ns,
                                     tex.stat().st_mtime_ns):
        missing.append(
            "`progress/reading/companion.pdf` is older than its Markdown or "
            "LaTeX source. "
            "Regenerate all reader-facing outputs after the last guide edit.")


def check_map(reading: Path, missing: list[str]) -> None:
    graph = reading / "graph.json"
    source = reading / "map.mmd"
    if not source.is_file() or not source.read_text(
            encoding="utf-8", errors="replace").strip():
        missing.append(
            "`progress/reading/map.mmd` — the Mermaid source generated from "
            "the argument graph.")
    elif graph.is_file() and source.stat().st_mtime_ns < graph.stat().st_mtime_ns:
        missing.append(
            "`progress/reading/map.mmd` is older than `graph.json`. Regenerate "
            "it from the final graph before finishing.")
    pdf = reading / "map.pdf"
    if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        missing.append(
            "`progress/reading/map.pdf` — the directly viewable graph. Generate "
            "it with `python -m deslopify map progress/reading/graph.json "
            "--pdf progress/reading/map.pdf`.")
    elif graph.is_file() and pdf.stat().st_mtime_ns < graph.stat().st_mtime_ns:
        missing.append(
            "`progress/reading/map.pdf` is older than `graph.json` and may show "
            "the previous graph. Regenerate it with `python -m deslopify map "
            "progress/reading/graph.json --pdf progress/reading/map.pdf`.")


def check_lane(args, reading: Path, missing: list[str]) -> None:
    """A critique pass must have left the companion alone.

    The controller restores anything it changed regardless, so this is not the
    enforcement — it is the feedback. Being told now, while the pass can still
    act, is worth more than discovering afterwards that an edit was discarded.
    """
    snapshot = args.run / "loop" / "lane"
    if not snapshot.is_dir():
        return
    # A deletion counts. Comparing only files present in both would have let
    # `rm guide.md` pass the check that exists to notice edits to it.
    owned = ({"companion.md", "companion.tex", "companion.pdf"}
             if args.role == "auditor" else set())
    touched = [
        name for name in ("graph.json", "guide.md",
                          "map.mmd", "map.pdf", "companion.md", "companion.tex",
                          "companion.pdf")
        if name not in owned
        if (snapshot / name).is_file()
        and (not (reading / name).is_file()
             or (snapshot / name).read_bytes() != (reading / name).read_bytes())
    ]
    if touched:
        missing.append(
            "you have edited or removed "
            + ", ".join(f"`progress/reading/{n}`" for n in touched)
            + ", which this pass does not own. A critique records what is wrong; "
              "the revision pass that follows decides what to do about it. Restore "
              "what you changed — it will be restored anyway, so the edit is lost "
              "work either way — and put what you wanted to change into a finding.")


def check_expected(args, root: Path, missing: list[str]) -> None:
    """Require the pass's output file; only non-review outputs are graded."""
    expected = root / args.expect
    reading = root / "progress" / "reading"
    graph = reading / "graph.json"

    if not expected.is_file() or not expected.read_text(
            encoding="utf-8", errors="replace").strip():
        missing.append(
            f"`{args.expect}` — the file this pass exists to produce. "
            + ("Record concrete questions left by the guide and graph before "
               "consulting the original paper."
               if args.validate == "clarity" else
               "Record what the companion gets wrong, each finding located and, "
               "where it is a claim about the document, quoted."
               if args.validate in {"review", "review-output"} else
               "Record every finding in the review as fixed or rejected; "
               "rejecting one is free, passing over it in silence is not."))
        return

    if args.validate in {"clarity", "review", "review-output"}:
        return

    review = reading / f"review-{args.round}.json"
    # The companion as the critique found it: a fix may legitimately name
    # the node, edge or section it removed, which no longer resolves in the
    # state this pass is leaving behind.
    lane = args.run / "loop" / "lane"
    cmd = ["dispose", str(review), str(expected),
           "--guide", str(reading / "guide.md"),
           "--before-guide", str(lane / "guide.md")]
    if graph.is_file():
        cmd += ["--graph", str(graph),
                "--before-graph", str(lane / "graph.json")]
    if (doc := recorded_document(args.run)):
        cmd += ["--document", doc]
    code, detail = deslopify(*cmd)
    if code != 0:
        missing.append(f"`{args.expect}` does not validate:\n" + indent(detail)
                       + "\n  Fix these and run the same check yourself until it passes.")


def main() -> None:
    args = parse_args()
    payload = read_payload()
    root = args.problem.resolve()
    reading = root / "progress" / "reading"

    already_active = bool(payload.get("stop_hook_active"))
    missing: list[str] = []

    check_document(args.run, missing)
    try:
        requested = json.loads((args.run / "config.json").read_text()).get(
            "resolved", {}).get("include_proof_map") is True
    except (OSError, ValueError, AttributeError):
        requested = False
    if args.contract == "guide":
        # Only the guide is owed; a pass of such a method that keeps a graph
        # still has it checked, as any graph is.
        guide_check = check_guide_written

        def companion_check(reading: Path, missing: list[str]) -> None:
            return None
    else:
        guide_check, companion_check = check_guide, check_companion

    if args.validate == "draft":
        # The controller carries structural omissions to review and revision;
        # only final delivery requires a complete companion.
        check_guide_written(reading, missing)
    elif args.validate == "guide":
        guide_check(reading, missing)
    elif args.validate == "graph":
        check_lane(args, reading, missing)
        check_graph(reading, missing, recorded_document(args.run))
        check_map(reading, missing)
        companion_check(reading, missing)
    elif args.validate == "clarity":
        check_lane(args, reading, missing)
        check_expected(args, root, missing)
    elif args.validate in {"review", "review-output"}:
        # Balanced's auditor may export the companion, but only reviews the diagram.
        check_lane(args, reading, missing)
        check_expected(args, root, missing)
        if args.role == "auditor":
            companion_check(reading, missing)
            if not requested and (reading / "graph.json").is_file():
                check_graph(reading, missing, recorded_document(args.run))
                check_map(reading, missing)
    elif args.validate == "dispose":
        check_expected(args, root, missing)
        guide_check(reading, missing)
        companion_check(reading, missing)
        if not requested and (args.role != "balanced-reviser" or (reading / "graph.json").is_file()):
            check_graph(reading, missing, recorded_document(args.run))
            check_map(reading, missing)
    elif args.contract == "guide":
        # A single pass that owes only its guide: no graph, no map.
        guide_check(reading, missing)
    else:
        check_graph(reading, missing, recorded_document(args.run))
        check_guide(reading, missing)
        check_map(reading, missing)
        check_companion(reading, missing)

    if requested and args.role in {"draft", "auditor", "balanced-reviser"}:
        from atelier.delivery import diagram_problems
        from deslopify.source import SourceError, read_source

        source = args.run / "source-excerpt.txt"
        document = None
        try:
            if source.is_file():
                document = source.read_text()
            elif path := recorded_document(args.run):
                document = read_source(Path(path))
        except (OSError, SourceError):
            pass
        missing.extend(diagram_problems(reading, True, document))

    notes = root / "progress" / "_session_notes.md"
    if not notes.is_file() or not notes.read_text(
            encoding="utf-8", errors="replace").strip():
        missing.append(
            "`progress/_session_notes.md` — your account of this session: what was "
            "hard to map and why, where the document defeated you, and what a "
            "later reading should look at first.")

    if not missing:
        allow()

    # Each pass gets its own two chances: a revision should not be refused a
    # hearing because the draft before it used them up.
    key = f"reading_contract{('_' + args.role + str(args.round)) if args.role else ''}"
    seen = counter(args.run, key)
    if already_active or seen > MAX_BLOCKS:
        try:
            record = args.run / "contract_violations.json"
            existing = json.loads(record.read_text(encoding="utf-8")) \
                if record.is_file() else {}
            if not isinstance(existing, dict) or "missing" in existing:
                existing = {"passes": [existing]} if existing else {"passes": []}
            existing.setdefault("passes", []).append(
                {"role": args.role or "draft", "round": args.round,
                 "missing": missing, "blocks": seen})
            record.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
        allow()

    label = f"This {args.role or 'reading'} pass" if args.role else "This reading session"
    block(f"{label} is not finished:\n\n"
          + "\n\n".join(f"  - {m}" for m in missing))


if __name__ == "__main__":
    main()
