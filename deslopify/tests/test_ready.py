"""Getting ready (ready.py), the running copy (instance.py), the icon (mac.py)
and reading a PDF with nothing installed (source.py's PDFium fallback).

Every program a check runs is a stand-in written here — a shell script that
says what the real one says — so these tests ask nothing of the machine they
run on, install nothing, sign in to nothing, and spend nothing.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import socket
import sys
import tarfile
import threading
from pathlib import Path

import pytest

from atelier.config import Account
from deslopify.app import instance, mac, ready

from test_app import make_pdf

# --------------------------------------------------------------------------
# stand-ins
# --------------------------------------------------------------------------


def script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_tex(folder: Path, missing: tuple[str, ...] = ()) -> Path:
    """latexmk, pdflatex and a kpsewhich that finds every file but `missing`."""
    script(folder / "latexmk", "exit 0")
    script(folder / "pdflatex", "exit 0")
    skip = "|".join(missing) or "NONE"
    script(folder / "kpsewhich", f"""for f in "$@"; do
  case "$f" in {skip}) ;; *) echo "/tex/dist/$f" ;; esac
done""")
    return folder


@pytest.fixture
def no_system_tex(monkeypatch, tmp_path):
    """No MacTeX, no TeX Live: only what a test puts in place."""
    monkeypatch.setattr(ready, "MACTEX_BIN", tmp_path / "no-mactex")
    monkeypatch.setattr(ready, "TEXLIVE_ROOT", tmp_path / "no-texlive")


# --------------------------------------------------------------------------
# the environment
# --------------------------------------------------------------------------


def test_a_process_started_from_the_dock_is_given_the_folders_a_terminal_has(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    env = {"PATH": "/usr/bin:/bin"}
    ready.prepare_environment(env, home)
    parts = env["PATH"].split(os.pathsep)
    assert parts[:2] == ["/usr/bin", "/bin"]           # what was there keeps its order
    assert str(home / ".local" / "bin") in parts
    assert "/sbin" in parts
    assert env["LANG"] == ("C.UTF-8" if sys.platform.startswith("linux") else "en_US.UTF-8")
    # Run again, nothing is added twice; a language already chosen is kept.
    env2 = {"PATH": env["PATH"], "LC_ALL": "de_DE.UTF-8"}
    ready.prepare_environment(env2, home)
    assert env2["PATH"] == env["PATH"] and "LANG" not in env2


# --------------------------------------------------------------------------
# LaTeX
# --------------------------------------------------------------------------


def test_latex_is_found_on_the_path_first_and_counts_only_when_complete(
        tmp_path, no_system_tex):
    home = tmp_path / "home"
    tiny = fake_tex(ready.tinytex_root(home) / "bin" / "universal-darwin",
                    missing=("tcolorbox.sty", "tocbibind.sty"))
    other = fake_tex(tmp_path / "texlive" / "bin")
    env = {"PATH": f"{other}:/usr/bin:/bin"}

    assert ready.tex_candidates(env, home)[:2] == [other, tiny]
    found = ready.find_tex(env, home)
    assert found.bindir == str(other) and not found.missing and found.label == "TeX Live"

    # With only the incomplete one, it is reported with what it lacks.
    alone = ready.find_tex({"PATH": "/usr/bin:/bin"}, home)
    assert alone.bindir == str(tiny) and alone.label == "TinyTeX" and alone.ours
    assert alone.missing == ["tcolorbox.sty", "tocbibind.sty"] and not alone.ready
    assert ready.find_tex({"PATH": "/usr/bin"}, tmp_path / "nobody").found is False


def test_the_preamble_files_are_exactly_what_the_companion_loads():
    """`TEX_FILES` is what readiness demands. A package added to the preamble
    and not here would pass a LaTeX that cannot typeset the companion."""
    import re

    from deslopify import render, summary_pdf

    source = Path(summary_pdf.__file__).read_text() + Path(render.__file__).read_text()
    loaded = set()
    for group in re.findall(r"\\{1,2}usepackage(?:\[[^\]]*\])?\{([^}]*)\}", source):
        loaded |= {f"{name.strip()}.sty" for name in group.split(",")}
    for cls in re.findall(r"\\{1,2}documentclass(?:\[[^\]]*\])?\{([^}]*)\}", source):
        loaded.add(f"{cls}.cls")
    assert loaded and loaded <= set(ready.TEX_FILES), loaded - set(ready.TEX_FILES)


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="needs a TeX installation")
def test_a_real_latex_typesets_the_test_page():
    bindir = str(Path(shutil.which("latexmk")).parent)
    assert ready.trial_typeset(bindir) == ""


# --------------------------------------------------------------------------
# the AI
# --------------------------------------------------------------------------


CLAUDE = Account(id="default", provider="claude", auth="subscription")
CODEX = Account(id="codex", provider="codex", auth="subscription")


def fake_claude(tmp_path: Path, status: str) -> str:
    return str(script(tmp_path / "claude", f"""case "$1" in
  --version) echo "2.1.300 (Claude Code)" ;;
  auth) echo '{status}' ;;
esac"""))


def test_claude_is_read_from_its_own_status_never_assumed(tmp_path):
    signed = ready.claude_status(CLAUDE, fake_claude(tmp_path, json.dumps({
        "loggedIn": True, "authMethod": "claude.ai", "email": "a@b.org",
        "subscriptionType": "max"})))
    assert (signed.installed, signed.signed_in, signed.who, signed.plan) == \
        (True, True, "a@b.org", "Max")
    assert signed.version.startswith("2.1.300")

    out = ready.claude_status(CLAUDE, fake_claude(tmp_path, json.dumps(
        {"loggedIn": False, "authMethod": "none"})))
    assert out.installed and not out.signed_in

    free = ready.claude_status(CLAUDE, fake_claude(tmp_path, json.dumps(
        {"loggedIn": True, "subscriptionType": "free"})))
    assert free.signed_in and "does not include Claude Code" in free.note

    assert ready.claude_status(CLAUDE, None).installed is False


def test_codex_is_read_from_its_credentials_file(tmp_path):
    exe = str(script(tmp_path / "codex", 'echo "codex-cli 0.160.1"'))
    home = tmp_path / "codex-home"
    account = Account(id="codex", provider="codex", auth="subscription", config_dir=home)
    assert ready.codex_status(account, exe).signed_in is False
    home.mkdir()
    (home / "auth.json").write_text(json.dumps(
        {"auth_mode": "chatgpt", "tokens": {"access_token": "x", "refresh_token": "y"}}))
    got = ready.codex_status(account, exe)
    assert got.signed_in and got.plan == "ChatGPT" and got.version == "codex-cli 0.160.1"
    (home / "auth.json").write_text("{ not json")
    assert ready.codex_status(account, exe).signed_in is False


def readiness(*ais, tex=None):
    return ready.Readiness(ais=list(ais), tex=tex or ready.Tex(), pdf="PDFium", checked=0)


def test_what_stands_in_the_way_is_said_plainly():
    claude_in = ready.SignIn(account="default", provider="claude", installed=True,
                             signed_in=True)
    codex_out = ready.SignIn(account="codex", provider="codex", installed=True)
    good_tex = ready.Tex(bindir="/t", label="MacTeX", trial="ok")

    r = readiness(claude_in, codex_out, tex=good_tex)
    assert r.ok and r.blocking("claude") == []
    assert r.blocking("codex") == ["ChatGPT is not signed in on this computer."]
    assert r.blocking("codex", "codex") == ["ChatGPT is not signed in on this computer."]

    nothing = readiness(ready.SignIn(account="default", provider="claude"))
    assert not nothing.ok
    assert nothing.blocking("claude")[0] == "Claude is not installed on this computer."
    assert nothing.blocking()[0] == "No AI is signed in on this computer."
    assert "LaTeX is not installed" in nothing.blocking()[1]

    lacking = readiness(claude_in, tex=ready.Tex(bindir="/t", label="TinyTeX",
                                                 missing=["tcolorbox.sty"]))
    assert "missing 1 of the pieces" in lacking.blocking("claude")[0]
    broken = readiness(claude_in, tex=ready.Tex(bindir="/t", label="MacTeX",
                                                trial="failed", trial_error="! Oops"))
    assert broken.blocking("claude") == ["LaTeX could not typeset a test page: ! Oops"]
    # Not yet tried is not a failure: a reading is not held up by it.
    assert readiness(claude_in, tex=ready.Tex(bindir="/t", trial="pending")).ok


# --------------------------------------------------------------------------
# failures, plainly
# --------------------------------------------------------------------------


@pytest.mark.parametrize("said, means", [
    ("Not logged in · Please run /login", "not signed in"),
    ("Invalid API key · Fix external API key", "not signed in"),
    ("Claude AI usage limit reached|1759780800", "usage limit"),
    ("You've hit your usage limit. Upgrade to Pro", "usage limit"),
    ("API Error: 529 Overloaded", "overloaded"),
    ("getaddrinfo ENOTFOUND api.anthropic.com", "internet"),
    ("the draft produced no valid map.pdf, so the graph is not drawn", "proof map"),
    ("the draft produced no valid companion.pdf containing the guide", "typeset"),
    ("2026-10-14T21:02:01Z ERROR codex_core::tools::router: error=failed to spawn code-mode "
     "host /Users/x/.local/bin/codex-code-mode-host: No such file or directory (os error 2)",
     "codex-code-mode-host"),
    ("failed to decode code-mode IPC frame: missing field code_mode_host_duration_ns",
     "file and shell helper"),
])
def test_known_failures_are_said_in_words_a_reader_can_act_on(said, means):
    assert means in ready.plain_failure(said)


def test_an_unknown_failure_is_left_to_speak_for_itself():
    assert ready.plain_failure("the run was stopped from the reader", None, "") == ""
    # A paper's own words are never mistaken for a failure.
    assert ready.plain_failure("We study the network of 500 nodes and 429 edges.") == ""


def test_a_runs_own_error_messages_are_found_in_its_event_log(tmp_path):
    lines = [{"type": "usage", "payload": {}},
             {"type": "error", "payload": {"message": "Not logged in · Please run /login"}},
             {"type": "text", "payload": {"text": "an \"error\" mentioned in prose"}}]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    assert ready.failure_texts(tmp_path) == ["Not logged in · Please run /login"]
    assert ready.failure_texts(tmp_path / "nowhere") == []


# --------------------------------------------------------------------------
# jobs: installing and signing in, with stand-ins for the downloads
# --------------------------------------------------------------------------


def test_a_codex_too_old_for_a_readings_protections_cannot_read(tmp_path):
    # Codex 0.147 refused `features.skip_host_skill_discovery`, by design
    # stopping every ChatGPT reading (2026-10-13): Getting ready says so, and
    # the launch panel will not start one. Found without asking the model.
    calls = tmp_path / "calls"
    old = script(tmp_path / "old" / "codex", f"""
if [ "$1" = "--version" ]; then echo "codex-cli 0.147.0"; exit 0; fi
echo run >> {calls}
echo "Error loading config.toml: unknown configuration field \\`features.skip_host_skill_discovery\\` in -c/--config override" >&2
exit 1""")
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"tokens": {"access_token": "x"}}))
    account = Account(id="codex", provider="codex", auth="subscription", config_dir=home)
    got = ready.codex_status(account, str(old))
    assert got.signed_in and got.outdated == "features.skip_host_skill_discovery"
    assert not got.usable and "too old" in got.note
    readiness = ready.Readiness(ais=[got], tex=ready.Tex(), pdf="", checked=0)
    assert readiness.signed_in("codex") is False
    assert "too old" in " ".join(readiness.blocking("codex", latex=False))
    # Asked once per program, not at every look.
    ready.codex_status(account, str(old))
    assert calls.read_text().count("run") == 1

    new = script(tmp_path / "new" / "codex", """
if [ "$1" = "--version" ]; then echo "codex-cli 0.170.0"; exit 0; fi
echo "Error: Model provider \\`unpacman-offline-check\\` not found" >&2
exit 1""")
    got = ready.codex_status(account, str(new))
    assert got.outdated == "" and got.usable


def test_a_codex_installed_without_its_host_cannot_read(tmp_path):
    # What Getting ready installed until 2026-10-15 was the bare program: every
    # command a model asked for failed ("failed to spawn code-mode host"), and
    # three readings of 2026-10-14 stopped after their first step. Getting
    # ready says so, and the launch panel will not start one.
    calls = tmp_path / "calls"
    body = f"""
if [ "$1" = "--version" ]; then echo "codex-cli 0.162.1"; exit 0; fi
if [ "$1 $2" = "features list" ]; then
  echo features >> {calls}
  echo "code_mode                                under development  false"
  echo "code_mode_host                           stable             true"
  exit 0
fi
echo "Error: Model provider \\`unpacman-offline-check\\` not found" >&2
exit 1"""
    bare = script(tmp_path / "bare" / "codex", body)
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"tokens": {"access_token": "x"}}))
    account = Account(id="codex", provider="codex", auth="subscription", config_dir=home)
    got = ready.codex_status(account, str(bare))
    assert got.signed_in and got.outdated == ready.CODE_MODE_HOST and not got.usable
    assert "codex-code-mode-host" in got.note and "Update it here" in got.note
    readiness = ready.Readiness(ais=[got], tex=ready.Tex(), pdf="", checked=0)
    assert readiness.blocking("codex", latex=False) == [
        "Codex, which ChatGPT reads with here, was installed without one of its parts "
        "(codex-code-mode-host), so it cannot run the commands a reading needs: update it."]
    # Asked once per program, not at every look.
    ready.codex_status(account, str(bare))
    assert calls.read_text().count("features") == 1

    # Whole, as Codex's installer and Homebrew lay it out: the host beside the
    # real program, which the `codex` on PATH is a link to.
    real = script(tmp_path / "release" / "bin" / "codex", body)
    script(tmp_path / "release" / "bin" / ready.CODE_MODE_HOST, "exit 0")
    (tmp_path / "linked").mkdir()
    (tmp_path / "linked" / "codex").symlink_to(real)
    assert ready.codex_status(account, str(tmp_path / "linked" / "codex")).usable

    # A Codex that runs commands without one needs none.
    older = script(tmp_path / "older" / "codex",
                   'if [ "$1" = "--version" ]; then echo "codex-cli 0.140.0"; exit 0; fi\nexit 1')
    assert ready.codex_status(account, str(older)).usable


def test_a_codex_with_an_older_tool_host_is_offered_an_update(tmp_path):
    exe = script(tmp_path / "local" / "bin" / "codex", 'echo "codex-cli 0.162.1"')
    old = tmp_path / "old-package"
    script(old / "bin" / ready.CODE_MODE_HOST, "exit 0")
    (old / "codex-package.json").write_text('{"version":"0.147.0"}')
    exe.with_name(ready.CODE_MODE_HOST).symlink_to(old / "bin" / ready.CODE_MODE_HOST)
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"tokens": {"access_token": "x"}}))
    account = Account(id="codex", provider="codex", auth="subscription", config_dir=home)

    got = ready.codex_status(account, str(exe))
    assert got.outdated == ready.CODE_MODE_HOST_MISMATCH and not got.usable
    assert "different version" in got.note
    readiness = ready.Readiness(ais=[got], tex=ready.Tex(), pdf="", checked=0)
    assert "different version" in readiness.blocking("codex", latex=False)[0]


def test_a_codex_updated_here_comes_first_on_path(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    env = {"PATH": f"/opt/homebrew/bin:{home / '.local' / 'bin'}:/usr/bin"}
    ready.prepare_environment(env, home)
    assert env["PATH"].split(":")[0] == "/opt/homebrew/bin"       # nothing of ours yet
    script(home / ".local" / "bin" / "codex", "echo")
    ready.prepare_environment(env, home)
    assert env["PATH"].split(":")[0] == str(home / ".local" / "bin")


async def until_done(job: ready.Job, seconds: float = 20) -> ready.Job:
    for _ in range(int(seconds * 20)):
        if job.state != "running":
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"{job.kind} still running: {job.step}")


async def test_a_job_keeps_what_the_program_says_and_finds_a_sign_in_page(tmp_path):
    job = ready.Job(id="j", kind="signin-claude", title="t")
    prog = script(tmp_path / "p", """echo "Opening browser to sign in…"
echo "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&x=1"
read -r code
[ "$code" = "ABC-123" ] && echo "Login successful" && exit 0
exit 1""")
    task = asyncio.create_task(job.run([str(prog)], env=dict(os.environ), timeout=10,
                                       stdin=True, url=ready.re.compile(r"https://\S*oauth\S*")))
    for _ in range(100):
        if job.url:
            break
        await asyncio.sleep(0.02)
    assert job.url.startswith("https://claude.com/cai/oauth/authorize")
    assert await job.send("ABC-123")
    assert await task == 0
    assert list(job.lines)[-1] == "Login successful"


async def test_a_job_that_hangs_is_stopped(tmp_path):
    job = ready.Job(id="j", kind="install-claude", title="t")
    with pytest.raises(ready.JobFailed, match="far longer"):
        await job.run([str(script(tmp_path / "slow", "sleep 30"))], env=dict(os.environ),
                      timeout=0.5)


def fake_codex_installer(tmp_path: Path, *, host: bool = True, fail: bool = False):
    """Codex's own installer, as far as unpacman relies on it: the release under
    ~/.codex/packages, its program linked into CODEX_INSTALL_DIR, the host
    beside the program — and what it was given, kept."""
    seen = tmp_path / "installer-saw"
    text = """#!/bin/sh
set -eu
env > SEEN
FAIL
release="$HOME/.codex/packages/standalone/releases/9.9.9-test"
mkdir -p "$release/bin"
printf '%s\\n' '#!/bin/sh' 'case "$1 $2" in' \\
  '  "--version "*) echo "codex-cli 9.9.9" ;;' \\
  '  "features list") echo "code_mode_host   stable   true" ;;' \\
  '  *) exit 1 ;;' 'esac' > "$release/bin/codex"
chmod 755 "$release/bin/codex"
HOST
ln -sfn "$release" "$HOME/.codex/packages/standalone/current"
mkdir -p "$CODEX_INSTALL_DIR"
ln -sf "$HOME/.codex/packages/standalone/current/bin/codex" "$CODEX_INSTALL_DIR/codex"
echo "Codex CLI 9.9.9 installed successfully."
"""
    text = text.replace("SEEN", str(seen))
    text = text.replace("FAIL", "echo 'Could not download the Codex package.' >&2; exit 1"
                        if fail else "")
    text = text.replace("HOST", "printf '#!/bin/sh\\nexit 0\\n' > \"$release/bin/codex-code-mode-host\"; "
                        "chmod 755 \"$release/bin/codex-code-mode-host\"" if host else "")
    installer = tmp_path / "install.sh"
    installer.write_text(text)
    return installer, seen


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"),
                    reason="installs a Mac or Linux build")
async def test_codex_is_installed_whole_by_its_own_installer(tmp_cfg, tmp_path, monkeypatch):
    installer, seen = fake_codex_installer(tmp_path)
    monkeypatch.setattr(ready, "CODEX_INSTALLER", f"file://{installer}")
    home = tmp_path / "home"
    # Over the bare program Getting ready installed until 2026-10-15.
    script(home / ".local" / "bin" / "codex", 'echo "codex-cli 0.162.1"')
    job = await until_done(ready.Checker(tmp_cfg, home=home).start("install-codex"))
    assert job.state == "done", job.error
    codex = home / ".local" / "bin" / "codex"
    assert codex.is_symlink() and ready.codex_incomplete(str(codex)) == ""
    assert "Next, sign in" in job.step
    saw = dict(line.split("=", 1) for line in seen.read_text().splitlines() if "=" in line)
    assert saw["CODEX_NON_INTERACTIVE"] == "1" and saw["HOME"] == str(home)
    assert saw["CODEX_INSTALL_DIR"] == str(home / ".local" / "bin")
    # Shown no other Codex (Homebrew's, say), it asks nothing and leaves the
    # person's shell settings alone.
    assert saw["PATH"].split(":") == [str(home / ".local" / "bin"), "/usr/bin", "/bin",
                                      "/usr/sbin", "/sbin"]


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"),
                    reason="installs a Mac or Linux build")
async def test_a_codex_install_that_fails_or_comes_out_partial_says_so(tmp_cfg, tmp_path,
                                                                        monkeypatch):
    installer, _ = fake_codex_installer(tmp_path, fail=True)
    monkeypatch.setattr(ready, "CODEX_INSTALLER", f"file://{installer}")
    job = await until_done(ready.Checker(tmp_cfg, home=tmp_path / "a").start("install-codex"))
    assert job.state == "failed" and "could not be installed" in job.error
    assert "Could not download the Codex package" in job.error

    installer, _ = fake_codex_installer(tmp_path, host=False)
    monkeypatch.setattr(ready, "CODEX_INSTALLER", f"file://{installer}")
    job = await until_done(ready.Checker(tmp_cfg, home=tmp_path / "b").start("install-codex"))
    assert job.state == "failed" and "codex-code-mode-host" in job.error


@pytest.mark.skipif(sys.platform != "darwin", reason="installs the macOS build")
async def test_latex_is_installed_then_proved_by_a_test_page(tmp_cfg, tmp_path, monkeypatch):
    calls = tmp_path / "tlmgr-calls"
    stage = tmp_path / "stage"
    script(stage / "TinyTeX" / "bin" / "universal-darwin" / "tlmgr",
           f'echo "$@" >> {calls}; exit 0')
    archive = tmp_path / "tinytex.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(stage / "TinyTeX", arcname="TinyTeX")
    monkeypatch.setattr(ready, "TINYTEX_URL", f"file://{archive}")
    tried = []
    monkeypatch.setattr(ready, "trial_typeset", lambda bindir: tried.append(bindir) or "")
    home = tmp_path / "home"
    job = await until_done(ready.Checker(tmp_cfg, home=home).start("install-latex"))
    assert job.state == "done", job.error
    said = calls.read_text().splitlines()
    assert said[0] == "update --self"
    assert said[1].startswith("install ") and "tcolorbox" in said[1] and "cm-super" in said[1]
    assert tried == [str(home / "Library" / "TinyTeX" / "bin" / "universal-darwin")]

    # A test page that will not typeset is a failure, said with LaTeX's words.
    monkeypatch.setattr(ready, "trial_typeset", lambda bindir: "! Undefined control sequence.")
    job = await until_done(ready.Checker(tmp_cfg, home=home).start("install-latex"))
    assert job.state == "failed" and "Undefined control sequence" in job.error


async def test_signing_in_to_claude_finishes_when_claude_says_so(tmp_cfg, tmp_path, monkeypatch):
    state = tmp_path / "signed"
    exe = script(tmp_path / "claude", f"""case "$1 $2" in
  "auth login") echo "visit: https://claude.com/cai/oauth/authorize?code=true"; read -r code;
                [ "$code" = "OK-1" ] && touch {state} && exit 0; exit 1 ;;
  "auth status") [ -f {state} ] && echo '{{"loggedIn": true, "email": "r@x.org"}}' \\
                   || echo '{{"loggedIn": false}}' ;;
  *) echo "2.1.300 (Claude Code)" ;;
esac""")
    monkeypatch.setattr(ready, "_executable", lambda provider: str(exe))
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    job = checker.start("signin-claude")
    assert checker.start("signin-claude") is job          # one at a time
    for _ in range(200):
        if job.url:
            break
        await asyncio.sleep(0.02)
    assert job.to_dict()["waiting_for_code"]
    await job.send("OK-1")
    await until_done(job)
    assert job.state == "done" and "r@x.org" in job.step


async def test_a_sign_in_can_be_cancelled(tmp_cfg, tmp_path, monkeypatch):
    exe = script(tmp_path / "claude", 'case "$1" in auth) sleep 30 ;; *) echo 2.1 ;; esac')
    monkeypatch.setattr(ready, "_executable", lambda provider: str(exe))
    job = ready.Checker(tmp_cfg, home=tmp_path).start("signin-claude")
    await asyncio.sleep(0.3)
    job.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job.task
    assert job.state == "cancelled" and job.proc.returncode is not None


def test_an_unknown_job_is_refused(tmp_cfg):
    with pytest.raises(ValueError):
        asyncio.run(_start(ready.Checker(tmp_cfg), "install-everything"))


async def _start(checker, kind):
    return checker.start(kind)


# --------------------------------------------------------------------------
# the running copy
# --------------------------------------------------------------------------


def test_a_port_in_use_is_skipped(tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert not instance.port_free("127.0.0.1", port)
        chosen = instance.choose_port("127.0.0.1", port, span=10)
        assert chosen != port and instance.port_free("127.0.0.1", chosen)


def test_the_running_server_is_recognised_by_asking_it(tmp_path, monkeypatch):
    # A server record is local; its health check must not be sent through a
    # proxy, even when an environment normally routes HTTP that way.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    root = tmp_path / "copy"
    root.mkdir()
    answer = {"app": "unpacman", "root": str(root)}

    class Hello(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(answer).encode()
            allowed = (self.path == "/api/hello"
                       and self.headers.get("X-Unpacman-Token") == "test-secret")
            self.send_response(200 if allowed else 403)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        assert instance.find_running(root) is None                 # no record yet
        instance.write_record(root, url=url, port=server.server_port,
                              background=True, token="test-secret")
        assert instance.find_running(root) == url + "#auth=test-secret"
        assert instance.record_path(root).stat().st_mode & 0o077 == 0
        answer["root"] = str(tmp_path / "another-copy")             # someone else's
        assert instance.find_running(root) is None
        answer["app"] = "something else"
        assert instance.hello(url) is None
        instance.clear_record(root)
        assert not instance.record_path(root).exists()
    finally:
        server.shutdown()
    # A record left by a crash points at nothing, and is ignored.
    instance.write_record(root, url=url, port=server.server_port,
                          background=True, token="test-secret")
    assert instance.find_running(root) is None


def test_a_record_belongs_to_the_process_that_wrote_it(tmp_path):
    path = instance.record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": os.getpid() + 1, "url": "http://x/"}))
    instance.clear_record(tmp_path)
    assert path.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="caffeinate is macOS")
def test_the_mac_is_kept_awake_only_while_asked():
    awake = instance.KeepAwake()
    awake.set(True)
    try:
        assert awake.on
        first = awake.proc
        awake.set(True)
        assert awake.proc is first                                  # not twice
    finally:
        awake.set(False)
    assert not awake.on and first.poll() is not None


# --------------------------------------------------------------------------
# the icon
# --------------------------------------------------------------------------


def test_the_icon_runs_this_copy_and_says_when_it_has_gone(tmp_path):
    root = tmp_path / 'a folder with "quotes" and spaces'
    source = mac.applet_source(root)
    assert "--app" in source and "exit 3" in source
    assert '\\"quotes\\"' in source                       # AppleScript-escaped
    command = mac.launcher_command(root)
    # The shell sees one word per path, however odd the path.
    import shlex
    words = shlex.split(command.split(";")[0].replace("if [ ! -x ", "").replace(" ]", ""))
    assert words == [str(root / ".venv" / "bin" / "python")]


@pytest.mark.skipif(shutil.which("osacompile") is None, reason="needs macOS")
def test_the_icon_is_a_signed_application_with_its_own_picture(tmp_path):
    import plistlib
    import subprocess

    root = tmp_path / "copy"
    root.mkdir()
    apps = tmp_path / "Applications"
    app = mac.make_app(root, apps)
    assert app == apps / "Unpacman.app" and (app / "Contents" / "MacOS" / "applet").is_file()
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == mac.BUNDLE_ID and info["CFBundleName"] == "Unpacman"
    icon = app / "Contents" / "Resources" / f"{info.get('CFBundleIconFile', 'applet')}.icns"
    assert icon.read_bytes() == mac.ICON.read_bytes()
    verified = subprocess.run(["codesign", "--verify", str(app)], capture_output=True)
    assert verified.returncode == 0, verified.stderr
    assert json.loads((root / "var" / "unpacman-app.json").read_text())["app"] == str(app)
    # Made again, it replaces itself and leaves nothing beside it.
    mac.make_app(root, apps)
    assert sorted(p.name for p in apps.iterdir()) == ["Unpacman.app"]


# --------------------------------------------------------------------------
# a PDF, with nothing installed to read it
# --------------------------------------------------------------------------


@pytest.fixture
def nothing_installed(monkeypatch):
    import deslopify.app.library as library_mod
    import deslopify.source as source_mod

    # Page by page for explanations is library.page_texts since 2026-10-07.
    for module in (source_mod, library_mod):
        monkeypatch.setattr(module.shutil, "which", lambda name, *a, **k: None)


def test_a_pdf_is_read_without_pdftotext_or_ghostscript(tmp_path, nothing_installed):
    from deslopify.source import SourceError, pdf_pages, pdf_title, read_source

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(make_pdf(["First line of the paper.", "Second line."], title="On Things"))
    text = read_source(pdf)
    assert "First line of the paper." in text and "Second line." in text
    assert text.endswith("\f") and "\r" not in text
    assert len(pdf_pages(pdf)) == 1
    assert pdf_title(pdf) == "On Things"

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not really a PDF")
    with pytest.raises(SourceError):
        read_source(broken)
    assert pdf_title(broken) == ""


def test_a_pdf_with_ghostscript_padding_uses_pdfium_instead(tmp_path, monkeypatch):
    import subprocess

    import deslopify.source as source

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(make_pdf(["A short mathematical paper."]))
    monkeypatch.setattr(source.shutil, "which",
                        lambda name: "/usr/local/bin/gs" if name == "gs" else None)
    monkeypatch.setattr(source.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0,
                                                    b"A short mathematical paper.\n" + b" " * 1_100_000,
                                                    b""))

    text = source.read_source(pdf)
    assert "A short mathematical paper." in text
    assert len(text) < 1_000


def test_moderately_padded_ghostscript_output_uses_pdfium(tmp_path, monkeypatch):
    import subprocess

    import deslopify.source as source

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(make_pdf(["A short mathematical paper."]))
    # The failed real run had a million characters with 84% spaces: too little
    # for the old 90% rule, but several times longer than its actual text.
    padded = (b"ABCDE" + b" " * 25) * 35_000
    monkeypatch.setattr(source, "_pdfium_text", lambda _: "ABCDE" * 35_000)
    monkeypatch.setattr(source.shutil, "which",
                        lambda name: "/usr/local/bin/gs" if name == "gs" else None)
    monkeypatch.setattr(source.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, padded, b""))

    text = source.read_source(pdf)
    assert text == "ABCDE" * 35_000


def test_padded_extraction_keeps_words_if_pdfium_misses_them(tmp_path, monkeypatch):
    import subprocess

    import deslopify.source as source

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(make_pdf(["A short mathematical paper."]))
    padded = (b"ABCDE" + b" " * 25) * 35_000
    monkeypatch.setattr(source.shutil, "which",
                        lambda name: "/usr/local/bin/gs" if name == "gs" else None)
    monkeypatch.setattr(source.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, padded, b""))
    monkeypatch.setattr(source, "_pdfium_text", lambda _: "Only a small fragment.")

    text = source.read_source(pdf)
    assert text.count("ABCDE") == 35_000
    assert len(text) < 250_000


def test_pdfium_document_work_is_serialized_across_readings(monkeypatch):
    """Concurrent page and title requests must not overlap inside PDFium."""
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import time

    from deslopify.source import pdf_pages, pdf_title

    monitor = threading.Lock()
    start = threading.Barrier(3)
    active = 0
    maximum = 0

    class FakePage:
        def get_textpage(self):
            return SimpleNamespace(get_text_bounded=lambda: "A page", close=lambda: None)

        def close(self):
            pass

    class FakeDocument:
        def __init__(self, path):
            nonlocal active, maximum
            with monitor:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)  # let another request enter if there is no shared lock

        def __len__(self):
            return 1

        def __getitem__(self, index):
            return FakePage()

        def get_metadata_dict(self):
            return {"Title": "A title"}

        def close(self):
            nonlocal active
            with monitor:
                active -= 1

    monkeypatch.setitem(sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=FakeDocument))

    def call(fn, path):
        start.wait(timeout=2)
        return fn(path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(call, pdf_pages, "first.pdf"),
                   pool.submit(call, pdf_pages, "second.pdf"),
                   pool.submit(call, pdf_title, "third.pdf")]
        assert [f.result(timeout=5) for f in futures] == [
            ["A page"], ["A page"], "A title"]
    assert maximum == 1


# --------------------------------------------------------------------------
# another account for the same AI (2026-10-17)
# --------------------------------------------------------------------------


def id_token(email: str) -> str:
    """A token shaped as ChatGPT's sign-in keeps it: its middle says whose."""
    import base64

    claims = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode()
    return f"header.{claims.rstrip('=')}.signature"


@pytest.fixture
def codex_fit(monkeypatch):
    """A Codex whose own checks pass: what is under test is its sign-in."""
    monkeypatch.setattr(ready, "codex_unfit", lambda exe: "")
    monkeypatch.setattr(ready, "codex_incomplete", lambda exe: "")
    monkeypatch.setattr(ready, "codex_host_issue", lambda exe, version: "")


def test_chatgpt_says_whose_sign_in_it_is(tmp_path, codex_fit):
    from dataclasses import replace

    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "id_token": id_token("t@x.org"), "access_token": "a"}}))
    exe = script(tmp_path / "codex", 'echo "codex-cli 0.162.1"')
    out = ready.codex_status(replace(CODEX, config_dir=home), str(exe))
    assert (out.signed_in, out.who, out.plan) == (True, "t@x.org", "ChatGPT")
    # A token it cannot read names nobody, and is still a sign-in.
    (home / "auth.json").write_text(json.dumps({"tokens": {"id_token": "garbled",
                                                           "access_token": "a"}}))
    out = ready.codex_status(replace(CODEX, config_dir=home), str(exe))
    assert out.signed_in and out.who == ""


def fake_claude_accounts(tmp_path: Path, *, signs_in_as: str, login: str = "ok",
                         logout: int = 0) -> tuple[Path, Path]:
    """A Claude Code with a sign-in per folder, as the real one keeps them
    (signins.py): the Mac's own, as a@x.org, when it is told no folder; and one
    in each folder CLAUDE_CONFIG_DIR names. Its sign-in page signs in as
    `signs_in_as` — or fails (`login="fail"`), or waits (`login="wait"`). Each
    command goes to a file: what it was asked | its folder | the Keychain
    folder it was told, if any."""
    mac, calls = tmp_path / "mac-who", tmp_path / "calls"
    mac.write_text("a@x.org")
    exe = script(tmp_path / "bin" / "claude", f"""echo "$* | ${{CLAUDE_CONFIG_DIR:-mac}} | ${{CLAUDE_SECURESTORAGE_CONFIG_DIR-unset}}" >> {calls}
who={mac}
[ -n "$CLAUDE_CONFIG_DIR" ] && who="$CLAUDE_CONFIG_DIR/who"
case "$1 $2" in
  "auth logout") [ {logout} -eq 0 ] && rm -f "$who"; exit {logout} ;;
  "auth login") echo "visit: https://claude.com/cai/oauth/authorize?code=true"
                case {login} in
                  fail) exit 1 ;;
                  wait) sleep 30 ;;
                esac
                echo {signs_in_as} > "$who" ;;
  "auth status") if [ -f "$who" ]; then
                   printf '{{"loggedIn": true, "email": "%s", "subscriptionType": "max"}}\\n' "$(cat "$who")"
                 else echo '{{"loggedIn": false}}'; fi ;;
  *) echo "2.1.300 (Claude Code)" ;;
esac""")
    return exe, calls


def asked(calls: Path) -> list[tuple[str, str, str]]:
    """What the fake was asked: (command, folder, Keychain folder)."""
    if not calls.exists():
        return []
    return [tuple(line.split(" | ")) for line in calls.read_text().splitlines()]


def own_folders(cfg) -> list[Path]:
    from deslopify.app import signins

    found = signins.root(cfg)
    return sorted(p for p in found.iterdir() if p.is_dir()) if found.is_dir() else []


@pytest.fixture
def claude_only(monkeypatch):
    """Claude is the fake given; no Codex at all, so the Mac's own is never read."""
    def use(exe: Path) -> None:
        monkeypatch.setattr(ready, "_executable",
                            lambda provider: str(exe) if provider == "claude" else None)
    return use


async def test_another_claude_account_is_for_unpacman_alone(tmp_cfg, tmp_path, monkeypatch,
                                                            claude_only):
    from dataclasses import replace

    from deslopify.app.signins import Signins

    exe, calls = fake_claude_accounts(tmp_path, signs_in_as="b@x.org")
    claude_only(exe)
    # Set, even empty, this would name the Mac's own Keychain item instead.
    monkeypatch.setenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", "")
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    job = await until_done(checker.start("switch-claude"))
    assert job.state == "done", job.error
    assert job.step == ("unpacman reads with b@x.org from now on. Claude Code elsewhere on "
                        f"{ready.here()} keeps the sign-in it has.")
    # The sign-in page is there to open again, and its code box is offered
    # while it runs, as for a first sign-in.
    assert job.url.startswith("https://claude.com/")
    assert ready.Job(id="x", kind="switch-claude", title="").to_dict()["waiting_for_code"]
    folder = tmp_cfg.accounts["default"].config_dir
    assert folder is not None and checker.signins.owns(folder, "claude")
    assert own_folders(tmp_cfg) == [folder]
    # The Mac's own Claude Code was asked who it is, and nothing else; every
    # other command was told the new folder, and nothing but it.
    for command, where, keychain in asked(calls):
        if where == "mac":
            assert command in ("auth status --json", "--version"), command
        else:
            assert where == str(folder) and keychain == "unset", (command, where, keychain)
    assert ("auth login --claudeai", str(folder), "unset") in asked(calls)
    assert (tmp_path / "mac-who").read_text() == "a@x.org"
    # Kept for the next start: a reader started again reads with it too.
    again = replace(tmp_cfg, accounts={"default": CLAUDE, "codex": CODEX})
    Signins(again)
    assert again.accounts["default"].config_dir == folder


async def test_the_same_account_again_changes_nothing(tmp_cfg, tmp_path, claude_only):
    exe, calls = fake_claude_accounts(tmp_path, signs_in_as="a@x.org")
    claude_only(exe)
    job = await until_done(ready.Checker(tmp_cfg, home=tmp_path / "home").start("switch-claude"))
    assert job.state == "done", job.error
    assert job.step.startswith("That is a@x.org again — the account unpacman reads with already")
    assert "sign out of claude.ai" in job.step
    assert tmp_cfg.accounts["default"].config_dir is None and own_folders(tmp_cfg) == []
    # Its second sign-in to that account was signed out again, there only.
    outs = [(c, w) for c, w, _ in asked(calls) if c == "auth logout"]
    assert len(outs) == 1 and outs[0][1] != "mac"


@pytest.mark.parametrize("login", ["fail", "wait"])
async def test_a_sign_in_given_up_changes_nothing(tmp_cfg, tmp_path, claude_only, login):
    exe, calls = fake_claude_accounts(tmp_path, signs_in_as="b@x.org", login=login)
    claude_only(exe)
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    job = checker.start("switch-claude")
    if login == "wait":
        # Stopped from the page while the sign-in page waits.
        for _ in range(200):
            if job.url:
                break
            await asyncio.sleep(0.05)
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        assert job.state == "cancelled"
    else:
        job = await until_done(job)
        assert job.state == "failed" and "so unpacman reads as before" in job.error
    assert tmp_cfg.accounts["default"].config_dir is None and own_folders(tmp_cfg) == []
    outs = [w for c, w, _ in asked(calls) if c == "auth logout"]
    assert len(outs) == 1 and outs[0] != "mac"
    assert (tmp_path / "mac-who").read_text() == "a@x.org"


async def test_another_account_again_signs_the_one_before_out(tmp_cfg, tmp_path, claude_only):
    exe, calls = fake_claude_accounts(tmp_path, signs_in_as="b@x.org")
    claude_only(exe)
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    assert (await until_done(checker.start("switch-claude"))).state == "done"
    first = tmp_cfg.accounts["default"].config_dir
    fake_claude_accounts(tmp_path, signs_in_as="c@x.org")
    job = await until_done(checker.start("switch-claude"))
    assert job.state == "done" and job.step.startswith("unpacman reads with c@x.org")
    second = tmp_cfg.accounts["default"].config_dir
    assert second != first and own_folders(tmp_cfg) == [second]
    assert ("auth logout", str(first), "unset") in asked(calls)
    assert not [c for c, w, _ in asked(calls) if w == "mac" and c.startswith("auth log")]


async def test_a_reading_on_that_ai_keeps_its_account_until_it_ends(tmp_cfg, tmp_path,
                                                                     claude_only):
    """A reading started while another account was being signed in to may be
    running a step on the account before: that one is signed out only once
    the reading no longer uses Claude."""
    exe, calls = fake_claude_accounts(tmp_path, signs_in_as="b@x.org")
    claude_only(exe)
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    assert (await until_done(checker.start("switch-claude"))).state == "done"
    first = tmp_cfg.accounts["default"].config_dir
    fake_claude_accounts(tmp_path, signs_in_as="c@x.org")
    checker.in_use = lambda provider: provider == "claude"
    assert (await until_done(checker.start("switch-claude"))).state == "done"
    second = tmp_cfg.accounts["default"].config_dir
    assert own_folders(tmp_cfg) == sorted([first, second])
    await checker.sweep()
    assert first.is_dir()
    checker.in_use = lambda provider: False
    # Not one changed only just now — which may be another copy's sign-in under way.
    await checker.sweep(older_than=ready.SIGN_IN_TIMEOUT)
    assert first.is_dir()
    await checker.sweep()
    assert own_folders(tmp_cfg) == [second]
    assert ("auth logout", str(first), "unset") in asked(calls)


async def test_back_to_the_macs_sign_in(tmp_cfg, tmp_path, claude_only):
    exe, calls = fake_claude_accounts(tmp_path, signs_in_as="b@x.org")
    claude_only(exe)
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    assert (await until_done(checker.start("switch-claude"))).state == "done"
    folder = tmp_cfg.accounts["default"].config_dir
    job = await until_done(checker.start("revert-claude"))
    assert job.state == "done", job.error
    assert job.step == ("unpacman reads with Claude Code's sign-in on "
                        f"{ready.here()} again, a@x.org.")
    assert tmp_cfg.accounts["default"] == CLAUDE and own_folders(tmp_cfg) == []
    assert ("auth logout", str(folder), "unset") in asked(calls)
    assert (tmp_path / "mac-who").read_text() == "a@x.org"
    # Already there: nothing to do.
    job = await until_done(checker.start("revert-claude"))
    assert job.step == f"unpacman reads with Claude Code's sign-in on {ready.here()} already."
    # And with the Mac's own signed out, going back says a sign-in is needed.
    assert (await until_done(checker.start("switch-claude"))).state == "done"
    (tmp_path / "mac-who").unlink()
    job = await until_done(checker.start("revert-claude"))
    assert job.step.startswith("unpacman no longer reads with an account of its own. Claude "
                               f"Code is not signed in on {ready.here()}")


async def test_getting_ready_says_whose_the_macs_sign_in_is(tmp_cfg, tmp_path, monkeypatch,
                                                           claude_only):
    exe, _ = fake_claude_accounts(tmp_path, signs_in_as="b@x.org")
    claude_only(exe)
    monkeypatch.setattr(ready, "prepare_environment", lambda env=None, home=None: None)
    monkeypatch.setattr(ready, "find_tex", lambda env=None, home=None: ready.Tex())
    monkeypatch.setattr(ready, "pdf_engine", lambda: "PDFium")
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    claude = next(a for a in checker.measure().ais if a.provider == "claude")
    assert (claude.who, claude.own) == ("a@x.org", False)
    assert (await until_done(checker.start("switch-claude"))).state == "done"
    claude = next(a for a in checker.measure().ais if a.provider == "claude")
    assert (claude.who, claude.own, claude.mac_signed_in, claude.mac_who) == (
        "b@x.org", True, True, "a@x.org")
    assert claude.to_dict()["own"] is True and claude.to_dict()["mac_who"] == "a@x.org"
    assert f"unpacman's own sign-in; elsewhere on {ready.here()}, signed in as a@x.org." in \
        ready.describe(checker.measure())
    # Signed out at claude.ai, say: still unpacman's own, now to sign in again.
    (tmp_cfg.accounts["default"].config_dir / "who").unlink()
    claude = next(a for a in checker.measure().ais if a.provider == "claude")
    assert (claude.signed_in, claude.own, claude.mac_who) == (False, True, "a@x.org")
    # And "Sign in" signs in that account, not the Mac's.
    job = await until_done(checker.start("signin-claude"))
    assert job.state == "done" and job.step == "Signed in to Claude as b@x.org."
    assert (tmp_path / "mac-who").read_text() == "a@x.org"


async def test_only_unpacmans_own_sign_ins_are_ever_signed_out(tmp_cfg, tmp_path, claude_only):
    from deslopify.app import signins

    exe, calls = fake_claude_accounts(tmp_path, signs_in_as="b@x.org")
    claude_only(exe)
    checker = ready.Checker(tmp_cfg, home=tmp_path / "home")
    root = signins.root(tmp_cfg)
    # Stand-ins only: were the refusal ever lost, nothing real could go.
    macs = tmp_path / "home" / ".claude"
    for folder in (macs, root / "claude-NOTHEX!", root / "codex-0123abcd",
                   root / "sub" / "claude-0123abcd", root.parent / "claude-0123abcd"):
        folder.mkdir(parents=True)
        (folder / "kept").write_text("")
        with pytest.raises(ValueError):
            await checker._sign_out("claude", folder)
        assert (folder / "kept").is_file()
    assert asked(calls) == []
    # One the program could not sign out is kept, to be signed out later.
    folder = checker.signins.fresh("claude")
    fake_claude_accounts(tmp_path, signs_in_as="b@x.org", logout=3)
    assert await checker._sign_out("claude", folder) is False and folder.is_dir()


async def test_another_chatgpt_account_is_for_unpacman_alone(tmp_cfg, tmp_path, monkeypatch,
                                                              codex_fit):
    from dataclasses import replace

    from atelier.providers.codex import CodexAdapter

    mac = tmp_path / "codex-home"                  # what ~/.codex is to the Mac
    mac.mkdir()
    (mac / "auth.json").write_text(json.dumps({"tokens": {"id_token": id_token("a@x.org"),
                                                          "access_token": "a"}}))
    before = (mac / "auth.json").read_bytes()
    staged = tmp_path / "staged-auth.json"
    staged.write_text(json.dumps({"tokens": {"id_token": id_token("b@x.org"),
                                             "access_token": "b"}}))
    calls = tmp_path / "calls"
    exe = script(tmp_path / "bin" / "codex", f"""echo "$1 | ${{CODEX_HOME:-none}}" >> {calls}
case "$1" in
  logout) rm -f "$CODEX_HOME/auth.json" ;;
  login) echo "https://auth.openai.com/oauth/authorize?client=x"; cp {staged} "$CODEX_HOME/auth.json" ;;
  *) echo "codex-cli 0.162.1" ;;
esac""")
    monkeypatch.setattr(ready, "_executable", lambda provider: str(exe) if provider == "codex"
                        else None)
    cfg = replace(tmp_cfg, accounts={**tmp_cfg.accounts,
                                     "codex": replace(tmp_cfg.accounts["codex"], config_dir=mac)})
    checker = ready.Checker(cfg, home=tmp_path / "home")
    job = await until_done(checker.start("switch-codex"))
    assert job.state == "done", job.error
    assert job.step == ("unpacman reads with b@x.org from now on. Codex elsewhere on "
                        f"{ready.here()} keeps the sign-in it has.")
    folder = cfg.accounts["codex"].config_dir
    assert checker.signins.owns(folder, "codex") and (folder / "auth.json").is_file()
    assert (mac / "auth.json").read_bytes() == before
    said = calls.read_text().splitlines()
    assert f"login | {folder}" in said
    assert not [s for s in said if s.startswith("log") and not s.endswith(f"| {folder}")]
    # A reading's Codex is given that sign-in, and only that one.
    home = CodexAdapter()._sanitised_home(cfg, cfg.default_account("codex"),
                                          destination=tmp_path / "run" / "codex")
    assert (home / "auth.json").resolve() == (folder / "auth.json").resolve()
    # Back to the Mac's: unpacman's own is signed out, there only.
    job = await until_done(checker.start("revert-codex"))
    assert job.step == f"unpacman reads with Codex's sign-in on {ready.here()} again, a@x.org."
    assert cfg.accounts["codex"].config_dir == mac and own_folders(cfg) == []
    assert f"logout | {folder}" in calls.read_text().splitlines()
    assert (mac / "auth.json").read_bytes() == before


async def test_another_account_waits_for_that_ai_to_be_installed_or_signed_in(
        tmp_cfg, tmp_path, monkeypatch):
    # No program at all: a switch that starts stops at once.
    monkeypatch.setattr(ready, "_executable", lambda provider: None)
    checker = ready.Checker(tmp_cfg, home=tmp_path)
    checker.jobs["j"] = ready.Job(id="j", kind="install-claude", title="Installing Claude Code")
    for kind in ("switch-claude", "revert-claude"):
        with pytest.raises(ready.Busy, match="Claude is being installed or signed in"):
            checker.start(kind)
    # Another AI's job is no reason to wait.
    checker.jobs["j"].kind = "install-codex"
    job = await until_done(checker.start("switch-claude"))
    assert job.state == "failed" and job.error == "Install Claude Code first."
    assert own_folders(tmp_cfg) == []


async def test_a_job_that_ends_is_told_to_whoever_listens(tmp_cfg, tmp_path, monkeypatch):
    """The server forgets which models each AI offers when one is signed in
    anew (catalog.py): every job that ends, however it ends, is told."""
    monkeypatch.setattr(ready, "_executable", lambda provider: None)
    checker = ready.Checker(tmp_cfg, home=tmp_path)
    heard: list[str] = []
    checker.on_finish.append(lambda job: heard.append(f"{job.kind}:{job.state}"))
    checker.on_finish.insert(0, lambda job: 1 / 0)          # a listener that fails is no matter
    await until_done(checker.start("signin-claude"))
    assert heard == ["signin-claude:failed"]
