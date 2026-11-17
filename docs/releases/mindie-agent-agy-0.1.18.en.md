# mindie-agent-agy 0.1.18: `mindie-harness` Shared Substrate, Concurrent Dual-Framing MCP, and `0.1.13` Component Baseline

[中文](mindie-agent-agy-0.1.18.md)

- **Shared `mindie-harness v0.1.0` Substrate & Concurrent Stdio MCP**: Ships streaming-verified `plugins/mindie-agent/assets/mindie-harness-seed.pyz` + `seed-lock.json` (`mindie-harness-seed/1`), adopts `mindie_harness` concurrent dual-framing (`Content-Length` + JSONL) MCP dispatch, symlinked dev-root support (`AD-07`), and consolidated RSI helpers while preserving the frozen `plugins/mindie-agent/hooks.json` SHA-256 digest.
- **Single-Open SQLite WAL & Clean Session Isolation**: Initializes `RemoteReceipts` SQLite WAL + `busy_timeout=5000` once under lock and scrubs import-time `REMOTE_DEV_SESSION_ID` leakage, upholding `P2.2` per-conversation isolation alongside `P2.7` zero-binding deterministic tool access.
- **`0.1.13` Formal Component Baseline**: Pins `mindie-harness v0.1.0`, `mindie-knowledge v0.13.23`, `remote-dev v0.11.13`, `mindie-diagnostics v0.5.16`, `mindie-debugger v0.1.4`, `mindie-profiler v0.5.16`, `mindie-sim v0.9.18`, `mindie-coordinator v0.7.13`, and `npu-top v0.3.5` across `pyproject.toml`, `uv.lock`, and `product-contract.json`, aligned with `--profile host-plugin` governance.
