# Antigravity Harness 接口兼容性与验证证据

<b>中文</b> | <a href="harness-interfaces.md">English</a> · <a href="README.md">返回文档索引</a>

本文档记录 [`harness-compatibility.json`](../harness-compatibility.json) 中声明的 Google Antigravity（`agy` `2.0.0 .. 2.99.99`）7 项受控宿主接口、代码实现路径与验证证据。

## 1. 登记接口矩阵

| 接口 ID | 宿主契约要求 | 实现路径 | 验证测试 |
|---|---|---|---|
| `plugin.manifest` | `plugins/<name>/plugin.json`、**插件根目录** `hooks.json`（禁止放在 `hooks/hooks.json`）、`mcp_config.json` | `plugins/mindie-agent/plugin.json` | `tests/test_agy_plugin.py::test_plugin_manifest_and_root_hooks_layout` |
| `hooks.session-start` | `stdin` 传入 `conversationId` 与 `workspacePaths`；被动返回 `{}` 并原子写入更新检查标记 | `plugins/mindie-agent/scripts/bridge.py:handle_session_start` | `tests/test_agy_lifecycle.py::test_session_start_writes_pending_marker_then_stays_quiet` |
| `hooks.pre-invocation` | `stdin` 传入 `lastUserInput`；校验 JSON 对象并作为零 I/O 直通返回 `{}`（`SKILL.md` 配置 `disable-slash-command: true`） | `plugins/mindie-agent/scripts/bridge.py:handle_pre_invocation` | `tests/test_agy_lifecycle.py::test_pre_invocation_is_silent_passthrough` |
| `hooks.pre-tool-use` | `stdin` 传入 `toolCall`；零 `_meta` 绑定返回 `{"decision": "allow"}`，并拒绝伪造身份字段 | `plugins/mindie-agent/scripts/bridge.py:handle_pre_tool_use` | `tests/test_agy_lifecycle.py::test_pre_tool_use_allows_zero_meta_and_denies_forged_identity` |
| `hooks.stop` | `stdin` 传入 `transcriptPath` / `appDataDir`；**绝不返回 `{"decision": "continue"}`**，仅在已授权的 `contribute` / `local-only` 工作区捕获，并保持显式撤销 | `plugins/mindie-agent/scripts/bridge.py:handle_stop` | `tests/test_agy_lifecycle.py::test_stop_never_forces_continuation_and_captures_only_in_scope` |
| `mcp.stdio` | `mindie-remote-dev`（12 个 `agy` 原生工具）与 `mindie-tools`（组件工具 + `mindie_doctor` + `knowledge_*`）常驻并发 stdio MCP 服务 | `plugins/mindie-agent/scripts/mcp_server.py:serve` | `tests/test_agy_mcp_and_knowledge.py::test_agy_remote_and_tools_catalogs_require_zero_meta` |
| `transcript.brain-jsonl` | 解析 `<appDataDir>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl` | `plugins/mindie-agent/scripts/agy_transcript.py:read_material` | `tests/test_agy_transcript.py::test_transcript_extracts_public_messages_and_excludes_thinking_and_metadata` |

## 2. 子代理（Subagent）会话隔离边界

在 Google Antigravity 中，通过 `invoke_subagent` 启动的子代理拥有独立的 UUID `conversationId`。`Admission` 表严格以 `session`（即 `conversationId`）为主键：
- 单个会话的 `Admission.activate` 仅作用于当前会话自身的 `conversationId`；
- 子代理与并行会话保持独立的 `conversationId` 作用域，绝不共享 `Stop` 增量采集游标；
- 标准 MCP 调用无需 `PreToolUse` 单次绑定（`P2.7`），因此不存在跨会话泄漏的单次绑定令牌。
