"""Clean-architecture contract tests for `mindie-agent-agy` (Group G7)."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import threading
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "mindie-agent"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
FROZEN_HOOKS_SHA256 = "33b564c78b439205fd4acfe095c83b15770043ff462d636e2a89f14620cdfc3f"
SESSION_ID = "454c6ee9-afea-47be-8188-abb9c7fb1f26"


def test_hooks_json_frozen_byte_for_byte_contract():
    hooks_path = PLUGIN_ROOT / "hooks.json"
    raw = hooks_path.read_bytes()
    assert len(raw) == 979, f"hooks.json must remain 979 bytes, got {len(raw)}"
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == FROZEN_HOOKS_SHA256, f"hooks.json SHA-256 drifted: {digest}"


def test_passive_stop_hook_returns_empty_json_under_100ms_without_reexec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import mindie_launch
    import paths

    # Warm one cold Python startup measurement, then measure actual passive Stop subprocess latency
    launch_script = SCRIPTS_DIR / "mindie_launch.py"
    payload = json.dumps({"conversationId": SESSION_ID, "workspacePaths": [str(tmp_path)]})
    env = dict(os.environ)
    subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(launch_script), "hook", "stop"],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=5,
        check=True,
    )
    t0 = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(launch_script), "hook", "stop"],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=5,
        check=True,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert json.loads(completed.stdout) == {}
    assert elapsed_ms < 100.0, f"Passive Stop took {elapsed_ms:.1f}ms (>= 100ms budget)"
    assert not paths.admission_path().exists()

    # Malformed stdin JSON must still raise rather than silently emitting {}
    monkeypatch.setattr(sys, "stdin", type("BadIn", (), {"buffer": io.BytesIO(b"{not-json")})())
    with pytest.raises(ValueError):
        mindie_launch.main(["hook", "stop"])


def test_seed_bundle_verification_and_binary_staging(tmp_path: Path):
    import host_adapter
    import setup

    lock = host_adapter.verify_seed_bundle(PLUGIN_ROOT)
    assert lock["schema"] == "mindie-harness-seed/1"
    assert lock["version"] == "0.1.0"
    pyz_path, lock_path = host_adapter.seed_bundle_paths(PLUGIN_ROOT)
    orig_bytes = pyz_path.read_bytes()
    assert len(orig_bytes) == lock["size_bytes"]

    # Stage baseline and verify binary .pyz survives byte-for-byte without CRLF mangling
    staged = setup.stage_source_baseline(REPO_ROOT, tmp_path / "staged")
    staged_pyz = staged / "plugins" / "mindie-agent" / "assets" / "mindie-harness-seed.pyz"
    assert staged_pyz.read_bytes() == orig_bytes
    assert host_adapter.verify_seed_bundle(staged / "plugins" / "mindie-agent") == lock

    # Corrupting one byte in staged .pyz must fail verification
    tampered = bytearray(orig_bytes)
    tampered[-1] ^= 0xFF
    staged_pyz.write_bytes(bytes(tampered))
    with pytest.raises(ValueError, match="Seed integrity mismatch"):
        host_adapter.verify_seed_bundle(staged / "plugins" / "mindie-agent")


def test_host_adapter_and_harness_rsi_delegation(tmp_path: Path):
    from mindie_harness.api import HostAdapter
    import experiment_ledger
    import host_adapter
    import rsi_loop

    adapter = host_adapter.AGYHostAdapter()
    assert isinstance(adapter, HostAdapter)
    profile = adapter.resolve_profile()
    assert profile.host == "agy"
    assert adapter.extract_session_identity({}, {"AGY_CONVERSATION_ID": SESSION_ID}) == SESSION_ID
    assert adapter.extract_session_identity({"params": {"arguments": {"conversationId": SESSION_ID}}}, {}) is None
    tool_names = {t["name"] for t in adapter.mcp_tools("tools", profile)}
    assert {
        "mindie_status",
        "knowledge_query",
        "knowledge_explain",
        "knowledge_feedback",
        "knowledge_distill",
        "knowledge_sync",
    } <= tool_names

    assert rsi_loop.__name__.startswith("mindie_harness.rsi")
    assert experiment_ledger.__name__.startswith("mindie_harness.rsi")


def test_concurrent_mcp_stdio_dual_framing_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import host_adapter
    import mcp_server

    started = threading.Event()
    release = threading.Event()
    orig_tools = mcp_server.call_tools_surface

    def slow_or_fast(name: str, arguments=None, **kwargs):
        if name == "profiler":
            started.set()
            cancel_ev = kwargs.get("cancel")
            deadline = time.monotonic() + 3.0
            while not release.is_set() and time.monotonic() < deadline:
                if cancel_ev is not None and cancel_ev.is_set():
                    return {"content": [{"type": "text", "text": "cancelled"}], "structuredContent": {"cancelled": True}, "isError": True}
                time.sleep(0.01)
            return {"content": [{"type": "text", "text": "done"}], "structuredContent": {"ok": True}, "isError": False}
        return orig_tools(name, arguments, **kwargs)

    monkeypatch.setattr(mcp_server, "call_tools_surface", slow_or_fast)

    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    server_thread = threading.Thread(
        target=lambda: mcp_server.serve(
            "tools",
            stdin=os.fdopen(r_in, "rb", buffering=0),
            stdout=os.fdopen(w_out, "wb", buffering=0),
        ),
        daemon=True,
    )
    server_thread.start()
    writer = os.fdopen(w_in, "wb", buffering=0)
    reader = os.fdopen(r_out, "rb")
    try:
        # 1. Content-Length framed initialize
        host_adapter.write_wire_frame(writer, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, "content-length")
        init_resp, framing, eof = host_adapter.read_wire_frame(reader)
        assert not eof and framing == "content-length"
        assert init_resp["result"]["capabilities"]["tools"]["listChanged"] is True

        # 2. Slow mutation call (id=2) on mut_pool
        host_adapter.write_wire_frame(
            writer,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "profiler", "arguments": {"argv": ["--version"]}}},
            "jsonl",
        )
        assert started.wait(timeout=2.0)

        # 3. Fast-lane mindie_status (id=3) + ping (id=4) MUST complete BEFORE id=2 finishes (AD-03 fixed!)
        host_adapter.write_wire_frame(
            writer,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mindie_status", "arguments": {}}},
            "jsonl",
        )
        host_adapter.write_wire_frame(writer, {"jsonrpc": "2.0", "id": 4, "method": "ping"}, "jsonl")

        early = {}
        while len(early) < 2:
            msg, _, eof = host_adapter.read_wire_frame(reader)
            assert not eof
            early[msg["id"]] = msg
        assert set(early) == {3, 4}
        assert early[3]["result"]["isError"] is False
        assert early[4]["result"] == {}

        # 4. Cancel in-flight id=2 via notifications/cancelled
        host_adapter.write_wire_frame(
            writer,
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}},
            "jsonl",
        )
        cancelled_msg, _, eof = host_adapter.read_wire_frame(reader)
        assert not eof and cancelled_msg["id"] == 2
        assert cancelled_msg["result"]["structuredContent"]["cancelled"] is True
    finally:
        release.set()
        writer.close()
        server_thread.join(timeout=3.0)
        reader.close()


def test_symlinked_metadata_and_diagnostics_supported_ad07(tmp_path: Path):
    import diagnostic_support

    real_dir = tmp_path / "real-pkg"
    real_dir.mkdir()
    real_meta = real_dir / "METADATA"
    real_meta.write_text("Metadata-Version: 2.1\nName: mindie-profiler\nVersion: 0.5.6\n", encoding="utf-8")

    site_dir = tmp_path / "site-packages"
    dist_info = site_dir / "mindie_profiler-0.5.6.dist-info"
    dist_info.mkdir(parents=True)
    link_meta = dist_info / "METADATA"
    link_meta.symlink_to(real_meta)

    ver, _ = diagnostic_support._inspect_dist_info(site_dir, "mindie_profiler")
    assert ver == "0.5.6"


def test_skill_and_domain_docs_free_of_benchmark_and_install_spillover_ad10():
    skills_dir = PLUGIN_ROOT / "skills"
    skill_path = skills_dir / "mindie-agent" / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    assert len(skill_text.splitlines()) < 35, "SKILL.md must stay concise (< 35 lines)"

    relocated_install_docs = ("workspace-setup.md", "github-authentication.md", "user-notice.md")
    for doc_name in relocated_install_docs:
        assert not list(skills_dir.rglob(doc_name)), f"Installer doc {doc_name} must not live under skills/"
        assert (REPO_ROOT / "docs" / "install" / doc_name).is_file(), f"Missing docs/install/{doc_name}"

    forbidden_terms = (
        "(out_tensor, metrics_dict)",
        "mindie-bench-ablation/1",
        "experiment_ledger.py",
        "PROGRESS.md",
        "setup.py sharing",
        "vllm-ascend-workspace",
        "github-authentication.md",
        "workspace-setup.md",
        "user-notice.md",
    )
    for md_file in sorted(skills_dir.rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        for forbidden in forbidden_terms:
            assert forbidden not in content, f"Forbidden spillover {forbidden!r} found in {md_file.relative_to(REPO_ROOT)}"


def test_pr_checklist_host_plugin_profile():
    sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
    import check_pr_checklist

    valid_body = (
        "## 变更摘要 (Summary)\n\n"
        "- Refactor AGY host plugin onto mindie-harness v0.1.0 and concurrent stdio MCP.\n\n"
        "## 设计原则 Review (Pre-PR Checklist)\n\n"
        "- [x] **HOST-NATIVE** — Frozen hooks.json SHA-256 verified.\n"
        "- [x] **HOST-WIRE** — Dual framing concurrent MCP verified.\n"
        "- [x] **HOST-ADMISSION** — Zero pre-binding on deterministic tools verified.\n"
        "- [x] **HOST-UPGRADE** — Seed lock and v0.1.16 upgrade verified.\n\n"
        "## 自查证据 (Evidence)\n\n"
        "- **HOST-NATIVE**: Verified 979-byte hooks.json SHA-256 in pytest.\n"
        "- **HOST-WIRE**: Verified concurrent fast-lane and cancellation in test_clean_arch_g7.py.\n"
        "- **HOST-ADMISSION**: Verified zero-binding MCP tools/call and private knowledge gate.\n"
        "- **HOST-UPGRADE**: Verified seed-lock SHA-256 and v0.1.16 upgrade preservation.\n\n"
        "## 验证结果 (Verification)\n\n"
        "- Passed pytest -q and check_lf_eol.py.\n"
    )
    assert check_pr_checklist.validate_pr_body(valid_body, profile="host-plugin") == []
    assert check_pr_checklist.validate_pr_body(valid_body.replace("[x] **HOST-WIRE**", "[ ] **HOST-WIRE**"), profile="host-plugin")


def test_baseline_v0_1_16_profile_upgrade_preserves_consent_and_lease(tmp_path: Path):
    import admission
    import paths
    import setup

    old_tree = tmp_path / "agy-v0.1.16"
    old_tree.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", "v0.1.16"],
        capture_output=True,
        timeout=10,
        check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tf:
        tf.extractall(old_tree, filter="data")

    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = paths.config_path()
    staged_old = setup.stage_source_baseline(old_tree)
    assert json.loads((staged_old / "plugins" / "mindie-agent" / "plugin.json").read_text(encoding="utf-8"))["version"] == "0.1.16"

    # Activate lease + record local-only consent before upgrading to current checkout
    admission.activate(SESSION_ID, ws, config=cfg)
    setup.configure_community(enabled=False, config=cfg)
    consent_before = paths.consent_path(cfg).read_bytes()

    upgraded = setup.ensure_default_profile(config=cfg, workspace_root=ws)
    assert upgraded["product_validation"]["native_version"] == json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert paths.consent_path(cfg).read_bytes() == consent_before
    lease_after = admission.knowledge_lease(SESSION_ID, config=cfg)
    assert lease_after is not None and lease_after["enabled"] == 1


def test_concurrent_remote_sessions_isolate_read_ledger_without_env_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from identity import publish_bind
    import mcp_server
    from remote_dev.core import state_store

    monkeypatch.delenv("REMOTE_DEV_SESSION_ID", raising=False)
    monkeypatch.delenv("AGY_CONVERSATION_ID", raising=False)
    sess_a = "454c6ee9-afea-47be-8188-abb9c7fb1f26"
    sess_b = "554c6ee9-afea-47be-8188-abb9c7fb1f26"
    args_a = {"host": "127.0.0.1", "tag": "a"}
    args_b = {"host": "127.0.0.1", "tag": "b"}
    publish_bind(sess_a, "remote", "remote_probe", args_a)
    publish_bind(sess_b, "remote", "remote_probe", args_b)

    barrier = threading.Barrier(2, timeout=3.0)
    scopes_seen: dict[str, tuple[str, str]] = {}

    def interleaved_tool(name: str, args: dict, profile: str = "canonical"):
        before = state_store.resolve_ledger_scope()
        barrier.wait()
        after = state_store.resolve_ledger_scope()
        scopes_seen[args["tag"]] = (before, after)
        return {"outcome": "success", "status": "ok", "summary": "ok"}

    monkeypatch.setattr("remote_dev.mcp.tools.call_tool", interleaved_tool)
    receipts = mcp_server.RemoteReceipts(tmp_path / "receipts.sqlite3")
    errors: list[Exception] = []

    def worker(rpc_id: int, args: dict):
        try:
            res = mcp_server.call_remote_surface("remote_probe", args, rpc_id=rpc_id, receipts=receipts)
            assert res["isError"] is False
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(101, args_a))
    t2 = threading.Thread(target=worker, args=(102, args_b))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert not errors
    assert "REMOTE_DEV_SESSION_ID" not in os.environ
    expected_a = "id-" + hashlib.sha256(sess_a.encode("utf-8")).hexdigest()
    expected_b = "id-" + hashlib.sha256(sess_b.encode("utf-8")).hexdigest()
    assert scopes_seen["a"] == (expected_a, expected_a)
    assert scopes_seen["b"] == (expected_b, expected_b)


def test_transport_eof_cancels_hung_calls_and_aborts_queued_mutations():
    import host_adapter

    all_eight_started = threading.Event()
    started_ids: list[int] = []
    cancelled_ids: list[int] = []
    lock = threading.Lock()

    def fake_rpc(req: dict, cancel_ev: threading.Event | None):
        if req.get("method") == "initialize":
            return {"jsonrpc": "2.0", "id": req["id"], "result": {}}
        rid = int(req["id"])
        with lock:
            started_ids.append(rid)
            if len(started_ids) == 8:
                all_eight_started.set()
        assert cancel_ev is not None
        if cancel_ev.wait(timeout=3.0):
            with lock:
                cancelled_ids.append(rid)
        return {"jsonrpc": "2.0", "id": rid, "result": {"isError": True}}

    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    srv = threading.Thread(
        target=lambda: host_adapter.serve_concurrent_stdio(
            surface="tools",
            rpc_handler=fake_rpc,
            stdin=os.fdopen(r_in, "rb", buffering=0),
            stdout=os.fdopen(w_out, "wb", buffering=0),
            eof_drain_seconds=0.05,
        ),
        daemon=True,
    )
    srv.start()
    writer = os.fdopen(w_in, "wb", buffering=0)
    reader = os.fdopen(r_out, "rb")
    try:
        for rid in range(1, 10):
            host_adapter.write_wire_frame(
                writer,
                {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": "profiler", "arguments": {"argv": ["--version"]}}},
                "jsonl",
            )
        assert all_eight_started.wait(timeout=2.0)
        # Disconnect client pipe while 8 workers are hung and request #9 is queued behind them
        t0 = time.perf_counter()
        writer.close()
        srv.join(timeout=2.0)
        elapsed = time.perf_counter() - t0
        assert not srv.is_alive(), "serve_concurrent_stdio hung after stdin EOF"
        assert elapsed < 1.5
        assert sorted(cancelled_ids) == list(range(1, 9))
        assert 9 not in started_ids, "Queued 9th mutation must not start after client disconnect"
    finally:
        reader.close()
