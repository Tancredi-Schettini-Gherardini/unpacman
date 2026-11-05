"""unpacman inside WSL, on a Windows computer (wsl.py, instance.py; 2026-10-17,
branch unpacman_windows).

Windows is a stand-in here: a `powershell.exe` that decodes the script it is
sent, keeps it, and answers as Windows would; and a `wslpath`. These run on
any computer; that Windows then does what the scripts say is for a Windows
computer to show (WINDOWS.md).
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

from deslopify.app import instance, wsl

FAKE_POWERSHELL = '''#!{python}
import base64, os, sys
args = sys.argv[1:]
script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16-le")
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "scripts.log"), "a", encoding="utf-8") as fh:
    fh.write(" ".join(a for a in args if a != args[args.index("-EncodedCommand") + 1])
             + "\\n" + script + "\\n---\\n")
if "SetThreadExecutionState" in script:
    sys.stdin.read()                      # until unpacman closes it
    open(os.path.join(here, "awake-ended"), "w").write("ended")
    sys.exit(0)
if os.environ.get("FAKE_POWERSHELL_BOM"):
    sys.stdout.flush()
    sys.stdout.buffer.write(b"\\xef\\xbb\\xbf")   # as Windows may, first
    sys.stdout.buffer.flush()
if "LocalApplicationData" in script:
    print(r"C:\\Users\\Reader\\AppData\\Local")
if "CreateShortcut" in script:
    print(r"C:\\Users\\Reader\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Unpacman.lnk")
sys.exit(int(os.environ.get("FAKE_POWERSHELL_EXIT", "0")))
'''


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """A Linux inside Windows, as far as wsl.py can tell: WSL's own names, and
    Windows' programs on PATH."""
    bindir = tmp_path / "windows-bin"
    bindir.mkdir()
    ps = bindir / "powershell.exe"
    ps.write_text(FAKE_POWERSHELL.format(python=sys.executable))
    ps.chmod(0o755)
    monkeypatch.setattr(wsl.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setattr(wsl, "WINDOWS_PLACES", ())
    monkeypatch.setattr(wsl, "_windows_cwd", lambda: None)
    return bindir


def scripts(bindir: Path) -> list[str]:
    log = bindir / "scripts.log"
    return log.read_text(encoding="utf-8").split("\n---\n")[:-1] if log.exists() else []


def test_wsl_is_known_by_its_own_names(monkeypatch):
    monkeypatch.setattr(wsl.sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(wsl, "_osrelease", lambda: "6.6.36.6-microsoft-standard-wsl2")
    assert wsl.is_wsl() and wsl.wsl_version() == 2
    monkeypatch.setattr(wsl, "_osrelease", lambda: "4.4.0-19041-microsoft")
    assert wsl.is_wsl() and wsl.wsl_version() == 1
    # WSL 2's first kernels did not say WSL2; and a kernel of one's own, which
    # only WSL 2 runs, need not say Microsoft.
    monkeypatch.setattr(wsl, "_osrelease", lambda: "4.19.104-microsoft-standard")
    assert wsl.is_wsl() and wsl.wsl_version() == 2
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(wsl, "_osrelease", lambda: "6.8.12-custom")
    assert wsl.is_wsl() and wsl.wsl_version() == 2
    monkeypatch.delenv("WSL_DISTRO_NAME")
    monkeypatch.setattr(wsl, "_osrelease", lambda: "6.8.0-45-generic")
    assert not wsl.is_wsl() and wsl.wsl_version() is None
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert wsl.is_wsl() and wsl.distro() == "Ubuntu"
    # A Mac is never Windows, whatever it is told.
    monkeypatch.setattr(wsl.sys, "platform", "darwin")
    assert not wsl.is_wsl()


def test_scripts_travel_to_powershell_encoded(windows):
    done = wsl.powershell("Write-Output 'Grüße, “quotes” & $dollars'")
    assert done.returncode == 0
    args, script = scripts(windows)[-1].split("\n", 1)
    assert "-NoProfile" in args and "-NonInteractive" in args and "-EncodedCommand" in args
    assert script.endswith("Write-Output 'Grüße, “quotes” & $dollars'")
    # Its output in UTF-8 if the console allows it, and no progress bars.
    assert script.startswith("$ProgressPreference = 'SilentlyContinue'\n"
                             "try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }\n")


def test_what_powershell_says_is_read_as_text(windows, monkeypatch):
    # The mark Windows may put first is not part of what it said.
    monkeypatch.setenv("FAKE_POWERSHELL_BOM", "1")
    done = wsl.powershell("[Environment]::GetFolderPath('LocalApplicationData')")
    assert done.stdout.strip() == "C:\\Users\\Reader\\AppData\\Local"
    # Its errors, when it writes them as CLIXML, as their lines.
    clixml = ('#< CLIXML\r\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/'
              'powershell/2004/04"><S S="Error">Exception calling "Save" with "0" '
              'argument(s): &quot;Access is denied.&quot;_x000D__x000A_</S>'
              '<S S="Error">At line:9 char:1_x000D__x000A_</S></Objs>')
    assert wsl.said(clixml) == ('Exception calling "Save" with "0" argument(s): '
                                '"Access is denied."\nAt line:9 char:1')
    assert wsl.said("  plain words \n") == "plain words"
    assert wsl.said(None) == ""


def test_powershell_is_found_where_unpacman_put_windows_folders(windows, monkeypatch):
    # prepare_environment took Windows' folders off PATH, and kept them.
    from deslopify.app import ready

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(ready, "WINDOWS_PATH", [str(windows)])
    assert wsl.windows_program("powershell.exe") == str(windows / "powershell.exe")
    monkeypatch.setattr(ready, "WINDOWS_PATH", [])
    assert wsl.windows_program("powershell.exe") is None
    with pytest.raises(wsl.WindowsAppError, match="interop"):
        wsl.powershell("Write-Output 1")


def test_quoting_for_powershell_and_for_wsl_exe():
    assert wsl.ps_quote("it's") == "'it''s'"
    assert wsl.cmd_quote("/home/reader/.unpacman") == "/home/reader/.unpacman"
    assert wsl.cmd_quote("/home/my reader/x") == '"/home/my reader/x"'
    assert wsl.shortcut_arguments(Path("/home/r/.unpacman"), Path("/home/r/.unpacman/.venv/bin/python"),
                                  "Ubuntu-24.04") == (
        "-d Ubuntu-24.04 --cd /home/r/.unpacman -e /home/r/.unpacman/.venv/bin/python "
        "-m deslopify.app")


def test_the_page_opens_in_the_windows_browser(windows, monkeypatch):
    url = "http://127.0.0.1:7720/#auth=abc-DEF_123"
    assert wsl.open_url(url)
    assert scripts(windows)[-1].rstrip().endswith(f"Start-Process '{url}'")
    # PowerShell failing: wslu's own opener, where there is one.
    monkeypatch.setenv("FAKE_POWERSHELL_EXIT", "1")
    assert not wsl.open_url(url)
    viewer = windows / "wslview"
    viewer.write_text(f"#!/bin/sh\necho \"$1\" > {windows / 'viewed'}\n")
    viewer.chmod(0o755)
    assert wsl.open_url(url) and (windows / "viewed").read_text().strip() == url


def test_unpacman_opens_its_page_in_windows_inside_wsl(monkeypatch):
    opened = []
    monkeypatch.setattr(wsl, "is_wsl", lambda: True)
    monkeypatch.setattr(wsl, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr("webbrowser.open", lambda url: pytest.fail("not Python's browser"))
    assert instance.open_browser("http://127.0.0.1:7720/")
    assert opened == ["http://127.0.0.1:7720/"]


def test_the_start_menu_shortcut_opens_this_copy_in_this_linux(windows, tmp_path, monkeypatch):
    local = tmp_path / "windows-local"
    local.mkdir()
    wslpath = windows / "wslpath"
    # Asked for the folder Windows named, which exists; unpacman's is made in it.
    wslpath.write_text("#!/bin/sh\n[ \"$1\" = -u ] && [ \"$2\" = 'C:\\Users\\Reader\\AppData\\"
                       f"Local' ] && echo {local}\n")
    wslpath.chmod(0o755)
    monkeypatch.setenv("FAKE_POWERSHELL_BOM", "1")
    copy = tmp_path / "copy"
    python = Path("/home/reader/.unpacman/.venv/bin/python")
    link = wsl.make_shortcut(copy, python)
    assert link.endswith("\\Start Menu\\Programs\\Unpacman.lnk")
    # Its icon, where Windows reads it without starting Linux.
    assert (local / "unpacman" / "unpacman.ico").read_bytes() == wsl.ICON.read_bytes()
    made = next(s for s in scripts(windows) if "CreateShortcut" in s)
    assert "$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\\wsl.exe'" in made
    assert (f"$shortcut.Arguments = '-d Ubuntu-24.04 --cd {copy} -e {python} "
            "-m deslopify.app'") in made
    assert ("$shortcut.IconLocation = 'C:\\Users\\Reader\\AppData\\Local\\unpacman\\"
            "unpacman.ico,0'") in made
    assert "$shortcut.WindowStyle = 7" in made
    # A copy with one checks for new versions by itself, as with the Mac's icon.
    marker = json.loads((copy / "var" / "unpacman-app.json").read_text())
    assert marker["app"] == link


def test_no_shortcut_outside_wsl_or_without_its_name(windows, monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME")
    monkeypatch.setattr(wsl, "_osrelease", lambda: "6.6.36.6-microsoft-standard-wsl2")
    with pytest.raises(wsl.WindowsAppError, match="WSL_DISTRO_NAME"):
        wsl.make_shortcut(tmp_path, Path("/x/python"))
    monkeypatch.setattr(wsl.sys, "platform", "darwin")
    with pytest.raises(wsl.WindowsAppError, match="only be made from inside WSL"):
        wsl.make_shortcut(tmp_path, Path("/x/python"))


def test_windows_is_kept_awake_while_a_reading_runs(windows, monkeypatch):
    monkeypatch.setattr(instance.sys, "platform", "linux")
    awake = instance.KeepAwake()
    awake.set(True)
    assert awake.on
    awake.set(True)                                     # once, not again
    assert sum("SetThreadExecutionState" in s for s in scripts(windows)) <= 1
    awake.set(False)
    assert not awake.on
    for _ in range(100):
        if (windows / "awake-ended").exists():
            break
        time.sleep(0.05)
    assert (windows / "awake-ended").read_text() == "ended"
    assert "SetThreadExecutionState([uint32]2147483649)" in scripts(windows)[-1]


def test_no_windows_to_keep_awake_is_no_matter(monkeypatch):
    monkeypatch.setattr(instance.sys, "platform", "linux")
    monkeypatch.setattr(wsl, "is_wsl", lambda: False)
    awake = instance.KeepAwake()
    awake.set(True)
    assert not awake.on and awake.proc is None


def test_the_icon_is_a_windows_icon_of_every_size():
    blob = wsl.ICON.read_bytes()
    assert blob[:4] == b"\x00\x00\x01\x00"
    count = int.from_bytes(blob[4:6], "little")
    sizes = sorted((blob[6 + 16 * i] or 256) for i in range(count))
    assert sizes == [16, 24, 32, 48, 64, 128, 256]
