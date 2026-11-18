# 公开对话采集与本地脱敏（Antigravity Brain JSONL）

<b>中文</b> | <a href="public-transcript.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 在 [`agy_transcript.py`](../plugins/mindie-agent/scripts/agy_transcript.py) 中实现 `public-transcript` 契约，导出 `FileIdentity`、`identify`、`capture_source`、`read_material` 与 `read_increment`。

## 1. `transcript.jsonl` 自动提升至 `transcript_full.jsonl`

Antigravity 将会话日志存放在 `<appDataDir>/brain/<conversationId>/.system_generated/logs/`：
- `transcript.jsonl` 为紧凑日志，可能带有 `"truncated_fields": ["content"]`；
- `transcript_full.jsonl` 保存完整未截断的 JSONL 记录。

当 `capture_source()` 收到 `transcript.jsonl` 且同目录存在 `transcript_full.jsonl` 时，会自动提升到完整文件。

## 2. 严格白名单过滤

| 记录类型 | 采集行为 |
|---|---|
| `type == "USER_INPUT"` 且 `source == "USER_EXPLICIT"` | 提取 `<USER_REQUEST>...</USER_REQUEST>` 内文本，并剥离 `<ADDITIONAL_METADATA>`、`<USER_SETTINGS_CHANGE>`、`<SYSTEM_MESSAGE>`、`<EPHEMERAL_MESSAGE>` |
| `type == "PLANNER_RESPONSE"` 且 `source == "MODEL"` | 仅提取可见 `content`；**严格排除** `thinking` 与 `tool_calls` |
| 其他类型（`GENERIC` 工具输出、系统步骤） | 忽略，绝不导出 |
| 时间边界（`not_before`） | 早于租约 `activated_at`（默认 `0.0`）的步骤跳过 |

## 3. 增量游标与本地脱敏

- `FileIdentity` 对 JSONL 前 512 字节做 SHA-256 并记录字节长度，增量读取（`offset > 0`）可立即发现截断或文件替换。
- `read_material` 精确计算 `[start, end)` SHA-256 账目，并诚实返回 `unchanged`、`replaced`、`wrong-task`、`invalid-boundary` 或 `unknown-format`。
- `bridge.handle_stop` 将已授权 transcript 交给 `mindie_knowledge.loop.cli.capture_hook`；确定性脱敏、Gitleaks 扫描、导航索引与 C6 蒸馏均由 `mindie_knowledge` 完成。`v0.1.21` 不再随插件发布独立摘要 Worker。
