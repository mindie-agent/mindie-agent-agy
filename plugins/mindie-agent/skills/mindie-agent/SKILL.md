---
name: mindie-agent
description: Ascend NPU, vLLM-Ascend, xLLM, MindIE-SD, and AscendC/Triton engineering workflow: L1-L4 graded knowledge retrieval/distillation, remote NPU development, profiling, Roofline simulation, CANN fault triage, and cluster lease coordination.
disable-slash-command: true
---

# MindIE Agent Runtime Workflow

## 1. Default-Enabled MCP Tooling

- **Ready out of the box**: While the `mindie-agent` plugin is enabled in Antigravity, all tools on `mindie-remote-dev` (`remote_*`) and `mindie-tools` (`mindie_status`, `debugger`, `profiler`, `sim`, `coordinator`, `npu_top`, `diagnostics_status`, `diagnostics_bundle`, plus `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`) work over standard MCP without slash-command activation, `PreToolUse` binds, or `context_file`.
- **Task continuity**: If any optional helper fails, report the error once and continue with deterministic MCP or authorized host-native tools; reconcile any `unknown` remote write before repeating it.

## 2. Code-First Investigation, Knowledge & NPU Discipline

- **Inspect code first**: Read target source files and callers before querying knowledge; proceed directly when the code already explains the behavior.
- **Two-step `L1–L4` retrieval**:
  1. `knowledge_query`: pass concise technical terms, optional `domain`, and observed `conditions` (`token_budget` up to `64000`). Treat `condition_unknown` as unproven—verify missing facts before applying a rule.
  2. `knowledge_explain`: batch-read selected `mindie://<domain>/units/<id>@<rev>` `refs=[...]` with observed `conditions` and `known_refs=[...]`.
- **NPU leases & durable handles**: Acquire or verify a `coordinator` lease before running workloads on physical NPUs (respecting A3 dual-Die boundaries). Inspect live `coordinator` `execution_id` and `remote-dev` `job_id` handles before resubmitting, and pass explicit `--time-window`, `--step-window`, or `--full-range` plus `--clock-domain` to `profiler`.
- **De-biased exploration**: When repeated `L3` attempts stall on a structural bottleneck, query `L4` mutators (`mode="explore"`), optionally seed an isolated subagent (`invoke_subagent`) with `exploration_context.subagent_fact_pack.prompt_brief`, and verify multi-factor changes via leave-one-out ablations.

## 3. Progressive Runtime References

- Tool schemas, `L1–L4` distillation & NPU lease rules: [references/domain-skills.md](references/domain-skills.md)
- Default session lifecycle & `Stop` capture: [references/activation-lifecycle.md](references/activation-lifecycle.md)
- Deep diagnostic, distributed & accuracy reasoning: [references/diagnostic-reasoning.md](references/diagnostic-reasoning.md)
