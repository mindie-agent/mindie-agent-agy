# MindIE Agent — Google Antigravity Native Host Plugin (`mindie-agent-agy`)

`mindie-agent-agy` is a **`host-plugin`** repository implementing the Google Antigravity (`agy`) native host plugin (`plugins/mindie-agent/`). It bridges Antigravity's native hooks (`plugins/mindie-agent/hooks.json`), stdio MCP servers (`mcp_config.json`), skill entry (`skills/mindie-agent/SKILL.md`), and brain JSONL transcripts (`agy_transcript.py`) to `mindie-harness` (`v0.1.0`) and the shared MindIE runtime components.

## 1. Core Architectural & Contract Rules

- **Frozen Root `hooks.json` Contract**: `plugins/mindie-agent/hooks.json` MUST remain byte-for-byte identical (`979` bytes, `sha256 == 33b564c78b439205fd4acfe095c83b15770043ff462d636e2a89f14620cdfc3f`) at the plugin root (`plugins/mindie-agent/hooks.json`, never `hooks/hooks.json`).
- **Passive Default & `< 100ms` Inactive `Stop` (`P2.1` & `P2.2`)**:
  - Unactivated `Stop` (`hook stop` when no `/mindie-agent` session lease is active) MUST return `{}` in `< 100ms` with zero child re-exec, zero network I/O, zero virtualenv builds, and zero transcript reads, and `Stop` NEVER emits `"decision": "continue"`.
  - Default MCP `tools/list` (`mindie-remote-dev`, `mindie-tools`) NEVER exposes `knowledge_*` tools; private knowledge operations remain gated behind `knowledge_cli.py` and an active `/mindie-agent` session lease (`entry_enabled == 1`).
- **Zero Pre-Call Session-Binding Gates on Deterministic Tools (`P2.7`)**:
  - Standard MCP `tools/call` on `mindie-remote-dev` (`remote_*`) and `mindie-tools` (`profiler`, `sim`, `coordinator`, `debugger`, `npu_top`, `diagnostics_*`, `mindie_status`) works with zero `_meta`, zero `PreToolUse` binds, and zero `AGY_CONVERSATION_ID`, falling back to `_CONNECTION_SESSION_ID`.
- **Concurrent Resident Stdio MCP & `mindie-harness` Substrate (`AD-03`, `AD-07`, `AD-10`)**:
  - `mcp_server.py` and `host_adapter.py` (`AGYHostAdapter`) serve `Content-Length` and JSONL frames concurrently in-process across separate read/control and mutation worker pools while holding a runtime `.inuse` generation lease (`hold_runtime_lease`), with zero cold `--once` per-call spawns.
  - Bundled `assets/mindie-harness-seed.pyz` is verified in `64 KiB` streaming chunks against `assets/seed-lock.json` (`mindie-harness-seed/1`).
  - `rsi_loop.py` and `experiment_ledger.py` delegate to `mindie_harness.rsi` (`mindie_knowledge.graded` fallback on pre-Wave-B runtimes).
  - Never use blanket `O_NOFOLLOW` on config/checkout metadata reads (`AD-07`), never mutate `~/.gemini`, and never leak benchmark evaluation tuples or ledger gates into `SKILL.md` (`AD-10`).
- **State & Receipt Honesty (Principles 9, 10, 14, 17)**:
  - Keep every module in `plugins/mindie-agent/scripts/` and `tests/` `< 800` lines with `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in the top 30 lines of production scripts.
  - Always place `except TimeoutError: raise` before `except OSError:`. Once a primary remote/component operation succeeds, local receipt/bind accounting failures keep `isError=False` and attach `receipt_warning` + `receipt_unconfirmed=True`.

## 2. Plugin Script Map (`plugins/mindie-agent/scripts/`)

| Module | Role |
| :--- | :--- |
| `mindie_launch.py` | Entrypoint for `hook`, `mcp`, `setup`, and `update`; `< 100ms` passive `Stop` fast-path and paired `(python, runtime_scripts)` re-exec. |
| `host_adapter.py` | `AGYHostAdapter` (`mindie_harness.api.HostAdapter`), `verify_seed_bundle()`, and concurrent dual-framing stdio loop (`serve_concurrent_stdio`). |
| `bridge.py` / `admission.py` / `identity.py` | Antigravity `SessionStart`, `PreInvocation` (`/mindie-agent`), `PreToolUse` one-shot bind, and `Stop` handoff. |
| `mcp_server.py` / `knowledge_cli.py` | Public deterministic MCP surfaces (`remote`, `tools`) and private lease-gated knowledge CLI. |
| `setup.py` / `component_update.py` / `storage_lifecycle.py` | Atomic `agy.json` / `agy-source` provisioning, candidate-first component updates, and bounded `.trash/` generation reclamation. |
| `agy_transcript.py` / `organizer.py` | Read-only Antigravity brain JSONL projection and isolated summary/C6 distillation worker. |
| `bounded.py` / `diagnostic_support.py` / `rsi_loop.py` | `mindie_harness.process` subprocess delegation, symlink-safe sanitized fault recording, and `mindie_harness.rsi` entrypoints. |

## 3. Local Verification

Isolate `HOME`, `MINDIE_CONFIG_HOME`, `MINDIE_DATA_HOME`, and `MINDIE_DIAGNOSTICS_ROOT` (enforced by `tests/conftest.py`), and verify Unix `LF` line endings before every commit:

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
```
