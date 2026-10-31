# Test Boundaries & Contracts

<a href="testing-contracts.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

The `mindie-agent-agy` test suite follows the `v0.0.2` tiered test budget (full Tier 0 + Tier 1 suite completes in `< 2s`) and strict host isolation (`tests/conftest.py` redirects `MINDIE_AGENT_CONFIG`, `MINDIE_DATA_HOME`, and `MINDIE_DIAGNOSTICS_ROOT` into `tmp_path`).

## Contract Test Matrix

| Test File | Verified Contracts & Invariants |
|---|---|
| [`tests/test_agy_plugin.py`](../tests/test_agy_plugin.py) | `plugin.json`, plugin-root `hooks.json`, `mcp_config.json`, `product-contract.json`, `harness-compatibility.json` mapping integrity, mandatory 4-section script headers, `< 800` lines limit, and Unix `LF` line endings |
| [`tests/test_agy_lifecycle.py`](../tests/test_agy_lifecycle.py) | Passive `SessionStart` and ordinary `PreInvocation`, explicit `/mindie-agent` activation/deactivation, cross-`conversationId` isolation, `PreToolUse` one-shot bind replay rejection, and `Stop` never returning `{"decision": "continue"}` |
| [`tests/test_agy_mcp_and_knowledge.py`](../tests/test_agy_mcp_and_knowledge.py) | Public MCP catalogs excluding `knowledge_*`, private `knowledge_cli.py` lease gating, Receipt Honesty on local SQLite failure, and `660s >= 600s + 60s` timeout arithmetic |
| [`tests/test_agy_transcript.py`](../tests/test_agy_transcript.py) | Automatic promotion from `transcript.jsonl` to `transcript_full.jsonl`, stripping `thinking` / `tool_calls` / `<ADDITIONAL_METADATA>`, activation timestamp boundary, and incremental reads |
| [`tests/test_agy_organizer.py`](../tests/test_agy_organizer.py) | `organizer.identity()` validation against `summarizer.validate_identity`, `mindie-summary-outcome/1` emission, and post-transmission timeout marked as `outcome_unknown` |
