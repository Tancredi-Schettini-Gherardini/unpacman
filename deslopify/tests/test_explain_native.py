"""Actual Codex startup and tool denial, using a dummy host and no real sign-in."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from atelier.access import clean_environment
from deslopify.app import agents, explain
from test_app import app_cfg  # noqa: F401


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("codex")
                    or os.environ.get("UNPACMAN_SANDBOX_TEST") != "1",
                    reason="opt-in installed Codex test with a dummy model host")
@pytest.mark.parametrize("credential", [False, True])
async def test_native_web_answer_starts_at_high_effort_and_rejects_file_writes(
        app_cfg, tmp_path, monkeypatch, credential):
    requests, after_patch = [], []
    paths = {}
    secret = tmp_path / "credentials" / "auth.json"
    secret.parent.mkdir()
    original = '{"auth_mode":"api_key","OPENAI_API_KEY":"synthetic-private-key"}'
    secret.write_text(original)

    class Host(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            requests.append(body)
            if len(requests) == 1:
                target = secret if credential else paths["work"] / "forbidden.txt"
                patch = (f"*** Update File: {target}\n@@\n-{original}\n+OVERWRITTEN\n"
                         if credential else f"*** Add File: {target}\n+UNAUTHORIZED_WRITE\n")
                item = {"id": "patch_probe", "type": "custom_tool_call", "name": "apply_patch",
                        "call_id": "patch_call", "input": f"*** Begin Patch\n{patch}*** End Patch"}
            else:
                after_patch.append(secret.read_text() if credential
                                   else (paths["work"] / "forbidden.txt").exists())
                item = {"id": "msg_probe", "type": "message", "role": "assistant",
                        "phase": "final_answer", "status": "completed",
                        "content": [{"type": "output_text", "text": "Probe complete.", "annotations": []}]}
            response = {"id": f"resp_{len(requests)}", "object": "response", "created_at": 1,
                        "status": "completed", "model": "gpt-5.5", "output": [item],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                  "input_tokens_details": {"cached_tokens": 0}}}
            events = [
                {"type": "response.created", "response": dict(response, status="in_progress", output=[])},
                {"type": "response.output_item.added", "output_index": 0, "item": item},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in events:
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()

    host = ThreadingHTTPServer(("127.0.0.1", 0), Host)
    Thread(target=host.serve_forever, daemon=True).start()
    command = explain._codex_command

    def invocation(model, workdir, **kwargs):
        paths["work"] = workdir
        settings = {
            "model_provider": '"unpacman-probe"',
            "model_providers.unpacman-probe.name": '"Unpacman probe"',
            "model_providers.unpacman-probe.base_url": json.dumps(f"http://127.0.0.1:{host.server_port}/v1"),
            "model_providers.unpacman-probe.wire_api": '"responses"',
            "model_providers.unpacman-probe.env_key": '"PROBE_API_KEY"',
            "model_providers.unpacman-probe.requires_openai_auth": "false",
        }
        return command(model, workdir, **kwargs)[:-1] + [
            arg for key, value in settings.items() for arg in ("--config", f"{key}={value}")] + ["-"]

    def environment(cfg, account, workdir):
        state = workdir / "provider-state" / "codex"
        state.mkdir(parents=True)
        (state / "auth.json").symlink_to(secret)
        return clean_environment() | {"HOME": str(tmp_path), "CODEX_HOME": str(state),
                                      "PROBE_API_KEY": "dummy"}

    monkeypatch.setattr(explain, "_codex_command", invocation)
    monkeypatch.setattr(explain, "_codex_env", environment)
    agent = agents.Agent(account="codex", provider="codex", model="gpt-5.5", effort="high")
    try:
        async def collect():
            return [event async for event in explain.call(app_cfg, agent, "Synthetic paper", "Probe", web=True)]

        events = await asyncio.wait_for(collect(), timeout=30)
        assert events[-1][1].text == "Probe complete."
        assert len(requests) == 2
        assert requests[0]["reasoning"]["effort"] == "high"
        assert any(t["type"] == "web_search" for t in requests[0]["tools"])
        names = {t.get("name") for t in requests[0]["tools"]}
        assert not names & {"exec_command", "shell", "spawn_agent", "create_goal", "get_goal", "update_goal"}
        assert after_patch == ([original] if credential else [False])
        assert secret.read_text() == original
    finally:
        host.shutdown()
        host.server_close()
