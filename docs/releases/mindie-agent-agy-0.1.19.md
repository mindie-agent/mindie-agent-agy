# mindie-agent-agy 0.1.19：插件启用默认开放全量能力（含 `mindie-tools` 知识工具）与运行时 `SKILL.md` 安装边界收敛

[English](mindie-agent-agy-0.1.19.en.md)

- **插件启用即默认激活全量能力（消除手动 `/mindie-agent` 门禁错位）**：针对 Google Antigravity 插件整体启停且 `SKILL.md` 不支持禁用语义自动路由的特性，`mindie-tools` MCP 服务默认公开并直通 `knowledge_query`、`knowledge_explain`、`knowledge_feedback`、`knowledge_distill` 与 `knowledge_sync`；当用户显式开启社区贡献授权后，`Stop` Hook 默认对作用域内会话执行本地脱敏与增量采集（可通过 `/mindie-agent deactivate` 在单会话内静音知识与采集，通过 `/mindie-agent` 重新恢复），未配置或未开启共享时 `Stop` 保持纯标准库 `< 100ms` 零建库快速返回。
- **剥离安装引导文档，保持 `skills/mindie-agent/` 纯运行时语义**：将 `workspace-setup.md`、`github-authentication.md` 与 `user-notice.md` 迁移至仓库级 `docs/install/`，重写精简版纯运行时 `SKILL.md`（`< 35` 行），彻底移除首次安装引导、`setup.py sharing`、`vllm-ascend-workspace` 与 GitHub 登录/Star 文本，并保持冻结 `plugins/mindie-agent/hooks.json` 字节摘要完全不变。
