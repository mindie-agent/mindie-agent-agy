# Failure Diagnostics, Timeout Arithmetic & Receipt Honesty

<a href="failure-diagnostics.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

`mindie-agent-agy` enforces `v0.0.2` timeout arithmetic and Receipt Honesty across [`mcp_server.py`](../plugins/mindie-agent/scripts/mcp_server.py), [`organizer.py`](../plugins/mindie-agent/scripts/organizer.py), and [`bounded.py`](../plugins/mindie-agent/scripts/bounded.py).

## 1. Timeout Arithmetic Invariant

Every cross-process boundary satisfies `outer_timeout >= sum(inner_budgets) + cleanup_grace`:
- **Remote Execution (`remote_bash` / `remote_run_command`)**: The inner wait budget defaults to `600,000 ms` (`600s`); `mcp_server.py:remote_timeout` adds a `60.0s` cleanup grace window for a total outer budget of **`660.0s`**.
- **Summary & C6 Worker (`organizer.py`)**: Uses `mindie_knowledge.loop.summary_budget.native_timeout(max_seconds=120.0)`, reserving `5.0s` before the parent worker deadline for scratch cleanup and outcome serialization.

## 2. Receipt Honesty

- **Preserving Succeeded Remote Effects (`call_remote_surface`)**: If a remote SSH/NPU operation succeeds and local SQLite bookkeeping (`remote_receipts.sqlite3`) subsequently fails during `finish()`, `call_remote_surface` returns the succeeded remote payload intact with `"receipt_unconfirmed": True` and `"receipt_warning"` rather than reporting a false tool error.
- **No Replay of Uncertain Model Calls (`NativeSummaryModel`)**: Once `self.transmitted = True`, any timeout or post-transmission transport error in `organizer.py` emits `status="outcome_unknown"` (`billing_status="unknown"`) and never retries the model call.

## 3. Bounded Process Tree Cleanup (`bounded.py`)

- **Windows**: Attaches child processes to a Win32 Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) and terminates the entire tree via `TerminateJobObject` on timeout.
- **POSIX (Linux / macOS / WSL)**: Spawns children with `start_new_session=True` and reclaims the process group via `os.killpg(..., SIGKILL)`.
