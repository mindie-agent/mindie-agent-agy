# Failure Diagnostics, Timeout Arithmetic & Receipt Honesty

<a href="failure-diagnostics.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

`mindie-agent-agy v0.1.21` delegates timeout arithmetic and process ownership to `mindie-harness` while preserving honest results across [`mcp_server.py`](../plugins/mindie-agent/scripts/mcp_server.py), [`bridge.py`](../plugins/mindie-agent/scripts/bridge.py), and [`mindie_launch.py`](../plugins/mindie-agent/scripts/mindie_launch.py).

## 1. Timeout Arithmetic Invariant

Every cross-process boundary satisfies `outer_timeout >= sum(inner_budgets) + cleanup_grace`:
- **MCP remote tools**: `ManagedMCPAdapter` / `mindie_harness.mcp.gateway_dispatch` computes the outer deadline; read/control calls remain bounded and mutation calls keep cleanup grace.
- **Knowledge RPC**: the local fallback in `mcp_server.py` uses `35s` for reads and `110s` for `sync` / `distill`.
- **Hooks**: `mindie_launch._reexec` bounds hook re-exec to `15s`; passive hooks return before any child process.
- **Cold provisioning**: `mindie-harness-seed.pyz` is bounded to `240s` inside the seed and `300s` in `mindie_launch`.

## 2. Receipt Honesty

- **Knowledge RPC (`_rpc_knowledge`)**: if the primary RPC succeeds and the local `Admission.finish` raises `OSError` / `sqlite3.Error`, the result keeps `isError=False` and attaches `receipt_warning` + `receipt_unconfirmed=True`.
- **Remote / component calls**: `ManagedMCPAdapter` and `mindie_harness.mcp.receipts.apply_receipt_honesty` preserve a succeeded payload when local SQLite accounting fails; callers must not blindly replay.
- **Uncertain state**: report the structured reason and the submission state once; reconcile an `unknown` write before retrying.

## 3. Bounded Process Cleanup

- Windows: `mindie_harness.process.win32` uses Win32 Job Objects; plugin subprocesses pass `creationflags=CREATE_NO_WINDOW` and never open console windows.
- POSIX: `mindie_harness.process.supervisor` uses owned process groups and bounded cleanup grace.
- `mindie_doctor` is read-only: it does not start the knowledge loop, mutate consent, upload diagnostics, or install components.

## 4. Non-Blocking Task Continuity

MindIE tools are accelerators, never roadblocks. If `remote-dev`, `coordinator`, `knowledge`, or `mindie_doctor` fails, report once, reconcile uncertain writes, and continue immediately with native `ssh` / `scp` / `git` or local scripts.
