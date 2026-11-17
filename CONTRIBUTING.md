# 贡献指南 (`mindie-agent-agy`)

英文版请参阅 [CONTRIBUTING.en.md](CONTRIBUTING.en.md)。

`mindie-agent-agy` 是 MindIE Agent 面向 Google Antigravity 的原生宿主插件仓（仓库类别：`host-plugin`）。

## 1. 本地工作区与分支拓扑

推荐使用与组织内其他组件一致的扁平同级目录或独立 Git Worktree 布局（切勿修改主检出目录中的未隔离状态）：

```text
code/
  mindie-agent/           # 架构文档、发布清单与跨仓门禁
  harness/                # mindie-harness 公共底座
  mindie-agent-agy/       # Google Antigravity 宿主插件仓
  .worktrees/             # 隔离开发分支工作树
```

## 2. 宿主插件核心契约（不可破坏）

1. **根级 `hooks.json` 字节冻结契约**：`plugins/mindie-agent/hooks.json` 必须保持 `979` 字节且 SHA-256 恒等于 `33b564c78b439205fd4acfe095c83b15770043ff462d636e2a89f14620cdfc3f`。
2. **被动默认与 `< 100ms` `Stop` 快速路径（`P2.1` / `P2.2`）**：未显式调用 `/mindie-agent` 的会话在 `Stop` 阶段必须在 `100ms` 内返回 `{}`，零网络、零构建、零转录扫描；默认 MCP `tools/list` 严禁暴露 `knowledge_*` 工具。
3. **确定性工具零预绑定门禁（`P2.7`）**：`mindie-remote-dev` 与 `mindie-tools` 在无 `_meta`、无 `PreToolUse` 绑定、无 `AGY_CONVERSATION_ID` 时必须通过标准 MCP `tools/call` 正常执行。
4. **Windows 零弹窗与 Unix LF**：所有 Windows 子进程调用必须携带 `CREATE_NO_WINDOW`（严禁 `DETACHED_PROCESS` 或 `CREATE_NEW_CONSOLE`），全仓文本文件统一使用 Unix `LF`（`\n`）换行。

## 3. 测试隔离与提交前自查

所有测试必须在临时沙箱中隔离 `HOME`、`USERPROFILE`、`MINDIE_CONFIG_HOME`、`MINDIE_DATA_HOME`、`MINDIE_DIAGNOSTICS_ROOT` 与 `UV_CACHE_DIR`，严禁向真实 `~/.gemini`、`~/.config` 或 `~/.local` 写入任何数据。

提交 PR 前请完成 [`.github/pull_request_template.md`](.github/pull_request_template.md) 中的 `host-plugin` 四项门禁自查（`HOST-NATIVE`、`HOST-WIRE`、`HOST-ADMISSION`、`HOST-UPGRADE`）并运行：

```bash
python3 .github/scripts/check_lf_eol.py
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
pytest -q -m "not live_npu"
```
