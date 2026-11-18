# mindie-agent-agy 0.1.20：隐藏 `/` 斜杠菜单暴露、全静默默认生效与 macOS 共享 Runner 测试稳健化

[English](mindie-agent-agy-0.1.20.en.md)

- **隐藏斜杠命令并实现 100% 静默默认生效**：在 `plugins/mindie-agent/skills/mindie-agent/SKILL.md` Frontmatter 中启用 Google Antigravity 原生 `disable-slash-command: true` 标记（从用户 `/` 聊天菜单中隐藏技能入口，同时保持宿主按任务上下文自动语义加载 `SKILL.md`）；将 `mindie_launch.py` 与 `bridge.py` 的 `PreInvocation` 收敛为纯标准库零 I/O `{}` 直通，简化 `admission.py` / `knowledge_cli.py` / `mcp_server.py` 使插件启用期间全量工具与知识能力无感就绪，并保持冻结 `plugins/mindie-agent/hooks.json` 字节摘要（`33b564c7...`）及未开启共享/作用域外 `< 100ms` 零建库 `Stop` 快速路径不变。
- **消除共享 `macos-latest` CI Runner 时序抖动**：在 `test_zombie_in_progress_pid_is_reaped_and_allows_retry` 中改为等待内核进程状态进入真实 `Z`（Zombie）后再校验回收，并在 `< 100ms` 子进程/Hook 延迟测试中采用预热后 5 次采样的最小值（`min()`），消除共享虚拟化 Runner 调度抢占引发的单样本偶发抖动。
