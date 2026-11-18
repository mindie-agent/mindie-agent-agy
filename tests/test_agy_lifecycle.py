"""Tier 0 & Tier 1 tests for Antigravity hook lifecycle, silent default-enabled leases, and `< 100ms` Stop fast-paths."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import time
import pytest

import admission
import bridge
import identity
import setup


SESSION_A = "454c6ee9-afea-47be-8188-abb9c7fb1f26"
SESSION_B = "ec33ebf9-0cba-4100-8142-c61503f6c587"


def test_passive_session_start_and_ordinary_pre_invocation_stay_quiet(tmp_path: Path):
    import paths

    workspace = tmp_path / "ws"
    workspace.mkdir()
    start_out = bridge.handle_session_start(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
        }
    )
    assert start_out == {}
    assert not paths.admission_path().exists()
    assert admission.knowledge_lease(SESSION_A) is None

    pre_out = bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "Fix the failing unit test in model_runner.py",
            "invocationNum": 1,
        }
    )
    assert pre_out == {}
    assert not paths.admission_path().exists()
    facts = admission.status_facts(SESSION_A)
    assert facts["activated"] is True
    assert not paths.admission_path().exists()


def test_pre_invocation_is_silent_passthrough_and_skill_hides_slash_command(tmp_path: Path):
    import paths

    skill_path = Path(__file__).resolve().parents[1] / "plugins" / "mindie-agent" / "skills" / "mindie-agent" / "SKILL.md"
    frontmatter = skill_path.read_text(encoding="utf-8").split("---", 2)[1]
    assert "disable-slash-command: true" in frontmatter

    workspace = tmp_path / "ws"
    workspace.mkdir()

    # PreInvocation is a pure zero-I/O {} pass-through for all user inputs
    for user_input in (
        "排查 A3 双 Die HCCL 通信超时",
        "Show the HCCL ranktable",
    ):
        out = bridge.handle_pre_invocation(
            {
                "conversationId": SESSION_A,
                "workspacePaths": [str(workspace)],
                "lastUserInput": user_input,
                "invocationNum": 1,
            }
        )
        assert out == {}
    assert not paths.admission_path().exists()
    assert admission.status_facts(SESSION_A)["activated"] is True
    assert admission.status_facts(SESSION_B)["activated"] is True

    with pytest.raises(ValueError, match="JSON object"):
        bridge.handle_pre_invocation(["not-an-object"])  # type: ignore[arg-type]

    # Default lease provisioning creates an active lease for SESSION_A without touching SESSION_B
    lease = admission.ensure_default_lease(SESSION_A, workspace, not_before=0.0)
    assert lease["enabled"] == 1
    assert lease["entry_enabled"] == 1
    assert lease["activated_at"] == 0.0
    assert Path(lease["project_root"]) == workspace.resolve()
    assert admission.knowledge_lease(SESSION_A) is not None
    assert admission.knowledge_lease(SESSION_B) is None


def test_pre_tool_use_binds_mcp_call_and_rejects_replay(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    args = {"file_path": "/workspace/vllm_ascend/worker.py"}

    hook_res = bridge.handle_pre_tool_use(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "stepIdx": 7,
            "toolCall": {
                "name": "mcp_mindie-remote-dev_remote_read",
                "args": args,
            },
        }
    )
    assert hook_res == {"decision": "allow"}

    claimed = identity.claim_bind("remote", "remote_read", args)
    assert claimed["session"] == SESSION_A
    assert claimed["step_idx"] == 7
    identity.finish_bind(claimed["bind_id"], True)

    with pytest.raises(ValueError, match="no active PreToolUse bind"):
        identity.claim_bind("remote", "remote_read", args)

    # Also verify plugin-namespaced lazy `call_mcp_tool` (`mindie-agent_mindie-tools`)
    lazy_res = bridge.handle_pre_tool_use(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "stepIdx": 8,
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "mindie-agent_mindie-tools",
                    "ToolName": "mindie_status",
                    "Arguments": {},
                },
            },
        }
    )
    assert lazy_res == {"decision": "allow"}
    claimed_lazy = identity.claim_bind("tools", "mindie_status", {})
    assert claimed_lazy["session"] == SESSION_A
    assert claimed_lazy["step_idx"] == 8
    identity.finish_bind(claimed_lazy["bind_id"], True)


def test_stop_never_forces_continuation_and_captures_when_sharing_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import paths

    workspace = tmp_path / "ws"
    workspace.mkdir()
    brain_logs = tmp_path / "gemini" / "antigravity" / "brain" / SESSION_A / ".system_generated" / "logs"
    brain_logs.mkdir(parents=True)
    transcript = brain_logs / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-10-31T08:21:33Z",
                "content": "<USER_REQUEST>\nVerify custom Ascend operator\n</USER_REQUEST>",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # 1. Cold unconfigured Stop returns {} in < 100ms (min over 5 warm runs) without creating admission.sqlite3
    stop_event = {
        "conversationId": SESSION_A,
        "workspacePaths": [str(workspace)],
        "transcriptPath": str(transcript),
        "executionNum": 1,
        "terminationReason": "NO_TOOL_CALL",
    }
    bridge.handle_stop(stop_event)
    cold_samples_ms: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        passive_stop = bridge.handle_stop(stop_event)
        cold_samples_ms.append((time.perf_counter() - t0) * 1000.0)
        assert passive_stop == {}
        assert passive_stop.get("decision") != "continue"
    assert min(cold_samples_ms) < 100.0
    assert not paths.admission_path().exists()

    # 2. Sharing-disabled Stop also returns {} in < 100ms without creating admission.sqlite3
    setup.configure_community(enabled=False, project_root=workspace)
    bridge.handle_stop(stop_event)
    disabled_samples_ms: list[float] = []
    for _ in range(5):
        t1 = time.perf_counter()
        disabled_stop = bridge.handle_stop(stop_event)
        disabled_samples_ms.append((time.perf_counter() - t1) * 1000.0)
        assert disabled_stop == {}
    assert min(disabled_samples_ms) < 100.0
    assert not paths.admission_path().exists()

    # 3. Enable community sharing: out-of-scope workspace still short-circuits in < 100ms with zero SQLite writes
    setup.configure_community(enabled=True, fork="user/knowledge-vllm-ascend", project_root=workspace)

    captured_calls = []

    def fake_capture_hook(engine_cfg, payload):
        captured_calls.append((engine_cfg, payload))
        return {"stage": "accepted-local"}

    import mindie_knowledge.loop.cli as cli_mod

    monkeypatch.setattr(cli_mod, "capture_hook", fake_capture_hook)

    other_workspace = tmp_path / "other_ws"
    other_workspace.mkdir()
    out_of_scope_event = {
        "conversationId": SESSION_B,
        "workspacePaths": [str(other_workspace)],
        "transcriptPath": str(transcript),
        "executionNum": 1,
        "terminationReason": "NO_TOOL_CALL",
    }
    bridge.handle_stop(out_of_scope_event)
    out_of_scope_samples_ms: list[float] = []
    for _ in range(5):
        t2 = time.perf_counter()
        out_of_scope_stop = bridge.handle_stop(out_of_scope_event)
        out_of_scope_samples_ms.append((time.perf_counter() - t2) * 1000.0)
        assert out_of_scope_stop == {}
    assert min(out_of_scope_samples_ms) < 100.0
    assert not paths.admission_path().exists()
    assert len(captured_calls) == 0

    # 4. Authorized in-scope conversation captures cleanly by default with not_before=0.0
    active_stop = bridge.handle_stop(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "transcriptPath": str(transcript),
            "executionNum": 2,
            "terminationReason": "NO_TOOL_CALL",
        }
    )
    assert active_stop == {}
    assert active_stop.get("decision") != "continue"
    assert len(captured_calls) == 1
    _, forwarded = captured_calls[0]
    assert forwarded["harness"] == "agy"
    assert forwarded["session_id"] == SESSION_A
    assert forwarded["mindie_activation"]
    assert forwarded["transcript_path"] == str(transcript.resolve())
    lease = admission.knowledge_lease(SESSION_A)
    assert lease is not None and lease["activated_at"] == 0.0


def test_pre_tool_use_bind_failure_denies_with_host_readable_stdout(tmp_path: Path, monkeypatch, capsys):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    event = {
        "conversationId": SESSION_A,
        "workspacePaths": [str(workspace)],
        "stepIdx": 7,
        "toolCall": {
            "name": "mcp_mindie-remote-dev_remote_read",
            "args": {"file_path": "/workspace/vllm_ascend/worker.py"},
        },
    }

    def boom(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bridge, "publish_bind", boom)
    payload = bridge.handle_pre_tool_use(event)
    assert payload["decision"] == "deny"
    assert payload.get("decision") != "allow"
    assert "OSError" in payload["reason"]
    assert "No space left on device" in payload["reason"]

    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": io.BytesIO(json.dumps(event).encode())})())
    assert bridge.main(["pre-tool-use"]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["decision"] == "deny"
    assert emitted.get("decision") != "allow"
    assert "OSError" in emitted["reason"]


def test_pre_tool_use_bind_timeout_denies_with_host_readable_stdout(tmp_path: Path, monkeypatch, capsys):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    event = {
        "conversationId": SESSION_A,
        "workspacePaths": [str(workspace)],
        "stepIdx": 7,
        "toolCall": {
            "name": "mcp_mindie-remote-dev_remote_read",
            "args": {"file_path": "/workspace/vllm_ascend/worker.py"},
        },
    }

    def boom(*_args, **_kwargs):
        raise TimeoutError("mcp-binds sqlite busy_timeout")

    monkeypatch.setattr(bridge, "publish_bind", boom)
    payload = bridge.handle_pre_tool_use(event)
    assert payload["decision"] == "deny"
    assert payload.get("decision") != "allow"
    assert "TimeoutError" in payload["reason"]
    assert "busy_timeout" in payload["reason"]

    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": io.BytesIO(json.dumps(event).encode())})())
    assert bridge.main(["pre-tool-use"]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["decision"] == "deny"
    assert emitted.get("decision") != "allow"
    assert "TimeoutError" in emitted["reason"]
    assert emitted["reason"]


def test_status_facts_does_not_report_corrupt_community_as_disabled():
    comm = Path(os.environ["MINDIE_AGENT_CONFIG"]).parent / "mindie-community.json"
    comm.write_text("{not-json", encoding="utf-8")
    facts = admission.status_facts(SESSION_A)
    assert facts["community_sharing_enabled"] is None
    assert facts["community_sharing_enabled"] is not False
    assert facts["repair_required"] is True
    assert facts["community_state"] == "corrupt"


def test_status_facts_survives_invalid_community_metadata_types():
    comm = Path(os.environ["MINDIE_AGENT_CONFIG"]).parent / "mindie-community.json"
    comm.write_text(json.dumps({"enabled": True, "project_roots": 123}), encoding="utf-8")
    facts = admission.status_facts(SESSION_A)
    assert facts["repair_required"] is True
    assert facts["community_state"] == "corrupt"
    assert facts["community_sharing_enabled"] is None


def test_status_facts_survives_community_metadata_permission_error(monkeypatch):
    import paths as paths_mod

    comm = Path(os.environ["MINDIE_AGENT_CONFIG"]).parent / "mindie-community.json"
    comm.write_text("{}", encoding="utf-8")
    real_read = paths_mod.read_json

    def gated_read(path):
        if Path(path) == comm:
            raise PermissionError("community metadata not readable")
        return real_read(path)

    monkeypatch.setattr(admission, "read_json", gated_read)
    monkeypatch.setattr(setup, "read_json", gated_read)
    facts = admission.status_facts(SESSION_A)
    assert facts["community_sharing_enabled"] is None
    assert facts["community_sharing_enabled"] is not False
    assert facts["repair_required"] is True
    assert facts["community_state"] == "unreadable"


def test_status_surfaces_survive_stat_permission_error(tmp_path, monkeypatch):
    import mcp_server

    comm = Path(os.environ["MINDIE_AGENT_CONFIG"]).parent / "mindie-community.json"
    comm.write_text("{}", encoding="utf-8")
    orig_stat = Path.stat

    def gated_stat(self, *args, **kwargs):
        if self.name == "mindie-community.json":
            raise PermissionError("stat denied")
        return orig_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", gated_stat)
    facts = admission.status_facts(SESSION_A)
    assert facts["community_sharing_enabled"] is None
    assert facts["community_sharing_enabled"] is not False
    assert facts["repair_required"] is True
    assert facts["community_state"] == "unreadable"

    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert (
        bridge.handle_pre_invocation(
            {
                "conversationId": SESSION_A,
                "workspacePaths": [str(workspace)],
                "lastUserInput": "check status",
            }
        )
        == {}
    )

    monkeypatch.setenv("AGY_CONVERSATION_ID", SESSION_A)
    resp = mcp_server.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "mindie_status", "arguments": {}},
        },
        "tools",
    )
    assert "error" not in resp
    mcp_facts = resp["result"]["structuredContent"]
    assert mcp_facts["repair_required"] is True
    assert mcp_facts["community_state"] == "unreadable"
    assert mcp_facts["community_sharing_enabled"] is None


def test_hook_launcher_does_not_mask_exceptions_as_empty_success(monkeypatch, capsys):
    import mindie_launch
    import bridge as bridge_mod

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: None)

    def boom(_argv=None):
        raise RuntimeError("sqlite bind write failed")

    monkeypatch.setattr(bridge_mod, "main", boom)
    with pytest.raises(RuntimeError, match="sqlite bind write failed"):
        mindie_launch.main(["hook", "pre-tool-use"])
    captured = capsys.readouterr()
    assert captured.out.strip() != "{}"
    assert not captured.out.strip().endswith("{}")


@pytest.mark.skipif(os.name == "nt", reason="direct-dispatch hook budget uses SIGALRM")
def test_direct_hook_dispatch_denies_when_bind_exceeds_exec_budget(tmp_path, monkeypatch, capsys):
    import mindie_launch
    import bridge as bridge_mod

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: None)
    monkeypatch.setattr(mindie_launch, "HOOK_EXEC_BUDGET", 0.2)
    monkeypatch.delenv("_MINDIE_AGY_HOOK_BUDGETED", raising=False)

    def slow_bind(*_args, **_kwargs):
        time.sleep(10.25)
        return "bind-id"

    monkeypatch.setattr(bridge_mod, "publish_bind", slow_bind)
    event = {
        "conversationId": SESSION_A,
        "workspacePaths": [str(tmp_path)],
        "stepIdx": 7,
        "toolCall": {
            "name": "mcp_mindie-remote-dev_remote_read",
            "args": {"file_path": "/workspace/vllm_ascend/worker.py"},
        },
    }
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": io.BytesIO(json.dumps(event).encode())})())
    started = time.monotonic()
    assert mindie_launch.main(["hook", "pre-tool-use"]) == 0
    elapsed = time.monotonic() - started
    denied = json.loads(capsys.readouterr().out)
    assert denied["decision"] == "deny"
    assert denied.get("decision") != "allow"
    assert elapsed < 2.0
    assert elapsed < 10.0


def test_session_start_timeout_does_not_continue_to_lease(tmp_path, monkeypatch, capsys):
    import mindie_launch
    import bridge as bridge_mod

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: None)
    monkeypatch.delenv("_MINDIE_AGY_HOOK_BUDGETED", raising=False)
    lease_called: list[bool] = []

    def boom_update(*_args, **_kwargs):
        raise TimeoutError("updater spawn timed out")

    def mark_lease(*_args, **_kwargs):
        lease_called.append(True)
        time.sleep(10.5)
        return {
            "enabled": 1,
            "session": SESSION_A,
            "project_root": str(tmp_path),
            "token": "t",
            "activated_at": 1,
        }

    monkeypatch.setattr(bridge_mod, "trigger_background_update_if_due", boom_update)
    monkeypatch.setattr(bridge_mod, "knowledge_lease", mark_lease)
    event = {"conversationId": SESSION_A, "workspacePaths": [str(tmp_path)]}
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": io.BytesIO(json.dumps(event).encode())})())
    started = time.monotonic()
    assert mindie_launch.main(["hook", "session-start"]) == 0
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert lease_called == []
    payload = json.loads(capsys.readouterr().out)
    assert "injectSteps" in payload
    assert "TimeoutError" in payload["injectSteps"][0]["ephemeralMessage"]


@pytest.mark.skipif(os.name == "nt", reason="direct-dispatch hook budget uses SIGALRM")
def test_session_start_alarm_does_not_run_unbounded_lease(tmp_path, monkeypatch, capsys):
    import mindie_launch
    import bridge as bridge_mod

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: None)
    monkeypatch.setattr(mindie_launch, "HOOK_EXEC_BUDGET", 0.2)
    monkeypatch.delenv("_MINDIE_AGY_HOOK_BUDGETED", raising=False)
    lease_called: list[bool] = []

    def slow_update(*_args, **_kwargs):
        time.sleep(0.35)
        return False

    def slow_lease(*_args, **_kwargs):
        lease_called.append(True)
        time.sleep(10.5)
        return {
            "enabled": 1,
            "session": SESSION_A,
            "project_root": str(tmp_path),
            "token": "t",
            "activated_at": 1,
        }

    monkeypatch.setattr(bridge_mod, "trigger_background_update_if_due", slow_update)
    monkeypatch.setattr(bridge_mod, "knowledge_lease", slow_lease)
    event = {"conversationId": SESSION_A, "workspacePaths": [str(tmp_path)]}
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": io.BytesIO(json.dumps(event).encode())})())
    started = time.monotonic()
    assert mindie_launch.main(["hook", "session-start"]) == 0
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert lease_called == []
    payload = json.loads(capsys.readouterr().out)
    assert "injectSteps" in payload


def test_stop_capture_handoff_failure_uses_preinvocation_inject_field(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    brain_logs = tmp_path / "gemini" / "antigravity" / "brain" / SESSION_A / ".system_generated" / "logs"
    brain_logs.mkdir(parents=True)
    transcript = brain_logs / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-10-31T08:21:33Z",
                "content": "<USER_REQUEST>\nVerify custom Ascend operator\n</USER_REQUEST>",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    setup.configure_community(enabled=True, fork="user/knowledge-vllm-ascend", project_root=workspace)

    import mindie_knowledge.loop.cli as cli_mod

    def boom(_engine_cfg, _payload):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(cli_mod, "capture_hook", boom)
    failed_stop = bridge.handle_stop(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "transcriptPath": str(transcript),
            "executionNum": 2,
            "terminationReason": "NO_TOOL_CALL",
        }
    )
    assert failed_stop.get("decision") != "continue"
    assert "systemMessage" not in failed_stop
    assert "injectSteps" in failed_stop
    warning = failed_stop["injectSteps"][0]["ephemeralMessage"]
    assert "RuntimeError" in warning
    assert "engine unavailable" in warning


def test_stop_timeout_uses_inject_steps_instead_of_empty_stdout(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    brain_logs = tmp_path / "gemini" / "antigravity" / "brain" / SESSION_A / ".system_generated" / "logs"
    brain_logs.mkdir(parents=True)
    transcript = brain_logs / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    setup.configure_community(enabled=True, fork="user/knowledge-vllm-ascend", project_root=workspace)

    def boom(*_args, **_kwargs):
        raise TimeoutError("transcript read timed out")

    monkeypatch.setattr(bridge, "capture_source", boom)
    failed_stop = bridge.handle_stop(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "transcriptPath": str(transcript),
            "executionNum": 2,
            "terminationReason": "NO_TOOL_CALL",
        }
    )
    assert failed_stop.get("decision") != "continue"
    assert "systemMessage" not in failed_stop
    assert "injectSteps" in failed_stop
    warning = failed_stop["injectSteps"][0]["ephemeralMessage"]
    assert "TimeoutError" in warning
    assert "transcript read timed out" in warning


def test_session_start_updater_spawn_failure_stays_host_readable(tmp_path: Path, monkeypatch):
    import subprocess

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.delenv("MINDIE_CONFIG_HOME", raising=False)
    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    cfg.write_text(json.dumps({"python": sys.executable}), encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("cannot spawn updater")

    monkeypatch.setattr(subprocess, "Popen", boom)
    start_out = bridge.handle_session_start(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
        }
    )
    assert start_out.get("decision") != "continue"
    assert "injectSteps" in start_out
    warning = start_out["injectSteps"][0]["ephemeralMessage"]
    assert "OSError" in warning
    assert "cannot spawn updater" in warning
