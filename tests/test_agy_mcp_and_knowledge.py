"""Tier 0 & Tier 1 tests for MCP discovery separation, private `knowledge_cli`, and Receipt Honesty."""

from __future__ import annotations

import sqlite3
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
    assert {"mindie_status", "profiler", "sim", "coordinator", "diagnostics_status", "diagnostics_bundle"} <= tool_names

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

    monkeypatch.setattr(
        rd_tools,
        "call_tool",
        lambda name, args, profile="canonical": {"ok": True, "endpoints": [{"alias": "a3-node"}]},
    )

    class BrokenFinishReceipts(mcp_server.RemoteReceipts):
        def finish(self, call_id: str, status: str) -> None:
            raise sqlite3.OperationalError("database disk image is malformed on finish")

    res = mcp_server.call_remote_surface(
        "remote_endpoints",
        {},
        receipts=BrokenFinishReceipts(),
    )
    assert res["isError"] is False
    assert res["structuredContent"]["ok"] is True
    assert res["structuredContent"]["receipt_unconfirmed"] is True
    assert "receipt_warning" in res["structuredContent"]


def test_remote_timeout_arithmetic_enforces_outer_greater_than_inner_plus_grace():
    # remote_bash has a 600,000 ms (600s) native wait; with 60s grace, outer budget must be 660s
    assert mcp_server.remote_timeout("remote_bash", {}) == 660.0
    assert mcp_server.remote_timeout("remote_run_command", {"WaitMsBeforeAsync": 10000}) == 660.0
    assert mcp_server.remote_timeout("remote_read", {"timeout_ms": 200000}) == 260.0
