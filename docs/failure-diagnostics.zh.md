# 故障诊断、超时算术与回执诚实性

<b>中文</b> | <a href="failure-diagnostics.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 在 [`mcp_server.py`](../plugins/mindie-agent/scripts/mcp_server.py)、[`organizer.py`](../plugins/mindie-agent/scripts/organizer.py) 与 [`bounded.py`](../plugins/mindie-agent/scripts/bounded.py) 中严格落实 `v0.0.2` 超时算术与回执诚实性（Receipt Honesty）。

## 1. 超时算术不变量（Timeout Arithmetic）

所有跨进程调用均满足 `outer_timeout >= sum(inner_budgets) + cleanup_grace`：
- **远端命令执行（`remote_bash` / `remote_run_command`）**：内层原生超时预算为 `600,000 ms`（`600s`），外层预算固定叠加 `60.0s` 清理宽限期，即 **`660.0s`**（见 `mcp_server.py:remote_timeout`）。
- **摘要与 C6 蒸馏 Worker（`organizer.py`）**：调用 `mindie_knowledge.loop.summary_budget.native_timeout(max_seconds=120.0)`，在父进程截止时间前预留 `5.0s` 清理与回执输出窗口。

## 2. 回执诚实性（Receipt Honesty）

- **远端副作用保护（`call_remote_surface`）**：当 `remote_dev` 远端工具调用已成功完成，但本地 SQLite 回执库（`remote_receipts.sqlite3`）在 `finish()` 阶段发生磁盘或锁异常时，绝不覆盖或丢弃远端成功结果，而是原样返回 payload 并附加 `"receipt_unconfirmed": True` 与 `"receipt_warning"`。
- **计费模型调用防重放（`NativeSummaryModel`）**：在 `organizer.py` 中，一旦请求已发送（`self.transmitted = True`）后发生超时或 HTTP 5xx 错误，返回 `status="outcome_unknown"`（`billing_status="unknown"`），严禁自动重放不确定是否已计费的模型请求。

## 3. 有界进程树清理（`bounded.py`）

- **Windows**：通过 Win32 Job Object（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）绑定子进程树，超时或异常退出时调用 `TerminateJobObject` 一次性回收整棵子进程树。
- **POSIX（Linux / macOS / WSL）**：通过 `start_new_session=True` 独立进程组配合 `os.killpg(..., SIGKILL)` 回收全部后代进程。
