"""Per-paper conversations. Only individual messages are changed by a tab."""

from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import explain
from .library import Paper

STORE = Path(".atelier") / "conversations.json"
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def reference(value: Any) -> tuple[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or any(
        not isinstance(value.get(k), str) or not _ID.fullmatch(value[k])
        for k in ("note", "message")
    ):
        raise HTTPException(400, "Invalid conversation or message ID.")
    return value["note"], value["message"]


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.active: set[tuple[str, str, str]] = set()

    @staticmethod
    def _load(paper: Paper) -> dict[str, Any]:
        try:
            return json.loads((paper.root / STORE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "notes": [], "removed": []}

    @staticmethod
    def _save(paper: Paper, data: dict[str, Any]) -> None:
        path = paper.root / STORE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _note(data: dict[str, Any], uid: str) -> dict[str, Any] | None:
        return next((n for n in data["notes"] if n["uid"] == uid), None)

    def existing(self, paper: Paper, ref: tuple[str, str]) -> dict[str, Any] | None:
        with self.lock:
            data = self._load(paper)
            if ref[0] in data["removed"]:
                raise HTTPException(409, "This note was removed. Ask in a new note.")
            note = self._note(data, ref[0])
            message = next((m for m in note["messages"] if m["uid"] == ref[1]), None) if note else None
            # Retrying an interrupted or failed message is an explicit new attempt.
            if message and (message["status"] == "done" or
                            (paper.slug, *ref) in self.active):
                return deepcopy(message)
            return None

    def begin(self, paper: Paper, ref: tuple[str, str], request: dict[str, Any],
              metadata: dict[str, Any]) -> None:
        with self.lock:
            data = self._load(paper)
            if ref[0] in data["removed"]:
                raise HTTPException(409, "This note was removed. Ask in a new note.")
            note = self._note(data, ref[0])
            if note is None:
                seed = metadata.get("seed")
                if ref[1] != "root" and not (ref[1] == "more" and isinstance(seed, str)):
                    raise HTTPException(409, "Open the note again before asking a follow-up.")
                anchor = metadata.get("anchor")
                # Anchors are data for the existing text locator, never HTML or paths.
                anchor = {k: anchor[k] for k in ("kind", "find", "section", "label", "page", "note", "reading")
                          if k in anchor} if isinstance(anchor, dict) else None
                note = {
                    "uid": ref[0], "key": str(metadata.get("key", "")),
                    "text": request["text"], "context": request["context"],
                    "where": request["where"], "anchor": anchor,
                    "reading": {"level": request["level"], "run": request.get("run")},
                    "created": datetime.now(UTC).isoformat(), "messages": [],
                }
                if ref[1] == "more":
                    note["fromTerm"] = True
                    note["messages"].append({
                        "uid": "root", "status": "done", "request": request | {"question": ""},
                        "answer": {"markdown": seed},
                    })
                data["notes"].append(note)
            message = next((m for m in note["messages"] if m["uid"] == ref[1]), None)
            if message is None:
                message = {"uid": ref[1]}
                note["messages"].append(message)
            elif message.get("status") == "done" or (paper.slug, *ref) in self.active:
                return
            message.update(status="pending", request=deepcopy(request))
            message.pop("error", None)
            self._save(paper, data)
            self.active.add((paper.slug, *ref))

    def finish(self, paper: Paper, ref: tuple[str, str], item: dict[str, Any] | None,
               error: str | None) -> None:
        with self.lock:
            self.active.discard((paper.slug, *ref))
            data = self._load(paper)
            note = self._note(data, ref[0])
            if note is None:
                return                         # a removed note stays removed
            message = next(m for m in note["messages"] if m["uid"] == ref[1])
            if message.get("status") == "done":
                return
            if item is not None:
                message.update(status="done", answer=item)
            else:
                message.update(status="error", error=error or "The explanation failed.")
            self._save(paper, data)

    def remove(self, paper: Paper, uid: str) -> None:
        if not _ID.fullmatch(uid):
            raise HTTPException(400, "Invalid conversation ID.")
        with self.lock:
            data = self._load(paper)
            data["notes"] = [n for n in data["notes"] if n["uid"] != uid]
            if uid not in data["removed"]:
                data["removed"].append(uid)
            self._save(paper, data)

    def public(self, paper: Paper, pages: int) -> list[dict[str, Any]]:
        with self.lock:
            data = self._load(paper)
            cards = []
            for note in data["notes"]:
                card = {k: v for k, v in note.items() if k != "messages"}
                card.update(state="open", thread=[])
                for message in note["messages"]:
                    request = message["request"]
                    shown = {"uid": message["uid"], "status": message["status"],
                             "request": request, "q": request.get("question", "")}
                    if message["status"] == "done":
                        answer = explain.public(message["answer"], pages)
                        shown.update({k: answer[k] for k in ("html", "agent", "seconds", "cost")})
                    elif (paper.slug, note["uid"], message["uid"]) not in self.active:
                        shown.update(status="error", error=message.get("error") or
                                     "Unpacman stopped before this answer finished. Try again.")
                    if message["uid"] == "root":
                        card.update({k: v for k, v in shown.items() if k not in ("uid", "q")})
                        card["question"] = request.get("question") or None
                        card["who"] = {k: request[k] for k in ("account", "model", "effort", "custom")}
                    elif message["uid"] == "more":
                        card["more"] = shown
                    else:
                        card["thread"].append(shown)
                cards.append(card)
            return cards
