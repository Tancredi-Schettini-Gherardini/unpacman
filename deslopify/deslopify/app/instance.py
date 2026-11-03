"""One unpacman per copy, started however suits the person: from its icon, or
from a Terminal.

Started from the icon, unpacman must outlive the click: the icon's program
starts the server in the background, on its own, waits until it answers, opens
the browser and exits. Closing a window then cannot stop a reading, and the
icon clicked again finds the server already there and only opens the page.

A server records where it is listening in `var/unpacman-server.json`, and is
recognised by asking it (`/api/hello`) rather than by trusting that file, which
a crash leaves behind. Port 7720 is used when free; when something else holds
it, the next free one is — unless a port was named on the command line, which
is then meant.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_PORT = 7720
PORT_SPAN = 20
LOG_LIMIT = 2_000_000          # bytes; the log is started afresh beyond this

# The server record always names this computer's Unpacman server.  Going through
# a user's HTTP proxy can therefore turn a healthy local server into an apparent
# failure (and should never send this health check to that proxy).
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def record_path(root: Path) -> Path:
    return root / "var" / "unpacman-server.json"


def log_path(root: Path) -> Path:
    return root / "var" / "unpacman.log"


def app_marker(root: Path) -> Path:
    """Written when an icon is made for this copy: such a copy checks for new
    versions by itself."""
    return root / "var" / "unpacman-app.json"


def hello(url: str, timeout: float = 2.0,
          token: str | None = None) -> dict[str, Any] | None:
    """What the server at `url` says it is, or None if nothing answers as unpacman."""
    try:
        request = urllib.request.Request(
            url.rstrip("/") + "/api/hello",
            headers={"X-Unpacman-Token": token} if token else {})
        with _LOCAL_OPENER.open(request, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == "unpacman" else None


def _same_root(data: dict[str, Any], root: Path) -> bool:
    try:
        return Path(str(data.get("root"))).resolve() == root.resolve()
    except OSError:
        return False


def find_running(root: Path) -> str | None:
    """The authenticated browser address of this copy's running server."""
    path = record_path(root)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        url = str(record["url"])
        token = str(record.get("token") or "")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    data = hello(url, token=token or None)
    if not data or not _same_root(data, root):
        return None
    # A pre-upgrade server may still be finishing a reading. Reuse it rather
    # than starting a second copy on the same papers; it needs a later restart.
    return f"{url}#auth={quote(token)}" if token else url


def write_record(root: Path, *, url: str, port: int, background: bool,
                 token: str) -> None:
    path = record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The launch token must not be exposed by the ordinary 0644 file umask.
    fd, raw = tempfile.mkstemp(prefix="unpacman-server-", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "url": url, "port": port,
                       "background": background, "started": time.time(),
                       "token": token}, stream)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def clear_record(root: Path) -> None:
    """Remove the record, if it is this process's."""
    path = record_path(root)
    with contextlib.suppress(OSError, ValueError):
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()


def port_free(host: str, port: int) -> bool:
    """Whether a server could listen on this port now."""
    with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=0.3):
        return False                     # something is answering there
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # As uvicorn does, so a port in TIME_WAIT still counts as free.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(host: str, preferred: int = DEFAULT_PORT, span: int = PORT_SPAN) -> int:
    for port in range(preferred, preferred + span):
        if port_free(host, port):
            return port
    raise RuntimeError(f"No free port between {preferred} and {preferred + span - 1}.")


def rotate_log(root: Path) -> Path:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        if path.stat().st_size > LOG_LIMIT:
            path.replace(path.with_suffix(".log.1"))
    return path


def log_tail(root: Path, lines: int = 80) -> list[str]:
    path = log_path(root)
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 64_000))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def start_background(root: Path, *, host: str, port: int, extra: list[str],
                     wait: float = 90.0) -> str:
    """Start this copy's server on its own, logging to `var/unpacman.log`, and
    return its address once it answers. Raises RuntimeError if it does not."""
    log = rotate_log(root)
    url = f"http://{host}:{port}/"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- started from its icon, {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        fh.flush()
        proc = subprocess.Popen(
            [sys.executable, "-m", "deslopify.app", "--no-browser", "--port", str(port),
             "--background", *extra],
            cwd=root, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if launch_url := find_running(root):
            return launch_url
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    raise RuntimeError(f"unpacman did not start. What it said is in {log}.")


def alert(title: str, message: str) -> None:
    """A Mac dialog, for when there is no page to say it on."""
    if sys.platform != "darwin":
        print(f"{title}: {message}", file=sys.stderr)
        return
    script = ("on run argv\n display alert (item 1 of argv) message (item 2 of argv) "
              "as critical\nend run")
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["/usr/bin/osascript", "-e", script, title, message],
                       capture_output=True, timeout=600, check=False)


def open_browser(url: str) -> bool:
    """Open the page: in the Windows browser when this is WSL — which Python's
    own webbrowser does not know — else as the system does."""
    from .wsl import is_wsl, open_url

    if is_wsl() and open_url(url):
        return True
    import webbrowser

    return webbrowser.open(url)


class KeepAwake:
    """Keeps the Mac from falling asleep while a reading runs — `caffeinate`,
    tied to this server, so it can never outlive it. Closing the lid still
    puts it to sleep; the page says so. Inside WSL, Windows is kept awake
    instead (wsl.keep_awake), by a PowerShell that ends when its input does."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen[bytes] | None = None

    def set(self, wanted: bool) -> None:
        if wanted and self.proc is None:
            self.proc = self._start()
        elif not wanted and self.proc is not None:
            self.stop()

    @staticmethod
    def _start() -> subprocess.Popen[bytes] | None:
        if sys.platform == "darwin":
            if not Path("/usr/bin/caffeinate").is_file():
                return None
            with contextlib.suppress(OSError):
                return subprocess.Popen(
                    ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
            return None
        from .wsl import is_wsl, keep_awake

        return keep_awake() if is_wsl() else None

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            if proc.stdin is not None:
                # Windows' (WSL): its input closed is its end.
                with contextlib.suppress(OSError):
                    proc.stdin.close()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
            if proc.poll() is None:
                proc.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)

    @property
    def on(self) -> bool:
        return self.proc is not None and self.proc.poll() is None
