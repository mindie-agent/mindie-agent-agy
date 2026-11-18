# 故障诊断、超时算术与回执诚实性

<b>中文</b> | <a href="failure-diagnostics.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy v0.1.21` 将超时算术与进程所有权委托 `mindie-harness`，并在 [`mcp_server.py`](../plugins/mindie-agent/scripts/mcp_server.py)、[`bridge.py`](../plugins/mindie-agent/scripts/bridge.py) 与 [`mindie_launch.py`](../plugins/mindie-agent/scripts/mindie_launch.py) 中保持结果诚实。

## 1. 超时算术不变量

所有跨进程边界满足 `outer_timeout >= sum(inner_budgets) + cleanup_grace`：
- **MCP remote 工具**：由 `ManagedMCPAdapter` / `mindie_harness.mcp.gateway_dispatch` 计算外层 deadline；读/控制调用有界，变更调用保留清理宽限。
- **知识 RPC**：`mcp_server.py` 本地回退对读操作使用 `35s`，对 `sync` / `distill` 使用 `110s`。
- **Hooks**：`mindie_launch._reexec` 将 hook 重入限制为 `15s`；被动 hook 在创建任何子进程前返回。
- **冷启动**：`mindie-harness-seed.pyz` 内部 `240s`，`mindie_launch` 外层 `300s`。

## 2. 回执诚实性

- **知识 RPC（`_rpc_knowledge`）**：主 RPC 成功后，若本地 `Admission.finish` 抛 `OSError` / `sqlite3.Error`，结果保持 `isError=False` 并附加 `receipt_warning` + `receipt_unconfirmed=True`。
- **remote / 组件调用**：`ManagedMCPAdapter` 与 `mindie_harness.mcp.receipts.apply_receipt_honesty` 在本地 SQLite 记账失败时保留已成功载荷；调用方不得盲目重放。
- **状态不确定**：只报告一次结构化原因与提交状态；重试前先核对 `unknown` 写入。

## 3. 有界进程回收

- Windows：`mindie_harness.process.win32` 使用 Win32 Job Object；插件子进程统一传 `creationflags=CREATE_NO_WINDOW`，绝不弹黑框。
- POSIX：`mindie_harness.process.supervisor` 使用受管进程组与有界清理宽限。
- `mindie_doctor` 严格只读：不启动知识循环、不修改授权、不上传诊断、不安装组件。

## 4. 非阻塞任务连续性

MindIE 工具是加速器，绝不是路障。若 `remote-dev`、`coordinator`、`knowledge` 或 `mindie_doctor` 失败，报告一次、核对不确定写入，并立即改用原生 `ssh` / `scp` / `git` 或本地脚本继续任务。
