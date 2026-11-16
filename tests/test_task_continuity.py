"""Failed MindIE admission preserves the MCP connection and truthful results."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import mcp_server


def invoke(name, arguments, **kwargs):
    return mcp_server.call_remote_surface(name, arguments, **kwargs)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_CONVERSATION_ID", "454c6ee9-afea-47be-8188-abb9c7fb1f26")
    monkeypatch.setenv("REMOTE_DEV_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.delenv("REMOTE_DEV_CREDENTIAL_FILE", raising=False)


def test_real_path_rejection_is_failed_in_mcp_and_receipt(session, tmp_path):
    receipts = mcp_server.RemoteReceipts(tmp_path / "receipts.sqlite3")
    response = invoke("remote_write", {
        "host": "auth.example", "port": 22, "root": "/workspace", "cwd": "/workspace",
        "file_path": "/outside-root.txt", "content": "fixture",
    }, receipts=receipts)
    assert response["structuredContent"]["result"]["outcome"] == "blocked"
    assert response["isError"] is True
    with sqlite3.connect(receipts.path) as db:
        assert db.execute("SELECT status FROM receipts").fetchone()[0] == "failed"


def test_uncertain_result_stays_unknown_in_receipt(session, tmp_path, monkeypatch):
    import remote_dev.mcp.tools as tools

    monkeypatch.setattr(tools, "call_tool", lambda *args, **kwargs: {
        "text": "fixture transport capture", "result": {
            "outcome": "failed", "status": "submission_uncertain",
            "error_details": {"submission_state": "uncertain"},
        },
    })
    receipts = mcp_server.RemoteReceipts(tmp_path / "receipts.sqlite3")
    response = invoke("remote_bash", {"host": "auth.example", "port": 22, "command": "fixture"},
                      receipts=receipts)
    assert response["isError"] is True
    with sqlite3.connect(receipts.path) as db:
        assert db.execute("SELECT status FROM receipts").fetchone()[0] == "unknown"


def test_consumed_bind_never_leaks_previous_session_to_hookless_next_session(session, tmp_path, monkeypatch):
    from identity import publish_bind

    session_a = "454c6ee9-afea-47be-8188-abb9c7fb1f26"
    session_b = "554c6ee9-afea-47be-8188-abb9c7fb1f26"
    arguments = {"host": "auth.example", "port": 22, "action": "status"}
    receipts = mcp_server.RemoteReceipts(tmp_path / "receipts.sqlite3")
    publish_bind(session_a, "remote", "remote_auth", arguments)
    assert invoke("remote_auth", arguments, receipts=receipts)["isError"] is False
    monkeypatch.setenv("AGY_CONVERSATION_ID", session_b)
    # Hookless subagent / session_b call succeeds under session_b without reusing session_a.
    assert invoke("remote_auth", arguments, receipts=receipts)["isError"] is False
    with sqlite3.connect(receipts.path) as db:
        assert [row[0] for row in db.execute("SELECT session FROM receipts ORDER BY started")] == [session_a, session_b]


def test_remote_session_environment_is_never_adopted_over_connection_scope(tmp_path, monkeypatch):
    monkeypatch.delenv("AGY_CONVERSATION_ID", raising=False)
    forged = "454c6ee9-afea-47be-8188-abb9c7fb1f26"
    monkeypatch.setenv("REMOTE_DEV_SESSION_ID", forged)
    receipts = mcp_server.RemoteReceipts(tmp_path / "receipts.sqlite3")
    res = invoke("remote_auth", {"host": "auth.example", "port": 22, "action": "status"}, receipts=receipts)
    assert res["isError"] is False
    with sqlite3.connect(receipts.path) as db:
        recorded = db.execute("SELECT session FROM receipts").fetchone()[0]
    assert recorded != forged
    assert recorded.startswith("agy-mcp-")


def test_admission_failure_does_not_terminate_stdio_server(tmp_path, monkeypatch):
    import os
    from paths import remote_receipts_path

    monkeypatch.delenv("AGY_CONVERSATION_ID", raising=False)
    monkeypatch.delenv("REMOTE_DEV_SESSION_ID", raising=False)
    receipt = remote_receipts_path()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_bytes(b"fixture invalid sqlite database")
    script = Path(mcp_server.__file__)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "remote_auth", "arguments": {"host": "auth.example", "port": 22, "action": "status"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
    ]
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(script), "remote"],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
        env=dict(os.environ),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert completed.returncode == 0, completed.stderr
    replies = {item["id"]: item for item in map(json.loads, completed.stdout.splitlines())}
    assert set(replies) == {1, 2, 3, 4}
    assert replies[2]["result"]["isError"] is True
    assert replies[2]["result"]["structuredContent"]["execution"] == "not_started"
    assert replies[3]["result"] == {}
    assert replies[4]["result"]["tools"]


def test_duplicate_rpc_frame_is_rejected_even_after_bind_consumption(session, tmp_path):
    import os
    from identity import publish_bind

    receipts = mcp_server.RemoteReceipts(tmp_path / "receipts.sqlite3")
    arguments = {"host": "auth.example", "port": 22, "action": "status"}
    bound_session = "454c6ee9-afea-47be-8188-abb9c7fb1f26"
    publish_bind(bound_session, "remote", "remote_auth", arguments)
    req = {"jsonrpc": "2.0", "id": 77, "method": "tools/call", "params": {"name": "remote_auth", "arguments": arguments}}
    first = mcp_server.handle_rpc(req, "remote", receipts=receipts)
    assert first["result"]["isError"] is False
    publish_bind(bound_session, "remote", "remote_auth", arguments)
    replay = mcp_server.handle_rpc(req, "remote", receipts=receipts)
    assert replay["result"]["isError"] is True
    fresh = mcp_server.handle_rpc({**req, "id": 78}, "remote", receipts=receipts)
    assert fresh["result"]["isError"] is False
    assert os.environ["REMOTE_DEV_SESSION_ID"] == bound_session

