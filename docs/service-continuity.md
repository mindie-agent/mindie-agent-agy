# Service Identity & Unified Consent Continuity

<a href="service-continuity.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

`mindie-agent-agy` manages persistent configuration outside volatile host caches through [`setup.py`](../plugins/mindie-agent/scripts/setup.py) and `mindie_harness.paths.ProfileRef`, enforcing **One Fact, One Authority**.

## 1. Configuration Authorities

| File | Schema | Responsibility |
|---|---|---|
| `agy.json` | `mindie-adapter-config/1` | Adapter binding: Python interpreter, `runtime_scripts`, `engine_config`, `source_root`, and `admission.sqlite3` path |
| `agy.engine.json` | `mindie-engine-config/1` | `mindie_knowledge` engine config: binds `transcript_adapter` (`agy_transcript.py`), `redactor_executable`, `root`, and `admission_path` |
| `mindie-community.json` | `mindie-community-config/1` | Shared community contribution authority (validated and locked via `mindie_knowledge.loop.settings.CommunityWriteContext`) |
| `mindie-consent.json` | `mindie-consent/1` | Unified contribution and fault-reporting consent authority (`contribute`, `local-only`, `disabled`) |
| `state/agy/pending-update-check.json` | `mindie-pending-update/1` | `< 5ms` SessionStart handoff consumed by `mindie_harness.runtime.activation.write_pending_update_check` |

## 2. Cross-Session Persistence & Atomic Replacement

- All configuration writes use `mindie_harness.atomic.atomic_write_json` (sibling temp + `os.fsync` + `os.replace()`).
- Re-running `setup.py init` or upgrading the plugin preserves existing `mindie-community.json` and `mindie-consent.json` choices intact.
- Generation activation writes `agy.json` LAST under `mindie_harness` locks; an explicitly revoked admission lease is never reactivated by `Stop`.
