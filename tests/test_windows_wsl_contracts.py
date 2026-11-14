"""Tier 0 & Tier 1 regression tests for Windows Job Object tree-kill, WSL2 PATH filtering, `-I -X utf8` `knowledge_cli`, and UNC/organizer contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

import admission
import agy_transcript
import bounded
import bridge
import diagnostic_support
from mindie_knowledge.materials import summarizer
import organizer
import paths

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "plugins" / "mindie-agent" / "scripts"
SESSION_ID = "454c6ee9-afea-47be-8188-abb9c7fb1f26"


def test_which_native_filters_wsl_mnt_shims_and_windowsapps_stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wsl_win_dir = tmp_path / "mnt_c_npm"
    linux_bin_dir = tmp_path / "linux_bin"
    wsl_win_dir.mkdir()
    linux_bin_dir.mkdir()

    linux_uv = linux_bin_dir / "uv"
    linux_uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    linux_uv.chmod(0o755)

    monkeypatch.setattr(paths, "_is_wsl", lambda: True)
    mixed_path = os.pathsep.join(["/mnt/c/Users/user/AppData/Roaming/npm", "/mnt/c/Program Files (x86)/Git/cmd", str(linux_bin_dir)])
    resolved = paths.which_native("uv", path=mixed_path)
    if os.name != "nt":
        assert resolved == str(linux_uv)
    assert paths.which_native("nonexistent-wsl-tool", path="/mnt/c/Windows/System32") is None

    # Verify WindowsApps 0-byte stub rejection when os.name == "nt"
    monkeypatch.setattr(paths, "_is_wsl", lambda: False)
    winapps = tmp_path / "Microsoft" / "WindowsApps"
    winapps.mkdir(parents=True)
    stub = winapps / "python3.exe"
    stub.write_bytes(b"")
    monkeypatch.setattr(paths.os, "name", "nt")
    monkeypatch.setattr(paths.shutil, "which", lambda _cmd, path=None: str(stub))
    assert paths.which_native("python3", path=str(winapps)) is None


def test_bounded_run_timeout_kills_grandchild_holding_stdout_without_winerror32(tmp_path: Path):
    pid_file = tmp_path / "grandchild.pid"
    parent_code = (
        "import pathlib, subprocess, sys, time\n"
        f"pid_file = pathlib.Path({str(pid_file)!r})\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "pid_file.write_text(str(child.pid), encoding='utf-8')\n"
        "sys.stdout.write('spawned\\n'); sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(TimeoutError, match="process timed out"):
        bounded.run([sys.executable, "-c", parent_code], timeout=0.6)

    assert pid_file.is_file()
    grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
    import component_update

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and component_update._pid_running(grandchild_pid):
        time.sleep(0.05)
    assert component_update._pid_running(grandchild_pid) is False


def test_knowledge_cli_isolated_mode_and_session_flag_work_cross_platform(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    out = bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_ID,
            "workspacePaths": [str(ws)],
            "lastUserInput": "/mindie-agent check cross-platform knowledge_cli",
            "invocationNum": 1,
        }
    )
    msg = out["injectSteps"][0]["ephemeralMessage"]
    assert "--session" in msg
    assert "AGY_CONVERSATION_ID=" not in msg
    assert admission.knowledge_lease(SESSION_ID) is not None

    cli = SCRIPTS_DIR / "knowledge_cli.py"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("AGY_CONVERSATION_ID", None)
    res = bounded.run(
        [sys.executable, "-I", "-X", "utf8", str(cli), "--session", SESSION_ID, "describe"],
        timeout=10.0,
        env=env,
    )
    assert res.returncode == 0, res.stderr + res.stdout
    payload = json.loads(res.stdout)
    assert payload["isError"] is False
    assert {item["name"] for item in payload["tools"]} >= {"knowledge_query", "knowledge_explain", "knowledge_distill"}


def test_organizer_distinguishes_presend_refusal_from_midstream_drop(monkeypatch: pytest.MonkeyPatch):
    pol = organizer.identity()
    req = summarizer.make_request(
        task_id="b" * 64,
        body_version="rev-1",
        blocks=[{"block_id": "a" * 64, "text": "### user\nq\n\n### assistant\na", "source_range": {"start": 0, "end": 20, "digest": "c" * 64}}],
        prior_navigation=None,
        identity=pol,
    )

    # 1. Closed local port -> pre-send failure (status='failed', model_calls=0, billing_status='not_called')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    closed_port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setenv("MINDIE_SUMMARY_ENDPOINT", f"http://127.0.0.1:{closed_port}/summarize")
    pre = organizer.execute_request(req)
    assert pre["status"] == "failed"
    assert pre["model_calls"] == 0
    assert pre["billing_status"] == "not_called"

    # 2. Server accepts POST body then resets/truncates mid-response -> post-send outcome_unknown (model_calls=1)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    live_port = srv.getsockname()[1]

    def _drop_after_request():
        conn, _ = srv.accept()
        try:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n{\"partial\":")
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=_drop_after_request, daemon=True)
    t.start()
    monkeypatch.setenv("MINDIE_SUMMARY_ENDPOINT", f"http://127.0.0.1:{live_port}/summarize")
    mid = organizer.execute_request(req)
    t.join(timeout=2.0)
    assert mid["status"] == "outcome_unknown"
    assert mid["model_calls"] == 1
    assert mid["billing_status"] == "unknown"


def test_file_url_to_path_and_transcript_identity_canonicalization(tmp_path: Path):
    win_drive = diagnostic_support._file_url_to_path("file:///C:/Users/dev/mindie-agent")
    assert win_drive is not None
    assert str(win_drive).replace("\\", "/") == "C:/Users/dev/mindie-agent"

    wsl_unc = diagnostic_support._file_url_to_path("file://wsl.localhost/Ubuntu/home/dev/repo")
    assert wsl_unc is not None
    assert "wsl.localhost" in str(wsl_unc)
    assert "Ubuntu" in str(wsl_unc)

    brain_dir = tmp_path / "brain" / SESSION_ID
    brain_dir.mkdir(parents=True)
    tfile = brain_dir / "transcript.jsonl"
    record = {
        "type": "USER_INPUT",
        "source": "USER_EXPLICIT",
        "created_at": "2026-11-14T08:00:00Z",
        "content": "<USER_REQUEST>hello ascend</USER_REQUEST>",
    }
    tfile.write_text(json.dumps(record) + "\n", encoding="utf-8", newline="\n")
    ident = agy_transcript.identify(tfile)
    assert ident is not None
    unser = agy_transcript.FileIdentity.unserialize(ident.serialize(), str(tfile))
    assert unser is not None
    mat = agy_transcript.read_material(tfile, 0, session_id=SESSION_ID, expected=unser)
    assert mat["status"] == "ok"
    assert "hello ascend" in mat["text"]


def test_powershell_activation_syntax_bom_input_proxy_tunnel_and_cleanup_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from types import SimpleNamespace
    import subprocess
    import knowledge_cli

    # 1. PowerShell `& "C:\...\python.exe"` call operator when python path is quoted.
    ws = tmp_path / "ws_ps"
    ws.mkdir()
    fake_py = tmp_path / "Python 313" / "python.exe"
    fake_py.parent.mkdir(parents=True)
    fake_py.write_bytes(b"MZ")
    cfg = tmp_path / "agy.json"
    cfg.write_text(json.dumps({"python": str(fake_py)}), encoding="utf-8")
    out = bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_ID,
            "workspacePaths": [str(ws)],
            "lastUserInput": "/mindie-agent verify ps syntax",
            "invocationNum": 2,
        },
        config=cfg,
    )
    msg = out["injectSteps"][0]["ephemeralMessage"]
    assert f'& "{fake_py}" -I -X utf8' in msg

    # 2. PowerShell 5.1 UTF-8 BOM (`\xef\xbb\xbf`) accepted by `--input-file`.
    bom_file = tmp_path / "ps51_bom.json"
    bom_file.write_bytes(b"\xef\xbb\xbf" + json.dumps({"query": "ascend aclgraph"}).encode("utf-8"))
    seen_ops: list[tuple[str, dict]] = []

    def fake_invoke(op, arguments, **_kwargs):
        seen_ops.append((op, arguments))
        return {"isError": False, "ok": True}

    monkeypatch.setattr(knowledge_cli, "invoke", fake_invoke)
    rc = knowledge_cli.main(["--session", SESSION_ID, "--input-file", str(bom_file), "knowledge_query"])
    assert rc == 0
    assert seen_ops == [("knowledge_query", {"query": "ascend aclgraph"})]

    # 3. HTTPS proxy `CONNECT` tunnel negotiation does NOT mark `transmitted = True`.
    owner = SimpleNamespace(transmitted=False)
    conn = organizer._TrackedHTTPSConnection("api.example.invalid", 443, owner=owner)
    sent_chunks: list[bytes] = []
    monkeypatch.setattr(organizer.http.client.HTTPSConnection, "send", lambda self, data: sent_chunks.append(data))
    monkeypatch.setattr(
        organizer.http.client.HTTPSConnection,
        "_tunnel",
        lambda self: self.send(b"CONNECT api.example.invalid:443 HTTP/1.0\r\n\r\n"),
    )
    conn._tunnel()
    assert owner.transmitted is False
    conn.send(b"POST /v1/chat/completions HTTP/1.1\r\n\r\n")
    assert owner.transmitted is True

    # 4. Shared Windows fallback cleanup budget never exceeds `wait_timeout` (2.0s).
    waits: list[float] = []

    class HungPopen:
        def __init__(self, args=None, **_kwargs):
            self.args = args or ["owned-child"]
            self.pid = 4321
            self.returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            pass

        def wait(self, timeout=None):
            waits.append(float(timeout or 0.0))
            raise subprocess.TimeoutExpired(self.args, timeout or 0.0)

    monkeypatch.setattr(bounded, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(bounded, "_STDLIB_POPEN", HungPopen)
    failed = bounded._kill_process_tree(HungPopen(), job_handle=None, wait_timeout=2.0)
    assert failed is True
    assert sum(waits) <= 2.0 + 1e-6

