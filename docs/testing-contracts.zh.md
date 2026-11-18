# 测试边界与契约校验

<b>中文</b> | <a href="testing-contracts.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 测试套件是快速、隔离的契约门禁（`tests/conftest.py` 将 `HOME`、`MINDIE_AGENT_CONFIG`、`MINDIE_CONFIG_HOME`、`MINDIE_DATA_HOME`、`MINDIE_DIAGNOSTICS_ROOT`、`REMOTE_DEV_AUTH_DIR`、`UV_CACHE_DIR` 与 `TMPDIR` 全部重定向到 `tmp_path`）。

## 契约测试矩阵

| 测试文件 | 验证契约与不变量 |
|---|---|
| [`tests/test_agy_plugin.py`](../tests/test_agy_plugin.py) | 冻结 `hooks.json`（`979` 字节 / 精确 SHA-256）、`mcp_config.json`（`REMOTE_DEV_HARNESS_PROFILE == "agy"`、干净脚本参数）、`verify_seed_bundle`、`harness-compatibility.json` AST 解析、4 段脚本头、单文件 `< 800` 行与全量 `<= 1,050` 行预算 |
| [`tests/test_agy_lifecycle.py`](../tests/test_agy_lifecycle.py) | SessionStart 标记、零 I/O `PreInvocation`、零 `_meta` `PreToolUse` allow/deny、冷/禁用/范围外 `Stop` 零 SQLite、`local-only` / `contribute` 捕获与显式撤销保持 |
| [`tests/test_agy_mcp_and_knowledge.py`](../tests/test_agy_mcp_and_knowledge.py) | 12 个 `agy` 原生 remote 工具、组件 + `mindie_doctor` + 5 个 `knowledge_*` 工具、零 `_meta` catalog/调用、真实初始化域 `knowledge_query`，以及 `Admission.finish` 抛 `OSError` 时的 Receipt Honesty |
| [`tests/test_agy_transcript.py`](../tests/test_agy_transcript.py) | 自动提升至 `transcript_full.jsonl`、剥离 `thinking` / `tool_calls` / `<ADDITIONAL_METADATA>`、时间边界与增量读取 |
| [`tests/test_runtime_upgrade.py`](../tests/test_runtime_upgrade.py) | 只读 `--help` / `--dry-run` / `--dry`、`init`、sharing `--status` / `--enable` / `--local-only` / `--disable`、`release-metadata`，以及 `mindie_harness` 代际激活/重绑定 |
