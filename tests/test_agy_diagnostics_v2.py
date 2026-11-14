"""Tier 1 tests for Antigravity (`mindie-agent-agy`) Diagnostics 2.0 & Receipt Honesty (F1/F2/F4/F7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mindie_diagnostics.ingestion import ingest
from mindie_diagnostics.integration import configure_reporting, record_failure
from mindie_diagnostics.outbox import Outbox
import bridge
import diagnostic_support
import mcp_server

SESSION_A = "454c6ee9-afea-47be-8188-abb9c7fb1f26"


def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    base = tmp_path.resolve()
    diag_root = base / "diag"
    cfg_dir = base / "config" / "mindie-agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    agy_cfg = cfg_dir / "agy.json"
    agy_cfg.write_text(json.dumps({"host": "antigravity"}), encoding="utf-8")
    diag_cfg = cfg_dir / "diagnostics.json"
    monkeypatch.setenv("HOME", str(base / "home"))
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_ROOT", str(diag_root))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_CONFIG", str(diag_cfg))
    monkeypatch.setenv("AGY_CONVERSATION_ID", SESSION_A)
    (base / "home").mkdir(parents=True, exist_ok=True)
    diag_root.mkdir(parents=True, exist_ok=True)
    ws = base / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(ws)],
            "lastUserInput": "/mindie-agent activate session",
        },
        config=agy_cfg,
    )
    configure_reporting(True, roots=[str(diag_root)], config=diag_cfg)
    return diag_root, diag_cfg, agy_cfg


def test_agy_mcp_diagnostics_provenance_and_post_success_timeout_honesty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diag_root, diag_cfg, agy_cfg = _isolate_env(tmp_path, monkeypatch)
    queue = Outbox(tmp_path.resolve() / "queue.db")

    meta = diagnostic_support.build_metadata()
    assert meta["version"]
    assert meta["revision"] is None or len(meta["revision"]) == 40

    unknown_res = mcp_server.call_tools_surface("nonexistent_tool", {}, config=agy_cfg)
    assert unknown_res["isError"] is True
    assert ingest(diag_root, queue, config=diag_cfg)["enqueued"] == 0

    monkeypatch.setattr(
        mcp_server,
        "status_facts",
        lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("operational_permission_denied")),
    )
    perm_res = mcp_server.call_tools_surface("mindie_status", {}, config=agy_cfg)
    assert perm_res["isError"] is True
    assert "diagnostic" not in perm_res["structuredContent"]
    assert ingest(diag_root, queue, config=diag_cfg)["enqueued"] == 0

    monkeypatch.setattr(
        mcp_server,
        "status_facts",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("internal_agy_secret_detail")),
    )
    crash_res = mcp_server.call_tools_surface("mindie_status", {}, config=agy_cfg)
    assert crash_res["isError"] is True
    assert crash_res["structuredContent"]["diagnostic"]["incident_id"]

    assert ingest(diag_root, queue, config=diag_cfg)["enqueued"] == 1
    ev = queue.claim()["payload"]["events"][-1]
    assert ev["component"] == "mindie-agent-agy"
    assert ev["attributes"]["harness"] == "antigravity"
    assert "internal_agy_secret_detail" not in json.dumps(ev)

    # F2 & F1: Reject status="success" / unassociated markers; accept provenance-bound failure receipt!
    tok = "4" * 32
    ev_dir = diag_root / "events" / "mindie-coordinator"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "events.jsonl").write_text(
        json.dumps({"component": "mindie-coordinator", "event": "operation.end", "status": "success", "reportable": False, "operation_id": "b" * 32, "invocation_token": tok}) + "\n",
        encoding="utf-8",
    )
    assert diagnostic_support.record_component_crash(
        "coordinator", ["hosts"], 1, f"mindie-diagnostic-incident: {'b' * 32}\n", config=agy_cfg, invocation_token=tok
    ) is None
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_INVOCATION_TOKEN", tok)
    rec = record_failure("mindie-coordinator", "mindie.hosts", stage="cli_dispatch", category="internal_exception", exception=KeyError("k"))
    assert diagnostic_support.record_component_crash(
        "coordinator", ["hosts"], 1, f"mindie-diagnostic-incident: {rec['incident_id']}\n", config=agy_cfg, invocation_token=tok
    ) == {"incident_id": rec["incident_id"], "logging_failed": False}

    # F4: Post-success TimeoutError in receipt_store.finish MUST preserve completed result with receipt_unconfirmed=True!
    import remote_dev.mcp.outcome as rd_outcome
    import remote_dev.mcp.tools as rd_tools

    class _TimeoutFinishReceipts:
        def begin(self, *_a, **_k):
            return None

        def finish(self, *_a, **_k):
            raise TimeoutError("sqlite_finish_timeout")

    monkeypatch.setattr(rd_tools, "call_tool", lambda *_a, **_k: {"status": "succeeded", "stdout": "done"})
    monkeypatch.setattr(rd_outcome, "operation_outcome", lambda *_a, **_k: "succeeded")
    rem_ok = mcp_server.call_remote_surface("remote_bash", {"command": "true"}, config=agy_cfg, receipts=_TimeoutFinishReceipts())
    assert rem_ok["isError"] is False
    assert rem_ok["structuredContent"]["receipt_unconfirmed"] is True
    assert "TimeoutError" in rem_ok["structuredContent"]["receipt_warning"]

    # Pre-success TimeoutError in receipt_store.finish MUST still propagate!
    monkeypatch.setattr(rd_outcome, "operation_outcome", lambda *_a, **_k: "failed")
    with pytest.raises(TimeoutError):
        mcp_server.call_remote_surface("remote_bash", {"command": "false"}, config=agy_cfg, receipts=_TimeoutFinishReceipts())

    # R5-4: Post-update feed-sync TimeoutError preserves status="updated" AND sets receipt_warning + receipt_unconfirmed=True!
    import component_update
    import mindie_knowledge.loop.cli as mk_cli
    from paths import community_config_path, engine_config_path

    community_config_path(agy_cfg).write_text("{}", encoding="utf-8")
    engine_config_path(agy_cfg).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        component_update,
        "prepare",
        lambda *_a, **_k: {
            "python": "/tmp/fake-mindie-py",
            "components": {"runtime": "/tmp/fake-rt"},
            "installed": {"mindie-diagnostics": {"version": "0.5.11"}},
        },
    )
    monkeypatch.setattr(
        mk_cli, "sync_feeds", lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("feed_sync_deadline")), raising=False
    )
    upd = component_update.check(config=agy_cfg, sync_feeds=True)
    assert upd["status"] == "updated"
    assert upd["receipt_unconfirmed"] is True
    assert "TimeoutError" in upd["receipt_warning"]
    assert "TimeoutError" in upd["feed_sync_error"]

    # R6-1: Post-promotion Path.stat() TimeoutError / PermissionError on eng_file or community_config_path
    # must preserve status="updated", attach receipt_warning + receipt_unconfirmed=True, and persist status_file.
    orig_stat = Path.stat
    orig_atomic = component_update.atomic_write_json
    comm_p = community_config_path(agy_cfg)
    eng_p = engine_config_path(agy_cfg)
    for target_p in (eng_p, comm_p):
        for exc_cls in (TimeoutError, PermissionError):
            with monkeypatch.context() as mp:
                promoted_flag = {"done": False}

                def _tracking_write(path, payload, *, _orig=orig_atomic, _flag=promoted_flag):
                    _orig(path, payload)
                    if Path(path) == agy_cfg:
                        _flag["done"] = True

                def _faulty_stat(self, *args, _t=target_p, _e=exc_cls, _flag=promoted_flag, **kwargs):
                    if _flag["done"] and self == _t:
                        raise _e("post_promote_stat_fault")
                    return orig_stat(self, *args, **kwargs)

                mp.setattr(component_update, "atomic_write_json", _tracking_write)
                mp.setattr(Path, "stat", _faulty_stat)
                res_r6 = component_update.check(config=agy_cfg, sync_feeds=True)
                assert res_r6["status"] == "updated"
                assert res_r6["receipt_unconfirmed"] is True
                assert exc_cls.__name__ in res_r6["receipt_warning"]
                saved_status = json.loads((tmp_path.resolve() / "data" / "component-update-status.json").read_text(encoding="utf-8"))
                assert saved_status["status"] == "updated"
                assert saved_status["receipt_unconfirmed"] is True
