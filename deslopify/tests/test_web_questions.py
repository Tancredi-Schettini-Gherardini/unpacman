"""Questionnaire UI races, tested without starting a server or an AI run."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_questionnaire_async_navigation():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the questionnaire UI tests")
    script = Path(__file__).parent / "web" / "questions.test.mjs"
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--test", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
