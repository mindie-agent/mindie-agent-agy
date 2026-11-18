# Antigravity Harness Interface Evidence

<a href="harness-interfaces.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

This document records the 7 controlled Google Antigravity (`agy` `2.0.0 .. 2.99.99`) interfaces declared in [`harness-compatibility.json`](../harness-compatibility.json), their implementation paths, and contract tests.

## 1. Registered Interface Matrix

| Interface ID | Host Contract | Implementation Path | Verification Test |
|---|---|---|---|
| `plugin.manifest` | `plugins/<name>/plugin.json`, **plugin-root** `hooks.json` (never `hooks/hooks.json`), and `mcp_config.json` | `plugins/mindie-agent/plugin.json` | `tests/test_agy_plugin.py::test_plugin_manifest_and_root_hooks_layout` |
| `hooks.session-start` | Receives `conversationId` and `workspacePaths` on `stdin`; passively returns `{}` and enqueues the update marker | `plugins/mindie-agent/scripts/bridge.py:handle_session_start` | `tests/test_agy_lifecycle.py::test_session_start_writes_pending_marker_then_stays_quiet` |
| `hooks.pre-invocation` | Receives `lastUserInput` on `stdin`; validates a JSON object and returns a zero-I/O `{}` (`SKILL.md` sets `disable-slash-command: true`) | `plugins/mindie-agent/scripts/bridge.py:handle_pre_invocation` | `tests/test_agy_lifecycle.py::test_pre_invocation_is_silent_passthrough` |
| `hooks.pre-tool-use` | Receives `toolCall`; returns `{"decision": "allow"}` with zero `_meta` binding and denies forged identity fields | `plugins/mindie-agent/scripts/bridge.py:handle_pre_tool_use` | `tests/test_agy_lifecycle.py::test_pre_tool_use_allows_zero_meta_and_denies_forged_identity` |
| `hooks.stop` | Receives `transcriptPath` / `appDataDir`; **never returns `{"decision": "continue"}`**, captures only for authorized in-scope `contribute` / `local-only` workspaces, and preserves explicit revocation | `plugins/mindie-agent/scripts/bridge.py:handle_stop` | `tests/test_agy_lifecycle.py::test_stop_never_forces_continuation_and_captures_only_in_scope` |
| `mcp.stdio` | Exposes `mindie-remote-dev` (12 native `agy` tools) and `mindie-tools` (components + `mindie_doctor` + `knowledge_*`) over concurrent stdio | `plugins/mindie-agent/scripts/mcp_server.py:serve` | `tests/test_agy_mcp_and_knowledge.py::test_agy_remote_and_tools_catalogs_require_zero_meta` |
| `transcript.brain-jsonl` | Parses `<appDataDir>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl` | `plugins/mindie-agent/scripts/agy_transcript.py:read_material` | `tests/test_agy_transcript.py::test_transcript_extracts_public_messages_and_excludes_thinking_and_metadata` |

## 2. Subagent Session Isolation Boundary

In Google Antigravity, subagents spawned via `invoke_subagent` receive their own distinct UUID `conversationId`. Because `Admission` keys leases strictly by `conversationId`:
- `Admission.activate` in one conversation records state only for that `conversationId`;
- Subagents and sibling conversations keep independent `conversationId` scopes and never share turn-end `Stop` capture cursors;
- Standard MCP calls need no `PreToolUse` bind at all (`P2.7`), so there is no one-shot bind to leak across conversations.
