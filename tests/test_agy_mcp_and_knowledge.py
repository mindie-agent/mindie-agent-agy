"""AGY MCP catalogs, zero-`_meta` knowledge dispatch, and Receipt Honesty."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "mindie-agent"
KNOWLEDGE = {"knowledge_query", "knowledge_explain", "knowledge_feedback", "knowledge_distill", "knowledge_sync"}
REMOTE = {
    "remote_read_file", "remote_write_file", "remote_edit_file", "remote_run_command",
    "remote_manage_task", "remote_find_by_name", "remote_grep_search", "remote_list_dir",
    "remote_auth", "remote_probe", "remote_artifact_pull", "remote_artifact_push",
}


@pytest.fixture()
def profile():
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.paths import ProfileRef

    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    atomic_write_json(cfg, {"python": sys.executable, "engine_config": str(cfg.with_name("agy.engine.json"))})
    return ProfileRef.resolve("agy", plugin_root=PLUGIN, config_path=str(cfg), data_dir=os.environ.get("MINDIE_DATA_HOME"))


def _ctx(tool: str):
    from mindie_harness.mcp.router import RequestContext
    from mindie_harness.process.supervisor import Deadline

    return RequestContext(
        request_id=1, surface="tools", tool_name=tool, session_id="agy-conn-test",
        cwd=Path.cwd(), env=dict(os.environ), deadline=Deadline.from_timeout(10.0), cancel=threading.Event(),
    )


def _force_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import mindie_harness.mcp.gateway as gateway

    def unavailable(*args, **kwargs):
        return {"content": [{"type": "text", "text": "unavailable"}], "isError": True,
                "structuredContent": {"reason": "knowledge_unavailable", "retryable": False}}

    monkeypatch.setattr(gateway, "invoke_knowledge_tool", unavailable)


def test_agy_remote_and_tools_catalogs_require_zero_meta(profile) -> None:
    import mcp_server

    adapter = mcp_server.AGYManagedAdapter(profile=profile)
    remote = adapter.mcp_tools("remote", profile)
    tools = adapter.mcp_tools("tools", profile)
    assert {tool["name"] for tool in remote} == REMOTE
    assert len(remote) == 12
    names = {tool["name"] for tool in tools}
    assert KNOWLEDGE <= names
    assert {"mindie_status", "mindie_doctor"} <= names
    assert os.environ["REMOTE_DEV_HARNESS_PROFILE"] == "agy"
    assert os.environ["REMOTE_DEV_STATE_DIR"].endswith(os.path.join("state", "agy", "remote"))
    result = adapter.dispatch_tool(_ctx("mindie_status"), {}, profile)
    assert result["isError"] is False
    assert "_meta" not in json.dumps(result)


def test_zero_meta_knowledge_query_against_initialized_domain(profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mindie_knowledge.loop.cli as cli
    import mindie_knowledge.loop.transport as transport
    import mcp_server
    from mindie_knowledge.loop.store import Store

    store = Store(tmp_path / "knowledge", "vllm-ascend")
    _force_unavailable(monkeypatch)
    monkeypatch.setattr(cli, "ensure_service", lambda path: {"url": "http://127.0.0.1:1", "token": "fixture"})

    def fake_rpc(conn, method, payload, timeout=None):
        assert method == "query"
        return store.query(**{key: value for key, value in payload.items() if not key.startswith("_")})

    monkeypatch.setattr(transport, "rpc", fake_rpc)
    adapter = mcp_server.AGYManagedAdapter(profile=profile)
    result = adapter.dispatch_tool(_ctx("knowledge_query"), {"query": "A3 dual-Die"}, profile)
    store.close()
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert "cards" in structured or "results" in structured or "total" in structured
    assert "_meta" not in json.dumps(result)


def test_receipt_honesty_preserves_succeeded_knowledge_result(profile, monkeypatch: pytest.MonkeyPatch) -> None:
    import mindie_knowledge.loop.activation as activation
    import mindie_knowledge.loop.cli as cli
    import mindie_knowledge.loop.transport as transport
    import mcp_server

    _force_unavailable(monkeypatch)
    monkeypatch.setattr(cli, "ensure_service", lambda path: {"url": "http://127.0.0.1:1", "token": "fixture"})
    monkeypatch.setattr(transport, "rpc", lambda *args, **kwargs: {"cards": [{"id": "u-1"}], "total": 1})

    class BrokenAdmission:
        def __init__(self, path):
            self.path = path

        def active_lease(self, session):
            return {"token": "t"}

        def finish(self, session, token, succeeded):
            raise OSError("simulated local receipt failure")

    monkeypatch.setattr(activation, "Admission", BrokenAdmission)
    adapter = mcp_server.AGYManagedAdapter(profile=profile)
    result = adapter.dispatch_tool(_ctx("knowledge_query"), {"query": "anything"}, profile)
    assert result["isError"] is False
    assert result["receipt_unconfirmed"] is True
    assert "OSError" in result["receipt_warning"]
    assert result["structuredContent"]["receipt_unconfirmed"] is True
