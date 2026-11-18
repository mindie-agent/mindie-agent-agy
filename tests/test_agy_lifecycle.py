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

    atomic_write_json(profile.config_path, {"engine_config": str(root / "missing.engine.json")})
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

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_transcript(workspace)
    profile = _profile()
    seen: list[dict] = []
    import mindie_knowledge.loop.cli as cli

    monkeypatch.setattr(cli, "capture_hook", lambda path, forwarded, timeout=None: seen.append(dict(forwarded)))
    payload = {"conversationId": SESSION, "workspacePaths": [str(workspace)], "appDataDir": str(workspace)}
    for tier in ("local-only", "contribute"):
        _configure(profile, tier, workspace)
        result = bridge.handle_stop(payload)
        assert result == {}
        assert "decision" not in result
        assert len(seen) == (1 if tier == "local-only" else 2)
        assert seen[-1]["harness"] == "agy"
        assert seen[-1]["session_id"] == SESSION
        assert seen[-1]["transcript_path"].endswith("transcript_full.jsonl")
        assert seen[-1]["mindie_activation"]


def test_stop_preserves_explicit_revocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import bridge
    from mindie_knowledge.loop.activation import Admission

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_transcript(workspace)
    profile = _profile()
    _configure(profile, "local-only", workspace)
    Admission(str(profile.admission_db)).activate(SESSION, project_root=str(workspace), root_session=SESSION, not_before=0.0)
    assert Admission(str(profile.admission_db)).deactivate(SESSION) is True
    seen: list[dict] = []
    import mindie_knowledge.loop.cli as cli

    monkeypatch.setattr(cli, "capture_hook", lambda *args, **kwargs: seen.append({"args": args}))
    assert bridge.handle_stop({"conversationId": SESSION, "workspacePaths": [str(workspace)], "appDataDir": str(workspace)}) == {}
    assert seen == []
    assert Admission(str(profile.admission_db)).inspect(SESSION)["status"] == "inactive"
