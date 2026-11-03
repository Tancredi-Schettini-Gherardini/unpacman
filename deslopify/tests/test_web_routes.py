"""A collection run link must open that run, rather than the latest companion."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_reading_routes_preserve_the_audience_and_selected_run():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the page's own tests")
    script = Path(__file__).parent / "web" / "routes.test.mjs"
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--test", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
