# MindIE Agent for Google Antigravity · 技术文档索引

<b>中文</b> | <a href="README.en.md">English</a> · <a href="../README.md">返回仓库首页</a>

本目录汇总 `mindie-agent-agy`（Google Antigravity 原生插件）的宿主接口兼容性验证、公开对话采集脱敏、服务身份与授权连续性、故障诊断与超时算术、框架稳定性及测试契约文档。所有文档均提供结构对称的中英双语版本。

## 核心文档列表

| 中文文档 | English | 内容概要 |
|---|---|---|
| [Antigravity Harness 接口兼容性证据](harness-interfaces.zh.md) | [Harness Interface Evidence](harness-interfaces.md) | `harness-compatibility.json` 登记的 7 项接口、插件根目录 `hooks.json` 规范、`PreInvocation` 零 I/O 直通、`PreToolUse` 零 `_meta` 校验与子代理 `conversationId` 隔离 |
| [公开对话采集与本地脱敏](public-transcript.zh.md) | [Public Transcript Capture](public-transcript.md) | Brain `transcript.jsonl` 自动提升至 `transcript_full.jsonl`、`thinking` / `tool_calls` / `<ADDITIONAL_METADATA>` 剥离与隔离摘要生成 |
| [服务身份与统一授权连续性](service-continuity.zh.md) | [Service Identity & Consent Continuity](service-continuity.md) | `agy.json` / `agy.engine.json` 绑定、`mindie-community-config/1` 与 `mindie-consent/1` 跨会话持久化 |
| [故障诊断、超时算术与回执诚实性](failure-diagnostics.zh.md) | [Failure Diagnostics & Receipt Honesty](failure-diagnostics.md) | `receipt_unconfirmed` 远端/知识回执保护、`outcome_unknown` 防重放、`mindie_doctor` 只读就绪检查与有界进程树回收 |
| [框架稳定性与持久化配置](framework-stability.zh.md) | [Framework Stability](framework-stability.md) | 被动默认静默保障、`mindie-harness` 薄委托、跨平台原子文件替换与配置隔离 |
| [测试边界与契约校验](testing-contracts.zh.md) | [Test Boundaries & Contracts](testing-contracts.md) | Tier-0 契约测试矩阵、5 脚本行数门禁、`harness-compatibility.json` AST 映射校验与 LF 格式门禁 |
| [工作区配置指南](install/workspace-setup.md) / [GitHub 认证](install/github-authentication.md) / [用户须知](install/user-notice.md) | [Workspace Setup](install/workspace-setup.md) / [GitHub Sign-In](install/github-authentication.md) / [User Notice](install/user-notice.md) | 一句话安装、3 问动态 onboarding、`gh` 可选认证与社区共享/脱敏数据边界说明 |
| [Release v0.1.22 发布说明](releases/mindie-agent-agy-0.1.22.md) | [Release v0.1.22 Notes](releases/mindie-agent-agy-0.1.22.en.md) | `v0.1.22` 5 脚本薄插件、`mindie-harness v0.1.3` 种子握手、12 工具 `agy` remote-dev、`mindie_doctor` 与一句话安装 |
