# MindIE Agent for Google Antigravity `v0.1.0` 发布说明

<b>中文</b> | <a href="mindie-agent-agy-0.1.0.en.md">English</a> · <a href="../README.md">返回文档索引</a>

`v0.1.0` 是 `mindie-agent-agy`（Google Antigravity 原生插件）的首个正式版本，完整接入 MindIE Agent `v0.0.2` 架构规范与共享组件生态。

## 核心交付特性

1. **Google Antigravity 原生插件包（`plugins/mindie-agent/`）**：
   - 提供原生 `plugin.json`、插件根目录 `hooks.json`（`SessionStart`、`PreInvocation`、`PreToolUse`、`Stop`）与 `mcp_config.json`（`mindie-remote-dev`、`mindie-tools`）。
2. **手动会话门控与私有知识通道**：
   - `PreInvocation` 识别 `/mindie-agent` 显式指令，为当前 `conversationId` 签发会话租约；公开 MCP 目录严格排除 `knowledge_*`，由私有 `knowledge_cli.py` 提供受租约保护的知识检索、解释、反馈、C6 蒸馏与同步。
3. **Antigravity Brain Transcript 采集与脱敏**：
   - `agy_transcript.py` 自动从 `transcript.jsonl` 提升至 `transcript_full.jsonl`，严格剔除 `thinking`、`tool_calls` 与 `<ADDITIONAL_METADATA>`，配合 `mindie-knowledge` 与 Gitleaks 完成本地脱敏与分块索引。
4. **回执诚实性（Receipt Honesty）与超时算术**：
   - `mcp_server.py` 保护已成功的远端副作用回执，满足 `660s >= 600s + 60s` 超时算术；`organizer.py` 防止对 `outcome_unknown` 的计费模型调用自动重放。
