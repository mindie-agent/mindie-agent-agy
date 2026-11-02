# MindIE Agent for Google Antigravity `v0.1.3` 发布说明

<b>中文</b> | <a href="mindie-agent-agy-0.1.3.en.md">English</a> · <a href="../README.md">返回文档索引</a>

`v0.1.3` 将 `mindie-agent-agy` 升级至最新昇腾 AI Infra 共享组件基线（`knowledge 0.13.10`、`remote-dev 0.11.4`、`diagnostics 0.5.5`、`debugger 0.1.0`、`profiler 0.5.6`、`sim 0.9.2`、`coordinator 0.7.3` 与 `npu-top 0.3.1`），放宽 Antigravity 宿主版本前向兼容区间，并接入标准化的 GitHub Release 发布与 CPU 微基准门禁。

## 更新亮点（人话版）

1. **宿主版本前向兼容与远端 CANN 日志排障体验提升**：
   - `harness-compatibility.json` 支持 `2.0.0..2.99.99`（含预发布版本 `include_prerelease: true`），Antigravity 客户端日常升级不会导致插件或 MCP 工具失效。
   - `mindie-tools` MCP 服务中的 `debugger` 工具正式纳入 `pyproject.toml` 与 `product-contract.json` 组件契约，并补充远端 NPU 容器 `plog` 与 ACLGraph 故障排查指引（`remote-guide` + `remote-dev`）。
2. **全量共享组件同步升级（含昇腾 950PR / A5 仿真校准）**：
   - `mindie-knowledge 0.13.10`（`7936a4fda67f6808d8fa73824363a4d7345766e5`）
   - `remote-dev 0.11.4`（`7c67876d716feb3949e4dbf33eb5e06980ff0d36`）
   - `mindie-diagnostics 0.5.5`（`f2f2f946c172afc99342a84c0c1ed244f6d44e01`）
   - `mindie-debugger 0.1.0`（`e9406b2b178335190d5cb59dff4d6985894497ba`）
   - `mindie-profiler 0.5.6`（`79d97e75bff7351b0472b93c03b072c1cf47f5e7`）
   - `mindie-sim 0.9.2`（`ced2dba5741af2d98f3a75b2a9cad77b9b94c40d`，新增 Ascend 950PR / A5 硬件规格与超节点拓扑校准）
   - `mindie-coordinator 0.7.3`（`79a8b28eef72d57f965a01c6660849f024fbb1c0`）
   - `npu-top 0.3.1`（`8f0cb3ec66393812e90e118cd73704070c6027a2`）
3. **自动化发布流水线与微基准门禁**：
   - 新增 `.github/workflows/release.yml` 与 `benchmarks/release.json`，每次正式发版自动执行契约测试、生成 `SHA256SUMS` 与公开对话解析延迟微基准报告。
