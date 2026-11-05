"""The page's key on every request, and a newer one taken up once (dom.js;
2026-10-20) — tested without a server or a browser."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_the_page_sends_its_key_and_takes_up_a_newer_one():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the page's own tests")
    script = Path(__file__).parent / "web" / "auth.test.mjs"
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--test", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
