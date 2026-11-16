# mindie-agent-agy 0.1.17

- **确定性表面收据防重放与连接标识传递（`P2.7`）**：在 `remote` 与 `tools` 表面解析 `PreToolUse` 绑定前先行原子占位一次性 RPC 收据（防止重放帧误消耗后续新绑定），跨 `serve_selected` 子进程传递稳定 `MINDIE_MCP_CONNECTION_ID`，并保持确定性工具零前置会话门禁。
- **文档与技能润色**：对齐 `SKILL.md` 与 `AGENTS.md` 至五大铁律。
