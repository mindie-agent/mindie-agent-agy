---
name: mindie-agent
description: "Ascend NPU, vLLM-Ascend, xLLM, MindIE-SD, and AscendC/Triton engineering workflow: L1-L4 graded knowledge retrieval/distillation, remote NPU development, profiling, Roofline simulation, CANN fault triage, cluster lease coordination, and host readiness diagnosis."
disable-slash-command: true
---

# MindIE Agent Runtime Workflow

## 1. Default-Enabled MCP Tooling

- **Ready out of the box**: while the `mindie-agent` plugin is enabled, `mindie-remote-dev` exposes Antigravity's 12 native tools — `remote_read_file`, `remote_write_file`, `remote_edit_file`, `remote_run_command`, `remote_manage_task`, `remote_find_by_name`, `remote_grep_search`, `remote_list_dir`, `remote_auth`, `remote_probe`, `remote_artifact_pull`, `remote_artifact_push` — with zero `_meta`, zero `PreToolUse` binds, and zero slash-command activation.
- **`mindie-tools`**: `mindie_status`, `mindie_doctor`, `profiler`, `sim`, `coordinator`, `npu_top`, `debugger`, `rsi_loop`, `experiment_ledger`, `diagnostics_status`, `diagnostics_bundle`, plus `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`.
- **Readiness first**: call `mindie_doctor` (optionally `probe_remote=true`) for a bounded, read-only local/runtime/SSH readiness report before blaming a component or resubmitting a failed run.
- **One-step SSH NPU connection**: use `remote_auth(..., save_default=True, discover=True)` to persist and verify the default endpoint; never print or store the password in chat.
- **Sharing control**: `setup.py sharing --enable | --local-only | --disable` changes consent; `--status` is read-only.

## 2. Non-Blocking Task Continuity Rule

**MindIE tools are accelerators, NEVER roadblocks.** If `remote-dev`, `coordinator`, `knowledge`, `mindie_doctor`, or any other MindIE tool fails or is unavailable:

1. Report the failure once, with the structured reason and whether a write is uncertain.
2. Reconcile any uncertain remote write before retrying it; do not blindly replay.
3. Continue the task immediately through native `ssh` / `scp` / `git`, the host terminal, or local scripts.
4. Do not stop, loop, or ask the user to fix optional tooling unless the task genuinely cannot proceed without that capability.

## 3. Code-First Investigation, Knowledge & NPU Discipline

- **Inspect code first**: read target source files and callers before querying knowledge; proceed directly when the code already explains the behavior.
- **Two-step `L1–L4` retrieval**:
  1. `knowledge_query`: pass concise technical terms, optional `domain`, and observed `conditions` (`token_budget` up to `64000`). Treat `condition_unknown` as unproven—verify missing facts before applying a rule.
  2. `knowledge_explain`: batch-read selected `mindie://<domain>/units/<id>@<rev>` `refs=[...]` with observed `conditions` and `known_refs=[...]`.
- **NPU leases & durable handles**: acquire or verify a `coordinator` lease before running workloads on physical NPUs (respecting A3 dual-Die boundaries). Inspect live `coordinator` `execution_id` and `remote-dev` `job_id` handles before resubmitting, and pass explicit `--time-window`, `--step-window`, or `--full-range` plus `--clock-domain` to `profiler`.
- **De-biased exploration**: when repeated `L3` attempts stall on a structural bottleneck, query `L4` mutators (`mode="explore"`), optionally seed an isolated subagent (`invoke_subagent`) with `exploration_context.subagent_fact_pack.prompt_brief`, and verify multi-factor changes via leave-one-out ablations.

## 4. Progressive Runtime References

- Tool schemas, `L1–L4` distillation, NPU leases, `mindie_doctor`, the 12-tool `agy` profile, `remote_auth`, and sharing: [references/domain-skills.md](references/domain-skills.md)
- Default session lifecycle, `< 30ms` hook fast-paths, and `Stop` capture: [references/activation-lifecycle.md](references/activation-lifecycle.md)
- Deep diagnostic, distributed & accuracy reasoning: [references/diagnostic-reasoning.md](references/diagnostic-reasoning.md)
