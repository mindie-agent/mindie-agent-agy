# Antigravity Harness 接口兼容性与验证证据

<b>中文</b> | <a href="harness-interfaces.md">English</a> · <a href="README.md">返回文档索引</a>

本文档记录 [`harness-compatibility.json`](../harness-compatibility.json) 中声明的 Google Antigravity（`agy` `2.0.0 .. 2.99.99`）8 项受控宿主接口、代码实现路径与验证证据。

## 1. 登记接口矩阵

| 接口 ID | 宿主契约要求 | 实现路径 | 验证测试 |
|---|---|---|---|
| `plugin.manifest` | `plugins/<name>/plugin.json`、**插件根目录** `hooks.json`（禁止放在 `hooks/hooks.json`）、`mcp_config.json` | `plugins/mindie-agent/plugin.json` | `tests/test_agy_plugin.py::test_plugin_manifest_and_root_hooks_layout` |
| `hooks.session-start` | `stdin` 传入 `conversationId` 与 `workspacePaths`；被动静默返回 `{}` | `plugins/mindie-agent/scripts/bridge.py:handle_session_start` | `tests/test_agy_lifecycle.py::test_passive_session_start_and_ordinary_pre_invocation_stay_quiet` |
| `hooks.pre-invocation` | `stdin` 传入 `lastUserInput`；识别 `/mindie-agent` 时登记会话租约并返回 `{"injectSteps": [{"ephemeralMessage": "..."}]}` | `plugins/mindie-agent/scripts/bridge.py:handle_pre_invocation` | `tests/test_agy_lifecycle.py::test_explicit_mindie_agent_gesture_activates_and_deactivates_lease` |
| `hooks.pre-tool-use` | `stdin` 传入 `toolCall`（`mcp_<server>_<tool>`）与 `stepIdx`；签发 30s 单次绑定令牌并返回 `{"decision": "allow"}` | `plugins/mindie-agent/scripts/bridge.py:handle_pre_tool_use` | `tests/test_agy_lifecycle.py::test_pre_tool_use_binds_mcp_call_and_rejects_replay` |
| `hooks.stop` | `stdin` 传入 `transcriptPath`；**永远返回 `{}`**（绝不返回 `{"decision": "continue"}`），仅在会话已激活且共享开启时触发增量捕获 | `plugins/mindie-agent/scripts/bridge.py:handle_stop` | `tests/test_agy_lifecycle.py::test_stop_never_forces_continuation_and_requires_manual_lease` |
| `mcp.stdio` | `mindie-remote-dev` 与 `mindie-tools` 常驻 stdio MCP 服务；排除所有 `knowledge_*` 工具 | `plugins/mindie-agent/scripts/mcp_server.py:serve` | `tests/test_agy_mcp_and_knowledge.py::test_public_mcp_excludes_knowledge_even_when_session_is_active` |
| `transcript.brain-jsonl` | 解析 `<appDataDir>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl` | `plugins/mindie-agent/scripts/agy_transcript.py:read_material` | `tests/test_agy_transcript.py::test_transcript_extracts_public_messages_and_excludes_thinking_and_metadata` |
| `summary.structured-output` | 隔离 Worker 输出 `mindie-summary-outcome/1` 并支持 `--identity` 指纹校验 | `plugins/mindie-agent/scripts/organizer.py:main` | `tests/test_agy_organizer.py::test_organizer_emits_valid_summary_outcome_and_never_retries_unknown` |

## 2. 子代理（Subagent）会话隔离边界

在 Google Antigravity 中，通过 `invoke_subagent` 启动的子代理拥有独立的 UUID `conversationId`。`Admission` 表以 `session`（即 `conversationId`）为主键：
- 父会话输入 `/mindie-agent` 仅激活父会话自身的 `conversationId`；
- 子代理不会隐式继承父会话的知识租约，也不会在 `Stop` 阶段触发未经授权的对话采集。
