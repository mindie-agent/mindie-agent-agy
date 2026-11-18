# Antigravity Harness 接口兼容性与验证证据

<b>中文</b> | <a href="harness-interfaces.md">English</a> · <a href="README.md">返回文档索引</a>

本文档记录 [`harness-compatibility.json`](../harness-compatibility.json) 中声明的 Google Antigravity（`agy` `2.0.0 .. 2.99.99`）8 项受控宿主接口、代码实现路径与验证证据。

## 1. 登记接口矩阵

| 接口 ID | 宿主契约要求 | 实现路径 | 验证测试 |
|---|---|---|---|
| `plugin.manifest` | `plugins/<name>/plugin.json`、**插件根目录** `hooks.json`（禁止放在 `hooks/hooks.json`）、`mcp_config.json` | `plugins/mindie-agent/plugin.json` | `tests/test_agy_plugin.py::test_plugin_manifest_and_root_hooks_layout` |
| `hooks.session-start` | `stdin` 传入 `conversationId` 与 `workspacePaths`；被动静默返回 `{}` | `plugins/mindie-agent/scripts/bridge.py:handle_session_start` | `tests/test_agy_lifecycle.py::test_passive_session_start_and_ordinary_pre_invocation_stay_quiet` |
| `hooks.pre-invocation` | `stdin` 传入 `lastUserInput`；校验 JSON 载荷并作为零 I/O 直通返回 `{}`（`SKILL.md` 配置 `disable-slash-command: true`） | `plugins/mindie-agent/scripts/bridge.py:handle_pre_invocation` | `tests/test_agy_lifecycle.py::test_pre_invocation_is_silent_passthrough_and_skill_hides_slash_command` |
| `hooks.pre-tool-use` | `stdin` 传入 `toolCall`（`mcp_<server>_<tool>`）与 `stepIdx`；签发 120s 单次绑定令牌并返回 `{"decision": "allow"}` | `plugins/mindie-agent/scripts/bridge.py:handle_pre_tool_use` | `tests/test_agy_lifecycle.py::test_pre_tool_use_binds_mcp_call_and_rejects_replay` |
| `hooks.stop` | `stdin` 传入 `transcriptPath`；**绝不返回 `{"decision": "continue"}`**，在社区共享开启且工作区在授权范围内时触发增量捕获 | `plugins/mindie-agent/scripts/bridge.py:handle_stop` | `tests/test_agy_lifecycle.py::test_stop_never_forces_continuation_and_captures_when_sharing_enabled` |
| `mcp.stdio` | `mindie-remote-dev` 与 `mindie-tools`（含默认开放的 `knowledge_*` 工具）常驻并发 stdio MCP 服务 | `plugins/mindie-agent/scripts/mcp_server.py:serve` | `tests/test_agy_mcp_and_knowledge.py::test_mcp_tools_exposes_default_enabled_knowledge_and_remote_catalogs` |
| `transcript.brain-jsonl` | 解析 `<appDataDir>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl` | `plugins/mindie-agent/scripts/agy_transcript.py:read_material` | `tests/test_agy_transcript.py::test_transcript_extracts_public_messages_and_excludes_thinking_and_metadata` |
| `summary.structured-output` | 隔离 Worker 输出 `mindie-summary-outcome/1` 并支持 `--identity` 指纹校验 | `plugins/mindie-agent/scripts/organizer.py:main` | `tests/test_agy_organizer.py::test_organizer_emits_valid_summary_outcome_and_never_retries_unknown` |

## 2. 子代理（Subagent）会话隔离边界

在 Google Antigravity 中，通过 `invoke_subagent` 启动的子代理拥有独立的 UUID `conversationId`。`Admission` 表严格以 `session`（即 `conversationId`）为主键：
- 单个会话的默认租约创建（`ensure_default_lease`）仅作用于当前会话自身的 `conversationId`；
- 子代理与并行会话保持独立的 `conversationId` 作用域，绝不共享 `PreToolUse` 单次绑定令牌或 `Stop` 增量采集游标。
