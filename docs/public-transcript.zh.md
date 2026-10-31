# 公开对话采集与本地脱敏（Antigravity Brain JSONL）

<b>中文</b> | <a href="public-transcript.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 严格实施 `public-transcript` 公开对话采集契约，由 [`agy_transcript.py`](../plugins/mindie-agent/scripts/agy_transcript.py) 实现 `FileIdentity`、`identify`、`capture_source` 与 `read_material` 四个标准接口。

## 1. `transcript.jsonl` 到 `transcript_full.jsonl` 自动提升

Antigravity 将会话日志存储在 `<appDataDir>/brain/<conversationId>/.system_generated/logs/` 下：
- `transcript.jsonl` 为紧凑视图，长步骤可能包含 `"truncated_fields": ["content"]`；
- `transcript_full.jsonl` 保存完整、未截断的步骤记录。

当 Hook 传入 `transcript.jsonl` 且同级目录存在 `transcript_full.jsonl` 时，`capture_source()` 自动提升并绑定至 `transcript_full.jsonl`，防止因字段截断丢失关键工程上下文。

## 2. 严格白名单过滤规则

| 记录类型 | 采集行为 |
|---|---|
| `type == "USER_INPUT"` 且 `source == "USER_EXPLICIT"` | 提取 `<USER_REQUEST>...</USER_REQUEST>` 内的用户输入文本，自动剥离 `<ADDITIONAL_METADATA>`、`<USER_SETTINGS_CHANGE>`、`<SYSTEM_MESSAGE>`、`<EPHEMERAL_MESSAGE>` 等系统注入块 |
| `type == "PLANNER_RESPONSE"` 且 `source == "MODEL"` | 仅提取可见的 `content` 字符串；**严格丢弃** `thinking`（模型内部思维链）与 `tool_calls`（工具调用参数） |
| 其他步骤（`GENERIC` 工具输出、系统事件等） | 全部跳过，绝不进入公开语料 |
| 激活时间边界（`not_before`） | 早于当前会话 `/mindie-agent` 激活时间戳（`activated_at`）的历史记录全部跳过 |

## 3. 增量游标与本地 Gitleaks 脱敏

- `FileIdentity` 记录文件首行 SHA-256 摘要与字节长度；增量读取（`offset > 0`）前校验首行指纹，防止文件截断或轮转错位。
- 提取出的 Markdown 文本在本地经过 `mindie_knowledge` 确定性规则与 Gitleaks 扫描脱敏后，才会交由 [`organizer.py`](../plugins/mindie-agent/scripts/organizer.py) 生成分块导航索引。
