"""Tier 0 & Tier 1 tests for Antigravity hook lifecycle and manual `/mindie-agent` session gate."""

from __future__ import annotations

import json
from pathlib import Path
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
