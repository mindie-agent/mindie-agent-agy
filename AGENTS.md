# MindIE Agent — Google Antigravity Native Harness Plugin (`mindie-agent-agy`)

This repository implements the **Google Antigravity (AGY) Native Harness Plugin** (`plugins/mindie-agent/`) for MindIE Agent. It bridges Antigravity's native customization surfaces (`plugin.json`, root `hooks.json`, `mcp_config.json`, `skills/mindie-agent/SKILL.md`, and brain `transcript_full.jsonl` / `transcript.jsonl`) to the shared MindIE runtime components (`mindie_knowledge`, `remote_dev`, `mindie_diagnostics`, `mindie_coordinator`, `mindie_profiler`, and `mindie_sim`).

---

## 1. Plugin Script Navigation & Call Graph

All plugin scripts live under `plugins/mindie-agent/scripts/` and include standardized top-of-file Agent Contract docstrings (`[Role]`, `[Invariants]`, `[Timeouts & Locks]`, `[Side-Effect Policy]`):

| Layer | Script | Role & Key Responsibilities |
| :--- | :--- | :--- |
| **Native Entry & Setup** | `mindie_launch.py` | Stable entrypoint invoked by `hooks.json` and `mcp_config.json`. Enforces `HOOK_TOTAL=10.0s`, `HOOK_LOCK_BUDGET=2.0s`, and `BOOTSTRAP_TOTAL=540.0s`, re-executing into the configured `.venv` interpreter when needed. |
| | `setup.py` / `paths.py` | Resolves MindIE-owned paths (`~/.config/mindie-agent/agy.json`, never mutating `~/.gemini`), validates `product-contract.json` & `harness-compatibility.json`, and writes atomic configs via `os.replace()`. |
| **Host Hook Lifecycle** | `bridge.py` | Implements Antigravity `SessionStart`, `PreInvocation` (`/mindie-agent` manual activation/deactivation/status), `PreToolUse` (one-shot MCP call bind), and `Stop` (turn-end capture handoff; never returns `"decision": "continue"`). |
| | `admission.py` | Thin wrapper around `mindie_knowledge.loop.activation.Admission` (`activate`, `deactivate`, `knowledge_lease`, and factual `status_facts` without onboarding prompts). |
| | `identity.py` | Validates camelCase Antigravity hook payloads (`conversationId`, `workspacePaths`, `transcriptPath`, `lastUserInput`, `toolCall`, `stepIdx`) and manages `mcp-binds.sqlite3` (WAL + `busy_timeout=5000`). |
| **MCP Server & Private Knowledge CLI** | `mcp_server.py` | Public stdio JSON-RPC MCP server for `mindie-remote-dev` and `mindie-tools` (`profiler`, `sim`, `coordinator`, `diagnostics_*`, `mindie_status`). Exposes zero knowledge tools in `tools/list` and preserves succeeded remote/tool results if local receipt/bind accounting fails. |
| | `knowledge_cli.py` | Private CLI (`describe`, `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`) gated by `Admission.knowledge_lease(session_id)` (`entry_enabled == 1`). |
| **Transcript & Summary Worker** | `agy_transcript.py` | Incremental reader for Antigravity `<appDataDir>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl` (and `transcript.jsonl`), extracting only public `USER_INPUT` and `PLANNER_RESPONSE` text while stripping `thinking`, `tool_calls`, and `<ADDITIONAL_METADATA>`. |
| | `organizer.py` | Isolated summary and C6 distillation worker emitting `mindie-summary-outcome/1` envelopes (`returned` / `failed` / `outcome_unknown`) without retrying uncertain paid calls. |
| | `bounded.py` | Bounded subprocess runner with POSIX process-group and Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) tree termination. |

### Call Graph

```text
Antigravity Hook (SessionStart / PreInvocation / PreToolUse / Stop)
  └─► mindie_launch.py hook <event> (HOOK_TOTAL=10.0s)
        └─► bridge.py
              ├─► SessionStart ──► admission.knowledge_lease (passive when inactive)
              ├─► PreInvocation ──► identity.parse_mindie_gesture (/mindie-agent)
              │     ├─► admission.activate / deactivate
              │     └─► setup.ensure_default_profile (degrades gracefully without revoking lease)
              ├─► PreToolUse ──► identity.publish_bind (mcp-binds.sqlite3 WAL)
              └─► Stop ──► agy_transcript.capture_source ──► mindie_knowledge.loop.cli.capture_hook

Antigravity MCP Client (stdio JSON-RPC)
  └─► mindie_launch.py mcp (remote | tools) ──► mcp_server.py
        ├─► identity.claim_bind / finish_bind
        ├─► RemoteReceipts (remote-receipts.sqlite3 WAL + busy_timeout=5000)
        ├─► remote_dev.mcp.tools (remote_* + Antigravity native aliases)
        └─► bounded.run (profiler, sim, coordinator, diagnostics_*, mindie_status)

Antigravity Agent (after /mindie-agent activation)
  └─► knowledge_cli.py (describe | knowledge_query | knowledge_explain | knowledge_feedback | knowledge_distill | knowledge_sync)
        └─► admission.knowledge_lease (requires entry_enabled == 1) ──► mindie_knowledge service RPC
```

---

## 2. Verification Commands

```bash
# Run full Tier 0 + Tier 1 unit and contract suite
../.venv/Scripts/python.exe -m pytest tests -q
```
