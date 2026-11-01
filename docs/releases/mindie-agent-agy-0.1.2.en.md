# MindIE Agent for Google Antigravity `v0.1.2` Release Notes

<a href="mindie-agent-agy-0.1.2.md">中文</a> | <b>English</b> · <a href="../README.en.md">Back to Documentation Index</a>

`v0.1.2` synchronizes `mindie-agent-agy` with `mindie-sim 0.9.0` (`f5608b4943f515c401d2f9b12fffb579a1e2571b`) and `mindie-profiler 0.5.5` (`7cdd56b6cc9e0e7f603368393497487dd2dbfe39`) alongside `knowledge 0.13.8`, `remote-dev 0.11.2`, `coordinator 0.7.1`, `npu-top 0.3.0`, and `diagnostics 0.5.4`.

## Key Highlights

1. **`mindie-sim 0.9.0` & `mindie-profiler 0.5.5` Alignment and `sim` MCP Guidance**:
   - Advances `product-contract.json` and `pyproject.toml` component pins to `mindie-sim 0.9.0` and `mindie-profiler 0.5.5`, and updates the `sim` MCP tool description with direct catalog/HF composition examples (`predict`, `efficiency-breakdown`, `sla-frontier`, `pd-disaggregation-solver`, `compose`), `async_scheduling` delayed HBM release, and explicit case-file invocation.
2. **Enterprise-Network Installation & Background Component Update Hardening**:
   - `setup.py` configures enterprise system TLS certificates (`UV_SYSTEM_CERTS=true` / `UV_NATIVE_TLS=true`) and LF source baseline staging across macOS, Linux, and Windows; `mcp_server.py` and `bridge.py` support non-blocking background component update checks.
3. **Receipt Honesty & Timeout Arithmetic**:
   - `mcp_server.py` preserves succeeded remote receipts (`receipt_unconfirmed`) and enforces `660s >= 600s + 60s` timeout arithmetic; `organizer.py` marks post-transmission timeouts as `outcome_unknown` without replaying billed model calls.
