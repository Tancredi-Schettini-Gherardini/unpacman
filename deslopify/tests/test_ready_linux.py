"""Getting ready on Linux — Ubuntu under WSL, on a Windows computer (ready.py,
2026-10-17, branch unpacman_windows): TinyTeX's Linux bundle, where it goes,
what the sandbox needs, and how the page names the computer. Linux is
simulated: these run anywhere, with stand-in programs only.
"""

from __future__ import annotations

import os
import shlex
import sys
import tarfile
from pathlib import Path

import pytest

from deslopify.app import ready

from test_app import app_cfg, client  # noqa: F401  (fixtures)
from test_ready import script, until_done


@pytest.fixture
def linux(monkeypatch):
    """Linux, as far as ready.py asks."""
    monkeypatch.setattr(ready.sys, "platform", "linux")


def test_tinytex_goes_where_its_own_installer_puts_it(linux, monkeypatch, tmp_path):
    assert ready.tinytex_root(tmp_path) == tmp_path / ".TinyTeX"
    monkeypatch.setattr(ready.platform, "machine", lambda: "x86_64")
    assert ready.tinytex_url().endswith("/daily/TinyTeX-1-linux-x86_64.tar.xz")
    monkeypatch.setattr(ready.platform, "machine", lambda: "aarch64")
    assert ready.tinytex_url().endswith("/daily/TinyTeX-1-linux-arm64.tar.xz")


def test_a_mac_is_as_it_was(monkeypatch, tmp_path):
    monkeypatch.setattr(ready.sys, "platform", "darwin")
    assert ready.tinytex_root(tmp_path) == tmp_path / "Library" / "TinyTeX"
    assert ready.tinytex_url() == ready.TINYTEX_URL
    assert ready.here() == "this Mac" and ready.platform_name() == "mac"
    assert not ready.protection().needed and ready.protection().ok


async def test_latex_is_installed_on_linux_from_its_own_bundle(linux, tmp_cfg, tmp_path,
                                                               monkeypatch):
    calls = tmp_path / "tlmgr-calls"
    stage = tmp_path / "stage"
    script(stage / ".TinyTeX" / "bin" / "x86_64-linux" / "tlmgr",
           f'echo "$@" >> {calls}; exit 0')
    archive = tmp_path / "TinyTeX-1-linux-x86_64.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(stage / ".TinyTeX", arcname=".TinyTeX")
    monkeypatch.setattr(ready, "TINYTEX_LINUX_URL", f"file://{tmp_path}/TinyTeX-1-{{suffix}}.tar.xz")
    monkeypatch.setattr(ready.platform, "machine", lambda: "x86_64")
    # GNU tar invokes xz to unpack the archive; its stand-in must decompress,
    # unlike macOS tar, which handles xz internally.
    tools = tmp_path / "tools"
    script(tools / "perl", "exit 0")
    script(tools / "xz", f"exec {shlex.quote(sys.executable)} -c "
           "'import lzma, sys; "
           "sys.stdout.buffer.write(lzma.decompress(sys.stdin.buffer.read()))'")
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    tried = []
    monkeypatch.setattr(ready, "trial_typeset", lambda bindir: tried.append(bindir) or "")
    home = tmp_path / "home"
    job = await until_done(ready.Checker(tmp_cfg, home=home).start("install-latex"))
    assert job.state == "done", job.error
    said = calls.read_text().splitlines()
    assert said[0] == "update --self" and "tcolorbox" in said[1]
    assert tried == [str(home / ".TinyTeX" / "bin" / "x86_64-linux")]


async def test_latex_on_linux_says_which_programs_are_missing(linux, tmp_cfg, tmp_path,
                                                             monkeypatch):
    real = ready.shutil.which
    monkeypatch.setattr(ready.shutil, "which",
                        lambda name, *a, **k: None if name in ("xz", "perl") else real(name, *a, **k))
    job = await until_done(ready.Checker(tmp_cfg, home=tmp_path / "h").start("install-latex"))
    assert job.state == "failed"
    assert "lacks perl, xz" in job.error and "sudo apt install perl xz-utils" in job.error


def test_the_sandbox_is_needed_on_linux_and_says_what_to_type(linux, monkeypatch):
    real = ready.shutil.which
    monkeypatch.setattr(ready.shutil, "which",
                        lambda name, *a, **k: None if name in ("bwrap", "socat")
                        else real(name, *a, **k))
    p = ready.protection()
    assert p.needed and not p.ok and p.missing == ["bwrap", "socat"]
    assert p.command == "sudo apt install bubblewrap socat ripgrep"
    assert "Ubuntu password" in p.advice

    import atelier.access as access
    from deslopify.app import wsl

    monkeypatch.setattr(ready.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    monkeypatch.setattr(access, "bwrap_problem", lambda bwrap=None: "")
    assert ready.protection().ok
    # Ubuntu 24.04's restriction of user namespaces, said with its remedy.
    monkeypatch.setattr(access, "bwrap_problem",
                        lambda bwrap=None: "bwrap: setting up uid map: Permission denied")
    monkeypatch.setattr(wsl, "wsl_version", lambda: 2)
    p = ready.protection()
    assert not p.ok and "kernel.apparmor_restrict_unprivileged_userns=0" in p.command
    # WSL 1 cannot make one at all.
    monkeypatch.setattr(wsl, "wsl_version", lambda: 1)
    monkeypatch.setattr(wsl, "distro", lambda: "Ubuntu-24.04")
    assert ready.protection().command == "wsl --set-version Ubuntu-24.04 2"


def test_readiness_says_where_it_is_and_what_blocks(linux, monkeypatch):
    from deslopify.app import wsl

    monkeypatch.setattr(wsl, "is_wsl", lambda: True)
    guard = ready.Protection(needed=True, missing=["bwrap"], advice="Type this:",
                             command="sudo apt install bubblewrap ripgrep")
    ok_ai = ready.SignIn(account="default", provider="claude", installed=True, signed_in=True)
    tex = ready.Tex(bindir="/tex", label="TinyTeX", trial="ok")
    r = ready.Readiness(ais=[ok_ai], tex=tex, pdf="PDFium", checked=0, protection=guard)
    assert not r.ok
    assert any("sandbox" in b and "sudo apt install bubblewrap" in b for b in r.blocking())
    # A search, which typesets nothing, needs its sandbox all the same.
    assert any("sandbox" in b for b in r.blocking("claude", latex=False))
    shown = r.to_dict()
    assert shown["platform"] == "wsl" and shown["installable"] is True
    assert shown["protection"]["command"] == "sudo apt install bubblewrap ripgrep"
    r.protection = ready.Protection(needed=True)
    assert r.ok and not r.blocking()
    assert "Protection for readings (bubblewrap): ready" in ready.describe(r)


def test_the_page_names_the_computer(linux, monkeypatch):
    from deslopify.app import wsl

    assert ready.here() == "this computer"
    monkeypatch.setattr(wsl, "is_wsl", lambda: True)
    assert ready.platform_name() == "wsl"
    monkeypatch.setattr(wsl, "is_wsl", lambda: False)
    assert ready.platform_name() == "linux"


def test_a_language_linux_has_is_chosen(linux, tmp_path):
    env = {"PATH": "/usr/bin:/bin"}
    ready.prepare_environment(env, tmp_path)
    assert env["LANG"] == "C.UTF-8"


@pytest.fixture
def on_wsl(monkeypatch):
    """Inside WSL, from before the app is made (ask for it before `client`)."""
    monkeypatch.setattr(ready, "platform_name", lambda: "wsl")


def test_a_refused_tab_names_the_start_menu_on_windows(on_wsl, client):
    from fastapi.testclient import TestClient

    with TestClient(client.app) as visitor:
        shown = visitor.get("/api/papers/x/file/source.pdf", headers={
            "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none"})
    assert shown.status_code == 403
    assert "Open unpacman from the Start menu" in shown.text and "its icon" not in shown.text


def test_installs_are_offered_on_linux(monkeypatch, client):
    """The server lets the page install on Linux as on a Mac; elsewhere not —
    and tells the page which computer it is on."""
    monkeypatch.setattr(ready, "platform_name", lambda: "wsl")
    assert client.post("/api/ready/install-latex").status_code == 200
    assert '<html lang="en" data-platform="wsl">' in client.get("/").text
    monkeypatch.setattr(ready, "platform_name", lambda: "other")
    r = client.post("/api/ready/install-claude")
    assert r.status_code == 400 and "for a Mac, or Linux" in r.json()["detail"]
    monkeypatch.setattr(ready, "platform_name", lambda: "mac")
    assert '<html lang="en" data-platform="mac">' in client.get("/").text


def test_windows_folders_are_kept_off_unpacmans_path(linux, tmp_path, monkeypatch):
    """Under WSL, Windows' own folders are on PATH: a Codex installed on Windows
    with npm leaves a `codex` there, which must never be taken for Linux's."""
    monkeypatch.setattr(ready, "WINDOWS_PATH", [])
    env = {"PATH": "/usr/local/bin:/mnt/c/Windows/system32:/mnt/c/Users/R/AppData/Roaming/npm"
                   ":/usr/bin:/bin", "LANG": "C.UTF-8"}
    ready.prepare_environment(env, tmp_path)
    assert "/mnt/" not in env["PATH"] and env["PATH"].startswith("/usr/local/bin:")
    assert ready.WINDOWS_PATH == ["/mnt/c/Windows/system32", "/mnt/c/Users/R/AppData/Roaming/npm"]
    # Nothing else of /mnt is Windows' (a disk mounted by hand, say).
    env = {"PATH": "/mnt/data/tools:/usr/bin"}
    ready.prepare_environment(env, tmp_path)
    assert env["PATH"].startswith("/mnt/data/tools:")


async def test_a_sign_in_may_open_the_windows_browser(linux, tmp_cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(ready, "WINDOWS_PATH", ["/mnt/c/Windows/system32"])
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    seen = tmp_path / "path-seen"
    exe = script(tmp_path / "claude", f'''case "$1 $2" in
  "auth login") echo "$PATH" > {seen}; echo "https://claude.com/cai/oauth/authorize?x"; exit 1 ;;
  *) echo '{{"loggedIn": false}}' ;;
esac''')
    monkeypatch.setattr(ready, "_executable", lambda p: str(exe) if p == "claude" else None)
    job = await until_done(ready.Checker(tmp_cfg, home=tmp_path / "home").start("signin-claude"))
    assert job.state == "failed" and job.url.startswith("https://claude.com/")
    assert seen.read_text().strip().endswith(":/mnt/c/Windows/system32")


def test_without_a_sandbox_latex_fails_plainly(monkeypatch, tmp_path):
    """The companion is never typeset outside its sandbox; without one (on
    Linux, no bubblewrap) that is a LaTeX failure said in words."""
    from atelier.access import AccessPolicyError
    from deslopify import summary_pdf

    def refuse(argv, **kwargs):
        raise AccessPolicyError("Unpacman's restricted file access on Linux needs bubblewrap, "
                                "which is not installed (on Ubuntu: sudo apt install bubblewrap).")
    monkeypatch.setattr("atelier.access.sandbox_command", refuse)
    monkeypatch.setattr(summary_pdf, "_latex_engine", lambda: "latexmk")
    tex = tmp_path / "companion.tex"
    tex.write_text(r"\documentclass{article}\begin{document}x\end{document}")
    with pytest.raises(summary_pdf.SummaryPdfError, match="needs bubblewrap"):
        summary_pdf._compile_tex(tex, tmp_path / "companion.pdf")
