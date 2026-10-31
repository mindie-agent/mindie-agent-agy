# 框架稳定性与持久化配置

<b>中文</b> | <a href="framework-stability.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 保证在日常非昇腾任务中零侵入、零后台开销（Passive Default），并确保 Windows 与 POSIX 跨平台配置读写的一致性。

## 1. 被动默认（Passive Default）保证

- **`SessionStart`**：始终返回 `{}`，不启动后台服务，不联网同步。
- **普通 `PreInvocation`**：当用户输入不包含 `/mindie-agent` 时立即返回 `{}`，零注入、零状态变更。
- **未激活会话的 `Stop`**：当 `Admission.knowledge_lease(conversationId)` 为 `None` 时立即返回 `{}`，不读取 `mindie-community.json` 或会话 Transcript。

## 2. 跨平台原子读写与 SQLite WAL 纪律

- JSON 配置统一通过 `paths.py:atomic_write_json`（同目录临时文件 + `os.replace()`）原子更新。
- `admission.sqlite3` 与 `remote_receipts.sqlite3` 统一启用 `PRAGMA journal_mode=WAL`、`PRAGMA synchronous=NORMAL` 与 `PRAGMA busy_timeout=5000`（`5.0s`），热路径无 `.owner` 文件锁或动态 Schema 反射。
