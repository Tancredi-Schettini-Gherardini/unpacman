"""Hearing about new versions: a reader's email address, given once, for that alone.

Asked once per copy of unpacman, on its first screen, in a card that can be
dismissed: would they like an email when a new version is out? The address is
filled in from the AI they signed in with — Claude's own account, or the one in
ChatGPT's sign-in — so that saying yes is one click, but it is sent only when
they say yes. It is never read from anywhere else, never sent on its own, and
never used for anything but this; Advanced settings shows it and takes it back.

Where it goes:

- **a sign-up form** (`SIGNUP_FORM`), if the people who make unpacman have set
  one up — a Google Form, say — posted from here, nothing more for the reader
  to do;
- otherwise **an email** the reader sends themselves: their email program opens
  with a two-line message to `FEEDBACK_TO`, as Send feedback does, and the
  address it comes from is the one signed up.

Kept in `var/unpacman-news.json` — this copy's answer, so that it is asked once.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atelier.config import Config

from .places import var_dir

# A form to post sign-ups to, if one is set up: {"url": the form's address for
# answers, "email": the name of its email field, "version": that of a field for
# unpacman's version, or ""}. For a Google Form, the address ends in
# /formResponse and the fields are named entry.NNNNNNN. None: sign-ups come by
# email, to Send feedback's address.
SIGNUP_FORM: dict[str, str] | None = None

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,24}$")
_UA = "unpacman/1 (a reader for mathematics papers)"


class NewsError(RuntimeError):
    """Said so a reader can act on it."""


def store(cfg: Config) -> Path:
    return var_dir(cfg) / "unpacman-news.json"


def load(cfg: Config) -> dict[str, Any]:
    try:
        data = json.loads(store(cfg).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cfg: Config, data: dict[str, Any]) -> None:
    path = store(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def valid(email: str) -> bool:
    return bool(_EMAIL.fullmatch(email.strip()))


def codex_email(home: Path | None = None) -> str:
    """The email in ChatGPT's sign-in (Codex's `auth.json`), read on this
    computer only, to fill in the field the reader sees."""
    path = (home or Path.home() / ".codex") / "auth.json"
    try:
        token = json.loads(path.read_text(encoding="utf-8"))["tokens"]["id_token"]
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return ""
    email = str(claims.get("email") or "")
    return email if valid(email) else ""


def suggestion(readiness: Any) -> tuple[str, str]:
    """The address to fill in, and where it was found: the first signed-in
    AI that says whose it is."""
    for ai in getattr(readiness, "ais", []) or []:
        if ai.signed_in and ai.provider == "claude" and valid(ai.who or ""):
            return ai.who, "your Claude sign-in"
    for ai in getattr(readiness, "ais", []) or []:
        if ai.signed_in and ai.provider == "codex":
            # The sign-in unpacman reads with, which may be its own (signins.py).
            email = ai.who if valid(ai.who or "") else codex_email()
            if email:
                return email, "your ChatGPT sign-in"
    return "", ""


def mail_link(to: str, email: str, version: str, *, stop: bool = False) -> str:
    subject = "unpacman: stop the news" if stop else "unpacman: keep me posted"
    body = (f"Please stop emailing {email} about new versions of unpacman.\n" if stop else
            f"Please email me at {email} when a new version of unpacman is out.\n")
    body += f"\n(Sent from unpacman{f', version {version}' if version else ''}.)\n"

    def enc(text: str) -> str:
        return urllib.parse.quote(text.replace("\n", "\r\n"), safe="")
    return f"mailto:{to}?subject={enc(subject)}&body={enc(body)}"


def _post_form(form: dict[str, str], email: str, version: str, *, stop: bool) -> None:
    fields = {form["email"]: email}
    if form.get("version"):
        fields[form["version"]] = ("stop " if stop else "") + (version or "unknown")
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(form["url"], data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=15) as r:
            if r.status >= 400:
                raise NewsError(f"The sign-up could not be sent (it answered {r.status}).")
    except urllib.error.HTTPError as exc:
        raise NewsError(f"The sign-up could not be sent (it answered {exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise NewsError("The sign-up could not be sent. Check the internet connection, "
                        "and try again.") from exc


def answer(cfg: Config, *, email: str | None, to: str, version: str,
           form: dict[str, str] | None = None) -> dict[str, Any]:
    """The reader's answer: an address to sign up (sent now through the form,
    or as an email link for the page to open), or None for "no thanks"."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if email is None:
        _save(cfg, {**load(cfg), "asked": now, "answer": "no"})
        return {"answer": "no"}
    email = email.strip()
    if not valid(email):
        raise NewsError("That does not look like an email address.")
    form = SIGNUP_FORM if form is None else form
    if form:
        _post_form(form, email, version, stop=False)
        _save(cfg, {"asked": now, "answer": "yes", "email": email, "via": "form", "sent": now})
        return {"answer": "yes", "email": email, "via": "form"}
    _save(cfg, {"asked": now, "answer": "yes", "email": email, "via": "email"})
    return {"answer": "yes", "email": email, "via": "email",
            "link": mail_link(to, email, version)}


def stop(cfg: Config, *, to: str, version: str,
         form: dict[str, str] | None = None) -> dict[str, Any]:
    """Take the address back: said to the form, or as an email to send."""
    kept = load(cfg)
    email = str(kept.get("email") or "")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    form = SIGNUP_FORM if form is None else form
    out: dict[str, Any] = {"answer": "no"}
    if email and form and kept.get("via") == "form":
        with contextlib.suppress(NewsError):
            _post_form(form, email, version, stop=True)
    elif email:
        out["link"] = mail_link(to, email, version, stop=True)
    _save(cfg, {"asked": kept.get("asked") or now, "answer": "no", "stopped": now})
    return out
