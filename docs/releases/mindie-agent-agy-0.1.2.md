# MindIE Agent for Google Antigravity `v0.1.2` 发布说明

<b>中文</b> | <a href="mindie-agent-agy-0.1.2.en.md">English</a> · <a href="../README.md">返回文档索引</a>

`v0.1.2` 将 `mindie-agent-agy` 同步至共享组件 `mindie-sim 0.9.0`（`f5608b4943f515c401d2f9b12fffb579a1e2571b`）与 `mindie-profiler 0.5.5`（`7cdd56b6cc9e0e7f603368393497487dd2dbfe39`），配套支持 `knowledge 0.13.8`、`remote-dev 0.11.2`、`coordinator 0.7.1`、`npu-top 0.3.0` 与 `diagnostics 0.5.4`。

## 核心交付特性

1. **`mindie-sim 0.9.0` 与 `mindie-profiler 0.5.5` 同步及 `sim` MCP 工具引导**：
   - 更新 `product-contract.json` 与 `pyproject.toml` 中的组件基线锁定至 `mindie-sim 0.9.0` 与 `mindie-profiler 0.5.5`，并在 `mindie-tools` 的 `sim` 工具描述中补充目录/HF 模型直接组合（`predict`、`efficiency-breakdown`、`sla-frontier`、`pd-disaggregation-solver`、`compose`）、`async_scheduling` 延迟显存释放以及显式 `case.json` 调用范式。
2. **企业网络安装与后台组件热更新加固**：
   - `setup.py` 自动注入企业代理系统证书（`UV_SYSTEM_CERTS=true` / `UV_NATIVE_TLS=true`）与 Windows 长路径/LF 源码基线暂存；`mcp_server.py` 与 `bridge.py` 支持非阻塞后台组件更新检查。
3. **回执诚实性（Receipt Honesty）与超时算术**：
   - `mcp_server.py` 保护已成功的远端副作用回执，满足 `660s >= 600s + 60s` 超时算术；`organizer.py` 防止对 `outcome_unknown` 的计费模型调用自动重放。
