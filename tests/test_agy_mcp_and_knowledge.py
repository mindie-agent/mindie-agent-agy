"""Tier 0 & Tier 1 tests for MCP discovery separation, private `knowledge_cli`, and Receipt Honesty."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest

import bridge
import knowledge_cli
import mcp_server


SESSION_A = "454c6ee9-afea-47be-8188-abb9c7fb1f26"


def test_public_mcp_excludes_knowledge_even_when_session_is_active(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent activate session",
        }
    )

    remote_resp = mcp_server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "remote")
    tools_resp = mcp_server.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "tools")

    remote_names = {t["name"] for t in remote_resp["result"]["tools"]}
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}

    assert "remote_read" in remote_names
    assert "remote_bash" in remote_names
    assert {
        "mindie_status",
        "profiler",
        "sim",
        "coordinator",
        "npu_top",
        "debugger",
        "diagnostics_status",
        "diagnostics_bundle",
    } <= tool_names

    all_public = remote_names | tool_names
    for forbidden in ("knowledge_query", "knowledge_explain", "knowledge_feedback", "knowledge_distill", "knowledge_sync"):
        assert forbidden not in all_public


def test_private_knowledge_cli_requires_manual_session_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("AGY_CONVERSATION_ID", SESSION_A)

    with pytest.raises(knowledge_cli.KnowledgeInactive):
        knowledge_cli.invoke("describe")

    bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent enable knowledge",
        }
    )

    desc = knowledge_cli.invoke("describe")
    assert desc["isError"] is False
    names = {t["name"] for t in desc["tools"]}
    assert {"knowledge_query", "knowledge_explain", "knowledge_feedback", "knowledge_distill", "knowledge_sync"} <= names


def test_receipt_honesty_preserves_succeeded_remote_result_on_local_sqlite_error(monkeypatch: pytest.MonkeyPatch):
    import remote_dev.mcp.tools as rd_tools
    monkeypatch.setenv("AGY_CONVERSATION_ID", SESSION_A)

    monkeypatch.setattr(
        rd_tools,
        "call_tool",
        lambda name, args, profile="canonical": {"text": "fixture", "result": {"outcome": "success", "status": "ok"}},
    )

    class BrokenFinishReceipts(mcp_server.RemoteReceipts):
        def finish(self, call_id: str, status: str) -> None:
            raise sqlite3.OperationalError("database disk image is malformed on finish")

    res = mcp_server.call_remote_surface(
        "remote_probe",
        {},
        receipts=BrokenFinishReceipts(),
    )
    assert res["isError"] is False
    assert res["structuredContent"]["result"]["outcome"] == "success"
    assert res["structuredContent"]["receipt_unconfirmed"] is True
    assert "receipt_warning" in res["structuredContent"]


def test_remote_timeout_arithmetic_enforces_outer_greater_than_inner_plus_grace():
    # remote_bash has a 600,000 ms (600s) native wait; with 60s grace, outer budget must be 660s
    assert mcp_server.remote_timeout("remote_bash", {}) == 660.0
    assert mcp_server.remote_timeout("remote_run_command", {"WaitMsBeforeAsync": 10000}) == 660.0
    assert mcp_server.remote_timeout("remote_read", {"timeout_ms": 200000}) == 260.0


def test_knowledge_cli_explore_budget_and_rsi_wrappers(tmp_path: Path):
    import experiment_ledger
    import rsi_loop

    q_tool = next(t for t in knowledge_cli.KNOWLEDGE_TOOL_SCHEMAS if t["name"] == "knowledge_query")
    e_tool = next(t for t in knowledge_cli.KNOWLEDGE_TOOL_SCHEMAS if t["name"] == "knowledge_explain")
    assert q_tool["inputSchema"]["properties"]["token_budget"]["maximum"] == 64000
    assert e_tool["inputSchema"]["properties"]["token_budget"]["maximum"] == 64000

    ledger_path = tmp_path / "ledger.jsonl"
    assert experiment_ledger.main(["render", "--ledger", str(ledger_path), "--task-id", "test-rsi"]) == 0
    assert (
        rsi_loop.main(
            [
                "init",
                "--state",
                str(tmp_path / "state.json"),
                "--task-id",
                "test-rsi",
                "--ledger",
                str(ledger_path),
                "--baseline-repo",
                str(Path(__file__).resolve().parents[1]),
                "--direction",
                "minimize",
                "--repetitions",
                "3",
            ]
        )
        == 0
    )


def test_mcp_parse_error_returns_jsonrpc_minus_32700_and_keeps_serving(monkeypatch):
    class FakeIn:
        def __iter__(self):
            return iter(
                [
                    b"{not-json\n",
                    b"\n",
                    b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n',
                ]
            )

    written: list[bytes] = []

    class FakeOut:
        def write(self, data):
            written.append(data)
            return len(data)

        def flush(self):
            return None

    monkeypatch.setattr(mcp_server.sys, "stdin", type("Stdin", (), {"buffer": FakeIn()})())
    monkeypatch.setattr(mcp_server.sys, "stdout", type("Stdout", (), {"buffer": FakeOut()})())
    assert mcp_server.serve("tools") == 0
    frames = [json.loads(chunk) for chunk in written]
    assert frames[0]["jsonrpc"] == "2.0"
    assert frames[0]["id"] is None
    assert frames[0]["error"]["code"] == -32700
    assert "Parse error" in frames[0]["error"]["message"]
    assert frames[1]["id"] == 7
    assert frames[1]["result"] == {}


def test_mcp_non_object_frame_returns_invalid_request_and_keeps_serving(monkeypatch):
    class FakeIn:
        def __iter__(self):
            return iter(
                [
                    b"[1,2]\n",
                    b'"not-an-object"\n',
                    b'{"jsonrpc":"2.0","id":8,"method":"ping"}\n',
                ]
            )

    written: list[bytes] = []

    class FakeOut:
        def write(self, data):
            written.append(data)
            return len(data)

        def flush(self):
            return None

    monkeypatch.setattr(mcp_server.sys, "stdin", type("Stdin", (), {"buffer": FakeIn()})())
    monkeypatch.setattr(mcp_server.sys, "stdout", type("Stdout", (), {"buffer": FakeOut()})())
    assert mcp_server.serve("tools") == 0
    frames = [json.loads(chunk) for chunk in written]
    assert frames[0]["error"]["code"] == -32600
    assert "JSON object" in frames[0]["error"]["message"]
    assert frames[1]["error"]["code"] == -32600
    assert frames[2]["id"] == 8
    assert frames[2]["result"] == {}


def test_mcp_tools_call_survives_updater_spawn_failure(monkeypatch):
    monkeypatch.delenv("MINDIE_CONFIG_HOME", raising=False)
    monkeypatch.setenv("AGY_CONVERSATION_ID", SESSION_A)
    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    cfg.write_text(json.dumps({"python": sys.executable}), encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("cannot spawn updater")

    monkeypatch.setattr(subprocess, "Popen", boom)
    resp = mcp_server.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "mindie_status", "arguments": {}},
        },
        "tools",
    )
    assert resp["id"] == 9
    assert "error" not in resp
    facts = resp["result"]["structuredContent"]
    assert facts["harness"] == "antigravity"
    assert "repair_required" in facts

