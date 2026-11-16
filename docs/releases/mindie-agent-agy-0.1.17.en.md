# mindie-agent-agy 0.1.17

- **One-shot RPC receipt reservation & stable connection identity (`P2.7`)**: Reserves one-shot RPC receipts before claiming `PreToolUse` binds on `remote` and `tools` surfaces so replayed frames cannot consume subsequent binds, propagates `MINDIE_MCP_CONNECTION_ID` across `serve_selected` helpers, and keeps deterministic tools free of pre-call session gates.
- **Skill and guide polish**: Aligns `SKILL.md` and `AGENTS.md` with the 5 Iron Rules.
