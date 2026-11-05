"""The arXiv numbers the page finds in what is typed or pasted (2026-10-28)."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_the_page_finds_the_arxiv_numbers_in_what_is_typed():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the page's own tests")
    script = Path(__file__).parent / "web" / "arxiv-numbers.test.mjs"
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--test", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
