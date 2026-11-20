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


def _ctx(tool: str, *, cwd: Path | None = None):
    from mindie_harness.mcp.router import RequestContext
    from mindie_harness.process.supervisor import Deadline

    return RequestContext(
        request_id=1, surface="tools", tool_name=tool, session_id="agy-conn-test",
        cwd=cwd or Path.cwd(), env=dict(os.environ), deadline=Deadline.from_timeout(10.0), cancel=threading.Event(),
    )


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
    import setup
    from mindie_knowledge.loop.store import Store

    assert setup.main(["init", "--domain", "vllm-ascend"]) == 0
    seen_configs: list[str] = []
    store_default = Store(tmp_path / "knowledge", "vllm-ascend")
    store_ops = Store(tmp_path / "knowledge", "operators")

    def fake_ensure_service(path):
        seen_configs.append(str(path))
        return {"url": "http://127.0.0.1:1", "token": "fixture", "config_path": str(path)}

    def fake_rpc(conn, method, payload, timeout=None):
        assert method == "query"
        assert payload.get("_session_verified") is True
        assert "_activation" not in payload
        assert "domain" not in payload
        selected = store_ops if str(conn.get("config_path") or "").endswith("operators.engine.json") else store_default
        return selected.query(**{key: value for key, value in payload.items() if not key.startswith("_")})

    monkeypatch.setattr(cli, "ensure_service", fake_ensure_service)
    monkeypatch.setattr(transport, "rpc", fake_rpc)
    adapter = mcp_server.AGYManagedAdapter(profile=profile)
    result = adapter.dispatch_tool(_ctx("knowledge_query"), {"query": "A3 dual-Die"}, profile)
    ops_result = adapter.dispatch_tool(_ctx("knowledge_query"), {"query": "AscendC tiling", "domain": "operators"}, profile)
    store_default.close()
    store_ops.close()

    assert result["isError"] is False
    assert ops_result["isError"] is False
    assert len(seen_configs) == 2
    assert seen_configs[0].endswith("vllm-ascend.engine.json")
    assert seen_configs[1].endswith("operators.engine.json")
    structured = result["structuredContent"]
    assert "cards" in structured or "results" in structured or "total" in structured
    assert "_meta" not in json.dumps(result)


def test_receipt_honesty_preserves_succeeded_knowledge_result(profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mindie_harness.sharing import configure_sharing
    import mindie_knowledge.loop.activation as activation
    import mindie_knowledge.loop.cli as cli
    import mindie_knowledge.loop.transport as transport
    import mcp_server
    import setup

    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert setup.main(["init", "--domain", "vllm-ascend"]) == 0
    configure_sharing(profile, choice="local-only", project_roots=[str(workspace)])
    monkeypatch.setattr(cli, "ensure_service", lambda path: {"url": "http://127.0.0.1:1", "token": "fixture"})
    monkeypatch.setattr(transport, "rpc", lambda *args, **kwargs: {"status": "applied", "admitted_unit_ids": ["u-1"]})

    class BrokenAdmission:
        def __init__(self, path):
            self.path = path

        def inspect(self, session, **kwargs):
            return {"status": "active", "enabled": True}

        def active_lease(self, session, **kwargs):
            return {"token": "t", "enabled": 1, "entry_enabled": 1, "project_root": str(workspace), "activated_at": 0.0}

        def finish(self, *args, **kwargs):
            raise OSError("simulated local receipt failure")

    monkeypatch.setattr(activation, "Admission", BrokenAdmission)
    adapter = mcp_server.AGYManagedAdapter(profile=profile)
    result = adapter.dispatch_tool(
        _ctx("knowledge_distill", cwd=workspace),
        {"inputs": [{"kind": "experiment_ledger", "outcome": "verified", "source_locator": "exp.json", "content": "verified run", "evidence_refs": ["exp.json"]}], "task_state": "completed"},
        profile,
    )
    assert result["isError"] is False
    assert result["receipt_unconfirmed"] is True
    assert "OSError" in result["receipt_warning"]
    assert result["structuredContent"]["receipt_unconfirmed"] is True


def test_setup_init_provisions_nine_domains_and_preserves_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mindie_harness.atomic import atomic_write_json
    from mindie_knowledge.loop.domain_catalog import DOMAINS
    import mindie_harness.runtime.knowledge_binding as kb
    import mindie_knowledge.loop.transcript_redaction as redaction
    import setup

    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    monkeypatch.delenv("MINDIE_REDACTOR_EXECUTABLE", raising=False)
    monkeypatch.setattr(
        redaction,
        "install_scanner",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("offline init must never call install_scanner")),
    )
    gen_dir = tmp_path / "generations" / "gen-existing"
    gen_dir.mkdir(parents=True)
    atomic_write_json(gen_dir / "runtime.json", {"validation": {"status": "validated"}, "python": sys.executable})
    atomic_write_json(
        cfg,
        {
            "component_release": str(gen_dir),
            "product_validation": {
                "schema": "mindie-candidate-check/1",
                "status": "validated",
                "generation": "gen-existing",
                "wheelhouse_sha": "abc123",
            },
        },
    )
    assert setup.main(["init", "--domain", "operators"]) == 0
    adapter = json.loads(cfg.read_text(encoding="utf-8"))
    assert adapter["component_release"] == str(gen_dir)
    assert adapter["product_validation"]["generation"] == "gen-existing"
    assert adapter["product_validation"]["wheelhouse_sha"] == "abc123"
    assert adapter["product_validation"]["native_version"] == "0.1.22"
    assert adapter["domain"] == "operators"
    assert adapter["engine_config"].endswith("vllm-ascend.engine.json")
    assert set(adapter["domain_configs"]) == set(DOMAINS)
    assert len(adapter["domain_configs"]) == 9
    for domain, engine_path in adapter["domain_configs"].items():
        doc = json.loads(Path(engine_path).read_text(encoding="utf-8"))
        assert doc["domain"] == domain
        assert doc["transcript_adapter"].endswith("agy_transcript.py")

    # Verify fail-closed when ensure_knowledge_binding returns skipped/deferred
    monkeypatch.setattr(kb, "ensure_knowledge_binding", lambda *args, **kwargs: {"status": "deferred", "reason": "knowledge_service_busy"})
    assert setup.main(["init"]) == 1
