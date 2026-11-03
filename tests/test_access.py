"""Access policy regressions; live tests use only disposable canary files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import time

import pytest

from atelier import access


def test_environment_does_not_inherit_unrelated_credentials(monkeypatch):
    for key in ("AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "SSH_AUTH_SOCK", "PYTHONPATH", "BASH_ENV"):
        monkeypatch.setenv(key, "private-canary")
    assert "private-canary" not in json.dumps(access.clean_environment())


def test_codex_may_read_its_own_program_to_apply_edits(tmp_path, monkeypatch):
    # Codex applies each edit by starting itself again, by the path it was
    # started from, inside its sandbox: without these folders every edit failed
    # under the default-deny profile (2026-10-15, Codex 0.162.1).
    def program(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path

    # As Codex's own installer lays it out.
    home = tmp_path / "home"
    release = home / ".codex/packages/standalone/releases/9.9.9-test"
    program(release / "bin/codex")
    (release / "codex-package.json").write_text("{}")
    (release.parent.parent / "current").symlink_to(release)
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/codex").symlink_to(home / ".codex/packages/standalone/current/bin/codex")
    assert access.codex_program_paths(home / ".local/bin/codex") == [
        home / ".local/bin", home / ".codex/packages/standalone", release]

    # As Homebrew does, with a relative link.
    brew = tmp_path / "brew"
    program(brew / "Caskroom/codex/1.0/bin/codex")
    (brew / "Caskroom/codex/1.0/codex-package.json").write_text("{}")
    (brew / "bin").mkdir(parents=True)
    (brew / "bin/codex").symlink_to("../Caskroom/codex/1.0/bin/codex")
    assert access.codex_program_paths(brew / "bin/codex") == [
        brew / "bin", brew / "Caskroom/codex/1.0"]

    # The bare program: only its own folder. Not found: nothing at all.
    bare = program(tmp_path / "lone/bin/codex")
    assert access.codex_program_paths(bare) == [tmp_path / "lone/bin"]
    assert access.codex_program_paths("codex") == []
    assert access.codex_program_paths(tmp_path / "missing/codex") == []

    # Never the home folder, or anything above it.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / "codex").symlink_to(release / "bin/codex")
    assert access.codex_program_paths(home / "codex") == [release]

    # And a reading's Codex is told so.
    config = dict(v.split("=", 1) for v in access.codex_permissions(
        tmp_path, readable=access.codex_program_paths(brew / "bin/codex"))[1::2])
    assert f'"{(brew / "Caskroom/codex/1.0").resolve()}"="read"' in config[
        "permissions.unpacman.filesystem"]


def test_codex_policy_cannot_be_overridden_by_legacy_sandbox(tmp_path):
    args = access.codex_permissions(tmp_path, network=False)
    assert "--sandbox" not in args
    config = dict(v.split("=", 1) for v in args[1::2])
    assert config["default_permissions"] == '"unpacman"'
    assert '":root"="deny"' in config["permissions.unpacman.filesystem"]
    assert config["approval_policy"] == '"never"'
    assert config["web_search"] == '"disabled"'
    assert config["permissions.unpacman.network.enabled"] == "false"
    assert config["features.apps"] == "false"
    assert config["features.plugins"] == "false"
    assert config["shell_environment_policy.inherit"] == '"none"'


def test_codex_bundled_search_tool_is_readable_in_restricted_runs(tmp_path, monkeypatch):
    package = tmp_path / "codex-package"
    (package / "bin").mkdir(parents=True)
    (package / "codex-path").mkdir()
    (package / "bin" / "codex").write_text("binary")
    (package / "codex-path" / "rg").write_text("binary")
    (package / "codex-package.json").write_text('{"version":"1.2.3"}')
    monkeypatch.setattr(access.shutil, "which",
                        lambda name: str(package / "bin" / "codex") if name == "codex" else None)
    assert (package / "codex-path").resolve() in access.runtime_paths()


def test_public_research_does_not_relax_filesystem_or_connected_app_policy(tmp_path):
    offline = dict(v.split("=", 1) for v in access.codex_permissions(tmp_path)[1::2])
    online = dict(v.split("=", 1) for v in
                  access.codex_permissions(tmp_path, network=True)[1::2])
    for key in ("permissions.unpacman.filesystem", "approval_policy",
                "features.apps", "features.plugins", "features.browser_use",
                "features.computer_use", "shell_environment_policy.inherit"):
        assert online[key] == offline[key]
    assert online["web_search"] == '"live"'
    assert online["permissions.unpacman.network.enabled"] == "true"
    assert online["features.network_proxy"] == "true"
    assert online["permissions.unpacman.network.domains"] == '{"*"="allow"}'
    assert online["permissions.unpacman.network.allow_local_binding"] == "false"
    assert online["permissions.unpacman.network.allow_upstream_proxy"] == "false"
    assert online["permissions.unpacman.network.proxy_url"] == '"http://127.0.0.1:0"'
    assert online["permissions.unpacman.network.socks_url"] == '"http://127.0.0.1:0"'


def _filesystem(args: list[str]) -> str:
    return dict(v.split("=", 1) for v in args[1::2])["permissions.unpacman.filesystem"]


def test_codex_commands_are_refused_the_macs_shared_temporary_folders(tmp_path, monkeypatch):
    # Codex's `:minimal` lets a command list, read and WRITE /tmp and /var/tmp
    # (0.162.1; the other reviewer's finding 2, 2026-11-02): denied by name.
    monkeypatch.setattr(access.sys, "platform", "darwin")
    for network in (False, True):
        for writable in (True, False):
            fs = _filesystem(access.codex_permissions(tmp_path, network=network,
                                                      writable=writable))
            assert '"/private/tmp"="deny"' in fs and '"/private/var/tmp"="deny"' in fs
    # Never where it would take away a folder the job is given: in Codex a deny
    # wins over what is inside it.
    fs = _filesystem(access.codex_permissions(Path("/private/tmp/unpacman-x/workspace")))
    assert '"/private/tmp"="deny"' not in fs and '"/private/var/tmp"="deny"' in fs
    fs = _filesystem(access.codex_permissions(tmp_path, readable=[Path("/private/var/tmp/x")]))
    assert '"/private/tmp"="deny"' in fs and '"/private/var/tmp"="deny"' not in fs
    # Linux's Codex sandbox is another program (unpacman_windows): left as it was.
    monkeypatch.setattr(access.sys, "platform", "linux")
    fs = _filesystem(access.codex_permissions(tmp_path))
    assert "/private/tmp" not in fs and "/private/var/tmp" not in fs


def test_unpacmans_own_sandbox_hides_other_programs():
    # Their command lines and environments (sysctl KERN_PROCARGS2) are refused
    # only when both are: process information about the other program, and
    # kern.procargs* to sysctl-read; either alone left them open (2026-11-02).
    # Without a target the first also hides the list of programs.
    for network in (False, True):
        rules = access.seatbelt_profile(read=[], write=[], network=network).splitlines()
        assert rules[-3:] == [
            "(deny process-info*)", "(allow process-info* (target same-sandbox))",
            '(deny sysctl-read (sysctl-name-prefix "kern.procargs"))']


def test_unsupported_platform_refuses_instead_of_unsandboxing(monkeypatch):
    monkeypatch.setattr(access.sys, "platform", "unsupported")
    with pytest.raises(access.AccessPolicyError, match="no unrestricted fallback"):
        access.sandbox_command(["echo", "unsafe"], read=[], write=[])


def test_prompt_does_not_tell_a_restricted_run_to_search_the_home(tmp_path):
    prompt = access.reading_policy(tmp_path, tmp_path / "skills", network=False)
    assert "Do not look for AGENTS.md" in prompt
    assert "No web research" in prompt
    assert str(tmp_path / "skills") in prompt


def test_public_research_prompt_preserves_local_file_restrictions(tmp_path):
    prompt = access.reading_policy(tmp_path, network=True)
    assert "Public literature searches and downloads are allowed" in prompt
    assert "Do not search the home" in prompt
    assert "localhost services" in prompt
    assert "Never send model credentials" in prompt
    assert "No web research" not in prompt


def test_job_tree_rejects_links_before_a_trusted_copy(tmp_path):
    work = tmp_path / "run"
    work.mkdir()
    private = tmp_path / "private"
    private.write_text("private-canary")
    (work / "guide.md").symlink_to(private)
    with pytest.raises(access.AccessPolicyError, match="Unsafe link"):
        access.check_job_tree(work)
    (work / "source.pdf").write_text("uploaded")
    assert access.quarantine_job_links(work, tmp_path / "rejected") == 1
    access.check_job_tree(work)
    assert (work / "source.pdf").read_text() == "uploaded"
    assert private.read_text() == "private-canary"


def test_a_tool_free_claude_call_gets_its_own_scratch_and_its_keychain(tmp_path, monkeypatch):
    # Claude Code 2.1.296 under the profile failed with "EPERM … open
    # '/tmp/claude-501'", then "Not logged in" (2026-10-13): its scratch is
    # given to it here, and its sign-in is in the keychain.
    seen = {}
    monkeypatch.setattr(access, "sandbox_command",
                        lambda argv, **kw: seen.update(kw) or argv)
    _, env = access.protect_provider(["claude"], {"HOME": str(tmp_path)}, cwd=tmp_path / "w",
                                     state=tmp_path / "state", readable=[], provider="claude")
    scratch = Path(env["CLAUDE_CODE_TMPDIR"])
    assert scratch.is_dir() and scratch.is_relative_to(tmp_path / "state")
    assert scratch.stat().st_mode & 0o777 == 0o700
    assert any(p.name == "Keychains" for p in seen["read"]) or not (
        Path.home() / "Library" / "Keychains").exists()
    # An open-weight model signs in with a token, not the keychain.
    _, env = access.protect_provider(["claude"], {"HOME": str(tmp_path)}, cwd=tmp_path / "o",
                                     state=tmp_path / "s2", readable=[], provider="open")
    assert "CLAUDE_CODE_TMPDIR" in env
    assert not any(p.name == "Keychains" for p in seen["read"])


def test_a_reading_pass_gets_a_short_scratch_its_sandbox_allows(tmp_path):
    # Its commands' output and its shell's working-folder file go there; a
    # longer name than ~30 bytes and Claude Code falls back to the shared
    # /tmp/claude-<uid>, which the pass may not touch (2026-10-13).
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"sandbox": {"filesystem": {
        "denyRead": ["/"], "allowRead": ["/a"], "allowWrite": ["/w"]}}}))
    record = tmp_path / "env.json"
    record.write_text(json.dumps({"HOME": "/h", "ANTHROPIC_AUTH_TOKEN": "<redacted>"}))
    env = {}
    scratch = access.attach_claude_scratch(settings, env)
    try:
        assert scratch is not None and env["CLAUDE_CODE_TMPDIR"] == str(scratch)
        assert len(str(scratch)) <= 30 and scratch == scratch.resolve()
        assert scratch.stat().st_mode & 0o777 == 0o700
        files = json.loads(settings.read_text())["sandbox"]["filesystem"]
        assert files["allowRead"] == ["/a", str(scratch)]
        assert files["allowWrite"] == ["/w", str(scratch)]
        assert files["denyRead"] == ["/"]
        # The pass's record of its environment names it, and stays redacted.
        assert json.loads(record.read_text()) == {
            "HOME": "/h", "ANTHROPIC_AUTH_TOKEN": "<redacted>",
            "CLAUDE_CODE_TMPDIR": str(scratch)}
    finally:
        access.remove_claude_scratch(env)
    assert not scratch.exists()
    # Without a sandbox to allow it in, nothing is made; and nothing but such a
    # scratch is ever removed.
    plain = tmp_path / "plain.json"
    plain.write_text("{}")
    assert access.attach_claude_scratch(plain, {}) is None
    keep = tmp_path / "keep"
    keep.mkdir()
    access.remove_claude_scratch({"CLAUDE_CODE_TMPDIR": str(keep)})
    assert keep.is_dir()


@pytest.mark.parametrize("failure", [OSError("no such program"), RuntimeError("stopped")])
async def test_a_reading_pass_that_never_starts_leaves_no_scratch(
        tmp_cfg, tmp_db, sample_problem, monkeypatch, failure):
    # Only the end of a pass removes its scratch; a launch that fails — or is
    # interrupted — has no end (2026-10-15: twenty left by one test run).
    from atelier.compile import plan_run
    from atelier.events import EventHub
    from atelier.harnesses import get_harness
    from atelier.runner import RunHandle
    from atelier.spec import RunSpec

    sample_problem.ensure_scaffold()
    (sample_problem.progress_dir / "math.tex").write_text(
        r"\documentclass{article}\begin{document}A source paper.\end{document}")
    harness = get_harness(tmp_cfg.harnesses_dir, "deslopify-nonexpert")
    plan = plan_run(tmp_cfg, sample_problem, harness, RunSpec(
        problem=sample_problem.slug, harness=harness.id, provider="claude",
        account="default", model="test-model", max_minutes=40), isolate_reading=True)
    handle = RunHandle(tmp_cfg, plan, EventHub(), tmp_db)
    handle.loop_state = object()
    made, ended = [], []

    async def launch(*args, **kwargs):
        made.append(Path(plan.first.env["CLAUDE_CODE_TMPDIR"]))
        assert made[-1].is_dir()
        raise failure

    async def finalize(state, reason):
        ended.append(reason)

    monkeypatch.setattr("atelier.runner.asyncio.create_subprocess_exec", launch)
    monkeypatch.setattr(handle, "_finalize", finalize)
    try:
        if isinstance(failure, OSError):
            await handle._begin_pass(plan.first)
            assert "failed to launch" in handle.error and ended == ["launch failed"]
        else:
            with pytest.raises(RuntimeError):
                await handle._begin_pass(plan.first)
        assert made and not made[-1].exists()
    finally:
        handle.log.close()


_live = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("UNPACMAN_SANDBOX_TEST") != "1",
    reason="opt-in macOS kernel sandbox test (cannot nest inside the coding sandbox)")


@_live
def test_live_process_boundary_denies_reads_listing_symlinks_and_writes(tmp_path):
    workspace = tmp_path / "run"
    workspace.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.txt").write_text("private-canary")
    (workspace / "uploaded.txt").write_text("uploaded-canary")
    (workspace / "escape").symlink_to(private, target_is_directory=True)
    code = """
from pathlib import Path
import sys
cwd, private = map(Path, sys.argv[1:])
assert (cwd / 'uploaded.txt').read_text() == 'uploaded-canary'
(cwd / 'output.txt').write_text('allowed')
for action in (
    lambda: (private / 'secret.txt').read_text(),
    lambda: list(private.iterdir()),
    lambda: (cwd / 'escape/secret.txt').read_text(),
    lambda: (private / 'new.txt').write_text('denied'),
):
    try:
        action()
    except PermissionError:
        pass
    else:
        raise AssertionError('outside access was allowed')
print('allowlist enforced')
"""
    args = access.sandbox_command(
        [sys.executable, "-c", code, str(workspace), str(private)],
        read=[], write=[workspace])
    result = subprocess.run(args, cwd=workspace, env=access.clean_environment(),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "allowlist enforced" in result.stdout
    assert not (private / "new.txt").exists()


_OTHER_PROGRAM = """
import ctypes, os, sys
libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
def arguments_of(pid):
    mib = (ctypes.c_int * 3)(1, 49, pid)          # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(1 << 20)
    buf = ctypes.create_string_buffer(size.value)
    ok = libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) == 0
    return buf.raw[:size.value] if ok else b""
other = arguments_of(int(sys.argv[1]))
if sys.argv[2].encode() in other or sys.argv[3].encode() in other:
    sys.exit("another program's command line or environment was read")
if ctypes.CDLL("/usr/lib/libproc.dylib").proc_listallpids(None, 0) > 0:
    sys.exit("the list of programs was read")
assert b"own-canary" in arguments_of(os.getpid()), "its own command line was refused"
print("other programs hidden")
"""


_mac = pytest.mark.skipif(sys.platform != "darwin", reason="macOS's calls and folders")


@_live
@_mac
def test_live_job_cannot_read_another_programs_command_line_or_environment(tmp_path):
    marker, env_marker = f"argument-canary-{os.getpid()}", f"environment-canary-{os.getpid()}"
    other = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(60)  # {marker}"],
        env={"PATH": "/usr/bin:/bin", "DUMMY_CANARY": env_marker},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    try:
        time.sleep(0.3)
        probe = [sys.executable, "-c", _OTHER_PROGRAM, str(other.pid), marker, env_marker,
                 "own-canary"]
        # Outside any sandbox the call works: the probe can tell.
        bare = subprocess.run(probe, capture_output=True, text=True, timeout=20)
        assert "another program's command line or environment was read" in bare.stderr
        args = access.sandbox_command(probe, read=[], write=[tmp_path])
        result = subprocess.run(args, cwd=tmp_path, env=access.clean_environment(),
                                capture_output=True, text=True, timeout=20)
    finally:
        other.kill()
        other.wait()
    assert result.returncode == 0, result.stderr
    assert "other programs hidden" in result.stdout


@_live
def test_live_python_pdf_libraries_and_bundled_tools_still_work(tmp_path):
    args = access.sandbox_command([
        sys.executable, "-c",
        "import pypdfium2, deslopify, atelier; print('libraries available')"],
        read=[], write=[tmp_path])
    result = subprocess.run(args, cwd=tmp_path, env=access.clean_environment(),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "libraries available" in result.stdout


@_live
@pytest.mark.parametrize("name", ["codex", "claude"])
def test_live_provider_can_start_without_inference_or_credentials(tmp_path, name):
    exe = shutil.which(name)
    if not exe:
        pytest.skip(f"{name} is not installed")
    env = access.clean_environment() | {"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path)}
    args = access.sandbox_command([exe, "--version"], read=[], write=[tmp_path])
    result = subprocess.run(args, cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@_live
@pytest.mark.parametrize("network", [False, True])
def test_live_codex_command_profile_restricts_reads_without_model_call(tmp_path, network):
    exe = shutil.which("codex")
    if not exe:
        pytest.skip("codex is not installed")
    work = tmp_path / "work"
    work.mkdir()
    private = tmp_path / "outside"
    private.mkdir()
    secret = private / "secret"
    secret.write_text("canary")
    (work / "source").write_text("allowed")
    (tmp_path / "codex").mkdir()
    auth = tmp_path / "codex/auth.json"
    auth.write_text('{"auth_mode":"api_key","OPENAI_API_KEY":"private-canary"}')
    env = access.clean_environment() | {"CODEX_HOME": str(tmp_path / "codex"),
                                        "HOME": str(tmp_path), "TMPDIR": str(work)}
    # Only bundled binaries and synthetic files; no inference, sign-in or web.
    args = [exe, *access.codex_permissions(work, network=network), "sandbox", "-P", "unpacman",
            "-C", str(work), "--", "/bin/sh", "-c",
            'cat source && ! cat "$1" && ! ls "$2" && ! cat "$3" && echo enforced',
            "probe", str(secret), str(private), str(auth)]
    result = subprocess.run(args, cwd=work, env=env,
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert "enforced" in result.stdout
    assert "canary" not in result.stdout


@_live
@_mac
@pytest.mark.parametrize("network", [False, True])
def test_live_codex_commands_cannot_reach_the_shared_temporary_folders(tmp_path, network):
    exe = shutil.which("codex")
    if not exe:
        pytest.skip("codex is not installed")
    work = tmp_path / "work"
    (work / ".unpacman-tmp").mkdir(parents=True)
    (tmp_path / "codex").mkdir()
    env = access.clean_environment() | {"CODEX_HOME": str(tmp_path / "codex"),
                                        "HOME": str(tmp_path),
                                        "TMPDIR": str(work / ".unpacman-tmp")}
    dummies = []
    try:
        # Made inside the try: should the second fail, the first is removed.
        for shared in ("/private/tmp", "/private/var/tmp"):
            dummies.append(Path(tempfile.mkdtemp(prefix="unpacman-test-", dir=shared)))
        for dummy in dummies:
            (dummy / "canary").write_text("shared-canary")
        script = ('for d in "$@"; do ls "$d" >/dev/null 2>&1 && echo "listed $d"; '
                  'cat "$d/canary" 2>/dev/null; (echo x > "$d/written") 2>/dev/null '
                  '&& echo "wrote $d"; done; '
                  '(echo own-folder > "$TMPDIR/own") && cat "$TMPDIR/own"')
        args = [exe, *access.codex_permissions(work, network=network), "sandbox",
                "-P", "unpacman", "-C", str(work), "--", "/bin/sh", "-c", script,
                "probe", *map(str, dummies)]
        result = subprocess.run(args, cwd=work, env=env, capture_output=True, text=True,
                                timeout=25)
        written = [d for d in dummies if (d / "written").exists()]
    finally:
        for dummy in dummies:
            shutil.rmtree(dummy, ignore_errors=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["own-folder"], result.stdout
    assert not written


@_live
@pytest.mark.parametrize("network", [False, True])
def test_live_codex_accepts_strict_settings_without_inference(tmp_path, network):
    exe = shutil.which("codex")
    if not exe:
        pytest.skip("codex is not installed")
    state = tmp_path / "state"
    state.mkdir()
    env = access.clean_environment() | {"HOME": str(state), "CODEX_HOME": str(state)}
    args = [exe, "exec", "--ignore-user-config", "--strict-config",
            "--skip-git-repo-check", *access.codex_permissions(tmp_path, network=network),
            "--config", 'model_provider="unpacman-offline-probe"', "offline test"]
    args = access.sandbox_command(args, read=[], write=[tmp_path])
    result = subprocess.run(args, cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    # A deliberately nonexistent provider prevents any inference. Reaching this
    # validation error means the preceding strict settings parsed successfully.
    assert result.returncode != 0
    assert "unpacman-offline-probe" in result.stderr, result.stderr[-2000:]
    assert "not found" in result.stderr.lower(), result.stderr[-2000:]
    assert "unknown config" not in result.stderr.lower()


@_live
@pytest.mark.skipif(os.environ.get("UNPACMAN_NETWORK_TEST") != "1",
                    reason="opt-in public HTTPS request (no credentials or model call)")
def test_live_public_downloads_work_without_local_service_or_file_access(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    exe = shutil.which("codex")
    if not exe:
        pytest.skip("codex is not installed")
    requests = []

    class CanaryHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"private-service-canary")

        def log_message(self, *args):
            pass

    private = tmp_path / "outside.txt"
    private.write_text("private-file-canary")
    server = HTTPServer(("127.0.0.1", 0), CanaryHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def probe(index):
        work, state = tmp_path / f"work-{index}", tmp_path / f"state-{index}"
        work.mkdir()
        state.mkdir()
        env = access.clean_environment() | {
            "HOME": str(state), "CODEX_HOME": str(state), "TMPDIR": str(work)}
        script = '''
set -eu
curl --fail --silent --show-error --max-time 20 https://example.com -o public.html
test -s public.html
! cat "$1"
! curl --fail --silent --show-error --max-time 5 "$2"
! curl --noproxy '*' --fail --silent --show-error --max-time 5 "$2"
echo 'public research allowed; private access denied'
'''
        args = [exe, *access.codex_permissions(work, network=True),
                "sandbox", "-P", "unpacman", "-C", str(work), "--",
                "/bin/sh", "-c", script, "probe", str(private),
                f"http://127.0.0.1:{server.server_port}/canary"]
        result = subprocess.run(args, cwd=work, env=env, capture_output=True,
                                text=True, timeout=40)
        assert result.returncode == 0, result.stderr[-3000:]
        assert "public research allowed; private access denied" in result.stdout
        assert "private-file-canary" not in result.stdout
        assert "private-service-canary" not in result.stdout

    try:
        # Concurrent jobs must receive separate ports, not collide on 3128/8081.
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(probe, range(2)))
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@_live
def test_live_companion_pdf_compiles_without_personal_file_access(tmp_path):
    from deslopify.summary_pdf import _compile_tex, SummaryPdfError
    if not shutil.which("latexmk"):
        pytest.skip("TeX is not installed")
    work = tmp_path / "reading"
    work.mkdir()
    tex = work / "companion.tex"
    tex.write_text(r"\documentclass{article}\begin{document}Safe PDF.\end{document}")
    pdf = _compile_tex(tex, work / "companion.pdf")
    assert pdf.read_bytes().startswith(b"%PDF")
    # Neither absolute \input nor a symlink may import an unrelated file.
    secret = tmp_path / "private.tex"
    secret.write_text("private-canary")
    (work / "escape.tex").symlink_to(secret)
    for target in (str(secret), "escape.tex"):
        tex.write_text(r"\documentclass{article}\begin{document}\input{" +
                       target + r"}\end{document}")
        with pytest.raises(SummaryPdfError):
            _compile_tex(tex, work / "blocked.pdf")
    assert not (work / "blocked.pdf").exists()


@_live
def test_live_companion_symbol_and_bibliography_url_compile(tmp_path):
    from deslopify.summary_pdf import write_companion, SummaryPdfError
    if not shutil.which("latexmk"):
        pytest.skip("TeX is not installed")
    guide = tmp_path / "guide.md"
    guide.write_text(
        "# A mathematical companion\n\n"
        r"**Theorem.** The group $\Sha(E)$ is finite under the stated assumptions."
        "\n\n[Numdam](https://www.numdam.org/issues/AST_2009__324__R1_0/) "
        "and [author](https://example.org/~author/a_b?q=10%25&x=2#part).\n")
    outputs = [tmp_path / f"companion.{ext}" for ext in ("md", "tex", "pdf")]
    write_companion(guide, *outputs, audience="expert")
    assert outputs[-1].read_bytes().startswith(b"%PDF-")
    # Arbitrary macros must fail explicitly, never receive guessed definitions.
    guide.write_text(r"Unknown $\UndefinedScientificMacro(x)$.")
    with pytest.raises(SummaryPdfError, match="UndefinedScientificMacro"):
        write_companion(guide, *outputs, audience="expert")
    assert not outputs[-1].exists()


@_live
def test_live_pdf_helper_inherits_the_native_command_sandbox(tmp_path):
    exe = shutil.which("codex")
    if not exe or not shutil.which("latexmk"):
        pytest.skip("needs installed Codex and TeX")
    work, state = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    state.mkdir()
    (work / "companion.tex").write_text(
        r"\documentclass{article}\begin{document}Inside the sandbox.\end{document}")
    env = access.clean_environment() | {
        "CODEX_HOME": str(state), "HOME": str(state), "TMPDIR": str(work),
        "UNPACMAN_RESTRICTED_JOB": "1"}
    code = ("from pathlib import Path; from deslopify.summary_pdf import _compile_tex; "
            "_compile_tex(Path('companion.tex'), Path('companion.pdf')); print('compiled')")
    args = [exe, *access.codex_permissions(work), "sandbox", "-P", "unpacman",
            "-C", str(work), "--", sys.executable, "-c", code]
    result = subprocess.run(args, cwd=work, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-2500:]
    assert (work / "companion.pdf").read_bytes().startswith(b"%PDF")
