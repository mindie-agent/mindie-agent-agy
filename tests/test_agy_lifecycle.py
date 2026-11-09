"""Tier 0 & Tier 1 tests for Antigravity hook lifecycle and manual `/mindie-agent` session gate."""

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
    workspace = tmp_path / "ws"
    workspace.mkdir()
    start_out = bridge.handle_session_start(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
        }
    )
    assert start_out == {}
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
    assert admission.knowledge_lease(SESSION_A) is None


def test_explicit_mindie_agent_gesture_activates_and_deactivates_lease(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    out = bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent 排查 A3 双 Die HCCL 通信超时",
            "invocationNum": 1,
        }
    )
    assert "injectSteps" in out
    assert SESSION_A in out["injectSteps"][0]["ephemeralMessage"]

    lease = admission.knowledge_lease(SESSION_A)
    assert lease is not None
    assert lease["enabled"] == 1
    assert lease["entry_enabled"] == 1
    assert Path(lease["project_root"]) == workspace.resolve()

    # Another conversation (or subagent conversation) never inherits SESSION_A's lease
    assert admission.knowledge_lease(SESSION_B) is None

    # Subsequent ordinary prompt in SESSION_A keeps the lease active
    follow_up = bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "Show the HCCL ranktable",
            "invocationNum": 2,
        }
    )
    assert follow_up == {}
    assert admission.knowledge_lease(SESSION_A) is not None

    # Explicit deactivation revokes the lease
    deact = bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent deactivate",
            "invocationNum": 3,
        }
    )
    assert "deactivated" in deact["injectSteps"][0]["ephemeralMessage"]
    assert admission.knowledge_lease(SESSION_A) is None


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


def test_stop_never_forces_continuation_and_requires_manual_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
                "content": "<USER_REQUEST>\n/mindie-agent test\n</USER_REQUEST>",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # Unactivated Stop returns {} immediately and never forces continuation
    passive_stop = bridge.handle_stop(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "transcriptPath": str(transcript),
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
        }
    )
    assert passive_stop == {}
    assert passive_stop.get("decision") != "continue"

    # Activate session and enable community sharing for workspace
    bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent test",
        }
    )
    setup.configure_community(enabled=True, fork="user/knowledge-vllm-ascend", project_root=workspace)

    captured_calls = []

    def fake_capture_hook(engine_cfg, payload):
        captured_calls.append((engine_cfg, payload))
        return {"stage": "accepted-local"}

    import mindie_knowledge.loop.cli as cli_mod

    monkeypatch.setattr(cli_mod, "capture_hook", fake_capture_hook)

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
    assert forwarded["transcript_path"] == str(transcript.resolve())


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
    status_out = bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent status",
        }
    )
    assert "injectSteps" in status_out
    message = status_out["injectSteps"][0]["ephemeralMessage"]
    assert "unreadable" in message
    assert "PermissionError" not in message or "repair_required" in message
    reported = json.loads(message.split("MindIE factual status: ", 1)[1])
    assert reported["repair_required"] is True
    assert reported["community_state"] == "unreadable"
    assert reported["community_sharing_enabled"] is None

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
                "content": "<USER_REQUEST>\n/mindie-agent test\n</USER_REQUEST>",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent test",
        }
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
    bridge.handle_pre_invocation(
        {
            "conversationId": SESSION_A,
            "workspacePaths": [str(workspace)],
            "lastUserInput": "/mindie-agent test",
        }
    )
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
