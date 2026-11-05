"""Explanation effort stays valid when the companion's provider changes."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_explanation_effort_across_providers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the explanation effort UI tests")
    script = Path(__file__).parent / "web" / "explanation-effort.test.mjs"
    result = subprocess.run([node, "--experimental-vm-modules", "--test", str(script)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
