# mindie-agent-agy 0.1.22：统一委托 `mindie-harness v0.1.8` 9 域知识编排、清除影子回退与三平台原生协议门禁

[English](mindie-agent-agy-0.1.22.en.md)

- **清除本地知识影子回退（纯委托 `mindie-harness v0.1.8`）**：删除 `scripts/bridge.py` 中的 `rpc_knowledge` 与 `scripts/mcp_server.py` 中的 `knowledge_unavailable` 本地旁路分发（`mcp_server.py` 精简至 `43` 行，全部生产脚本合计 `792` 行 `< 800` 行），5 个 `knowledge_*` 工具统一由 `mindie-harness v0.1.8` 的正式 `mindie_knowledge.loop` 接口分发。
- **单锁 9 域内容寻址引擎编排与 `Stop` 语义对齐**：`setup.py` 与 `bridge.py` 统一在单把宿主锁下调用 `ensure_knowledge_binding(..., already_locked=True)` 注册全部 9 大公开知识域并完整保留 `product_validation` / `component_release` 元数据；无原生执行 ID 时 `Stop` 使用 `identity_kind="notification"` 并委托 `run_stop_capture` 严格保护显式撤销（`inactive`）状态。冻结 `hooks.json` 字节摘要保持不变。
- **三平台原生协议 CI 门禁与组件对齐**：更新 `mindie-harness-seed.pyz` / `seed-lock.json` 与 9 大共享运行时组件锁，并接入 `mindie-bench/.github/workflows/verify-host-plugin.yml` 三平台原生 Manifest/Hook/升级验收门禁。
