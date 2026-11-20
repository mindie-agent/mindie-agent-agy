"""Antigravity hook lifecycle, Stop fast-path, capture, and revocation tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "mindie-agent"
LAUNCHER = PLUGIN / "scripts" / "mindie_launch.py"
SESSION = "454c6ee9-afea-47be-8188-abb9c7fb1f26"


def _profile():
    from mindie_harness.paths import ProfileRef

    return ProfileRef.resolve("agy", plugin_root=PLUGIN, config_path=os.environ.get("MINDIE_AGENT_CONFIG"), data_dir=os.environ.get("MINDIE_DATA_HOME"))


def _run_stop(payload: dict) -> tuple[dict, float]:
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(LAUNCHER), "hook", "stop"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=15,
        cwd=str(ROOT),
        env=dict(os.environ),
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout), elapsed_ms


def _sqlite_files() -> list[Path]:
    return list(Path(os.environ["MINDIE_DATA_HOME"]).rglob("*.sqlite3"))


def _configure(profile, tier: str, root: Path) -> None:
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.sharing import configure_sharing

    engine_path = profile.config_path.with_name("agy.engine.json")
    atomic_write_json(
        engine_path,
        {
            "schema": "mindie-engine-config/1",
            "harness": "agy",
            "domain": "vllm-ascend",
            "root": str(Path(profile.data_dir) / "knowledge"),
            "admission_path": str(profile.admission_db),
            "community_config": str(profile.config_path.parent / "mindie-community.json"),
            "transcript_adapter": str(PLUGIN / "scripts" / "agy_transcript.py"),
            "redactor_executable": str(Path(profile.data_dir) / "bin" / ("gitleaks.exe" if os.name == "nt" else "gitleaks")),
        },
    )
    atomic_write_json(profile.config_path, {"engine_config": str(engine_path)})
    configure_sharing(profile, choice=tier, project_roots=[str(root)])


def _write_transcript(root: Path) -> None:
    logs = root / "brain" / SESSION / ".system_generated" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    record = {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": "2026-10-31T08:25:00Z", "content": "<USER_REQUEST>hello</USER_REQUEST>"}
    (logs / "transcript_full.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_session_start_writes_pending_marker_then_stays_quiet() -> None:
    import bridge

    assert bridge.handle_session_start({"conversationId": SESSION}) == {}
    marker = Path(os.environ["MINDIE_DATA_HOME"]) / "state" / "agy" / "pending-update-check.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["schema"] == "mindie-pending-update/1"
    assert payload["host"] == "agy"
    assert not _sqlite_files()


def test_pre_invocation_is_silent_passthrough() -> None:
    import bridge

    assert bridge.handle_pre_invocation({"conversationId": SESSION, "lastUserInput": "排查 A3 HCCL 超时"}) == {}
    assert bridge.handle_pre_invocation({}) == {}
    with pytest.raises(ValueError, match="JSON object"):
        bridge.handle_pre_invocation(["not-an-object"])  # type: ignore[arg-type]
    assert not _sqlite_files()


def test_pre_tool_use_allows_zero_meta_and_denies_forged_identity() -> None:
    import bridge

    allowed = {"toolCall": {"name": "mcp_mindie-tools_knowledge_query", "args": {"query": "A3 dual-Die"}}}
    assert bridge.handle_pre_tool_use(allowed) == {"decision": "allow"}
    forged = {"toolCall": {"name": "mcp_mindie-tools_knowledge_query", "arguments": {"_session_id": "forged"}}}
    assert bridge.handle_pre_tool_use(forged)["decision"] == "deny"
    malformed = {"toolCall": {"name": "mcp_mindie-tools_knowledge_query", "args": {"session_id": 7}}}
    assert bridge.handle_pre_tool_use(malformed)["decision"] == "deny"
    assert not _sqlite_files()


def test_cold_disabled_and_out_of_scope_stop_is_fast_and_creates_zero_sqlite(tmp_path: Path) -> None:
    payload = {"conversationId": SESSION, "workspacePaths": [str(tmp_path / "ws")]}
    assert _run_stop(payload)[0] == {}
    assert not _sqlite_files()

    import bridge

    profile = _profile()
    _configure(profile, "disabled", tmp_path / "ws")
    assert bridge.handle_stop(payload) == {}
    assert _run_stop(payload)[0] == {}
    assert not _sqlite_files()

    _configure(profile, "local-only", tmp_path / "other")
    started = time.monotonic()
    assert bridge.handle_stop(payload) == {}
    assert (time.monotonic() - started) * 1000.0 < (1500.0 if os.name == "nt" else 100.0)
    assert not _sqlite_files()


def test_stop_never_forces_continuation_and_captures_only_in_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import bridge
    from mindie_knowledge.loop.activation import Admission
    import mindie_knowledge.loop.handoff as handoff

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_transcript(workspace)
    profile = _profile()
    seen: list[dict] = []

    monkeypatch.setattr(handoff, "accept_stop", lambda path, forwarded, **kwargs: (seen.append(dict(forwarded)), {"stage": "accepted-local"})[1])
    payload = {"conversationId": SESSION, "workspacePaths": [str(workspace)], "appDataDir": str(workspace)}
    for tier in ("local-only", "contribute"):
        _configure(profile, tier, workspace)
        result = bridge.handle_stop(payload)
        assert result == {}
        assert "decision" not in result
        assert len(seen) == (1 if tier == "local-only" else 2)
        assert seen[-1]["harness"] == "agy"
        assert seen[-1]["session_id"] == SESSION
        assert seen[-1]["identity_kind"] == "notification"
        assert "turn_id" not in seen[-1]
        assert seen[-1]["event_id"].startswith(f"{SESSION}:eof:")
        assert seen[-1]["transcript_path"].endswith("transcript_full.jsonl")
        assert Admission(str(profile.admission_db)).inspect(SESSION)["enabled"]


def test_stop_uses_turn_identity_only_when_native_turn_id_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import bridge
    import mindie_knowledge.loop.handoff as handoff

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_transcript(workspace)
    profile = _profile()
    _configure(profile, "local-only", workspace)
    seen: list[dict] = []
    monkeypatch.setattr(handoff, "accept_stop", lambda path, forwarded, **kwargs: (seen.append(dict(forwarded)), {"stage": "accepted-local"})[1])

    assert bridge.handle_stop({"conversationId": SESSION, "workspacePaths": [str(workspace)], "appDataDir": str(workspace), "executionNum": 4, "terminationReason": "NO_TOOL_CALL"}) == {}
    assert len(seen) == 1
    assert seen[-1]["identity_kind"] == "turn"
    assert seen[-1]["turn_id"] == "exec:4"
    assert "event_id" not in seen[-1]

    forwarded_stop: list[dict] = []
    import mindie_harness.runtime.knowledge_binding as kb

    monkeypatch.setattr(kb, "run_stop_capture", lambda prof, ev, **kw: (forwarded_stop.append(dict(ev)), {"stage": "inert", "reason": "recursive-stop"})[1])
    monkeypatch.setattr(bridge, "run_stop_capture", kb.run_stop_capture)
    assert bridge.handle_stop({"conversationId": SESSION, "workspacePaths": [str(workspace)], "appDataDir": str(workspace), "stop_hook_active": True}) == {}
    assert len(forwarded_stop) == 1
    assert forwarded_stop[0]["stop_hook_active"] is True


def test_windows_apps_stub_rejection_and_mcp_config_forwarding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_server
    import mindie_launch
    from mindie_harness.atomic import atomic_write_json

    fake_stub = tmp_path / "Microsoft" / "WindowsApps" / "python.exe"
    fake_stub.parent.mkdir(parents=True)
    fake_stub.write_bytes(b"")
    assert mindie_launch._usable_py(str(fake_stub)) is None
    assert mcp_server._usable_py(str(fake_stub)) is False

    custom_cfg = tmp_path / "custom" / "agy.json"
    atomic_write_json(custom_cfg, {"python": str(fake_stub)})
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(mindie_launch, "_active_python", lambda cfg=None: sys.executable)
    monkeypatch.setattr(mcp_server, "serve_mcp", lambda surface, *, profile, adapter, managed=True: (captured.append((surface, str(profile.config_path))), 0)[1])
    assert mindie_launch.main(["mcp", "remote", "--config", str(custom_cfg)]) == 0
    assert captured == [("remote", str(custom_cfg))]


def test_stop_preserves_explicit_revocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import bridge
    from mindie_knowledge.loop.activation import Admission
    import mindie_knowledge.loop.handoff as handoff

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_transcript(workspace)
    profile = _profile()
    _configure(profile, "local-only", workspace)
    Admission(str(profile.admission_db)).activate(SESSION, project_root=str(workspace), root_session=SESSION, not_before=0.0)
    assert Admission(str(profile.admission_db)).deactivate(SESSION) is True
    seen: list[dict] = []

    monkeypatch.setattr(handoff, "accept_stop", lambda *args, **kwargs: (seen.append({"args": args}), {"stage": "accepted-local"})[1])
    assert bridge.handle_stop({"conversationId": SESSION, "workspacePaths": [str(workspace)], "appDataDir": str(workspace)}) == {}
    assert seen == []
    assert Admission(str(profile.admission_db)).inspect(SESSION)["status"] == "inactive"
