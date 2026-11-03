"""AI-writable files must not become code in an unsandboxed hook."""

from __future__ import annotations

from atelier.hooks import check_reading
from atelier.access import clean_environment


def test_reading_validator_does_not_import_from_ai_workspace(tmp_path, monkeypatch):
    (tmp_path / "deslopify.py").write_text(
        'print("UNTRUSTED MODULE EXECUTED")\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, output = check_reading.deslopify("--help")
    assert code == 0
    assert "UNTRUSTED MODULE EXECUTED" not in output
    assert "deslopify — check and query an argument graph" in output


def test_server_control_token_is_not_in_reader_environment(monkeypatch):
    monkeypatch.setenv("DESLOPIFY_APP_CONTROL_TOKEN", "test-secret")
    monkeypatch.setenv("DESLOPIFY_APP_SETTINGS", '{"control_token":"test-secret"}')
    assert "DESLOPIFY_APP_CONTROL_TOKEN" not in clean_environment()
    assert "DESLOPIFY_APP_SETTINGS" not in clean_environment()
