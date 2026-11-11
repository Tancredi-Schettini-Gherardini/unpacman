"""What the reader already knows: questions asked before a Non-expert reading.

A Non-expert companion is written for "a mathematician from outside the
field", and there are many of those: a topologist and a probabilist reading the
same geometry paper need different things explained. So before the reading
starts, the reader answers questions about what the paper assumes — "Do you
know the definition of a Riemannian metric?" — with follow-ups one step down
where they do not ("…of a smooth manifold?"). Their answers are written into
the run (`RunSpec.reader_profile`), which puts them in front of every pass: the
writer pitches the companion at them, the check measures it against them, and
the revision answers for them.

The questions are one call, like an explanation (`explain.call`): the paper,
page by page, and the method's instructions for choosing them
(`harness/questionnaire.md`, editable in Advanced settings). Kept with the
paper per model and per version of those instructions, so asking again is
free; the reader's last answers are kept too, to start from next time.

Several papers read together are asked about in the same way, before their
Non-expert collection companion (`ASKING_LEVEL`): one set of questions for the
whole set, made from every one of its papers (`whole_set`), the instructions
followed by a few words on what a set is (`TOGETHER`). Kept with the set.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import agents, explain, library
from .agents import Agent
from .library import Paper

STORE = Path(".atelier") / "questions.json"
ANSWERS = Path(".atelier") / "reader-answers.json"

# The reading that each kind of thing asks its questions before: a paper's
# Non-expert reading, and a collection's — several papers read together, one
# companion for them all (since 2026-10-26). An Expert reading asks nothing,
# and a search is written for no reader.
ASKING_LEVEL = {"paper": "general", "collection": "collection-nonexpert"}

# What the AI is told after the method's instructions, which are written for
# one paper, when the questions are for several papers read together. Here
# rather than in `harness/questionnaire.md`, so that it holds under a reader's
# own version of that file too, and so that a paper's instructions — and the
# questions kept under them — stay exactly as they were.
TOGETHER = """\
Several papers are read together here, and one companion will be written for
all of them. The text below holds each paper in turn, headed `## Paper i of
n: <title>`, with its pages marked `[page k]`; a paper too long to send whole
is given from its beginning, and says where it stops. Write one set of
questions for the whole collection:

- Ask first about the background that several of the papers rely on — once,
  not once for each paper — then about what each paper needs that the others
  do not, so that every paper is covered.
- Do not ask about any paper's own new definitions or results, even where
  another paper of the set relies on them: the companion explains those to
  everyone.
- Between 10 and 25 questions in all, follow-ups included, as for one paper;
  a collection that draws on several fields may need more than one paper
  alone, but never more than 25.
- In `where`, name the paper before the place: "Paper 2, §3, p. 5". For what
  several papers rely on, name the first that needs it, or give "" if they
  use it throughout.
"""

ANSWER_NAMES = {"yes": "Yes", "roughly": "Roughly", "no": "No"}
KINDS = ("definition", "theorem", "argument", "technique", "object", "notation")
MIN_QUESTIONS = 10
MAX_QUESTIONS = 30
# Below MIN_QUESTIONS the call is made once more; after that, as few as this
# are accepted rather than nothing — a short paper may honestly assume little.
FLOOR = 5
MAX_DEPTH = 3
TIMEOUT_S = 240
# The questions are made at this effort at most, whatever effort the reading
# itself will use: at the reading's own (high or above), a long paper took
# longer than TIMEOUT_S (2026-11-11). A lower effort chosen for the reading is
# kept, so that choosing Low makes them quicker still (TOO_LONG says so).
EFFORT = "medium"
_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
TOO_LONG = ("Generating the questions took too long. Try a lower effort for this stage, "
            "then switch back to a higher effort when you start the reading.")


def effort(chosen: str | None) -> str:
    """The effort the questions are made at: the one chosen for the reading if
    it is EFFORT or lower, else EFFORT."""
    if chosen in _EFFORTS and _EFFORTS.index(chosen) <= _EFFORTS.index(EFFORT):
        return chosen
    return EFFORT

_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")

ASK = ("Write the questions now. Reply with the JSON object only.")
ASK_MORE = ("That was only {n} usable question(s). Write the questions again: "
            "between 10 and 25, follow-ups included. Reply with the JSON object only.")


class QuestionsError(RuntimeError):
    """The questions could not be made, said so a reader can act on it."""


# --------------------------------------------------------------------------
# reading what the AI wrote
# --------------------------------------------------------------------------


def _json_object(text: str) -> Any:
    """The JSON object in an answer, with or without a code fence round it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    return json.loads(text[start:end + 1])


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse(text: str) -> list[dict[str, Any]]:
    """The usable questions in an answer, in order, at most MAX_QUESTIONS.

    A question with no text, a repeated id, a parent that is not an earlier
    question, or more than MAX_DEPTH levels deep is dropped — and every
    follow-up of a dropped question with it, since it would be asked about
    nothing.
    """
    try:
        raw = _json_object(text)
    except ValueError:
        return []
    items = raw.get("questions") if isinstance(raw, dict) else raw
    out: list[dict[str, Any]] = []
    depth: dict[str, int] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "").strip()
        question = _clean(item.get("question"), 300)
        if not _ID.match(ident) or ident in depth or not question:
            continue
        parent = item.get("parent")
        parent = str(parent).strip() if parent not in (None, "", "null") else None
        if parent is not None and parent not in depth:
            continue
        level = depth[parent] + 1 if parent else 1
        if level > MAX_DEPTH:
            continue
        ask_if = item.get("ask_if")
        ask_if = [a for a in (ask_if if isinstance(ask_if, list) else [])
                  if a in ANSWER_NAMES]
        if parent and not ask_if:
            ask_if = ["no", "roughly"]
        kind = str(item.get("kind") or "").strip().lower()
        out.append({
            "id": ident, "question": question,
            "topic": _clean(item.get("topic"), 120) or question,
            "kind": kind if kind in KINDS else "object",
            "where": _clean(item.get("where"), 80),
            "parent": parent, "ask_if": ask_if if parent else [],
        })
        depth[ident] = level
        if len(out) >= MAX_QUESTIONS:
            break
    return out


# --------------------------------------------------------------------------
# what the answers say, for every pass of the reading
# --------------------------------------------------------------------------


def asked(questions: list[dict[str, Any]], answers: dict[str, str]) -> list[dict[str, Any]]:
    """The questions the reader was actually shown: every top-level one, and a
    follow-up only when its parent's answer called for it."""
    shown: set[str] = set()
    out = []
    for q in questions:
        parent = q.get("parent")
        if parent is None or (parent in shown and answers.get(parent) in q.get("ask_if", [])):
            shown.add(q["id"])
            out.append(q)
    return out


def clean_answers(questions: list[dict[str, Any]], raw: Any) -> dict[str, str]:
    """The answers to questions that were shown, and nothing else."""
    raw = raw if isinstance(raw, dict) else {}
    given = {str(k): str(v) for k, v in raw.items() if v in ANSWER_NAMES}
    return {q["id"]: given[q["id"]] for q in asked(questions, given) if q["id"] in given}


def profile_text(instructions: str, questions: list[dict[str, Any]],
                 answers: dict[str, str]) -> str:
    """The reader, as every pass of the reading is told about them: the
    method's instructions for using the answers (`harness/reader-profile.md`),
    then the answers, grouped by what they say."""
    shown = asked(questions, answers)
    by_id = {q["id"]: q for q in questions}
    groups: dict[str, list[str]] = {"yes": [], "roughly": [], "no": [], "": []}
    for q in shown:
        line = f"- **{q['topic']}** ({q['kind']}"
        line += f", {q['where']})" if q.get("where") else ")"
        parent = by_id.get(q.get("parent") or "")
        if parent is not None:
            said = ANSWER_NAMES.get(answers.get(parent["id"], ""), "").lower()
            line += f" — asked because they answered “{said}” about {parent['topic']}"
        groups[answers.get(q["id"], "")].append(line)
    heads = {"yes": "They know these well", "roughly": "They know these roughly",
             "no": "They do not know these", "": "They did not answer about these"}
    body = [f"## The reader's answers\n\n{len(shown)} questions were asked; "
            f"{sum(1 for q in shown if q['id'] in answers)} answered."]
    for key in ("no", "roughly", "", "yes"):
        if groups[key]:
            body.append(f"### {heads[key]}\n\n" + "\n".join(groups[key]))
    return instructions.strip() + "\n\n" + "\n\n".join(body) + "\n"


# --------------------------------------------------------------------------
# kept with the paper
# --------------------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    staged.replace(path)


def store_key(model: str, instructions: str) -> str:
    """One set of questions per model and per version of the instructions."""
    import hashlib

    return f"{model}|{hashlib.sha256(instructions.encode('utf-8')).hexdigest()[:10]}"


def kept(paper: Paper, key: str) -> dict[str, Any] | None:
    entry = _read(paper.root / STORE).get(key)
    return entry if isinstance(entry, dict) and entry.get("questions") else None


def last_answers(paper: Paper, key: str, questions: list[dict[str, Any]]) -> dict[str, str]:
    """The reader's last answers on this paper: by id for these very
    questions, else by topic — a different model asks about the same things
    under other ids, and an id means nothing outside its own set."""
    saved = _read(paper.root / ANSWERS)
    by_id = saved.get("answers") if isinstance(saved.get("answers"), dict) else {}
    by_topic = saved.get("topics") if isinstance(saved.get("topics"), dict) else {}
    same = saved.get("key") == key
    out: dict[str, str] = {}
    for q in questions:
        hit = by_id.get(q["id"]) if same else None
        hit = hit or by_topic.get(q["topic"].lower())
        if hit in ANSWER_NAMES:
            out[q["id"]] = hit
    return clean_answers(questions, out)


def remember(paper: Paper, key: str, questions: list[dict[str, Any]],
             answers: dict[str, str]) -> None:
    by_id = {q["id"]: q for q in questions}
    _write(paper.root / ANSWERS, {
        "key": key, "answers": answers,
        "topics": {by_id[i]["topic"].lower(): a for i, a in answers.items() if i in by_id},
        "updated": datetime.now(UTC).isoformat(timespec="seconds"),
    })


# --------------------------------------------------------------------------
# making them
# --------------------------------------------------------------------------


def whole_set(pages: list[str]) -> list[str]:
    """A collection's papers as its questions are made from them: all of them,
    each cut to its beginning when together they are too long to send whole.

    Short of room, an explanation leaves whole papers out, keeping the first
    ones (`explain._select_pages`); questions made that way would ask nothing
    about a paper that the companion covers like the others, and what a paper
    assumes is mostly met in its first pages. The cut is the one a reading
    makes (`source.beginning_within`): the shorter papers whole, the longer
    ones each down to the same length, every cut marked where it falls.
    """
    limit = explain.MAX_PAPER_CHARS
    if sum(len(p) for p in pages) <= limit:
        return pages
    from deslopify.source import SourceError, beginning_within

    try:
        cut, _ = beginning_within("\n\n".join(pages), limit, collection=True)
    except SourceError:
        return pages
    return library.split_members(cut)


@dataclass(slots=True)
class Made:
    key: str
    questions: list[dict[str, Any]]
    agent: dict[str, Any]
    cost: float | None
    seconds: float
    created: str
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "questions": self.questions, "agent": self.agent,
                "cost": self.cost, "seconds": self.seconds, "created": self.created,
                "cached": self.cached}


class Questioner:
    """Makes the questions for a paper: once per model and instructions, kept,
    and asked for once however many pages ask at the same time."""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.flight: dict[str, asyncio.Future[Made | str]] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    async def get(self, paper: Paper, agent: Agent, instructions: str,
                  reader: str = "") -> Made:
        """`reader`: the reader's own account of their background (cv.block),
        if they gave one — questions chosen for one background are not the
        questions for another, so it is part of what they are kept under.
        Several papers read together are asked about with `TOGETHER` after the
        instructions, which are kept under it too."""
        if getattr(paper, "kind", "paper") == "collection":
            instructions = instructions.rstrip() + "\n\n" + TOGETHER
        key = store_key(agent.model, instructions + (f"\n\n{reader}" if reader else ""))
        if (hit := kept(paper, key)) is not None:
            return Made(key=key, questions=hit["questions"], agent=hit.get("agent") or {},
                        cost=hit.get("cost"), seconds=hit.get("seconds") or 0,
                        created=hit.get("created") or "", cached=True)
        flight = f"{paper.slug}|{key}"
        future = self.flight.get(flight)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self.flight[flight] = future
            task = asyncio.create_task(self._make(paper, agent, instructions, key,
                                                  flight, future, reader))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        result = await asyncio.shield(future)
        if isinstance(result, str):
            raise QuestionsError(result)
        return result

    async def _make(self, paper: Paper, agent: Agent, instructions: str, key: str,
                    flight: str, future: asyncio.Future[Made | str],
                    reader: str = "") -> None:
        # Whatever happens — even a cancellation — whoever waits is answered.
        outcome: Made | str = "The questions could not be made."
        try:
            started = time.monotonic()
            pages = await asyncio.to_thread(explain.paper_pages, paper)
            if getattr(paper, "kind", "paper") == "collection":
                pages = whole_set(pages)
            system = explain.system_prompt(
                paper, pages, None, with_companion=False,
                texts=explain.Texts(instructions=instructions.rstrip() + "\n"), reader=reader)
            cost = 0.0
            priced = True
            questions: list[dict[str, Any]] = []
            prompt = ASK
            for attempt in (1, 2):
                answer: explain.Answer | None = None
                async for kind, value in explain.call(self.cfg, agent, system, prompt,
                                                      timeout=TIMEOUT_S):
                    if kind == "answer":
                        answer = value
                assert answer is not None
                if answer.cost is None:
                    priced = False
                else:
                    cost += answer.cost
                questions = parse(answer.text)
                if len(questions) >= MIN_QUESTIONS:
                    break
                prompt = ASK_MORE.format(n=len(questions))
            if len(questions) < FLOOR:
                raise QuestionsError(
                    "The questions could not be made: the answer did not contain "
                    "usable questions. Try again, or start without them.")
            outcome = Made(
                key=key, questions=questions,
                agent=agents.describe(agent.provider, agent.account, agent.model, agent.effort),
                cost=round(cost, 5) if priced else None,
                seconds=round(time.monotonic() - started, 1),
                created=datetime.now(UTC).isoformat(timespec="seconds"))
            store = _read(paper.root / STORE)
            store[key] = {k: v for k, v in outcome.to_dict().items()
                          if k not in ("key", "cached")}
            _write(paper.root / STORE, store)
        except (QuestionsError, explain.ExplainError) as exc:
            # The time limit (explain.call raises it from a TimeoutError): said
            # of the questions, with what to do, not as an explanation's.
            outcome = TOO_LONG if isinstance(exc.__cause__, TimeoutError) else str(exc)
        except Exception as exc:                                    # noqa: BLE001
            outcome = f"The questions could not be made ({type(exc).__name__}: {exc})."
        finally:
            self.flight.pop(flight, None)
            if not future.done():
                future.set_result(outcome)
