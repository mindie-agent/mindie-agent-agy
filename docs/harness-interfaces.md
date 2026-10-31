# Antigravity Harness Interface Evidence

<a href="harness-interfaces.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

This document records the 8 controlled Google Antigravity (`agy` `2.0.0 .. 2.99.99`) interfaces declared in [`harness-compatibility.json`](../harness-compatibility.json), their implementation paths, and contract tests.

## 1. Registered Interface Matrix

| Interface ID | Host Contract | Implementation Path | Verification Test |
|---|---|---|---|
| `plugin.manifest` | `plugins/<name>/plugin.json`, **plugin-root** `hooks.json` (never `hooks/hooks.json`), and `mcp_config.json` | `plugins/mindie-agent/plugin.json` | `tests/test_agy_plugin.py::test_plugin_manifest_and_root_hooks_layout` |
| `hooks.session-start` | Receives `conversationId` and `workspacePaths` on `stdin`; passively returns `{}` | `plugins/mindie-agent/scripts/bridge.py:handle_session_start` | `tests/test_agy_lifecycle.py::test_passive_session_start_and_ordinary_pre_invocation_stay_quiet` |
| `hooks.pre-invocation` | Receives `lastUserInput` on `stdin`; activates lease on `/mindie-agent` and returns `{"injectSteps": [{"ephemeralMessage": "..."}]}` | `plugins/mindie-agent/scripts/bridge.py:handle_pre_invocation` | `tests/test_agy_lifecycle.py::test_explicit_mindie_agent_gesture_activates_and_deactivates_lease` |
| `hooks.pre-tool-use` | Receives `toolCall` (`mcp_<server>_<tool>`) and `stepIdx`; records a 30s one-shot bind and returns `{"decision": "allow"}` | `plugins/mindie-agent/scripts/bridge.py:handle_pre_tool_use` | `tests/test_agy_lifecycle.py::test_pre_tool_use_binds_mcp_call_and_rejects_replay` |
| `hooks.stop` | Receives `transcriptPath`; **always returns `{}`** (`decision != "continue"`), running incremental capture only when both lease and consent are active | `plugins/mindie-agent/scripts/bridge.py:handle_stop` | `tests/test_agy_lifecycle.py::test_stop_never_forces_continuation_and_requires_manual_lease` |
| `mcp.stdio` | Exposes `mindie-remote-dev` and `mindie-tools` over stdio while excluding all `knowledge_*` tools | `plugins/mindie-agent/scripts/mcp_server.py:serve` | `tests/test_agy_mcp_and_knowledge.py::test_public_mcp_excludes_knowledge_even_when_session_is_active` |
| `transcript.brain-jsonl` | Parses `<appDataDir>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl` | `plugins/mindie-agent/scripts/agy_transcript.py:read_material` | `tests/test_agy_transcript.py::test_transcript_extracts_public_messages_and_excludes_thinking_and_metadata` |
| `summary.structured-output` | Emits `mindie-summary-outcome/1` in an isolated worker and supports `--identity` verification | `plugins/mindie-agent/scripts/organizer.py:main` | `tests/test_agy_organizer.py::test_organizer_emits_valid_summary_outcome_and_never_retries_unknown` |

## 2. Subagent Session Isolation Boundary

In Google Antigravity, subagents spawned via `invoke_subagent` receive their own distinct UUID `conversationId`. Because `Admission` keys leases strictly by `conversationId`:
- Typing `/mindie-agent` in the parent conversation authorizes only the parent `conversationId`;
- Subagents never inherit the parent's knowledge lease or trigger unauthorized `Stop` capture.
