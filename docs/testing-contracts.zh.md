# 测试边界与契约校验

<b>中文</b> | <a href="testing-contracts.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 测试套件遵循 `v0.0.2` 分级测试预算（Tier 0 + Tier 1 全套在 `< 2s` 内完成）与宿主机零污染纪律（`tests/conftest.py` 将 `MINDIE_AGENT_CONFIG`、`MINDIE_DATA_HOME` 与 `MINDIE_DIAGNOSTICS_ROOT` 重定向至 `tmp_path`）。

## 测试矩阵

| 测试文件 | 验证的契约与不变量 |
|---|---|
| [`tests/test_agy_plugin.py`](../tests/test_agy_plugin.py) | `plugin.json`、根目录 `hooks.json`、`mcp_config.json`、`product-contract.json`、`harness-compatibility.json` 映射完整性、全脚本四段式文件头注释、`< 800` 行约束及 Unix `LF` 换行符 |
| [`tests/test_agy_lifecycle.py`](../tests/test_agy_lifecycle.py) | `SessionStart` 与普通 `PreInvocation` 静默、`/mindie-agent` 显式激活与停用、跨 `conversationId` 隔离、`PreToolUse` 单次绑定防重放、`Stop` 永不返回 `{"decision": "continue"}` |
| [`tests/test_agy_mcp_and_knowledge.py`](../tests/test_agy_mcp_and_knowledge.py) | 公开 MCP 服务排除 `knowledge_*`、私有 `knowledge_cli.py` 强制要求会话租约、本地 SQLite 异常下的 Receipt Honesty 保护、`660s >= 600s + 60s` 超时算术 |
| [`tests/test_agy_transcript.py`](../tests/test_agy_transcript.py) | `transcript.jsonl` 自动提升至 `transcript_full.jsonl`、`thinking` / `tool_calls` / `<ADDITIONAL_METADATA>` 过滤、激活时间戳边界与增量读取 |
| [`tests/test_agy_organizer.py`](../tests/test_agy_organizer.py) | `organizer.identity()` 通过 `summarizer.validate_identity`、`mindie-summary-outcome/1` 正常返回、发送后超时标记为 `outcome_unknown` |
