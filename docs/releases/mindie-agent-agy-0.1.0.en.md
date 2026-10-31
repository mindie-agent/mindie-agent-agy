# MindIE Agent for Google Antigravity `v0.1.0` Release Notes

<a href="mindie-agent-agy-0.1.0.md">中文</a> | <b>English</b> · <a href="../README.en.md">Back to Documentation Index</a>

`v0.1.0` is the initial release of `mindie-agent-agy`, the native Google Antigravity plugin for MindIE Agent `v0.1` (`v0.1.5`), integrating all 7 shared runtime components (`knowledge 0.13.7`, `remote-dev 0.11.2`, `coordinator 0.7.0`, `profiler 0.5.3`, `sim 0.8.1`, `npu-top 0.3.0`, and `diagnostics 0.5.4`).

## Key Highlights

1. **Native Google Antigravity Plugin Bundle (`plugins/mindie-agent/`)**:
   - Ships `plugin.json`, plugin-root `hooks.json` (`SessionStart`, `PreInvocation`, `PreToolUse`, `Stop`), and `mcp_config.json` (`mindie-remote-dev`, `mindie-tools`), exposing `diagnostics_status`, `diagnostics_bundle`, `coordinator`, `profiler`, `sim`, and `npu_top` while propagating the verified `AGY_CONVERSATION_ID` to `coordinator`.
2. **Manual Session Gate & Private Knowledge Channel**:
   - `PreInvocation` activates a per-`conversationId` lease when the user types `/mindie-agent`; public MCP discovery excludes all `knowledge_*` tools while private `knowledge_cli.py` gates knowledge retrieval, explanation, feedback, C6 distillation, and sync.
3. **Antigravity Brain Transcript Capture & Redaction**:
   - `agy_transcript.py` automatically promotes `transcript.jsonl` to `transcript_full.jsonl` and strips `thinking`, `tool_calls`, and `<ADDITIONAL_METADATA>` before local deterministic + Gitleaks redaction.
4. **Receipt Honesty & Timeout Arithmetic**:
   - `mcp_server.py` preserves succeeded remote receipts (`receipt_unconfirmed`) and enforces `660s >= 600s + 60s` timeout arithmetic; `organizer.py` marks post-transmission timeouts as `outcome_unknown` without replaying billed model calls.
