# mindie-agent-agy 0.1.18：接入 `mindie-harness` 共享底座、并发双帧常驻 MCP 与 `0.1.13` 组件基线对齐

[English](mindie-agent-agy-0.1.18.en.md)

- **接入 `mindie-harness v0.1.0` 共享底座与并发常驻 MCP 网关**：打包并流式校验 `plugins/mindie-agent/assets/mindie-harness-seed.pyz` + `seed-lock.json`（`mindie-harness-seed/1`），复用 `mindie_harness` 并发双帧（`Content-Length` + JSONL）stdio 服务、安全符号链接开发挂载（`AD-07`）与单状态源 RSI 闭环，保持冻结 `plugins/mindie-agent/hooks.json` 字节摘要完全不变。
- **单次 WAL 初始化与干净环境隔离**：`RemoteReceipts` 在锁内单次完成 SQLite WAL + `busy_timeout=5000` 初始化，并清理导入期残留 `REMOTE_DEV_SESSION_ID` 副作用，全面贯彻 `P2.2` 会话隔离与 `P2.7` 确定性工具零绑定直通。
- **`0.1.13` 全量组件正式版锁定**：在 `pyproject.toml`、`uv.lock` 与 `product-contract.json` 中同步锁定 `mindie-harness v0.1.0`、`mindie-knowledge v0.13.23`、`remote-dev v0.11.13`、`mindie-diagnostics v0.5.16`、`mindie-debugger v0.1.4`、`mindie-profiler v0.5.16`、`mindie-sim v0.9.18`、`mindie-coordinator v0.7.13` 与 `npu-top v0.3.5`，并启用 `--profile host-plugin` PR 门禁。
