---
name: mindie-agent
description: Manually enable MindIE knowledge, domain skills, feedback, distillation and background capture for the current Antigravity conversation through /mindie-agent or $mindie-agent. Configured deterministic tools remain available without this entry.
---

# MindIE Agent (Antigravity Unified Entry)

Use this skill **only** after the user explicitly invokes `/mindie-agent` or `$mindie-agent` in the current Antigravity conversation. Do not activate it from topic matching.

1. **Manual Session Activation**:
   - The native Antigravity `PreInvocation` hook detects `/mindie-agent` in `lastUserInput` and activates the session lease (`entry_enabled = 1`) in `admission.sqlite3` for `conversationId`, injecting the bound `AGY_CONVERSATION_ID` and `knowledge_cli.py` path into context.
   - If the `PreInvocation` hook was not yet run or you need to verify the session lease, run:
     ```bash
     AGY_CONVERSATION_ID="<conversation-id>" python -I "<plugin_root>/scripts/knowledge_cli.py" describe
     ```
2. **Private Knowledge Catalog (`knowledge_cli.py`)**:
   - Knowledge tools (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`) are intentionally absent from default MCP discovery (`mcp_config.json`).
   - Execute any knowledge operation via `knowledge_cli.py` with JSON arguments on `stdin`:
     ```bash
     AGY_CONVERSATION_ID="<conversation-id>" python -I "<plugin_root>/scripts/knowledge_cli.py" knowledge_query <<'EOF'
     {"query": "Ascend A3 ACLGraph capture", "conditions": {"chip": "A3"}}
     EOF
     ```
   - Preserve the returned `isError` and nonzero exit status; never fabricate a session ID.
3. **Always-On Deterministic MCP Tools (`mindie-remote-dev` & `mindie-tools`)**:
   - `remote_*` (and Antigravity aliases `remote_view_file`, `remote_write_to_file`, `remote_replace_file_content`, `remote_run_command`, `remote_manage_task`, `remote_find_by_name`, `remote_grep_search`, `remote_list_dir`), `profiler`, `sim`, `coordinator`, `diagnostics_status`, `diagnostics_bundle`, and `mindie_status` are available in every session via MCP without requiring `/mindie-agent`.
4. **Two-Step Progressive Knowledge Loading & De-Biased Exploration (`mode="explore"`)**:
   - **Step 1 (`knowledge_query`)**: Query with observed `conditions` (`matched` | `mismatched` | `condition_unknown`; never treat `condition_unknown` as a match).
   - **Step 2 (`knowledge_explain`)**: Batch-read selected `refs=[...]` with `known_refs=[...]` and `token_budget=4096`.
   - **De-Biased Exploration**: When 3 consecutive attempts stagnate (`assess_stagnation`), call `knowledge_query(..., mode="explore", native_subcontext=True)` and spawn an isolated subagent (`invoke_subagent`) seeded **only** with `exploration_context.subagent_fact_pack.prompt_brief` (`L1` constraints + `L2` bottleneck evidence + `L4` mutators + `conditional_avoid`, with `L3` workflows stripped).

## Progressive Context Disclosure (Read Only on Demand)

- **Detailed tool schemas (`knowledge_*`, `remote_*`), dual-layer ledger (`PROGRESS.md` + `experiment-ledger.jsonl`), and NPU timing / profiler discipline**: read [references/domain-skills.md](references/domain-skills.md).
- **Session lifecycle, `/mindie-agent deactivate`, and `Stop` capture rules**: read [references/activation-lifecycle.md](references/activation-lifecycle.md).
