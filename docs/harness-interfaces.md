# Antigravity Harness Interface Evidence

<a href="harness-interfaces.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

This document records the 8 controlled Google Antigravity (`agy` `2.0.0 .. 2.99.99`) interfaces declared in [`harness-compatibility.json`](../harness-compatibility.json), their implementation paths, and contract tests.

## 1. Registered Interface Matrix

| Interface ID | Host Contract | Implementation Path | Verification Test |
|---|---|---|---|
| `plugin.manifest` | `plugins/<name>/plugin.json`, **plugin-root** `hooks.json` (never `hooks/hooks.json`), and `mcp_config.json` | `plugins/mindie-agent/plugin.json` | `tests/test_agy_plugin.py::test_plugin_manifest_and_root_hooks_layout` |
| `hooks.session-start` | Receives `conversationId` and `workspacePaths` on `stdin`; passively returns `{}` | `plugins/mindie-agent/scripts/bridge.py:handle_session_start` | `tests/test_agy_lifecycle.py::test_passive_session_start_and_ordinary_pre_invocation_stay_quiet` |
| `hooks.pre-invocation` | Receives `lastUserInput` on `stdin`; validates JSON payload and returns `{}` as a zero-I/O pass-through (`SKILL.md` sets `disable-slash-command: true`) | `plugins/mindie-agent/scripts/bridge.py:handle_pre_invocation` | `tests/test_agy_lifecycle.py::test_pre_invocation_is_silent_passthrough_and_skill_hides_slash_command` |
| `hooks.pre-tool-use` | Receives `toolCall` (`mcp_<server>_<tool>`) and `stepIdx`; records a 120s one-shot bind and returns `{"decision": "allow"}` | `plugins/mindie-agent/scripts/bridge.py:handle_pre_tool_use` | `tests/test_agy_lifecycle.py::test_pre_tool_use_binds_mcp_call_and_rejects_replay` |
| `hooks.stop` | Receives `transcriptPath`; **never returns `{"decision": "continue"}`**, running incremental capture when community sharing consent is active for an in-scope workspace | `plugins/mindie-agent/scripts/bridge.py:handle_stop` | `tests/test_agy_lifecycle.py::test_stop_never_forces_continuation_and_captures_when_sharing_enabled` |
| `mcp.stdio` | Exposes `mindie-remote-dev` and `mindie-tools` (including default-enabled `knowledge_*` tools) over concurrent stdio | `plugins/mindie-agent/scripts/mcp_server.py:serve` | `tests/test_agy_mcp_and_knowledge.py::test_mcp_tools_exposes_default_enabled_knowledge_and_remote_catalogs` |
| `transcript.brain-jsonl` | Parses `<appDataDir>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl` | `plugins/mindie-agent/scripts/agy_transcript.py:read_material` | `tests/test_agy_transcript.py::test_transcript_extracts_public_messages_and_excludes_thinking_and_metadata` |
| `summary.structured-output` | Emits `mindie-summary-outcome/1` in an isolated worker and supports `--identity` verification | `plugins/mindie-agent/scripts/organizer.py:main` | `tests/test_agy_organizer.py::test_organizer_emits_valid_summary_outcome_and_never_retries_unknown` |

## 2. Subagent Session Isolation Boundary

In Google Antigravity, subagents spawned via `invoke_subagent` receive their own distinct UUID `conversationId`. Because `Admission` keys default leases strictly by `conversationId`:
- Default lease provisioning (`ensure_default_lease`) in one conversation records state only for that `conversationId`;
- Subagents and sibling conversations keep independent `conversationId` scopes and never share one-shot `PreToolUse` binds or turn-end `Stop` capture cursors.
