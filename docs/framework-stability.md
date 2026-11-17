# Framework Stability & Persistent Configuration

<a href="framework-stability.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

`mindie-agent-agy` guarantees zero overhead during ordinary non-Ascend work (Passive Default) and consistent atomic persistence across Windows and POSIX platforms.

## 1. Passive Default Guarantees

- **`SessionStart`**: Always returns `{}` without starting background workers or syncing feeds.
- **Ordinary `PreInvocation`**: Immediately returns `{}` when `lastUserInput` does not invoke `/mindie-agent`.
- **Sharing-Disabled `Stop`**: Immediately returns `{}` in `< 100ms` when unconfigured or community sharing is disabled (`sharing_enabled == False`), without creating `admission.sqlite3` or reading the transcript file.

## 2. Atomic File Replacement & SQLite WAL Discipline

- JSON configuration files are updated atomically via `paths.py:atomic_write_json` (sibling temp file + `os.replace()`).
- Both `admission.sqlite3` and `remote_receipts.sqlite3` enable `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, and `PRAGMA busy_timeout=5000` (`5.0s`), with zero hot-path `.owner` lockfiles or runtime schema introspection.
