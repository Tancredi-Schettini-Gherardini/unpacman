"""Stand-ins for the `claude` and `codex` programs, for testing at no cost.

Put this folder first on PATH (`isolated_server.py --standin` does) and every
reading, explanation, key-term list and set of questions is answered by these
scripts instead of an AI: no network, no account, nothing spent. They speak
each CLI's own event stream, so the lab's runner, the loop controller and the
reader see what they would see from the real thing — passes that take time,
files written where each pass owes them, usage reported, a pass that fails on
request.

They write a companion of their own (the seven sections, Notice and cited
references the method's checks ask for; a review with one finding; an answer to
it), never anything taken from the paper, and make its reader-facing copies
with the reader's own `deslopify companion` command, as a model is told to. Each
invocation is logged, one JSON line, to `$STANDIN_LOG` if it is set: which
program, which model, which pass, which endpoint — which is how a test sees
which AI each step of a reading ran on.

Knobs (environment):
    STANDIN_SECONDS          how long one pass takes (default 2)
    STANDIN_EXPLAIN_SECONDS  how long an explanation takes (default 0.5)
    STANDIN_FAIL             a pass role to fail ("auditor", "draft", …)
    STANDIN_LOG              where to log each invocation
    STANDIN_FINDING          "minor" makes the check's one finding minor, so
                             the revision is skipped

Standard library only, and Python 3.9 syntax: the Mac's own python3 runs it.
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid

VERSION = {"claude": "2.1.300 (Claude Code) [stand-in]",
           "codex": "codex-cli 0.147.0 [stand-in]"}

# What each program says it offers when the reader asks (catalog.py): as the
# real ones said on 2026-10-15 — Claude Code the newest of each kind, Codex the
# models a ChatGPT plan can use, in its order.
CLAUDE_MODELS = (("default", "claude-opus-5-5"), ("opus", "claude-opus-5-5"),
                 ("claude-fable-5-1[1m]", "claude-fable-5-1"), ("sonnet", "claude-sonnet-5-5"),
                 ("haiku", "claude-haiku-5-5"))
CODEX_MODELS = ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol",
                "gpt-5.6-terra", "gpt-5.6-luna")


# --------------------------------------------------------------------------
# what a pass writes
# --------------------------------------------------------------------------

def tiny_pdf(text):
    """A valid one-page PDF saying `text` — enough for PDF.js and the checks."""
    safe = re.sub(r"[()\\]", "", text)[:80]
    stream = ("BT /F1 18 Tf 72 760 Td (%s) Tj ET" % safe).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return out


GUIDE = """# A stand-in companion

*Written by a stand-in program for testing ({model}, {role}). Nothing here
comes from the paper.*

**Notice.** This companion is not a certification of the paper's correctness:
a stand-in program wrote it, and the reader is
responsible for checking the source.

## Main results

**Theorem 1 (stand-in).** Every stand-in companion has seven sections before
its references.

The main result is stated once and used throughout.

## Main ideas

The central idea is that a worked example makes an abstract statement concrete.

## About the proofs

The proof is a count of the sections, checked one by one.

## Q&A

**Why seven?** This stand-in includes an optional worked example.

## Worked examples

Take this companion: count its sections, and the count is seven.

## Historical context

Stand-in companions were introduced for testing [Std26].

## Where the document is thin

Nothing here is mathematics; it is a fixture.

## References cited in this companion

- [Std26] A. Stand-in and B. Fixture, *Companions for testing readers*, Stand-in
  Press, Testing Series 1, 2026.
"""

TEX = r"""\documentclass{article}
\begin{document}
\tableofcontents
\section{A stand-in companion}
Written by a stand-in program for testing.
\end{document}
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    mode = "wb" if isinstance(text, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(text)


def companion_files(reading, model, role, prompt=""):
    """companion.md, companion.tex and companion.pdf, made as a model is told
    to make them: by the reader's own `deslopify companion` command, quoted in
    the pass's prompt (else the repository's interpreter, and the reading's
    level from its config.json). Without either, simple files of our own."""
    cwd = os.path.dirname(os.path.dirname(reading))
    command = re.search(r"(\S*python\S*)\s+-m\s+deslopify\s+companion\s", prompt)
    python = command.group(1) if command else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".venv", "bin", "python")
    audience = re.search(r"--audience\s+([A-Za-z-]+)", prompt)
    if audience:
        audience = audience.group(1)
    else:
        audience = "expert"
        try:
            notes = json.loads(read(os.path.join(os.environ.get("ATELIER_RUN_DIR", ""),
                                                 "config.json")))["spec"].get("notes") or ""
            if "general" in notes or "nonexpert" in notes:
                audience = "nonexpert"
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    if os.path.isfile(python):
        done = subprocess.run(
            [python, "-m", "deslopify", "companion", "progress/reading/guide.md",
             "--markdown", "progress/reading/companion.md",
             "--tex", "progress/reading/companion.tex",
             "--audience", audience, "--pdf", "progress/reading/companion.pdf"],
            cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        if done.returncode == 0:
            return
        log({"program": "stand-in", "kind": "companion-failed", "role": role,
             "stderr": (done.stderr or done.stdout)[-2000:]})
    guide = read(os.path.join(reading, "guide.md"))
    write(os.path.join(reading, "companion.md"), guide.strip() + "\n")
    write(os.path.join(reading, "companion.tex"), TEX)
    write(os.path.join(reading, "companion.pdf"),
          tiny_pdf("Stand-in companion (%s, %s)" % (model, role)))


def do_pass(cwd, role, model, rnd, contract, prompt=""):
    """Write what `role` owes, in the workspace `cwd`. Returns a summary."""
    reading = os.path.join(cwd, "progress", "reading")
    os.makedirs(reading, exist_ok=True)
    if role == "draft":
        write(os.path.join(reading, "guide.md"), GUIDE.format(model=model, role=role))
        # What the method's router records before it picks a skill.
        write(os.path.join(reading, "paper-profile.json"), json.dumps({"papers": [{
            "source": "progress/source.pdf", "profile": "pure",
            "skill": "SKILL-pure-%s.md" % (
                "nonexpert" if re.search(r"non-?expert", prompt, re.I) else "expert"),
            "reason": "A stand-in: it reads nothing, and takes every paper for pure "
                      "mathematics."}]}, indent=2))
        if os.path.isfile(os.path.join(cwd, "progress", "source.md")) and contract == "guide":
            # A search owes its results too; harmless for a collection.
            write(os.path.join(reading, "results.json"), json.dumps({"results": [{
                "title": "A stand-in result", "authors": ["A. Stand-in"], "year": 2026,
                "url": "https://example.org/stand-in", "summary": "Found by a stand-in.",
                "why": "It is a fixture.", "group": "Papers found"}]}, indent=1))
        summary = "wrote the guide"
    elif role in ("auditor", "verifier", "critic"):
        severity = os.environ.get("STANDIN_FINDING") or "worth-fixing"
        write(os.path.join(reading, "review-%d.json" % rnd), json.dumps({
            "round": rnd, "document": "progress/source.pdf",
            "summary": "One point, raised by a stand-in check.",
            "findings": [{
                "id": "F1", "kind": "unclear", "severity": severity,
                "where": "guide:Worked examples",
                "problem": "The worked example does not say what is counted.",
                "fix": "Say that the sections are what is counted."}],
        }, indent=1))
        if role == "auditor":
            companion_files(reading, model, role, prompt)
        summary = "raised one finding"
    elif role in ("balanced-reviser", "reviser"):
        guide_path = os.path.join(reading, "guide.md")
        guide = read(guide_path)
        guide = guide.replace("count its sections, and the count is seven.",
                              "count its sections (the headings), and the count is seven.")
        write(guide_path, guide)
        write(os.path.join(reading, "dispositions-%d.json" % rnd), json.dumps({
            "round": rnd, "dispositions": [{
                "id": "F1", "disposition": "fixed",
                "note": "The example now says the headings are what is counted.",
                "where": "guide:Worked examples"}]}, indent=1))
        companion_files(reading, model, role, prompt)
        summary = "answered one finding"
    else:
        summary = "nothing to do for role %s" % role
    write(os.path.join(cwd, "progress", "_session_notes.md"),
          "Stand-in pass: %s on %s. %s.\n" % (role, model, summary))
    return summary


# --------------------------------------------------------------------------
# what a one-call question is answered with
# --------------------------------------------------------------------------

def answer_for(system, prompt):
    text = system + "\n" + prompt
    if "ask_if" in text:
        questions = []
        for i in range(1, 11):
            questions.append({"id": "q%d" % i, "question": "Are you familiar with stand-in idea %d?" % i,
                              "topic": "stand-in idea %d" % i, "kind": "definition",
                              "where": "page %d" % i})
            if i == 2:
                questions.append({"id": "q2a", "parent": "q2", "ask_if": ["no", "roughly"],
                                  "question": "Do you know what stand-in idea 2 rests on?",
                                  "topic": "the basis of idea 2", "kind": "definition"})
        return json.dumps({"questions": questions})
    if "terms in the companion" in text or "key terms" in text.lower():
        words = []
        for word in re.findall(r"\b[a-z]{9,}\b", text[len(text) // 2:]):
            if word not in words:
                words.append(word)
            if len(words) >= 6:
                break
        return json.dumps([{"term": w, "explanation": "A stand-in explanation of *%s*." % w}
                           for w in words])
    asked = prompt.strip().splitlines()[-1][:120] if prompt.strip() else ""
    return ("A stand-in explanation. Nothing here comes from an AI: this answer "
            "was written by the test program. (Asked: %s)" % asked)


# --------------------------------------------------------------------------
# the two programs
# --------------------------------------------------------------------------

def arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def pass_role(argv, prompt="", cwd=None):
    """The pass's role, round and contract.

    A pass's Stop hook named them (`--role`, `--round`, `--contract`) until
    reading passes stopped having one (Antoine's fa61b08, 2026-10-16). Since
    then: the role is the stage the controller records before it starts each
    pass (`loop/time-remaining.json`); the round is the number of the review
    file the pass is asked for; and the contract is "guide" for a reading with
    a source.md (several papers, or a search), as their Stop hook said.
    """
    settings = arg(argv, "--settings")
    run_dir = os.environ.get("ATELIER_RUN_DIR")
    if not settings:
        settings = os.path.join(run_dir, "settings.json") if run_dir else None
    role, rnd, contract = "draft", 0, "companion"
    try:
        data = json.loads(read(settings))
    except (OSError, ValueError, TypeError):
        data = {}
    hooked = False
    for entry in (data.get("hooks") or {}).get("Stop") or []:
        for hook in entry.get("hooks") or []:
            words = str(hook.get("command") or "").split()
            hooked = hooked or "--role" in words
            role = arg(words, "--role", role)
            rnd = int(arg(words, "--round", rnd) or 0)
            contract = arg(words, "--contract", contract)
    if hooked or not run_dir:
        return role.strip('"'), rnd, contract
    # ATELIER_RUN_DIR is the run's own folder for the first pass, and the
    # pass's (`<run>/pass-2`, …) for each later one.
    stage = None
    for folder in (run_dir, os.path.dirname(os.path.normpath(run_dir))):
        try:
            stage = json.loads(read(os.path.join(folder, "loop", "time-remaining.json")))["stage"]
            break
        except (OSError, ValueError, TypeError, KeyError):
            continue
    role = str(stage or "draft")
    # The newest round named: a later round's prompt also quotes earlier ones.
    asked = [int(n) for n in re.findall(
        r"(?:clarity-review|review|dispositions)-(\d+)\.(?:json|md)", prompt or "")]
    rnd = max(asked) if asked else (0 if role == "draft" else 1)
    if os.path.isfile(os.path.join(cwd or os.getcwd(), "progress", "source.md")):
        contract = "guide"
    return role, rnd, contract


def log(entry):
    path = os.environ.get("STANDIN_LOG")
    if path:
        entry["time"] = round(time.time(), 3)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def seconds(name, default):
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except ValueError:
        return default


USAGE = {"input_tokens": 12000, "output_tokens": 1500,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def claude(argv):
    if "--version" in argv or argv[:1] == ["-v"]:
        print(VERSION["claude"])
        return 0
    if argv[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                          "email": "standin@example.org", "subscriptionType": "max"}))
        return 0
    model = arg(argv, "--model", "claude-stand-in")
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    session = arg(argv, "--session-id") or str(uuid.uuid4())
    if arg(argv, "--input-format") == "stream-json":
        first = sys.stdin.readline()
        try:
            request = json.loads(first)
        except ValueError:
            request = None
        if (isinstance(request, dict) and request.get("type") == "control_request"
                and (request.get("request") or {}).get("subtype") == "initialize"):
            # Which models it offers, as the Agent SDK asks: no message, no pass.
            log({"program": "claude", "kind": "models"})
            emit({"type": "control_response", "response": {
                "subtype": "success", "request_id": request.get("request_id"),
                "response": {"models": [{"value": value, "resolvedModel": model}
                                        for value, model in CLAUDE_MODELS]}}})
            for _ in sys.stdin:
                pass
            return 0
        try:
            prompt = request["message"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            prompt = first
        role, rnd, contract = pass_role(argv, prompt, os.getcwd())
        log({"program": "claude", "kind": "pass", "role": role, "round": rnd,
             "model": model, "effort": arg(argv, "--effort"), "base_url": base,
             "cwd": os.getcwd(), "config_dir": os.environ.get("CLAUDE_CONFIG_DIR", "")})
        emit({"type": "system", "subtype": "init", "session_id": session, "model": model,
              "tools": ["Read", "Write"], "cwd": os.getcwd(), "permissionMode": "default"})
        emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": prompt[:200]}]}, "session_id": session})
        if role == os.environ.get("STANDIN_FAIL"):
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "result": "stand-in failure on request", "session_id": session,
                  "num_turns": 1, "usage": USAGE})
            return 1
        total = seconds("STANDIN_SECONDS", 2.0)
        steps = max(1, int(total / 0.5))
        for i in range(steps):
            time.sleep(total / steps)
            emit({"type": "assistant", "session_id": session, "parent_tool_use_id": None,
                  "message": {"id": "msg_%s_%d" % (session[:8], i), "model": model,
                              "role": "assistant",
                              "content": [{"type": "text",
                                           "text": "Stand-in %s, step %d of %d." % (role, i + 1, steps)}],
                              "usage": {"input_tokens": USAGE["input_tokens"] // steps,
                                        "output_tokens": USAGE["output_tokens"] // steps,
                                        "cache_read_input_tokens": 0,
                                        "cache_creation_input_tokens": 0}}})
        summary = do_pass(os.getcwd(), role, model, rnd or 1, contract, prompt)
        emit({"type": "result", "subtype": "success", "is_error": False,
              "duration_ms": int(total * 1000), "num_turns": steps, "session_id": session,
              "result": "Stand-in %s: %s." % (role, summary), "usage": USAGE})
        # The runner closes stdin once it has the result; then end, as the CLI does.
        for _ in sys.stdin:
            pass
        return 0

    # One call: an explanation, key terms, or the questions before a reading.
    prompt = sys.stdin.read()
    system_file = arg(argv, "--system-prompt-file")
    system = read(system_file) if system_file else ""
    log({"program": "claude", "kind": "call", "model": model, "base_url": base,
         "config_dir": os.environ.get("CLAUDE_CONFIG_DIR", "")})
    time.sleep(seconds("STANDIN_EXPLAIN_SECONDS", 0.5))
    text = answer_for(system, prompt)
    for i in range(0, len(text), 60):
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {
            "type": "text_delta", "text": text[i:i + 60]}}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text,
          "session_id": session, "usage": {"input_tokens": 3000, "output_tokens": 200}})
    return 0


def codex(argv):
    if "--version" in argv:
        print(VERSION["codex"])
        return 0
    if "login" in argv[:2]:
        print("Logged in using ChatGPT (stand-in)")
        return 0
    if argv[:2] == ["debug", "models"]:
        log({"program": "codex", "kind": "models"})
        print(json.dumps({"models": [{"slug": slug, "visibility": "list", "priority": i + 1}
                                     for i, slug in enumerate(CODEX_MODELS)]}))
        return 0
    model = arg(argv, "--model", "gpt-stand-in")
    cwd = arg(argv, "--cd", os.getcwd())
    prompt = sys.stdin.read()          # codex exec reads its prompt to the end
    thread = str(uuid.uuid4())
    emit({"type": "thread.started", "thread_id": thread})
    emit({"type": "turn.started"})
    if os.environ.get("ATELIER_RUN_DIR"):
        role, rnd, contract = pass_role(argv, prompt, cwd)
        log({"program": "codex", "kind": "pass", "role": role, "round": rnd, "model": model,
             "effort": arg(argv, "--config"), "cwd": cwd})
        if role == os.environ.get("STANDIN_FAIL"):
            emit({"type": "turn.failed", "error": {"message": "stand-in failure on request"}})
            return 1
        time.sleep(seconds("STANDIN_SECONDS", 2.0))
        summary = do_pass(cwd, role, model, rnd or 1, contract, prompt)
        text = "Stand-in %s: %s." % (role, summary)
    else:
        log({"program": "codex", "kind": "call", "model": model})
        time.sleep(seconds("STANDIN_EXPLAIN_SECONDS", 0.5))
        text = answer_for("", prompt)
    emit({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                             "text": text}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 12000, "cached_input_tokens": 0,
                                              "output_tokens": 1500}})
    return 0


def main(program):
    try:
        return {"claude": claude, "codex": codex}[program](sys.argv[1:])
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(os.path.basename(sys.argv[0])))
