"""Tier 0 & Tier 1 tests for unconditionally default-enabled `mindie-tools` knowledge MCP and Receipt Honesty."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest

import admission
import bridge
import knowledge_cli
import mcp_server
import paths


SESSION_A = "454c6ee9-afea-47be-8188-abb9c7fb1f26"
SESSION_B = "ec33ebf9-0cba-4100-8142-c61503f6c587"


def test_mcp_tools_exposes_default_enabled_knowledge_and_remote_catalogs(tmp_path: Path):
    remote_resp = mcp_server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "remote")
    tools_resp = mcp_server.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "tools")

    remote_tools = remote_resp["result"]["tools"]
    tools_list = tools_resp["result"]["tools"]
    remote_names = {t["name"] for t in remote_tools}
    by_name = {t["name"]: t for t in tools_list}
    tool_names = set(by_name)

    assert "remote_read" in remote_names
    assert "remote_bash" in remote_names
    assert not any(name.startswith("knowledge_") for name in remote_names)

    expected_knowledge = {
        "knowledge_query",
        "knowledge_explain",
        "knowledge_feedback",
        "knowledge_distill",
        "knowledge_sync",
    }
    assert {
        "mindie_status",
        "profiler",
        "sim",
        "coordinator",
        "npu_top",
        "debugger",
        "diagnostics_status",
        "diagnostics_bundle",
    } | expected_knowledge <= tool_names

    for kname in expected_knowledge:
        ann = by_name[kname]["annotations"]
        assert ann["readOnlyHint"] is (kname in {"knowledge_query", "knowledge_explain"})
        assert ann["destructiveHint"] is False
        assert ann["openWorldHint"] is (kname == "knowledge_sync")


def test_default_enabled_knowledge_out_of_the_box_and_session_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("AGY_CONVERSATION_ID", raising=False)

    # 1. Read-only describe works out-of-the-box without creating admission.sqlite3
    desc = knowledge_cli.invoke("describe")
    assert desc["isError"] is False
    assert not paths.admission_path().exists()
    assert {t["name"] for t in desc["tools"]} == {
        "knowledge_query",
        "knowledge_explain",
        "knowledge_feedback",
        "knowledge_distill",
        "knowledge_sync",
    }

    # 2. MCP call_tools_surface("knowledge_query", ...) works out-of-the-box with zero pre-binding
    import mindie_knowledge.loop.cli as cli_mod
    import mindie_knowledge.loop.transport as tr_mod

    seen_rpc: list[tuple[str, dict]] = []
    monkeypatch.setattr(cli_mod, "ensure_service", lambda cfg_path: {"config_path": cfg_path})

    def fake_rpc(_conn, method, payload, timeout=None):
        seen_rpc.append((method, dict(payload)))
        return {"cards": [{"id": "u-1", "title": "A3 HCCL dual-Die rule"}], "total": 1}

    monkeypatch.setattr(tr_mod, "rpc", fake_rpc)

    zero_bind_res = mcp_server.call_tools_surface(
        "knowledge_query", {"query": "A3 dual-Die HCCL"}, rpc_id=101
    )
    assert zero_bind_res["isError"] is False
    assert zero_bind_res["structuredContent"]["total"] == 1
    assert seen_rpc[-1][0] == "query"
    assert seen_rpc[-1][1]["_session_verified"] is True

    # 3. Receipt Honesty on knowledge_* if local RemoteReceipts.finish fails after primary success
    class BrokenFinishReceipts(mcp_server.RemoteReceipts):
        def finish(self, call_id: str, status: str) -> None:
            raise sqlite3.OperationalError("receipt sqlite locked on finish")

    honest_res = mcp_server.call_tools_surface(
        "knowledge_query",
        {"query": "aclgraph replay"},
        rpc_id=102,
        receipts=BrokenFinishReceipts(),
    )
    assert honest_res["isError"] is False
    assert honest_res["structuredContent"]["receipt_unconfirmed"] is True
    assert "receipt_warning" in honest_res["structuredContent"]

    # 4. Per-conversation leases stay isolated across SESSION_A and SESSION_B with not_before=0.0
    monkeypatch.setenv("AGY_CONVERSATION_ID", SESSION_A)
    res_a = mcp_server.call_tools_surface("knowledge_query", {"query": "session-a"}, rpc_id=103)
    assert res_a["isError"] is False
    lease_a = admission.knowledge_lease(SESSION_A)
    assert lease_a is not None and lease_a["activated_at"] == 0.0
    assert admission.knowledge_lease(SESSION_B) is None

    monkeypatch.setenv("AGY_CONVERSATION_ID", SESSION_B)
    res_b = mcp_server.call_tools_surface("knowledge_query", {"query": "session-b"}, rpc_id=104)
    assert res_b["isError"] is False
    lease_b = admission.knowledge_lease(SESSION_B)
    assert lease_b is not None and lease_b["activated_at"] == 0.0
    assert lease_a["token"] != lease_b["token"]


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

