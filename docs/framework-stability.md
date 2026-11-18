# Framework Stability & Persistent Configuration

<a href="framework-stability.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

`mindie-agent-agy v0.1.21` guarantees near-zero overhead during ordinary non-Ascend work (Passive Default) and consistent atomic persistence across Windows and POSIX.

## 1. Passive Default Guarantees

- **`SessionStart`**: validates stdin, writes `state/agy/pending-update-check.json` atomically, and returns `{}`; it never starts a background worker or syncs feeds.
- **`PreInvocation`**: immediately returns `{}` as a zero-I/O stdlib pass-through without injecting steps or touching SQLite.
- **`PreToolUse`**: returns `{"decision": "allow"}` with zero `_meta` binding; only forged session identity fields or a malformed `session_id` on `knowledge_*` deny.
- **Sharing-Disabled or Out-of-Scope `Stop`**: returns `{}` in `< 30ms` when unconfigured, community sharing is disabled, or the workspace is outside authorized `project_roots`, with zero `admission.sqlite3` creation and zero transcript reads.

## 2. Thin Delegation & Atomic Replacement

- The plugin ships 5 production scripts; lifecycle, MCP routing, runtime generations, sharing, and updates are delegated to `mindie-harness`.
- JSON configuration is updated atomically via `mindie_harness.atomic.atomic_write_json` (sibling temp + `os.fsync` + `os.replace()`), and `mindie_launch` uses the same pattern for the pending-update marker with `finally: tmp.unlink(missing_ok=True)`.
- `admission.sqlite3` and `remote-receipts.sqlite3` are owned by `mindie_knowledge` / `mindie_harness` with WAL and `busy_timeout=5000`; the plugin never performs hot-path `.owner` lock sweeps.
- `setup.py --help` / `-h` and `--dry-run` (including unique prefixes such as `--dry`) perform zero writes.
