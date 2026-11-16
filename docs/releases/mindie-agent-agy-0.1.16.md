# mindie-agent-agy 0.1.16：存储生命周期治理与组件版本锁定

[English](mindie-agent-agy-0.1.16.en.md)

- **组件更新互斥与切换后两阶段安全退役（`component_update.py`、`setup.py`、新增 `storage_lifecycle.py`）**：`component_update.check()` 在跨进程互斥锁 `<data_home>/agy-component-update.lock` 下执行构建与切换，并发调用有界等待后返回诚实的 `status="busy"`，不再并发踩踏同一代际；`agy.json` / `agy.engine.json` 原子切换完成后，在独立且有界的子预算内调用 `mindie_knowledge.storage.prune_component_runtimes` 与 `sweep_legacy_installations`，静默回收超量 `component-runtimes` 代际、半途失败 staging 与多代 `agy-source`，保留当前代、最近回滚代、跨插件借用解释器、活租约/进程，外部用户 Git checkout 作为 forbidden root 严格保护。回收遇到占用或 `PermissionError` 时更新仍为 `status="updated"`，只追加 `cleanup_warning`。
- **常驻 MCP 运行租约（`mcp_server.py`）**：常驻 stdio 服务对 `owning_dir(sys.executable)` 指向的受管代目录持有 `acquire_runtime_lease` 共享租约，贯穿整个服务生命周期；并发退役无法在解释器服务期间移走该代目录，缺失或只读代目录降级为无租约而不阻断启动。
- **避免重复构建基线 `agy-venv`（`setup.py`）**：当 profile 记录的 `current_python` 仍可导入运行时 API 时，直接在该解释器内执行组件升级；仅在没有可用解释器时冷启动构建 `agy-venv`，且未显式设置 `UV_CACHE_DIR` 时把缓存固定到同卷 `<data_home>/.uv-cache`，避免跨卷全量复制。
- **测试隔离修正（`tests/test_runtime_upgrade.py`）**：移除在 `ROOT.parent` 创建 `mindie-upgrade-*` 临时目录的污染路径，改用 `tmp_path` 并保留确定性清理与父目录断言；新增 `tests/test_storage_lifecycle_agy.py` 覆盖多代回收、并发互斥、真实子进程租约保护与 NTFS `PermissionError` 零半删退避。
- **组件版本锁定**：`mindie-knowledge` 0.13.22、`remote-dev` 0.11.12、`mindie-diagnostics` 0.5.15、`mindie-coordinator` 0.7.11、`mindie-debugger` 0.1.3、`mindie-profiler` 0.5.15、`mindie-sim` 0.9.15、`npu-top` 0.3.4（`pyproject.toml` 的 `[tool.uv.sources]` / `override-dependencies` 锁定正式提交，`uv.lock` 同步更新）。
