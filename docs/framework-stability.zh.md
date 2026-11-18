# 框架稳定性与持久化配置

<b>中文</b> | <a href="framework-stability.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy v0.1.21` 保证普通非昇腾任务近零开销（Passive Default），并在 Windows 与 POSIX 上保持一致原子持久化。

## 1. 被动默认保障

- **`SessionStart`**：校验 stdin 后原子写入 `state/agy/pending-update-check.json` 并返回 `{}`；不启动后台 Worker，也不同步 feed。
- **`PreInvocation`**：立即返回零 I/O `{}` 纯标准库直通，不注入步骤、不触碰 SQLite。
- **`PreToolUse`**：零 `_meta` 绑定返回 `{"decision": "allow"}`；仅伪造身份字段或 `knowledge_*` 上畸形 `session_id` 才 deny。
- **共享禁用或范围外 `Stop`**：未配置、社区共享禁用或工作区不在授权 `project_roots` 内时，`< 30ms` 返回 `{}`，零 `admission.sqlite3`、零 transcript 读取，绝不返回 `{"decision": "continue"}`。

## 2. 薄委托与原子替换

- 插件仅保留 5 个生产脚本；生命周期、MCP 路由、运行时代际、共享授权与更新全部委托 `mindie-harness`。
- JSON 配置经 `mindie_harness.atomic.atomic_write_json` 原子更新（同目录临时文件 + `os.fsync` + `os.replace()`）；`mindie_launch` 对待更新标记使用同一模式，并在 `finally` 中 `tmp.unlink(missing_ok=True)`。
- `admission.sqlite3` 与 `remote-receipts.sqlite3` 由 `mindie_knowledge` / `mindie_harness` 以 WAL 与 `busy_timeout=5000` 管理；插件不做热路径 `.owner` 锁扫描。
- `setup.py --help` / `-h` 与 `--dry-run`（含 `--dry` 等唯一缩写）零写入。
