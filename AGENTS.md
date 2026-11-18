# MindIE Agent — Google Antigravity Native Host Plugin (`mindie-agent-agy`)

`mindie-agent-agy` is a **`host-plugin`** repository implementing the Google Antigravity (`agy`) native host plugin (`plugins/mindie-agent/`). In `v0.1.21` the plugin is a **5-script thin adapter** over `mindie-harness`: native hooks (`plugins/mindie-agent/hooks.json`), stdio MCP servers (`mcp_config.json`), the `mindie-agent` skill, and brain JSONL transcripts (`agy_transcript.py`).

## 1. Core Architectural & Contract Rules

- **Frozen Root `hooks.json` Contract**: `plugins/mindie-agent/hooks.json` MUST remain byte-for-byte identical (`979` bytes, `sha256 == 33b564c78b439205fd4acfe095c83b15770043ff462d636e2a89f14620cdfc3f`) at the plugin root (never `hooks/hooks.json`).
- **Silent Default-Enabled, `< 30ms` Hooks**:
  - Enabling the plugin silently activates `mindie-remote-dev` (native `agy` 12-tool profile), `mindie-tools` (component tools + `mindie_doctor` + `knowledge_*`), and the skill (`disable-slash-command: true`).
  - `hook pre-invocation` validates a JSON object and prints `{}` with zero I/O. `hook pre-tool-use` prints `{"decision": "allow"}` and rejects forged `_session_id` / `_activation` / `mindie_activation` keys (or malformed `session_id` on `knowledge_*`).
  - `hook session-start` atomically writes `<data>/state/agy/pending-update-check.json` (`mindie-pending-update/1`) via a sibling temp + `os.replace`, with `finally: tmp.unlink(missing_ok=True)`.
  - `hook stop` emits `{}` in `< 30ms` when unconfigured, sharing-disabled, or out-of-scope, creates zero SQLite, and **NEVER emits `"decision": "continue"`**. Only an authorized in-scope Stop activates a default lease (`not_before=0.0`) and hands off `capture_hook`.
- **Zero Pre-Call Session-Binding Gates (`P2.7`)**: standard `tools/call` on `remote_*` and `mindie-tools` works with zero `_meta`, zero `PreToolUse` binds, and zero `AGY_CONVERSATION_ID`, falling back to the MCP connection scope.
- **Thin Delegation to `mindie-harness`**:
  - `mcp_server.py` subclasses `ManagedMCPAdapter`; `setup.py` delegates init/sharing/doctor/update/release-metadata; `bridge.py` uses `ProfileRef`, `sharing_status`, and `write_pending_update_check`.
  - Bundled `assets/mindie-harness-seed.pyz` is verified in `64 KiB` chunks against `assets/seed-lock.json` (`mindie-harness-seed/1`); `plugins/mindie-agent/runtime-requirements.txt` lets copied plugin payloads cold-bootstrap.
- **State & Receipt Honesty (Principles 9, 10, 14, 17)**:
  - Keep every `plugins/mindie-agent/scripts/*.py` file `< 800` lines and preserve `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in the top 30 lines.
  - Always place `except TimeoutError: raise` before `except OSError:` or broad `except Exception:`. Once a primary remote/component operation succeeds, local receipt/bind accounting failures keep `isError=False` and attach `receipt_warning` + `receipt_unconfirmed=True`.
  - Every `subprocess.run` / `Popen` passes `creationflags=CREATE_NO_WINDOW` (`0x08000000` on Windows); never reference `CREATE_NEW_CONSOLE` / `DETACHED_PROCESS`, never use unguarded `os.kill(pid, 0)`, never import `torch_npu` at top level, and never mutate `~/.gemini`.

## 2. Plugin Script Map (`plugins/mindie-agent/scripts/`)

| Module | Role |
| :--- | :--- |
| `mindie_launch.py` | Pure-stdlib entrypoint for `hook`, `mcp`, `setup`, `update`, `doctor`, and `sharing`; `< 30ms` hook fast-paths, seed verification, cold handoff, and active-Python re-exec. |
| `bridge.py` | `handle_session_start`, `handle_pre_invocation`, `handle_pre_tool_use`, `handle_stop`, and `main`; preserves explicit lease revocation. |
| `mcp_server.py` | `AGYManagedAdapter` (`host="agy"`), `serve(surface)`, and `main`; 12 native remote tools, `mindie_doctor`, 5 `knowledge_*` tools, and Receipt Honesty. |
| `agy_transcript.py` | Public `FileIdentity`, `identify`, `to_seconds`, `capture_source`, `read_material`, and `read_increment` for `transcript_full.jsonl`. |
| `setup.py` | `init` (default), `sharing --status/--enable/--local-only/--disable`, `doctor`, `update`, `release-metadata`, and read-only `--help` / `--dry-run`. |
| `__init__.py` | Minimal package marker. |

## 3. Local Verification

Isolate `HOME`, `MINDIE_CONFIG_HOME`, `MINDIE_DATA_HOME`, and `MINDIE_DIAGNOSTICS_ROOT` (enforced by `tests/conftest.py`), and verify Unix `LF` line endings before every commit:

```bash
python3 .github/scripts/check_lf_eol.py
shasum -a 256 plugins/mindie-agent/hooks.json
wc -l plugins/mindie-agent/scripts/*.py
pytest -q -m "not live_npu"
```

The suite covers frozen hooks/MCP config, seed verification, compatibility AST resolution, cold/disabled/out-of-scope Stop fast-paths, authorized local-only/contribute capture with revocation, 12-tool remote catalogs, zero-`_meta` knowledge reads with Receipt Honesty, setup read-only guarantees, sharing tiers, and harness generation activation/rebinding.
