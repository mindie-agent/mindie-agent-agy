# mindie-agent-agy 0.1.22: Unified `mindie-harness v0.1.7` 9-Domain Provisioning, Shadow-Fallback Removal & 3-OS Wire Gate

[中文](mindie-agent-agy-0.1.22.md)

- **Removed Plugin-Local Knowledge Shadow Fallback**: Deleted `rpc_knowledge` in `scripts/bridge.py` and the `knowledge_unavailable` override in `scripts/mcp_server.py` (`43` lines; total production Python `792` lines `< 800`), delegating all 5 `knowledge_*` MCP tools strictly to `mindie-harness v0.1.7`.
- **Single-Lock 9-Domain Provisioning & `Stop` Notification Envelope**: `setup.py` and `bridge.py` invoke `ensure_knowledge_binding(..., already_locked=True)` to register all 9 public corpora while preserving `product_validation` and `component_release` metadata; `Stop` emits `identity_kind="notification"` when native execution IDs are absent and delegates to `run_stop_capture` with permanent `inactive` revocation enforcement. Byte-frozen `hooks.json` remains unchanged.
- **3-OS Native Wire CI Gate & Component Pin Sync**: Refreshed `mindie-harness-seed.pyz` / `seed-lock.json` and 9-package runtime pins, and wired `mindie-bench/.github/workflows/verify-host-plugin.yml` across Ubuntu, macOS, and Windows.
