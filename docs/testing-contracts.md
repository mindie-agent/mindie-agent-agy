# Test Boundaries & Contracts

<a href="testing-contracts.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

The `mindie-agent-agy` suite is a fast, isolated contract gate (`tests/conftest.py` redirects `HOME`, `MINDIE_AGENT_CONFIG`, `MINDIE_CONFIG_HOME`, `MINDIE_DATA_HOME`, `MINDIE_DIAGNOSTICS_ROOT`, `REMOTE_DEV_AUTH_DIR`, `UV_CACHE_DIR`, and `TMPDIR` into `tmp_path`).

## Contract Test Matrix

| Test File | Verified Contracts & Invariants |
|---|---|
| [`tests/test_agy_plugin.py`](../tests/test_agy_plugin.py) | Frozen `hooks.json` (`979` bytes / exact SHA-256), `mcp_config.json` (`REMOTE_DEV_HARNESS_PROFILE == "agy"`, clean script args), `verify_seed_bundle`, `harness-compatibility.json` AST resolution, 4-section script headers, `< 800` lines per script, and a `<= 1,050` total line budget |
| [`tests/test_agy_lifecycle.py`](../tests/test_agy_lifecycle.py) | SessionStart marker, zero-I/O `PreInvocation`, zero-`_meta` `PreToolUse` allow/deny, cold/disabled/out-of-scope `Stop` with zero SQLite, `local-only` / `contribute` capture, and explicit revocation preservation |
| [`tests/test_agy_mcp_and_knowledge.py`](../tests/test_agy_mcp_and_knowledge.py) | 12 native `agy` remote tools, component + `mindie_doctor` + 5 `knowledge_*` tools, zero-`_meta` catalogs/calls, real initialized-domain `knowledge_query`, and Receipt Honesty when `Admission.finish` raises `OSError` |
| [`tests/test_agy_transcript.py`](../tests/test_agy_transcript.py) | Automatic promotion to `transcript_full.jsonl`, stripping `thinking` / `tool_calls` / `<ADDITIONAL_METADATA>`, timestamp boundary, and incremental reads |
| [`tests/test_runtime_upgrade.py`](../tests/test_runtime_upgrade.py) | Read-only `--help` / `--dry-run` / `--dry`, `init`, sharing `--status` / `--enable` / `--local-only` / `--disable`, `release-metadata`, and `mindie_harness` generation activation/rebinding |
