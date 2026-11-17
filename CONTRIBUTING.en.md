# Contributing to `mindie-agent-agy`

For the Chinese version, see [CONTRIBUTING.md](CONTRIBUTING.md).

`mindie-agent-agy` is the Google Antigravity native host plugin repository (`host-plugin` profile) in the MindIE Agent organization.

## 1. Workspace Layout

Develop against a flat sibling checkout or an isolated `.worktrees/` checkout:

```text
code/
  mindie-agent/           # Hub architecture docs, channel manifests, and org gates
  harness/                # Shared mindie-harness substrate
  mindie-agent-agy/       # Google Antigravity host plugin
  .worktrees/             # Isolated feature/refactoring worktrees
```

## 2. Non-Negotiable Host-Plugin Contracts

1. **Frozen Root `hooks.json`**: `plugins/mindie-agent/hooks.json` must stay byte-for-byte identical (`979` bytes, `sha256 == 33b564c78b439205fd4acfe095c83b15770043ff462d636e2a89f14620cdfc3f`).
2. **Default-Enabled Capabilities & `< 100ms` Sharing-Disabled `Stop`**: Enabling the plugin activates `mindie-remote-dev` and `mindie-tools` (including `knowledge_*` tools; `/mindie-agent deactivate` mutes a conversation); unconfigured or sharing-disabled `Stop` hooks must return `{}` in `< 100ms` with zero network I/O, zero `admission.sqlite3` creation, and zero transcript reads.
3. **Zero Pre-Call Binding on Deterministic Tools (`P2.7`)**: `mindie-remote-dev` and `mindie-tools` must succeed on standard MCP `tools/call` without `_meta`, `PreToolUse` binds, or `AGY_CONVERSATION_ID`.
4. **Windows Zero-Popup & Unix LF**: Every Windows subprocess call must include `CREATE_NO_WINDOW` (never `DETACHED_PROCESS` or `CREATE_NEW_CONSOLE`), and all tracked text files must use Unix `LF` (`\n`) line endings.

## 3. Test Isolation & Pre-PR Gate

Tests must isolate `HOME`, `USERPROFILE`, `MINDIE_CONFIG_HOME`, `MINDIE_DATA_HOME`, `MINDIE_DIAGNOSTICS_ROOT`, and `UV_CACHE_DIR` (never touching `~/.gemini`, `~/.config`, or `~/.local`).

Before opening or updating a PR, complete the `host-plugin` checklist (`HOST-NATIVE`, `HOST-WIRE`, `HOST-ADMISSION`, `HOST-UPGRADE`) in [`.github/pull_request_template.md`](.github/pull_request_template.md) and run:

```bash
python3 .github/scripts/check_lf_eol.py
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
pytest -q -m "not live_npu"
```
