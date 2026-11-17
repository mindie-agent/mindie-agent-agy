## 变更摘要 (Summary)

-

## 设计原则 Review (Pre-PR Checklist)

> 对照 `AGENTS.md` 与 `host-plugin` 仓契约完成核验（不适用项须填写 `not-applicable: <具体原因>`）：

- [ ] **HOST-NATIVE** — 原生宿主配置与钩子契约保持兼容（`plugins/mindie-agent/hooks.json` 字节级冻结 `sha256 == 33b564c78b439205fd4acfe095c83b15770043ff462d636e2a89f14620cdfc3f`，绝不篡改宿主 `~/.gemini` 目录，Windows 子进程零黑框弹窗 `CREATE_NO_WINDOW`）。
- [ ] **HOST-WIRE** — 常驻并发 stdio MCP（同时兼容 `Content-Length` 与 JSONL 帧、读写/控制快慢池隔离、零 `--once` 冷启动锁阻塞）及超时预算算术（`HOOK_TOTAL >= HOOK_EXEC_BUDGET + CLEANUP_GRACE`，未配置/未开启共享 `Stop` `< 100ms` 返回 `{}`）已验证。
- [ ] **HOST-ADMISSION** — 全量 MCP 工具满足零预绑定门禁（`P2.7`：无 `_meta` / `PreToolUse` / `AGY_CONVERSATION_ID` 亦可直接调用 `mindie-remote-dev` 与 `mindie-tools` 含默认开放的 `knowledge_*`，`/mindie-agent deactivate` 支持单会话静音），且远端/组件/知识调用成功后本地记账失败保持 `isError=False` + `receipt_warning` + `receipt_unconfirmed=True`（Principle 14）。
- [ ] **HOST-UPGRADE** — 种子包校验（`assets/seed-lock.json` 流式 SHA-256）、`agy.json` 原子切换、`.inuse` 运行时租约保护及历史版本平滑升级兼容性已验证，全仓文本保持 Unix `LF`（`\n`）且单模块 `< 800` 行。

## 自查证据 (Evidence)

- **HOST-NATIVE**:
- **HOST-WIRE**:
- **HOST-ADMISSION**:
- **HOST-UPGRADE**:

## 验证结果 (Verification)

-
