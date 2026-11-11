"""The explanations by run and in threads, and Back from a collection's paper
(Tancredi, 2026-11-11): the page's own logic, run under Node."""

from pathlib import Path
import shutil
import subprocess

import pytest


def _node_test(name: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the page's own tests")
    script = Path(__file__).parent / "web" / name
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--test", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_notes_show_the_run_on_screen_each_followed_by_its_thread():
    _node_test("notes-threads.test.mjs")


def test_back_from_a_paper_of_a_collection_returns_to_the_collection():
    _node_test("back-from-collection.test.mjs")
