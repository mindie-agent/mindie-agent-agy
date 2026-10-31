# Service Identity & Unified Consent Continuity

<a href="service-continuity.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

`mindie-agent-agy` manages persistent configuration files outside volatile host caches through [`setup.py`](../plugins/mindie-agent/scripts/setup.py) and [`paths.py`](../plugins/mindie-agent/scripts/paths.py), enforcing **One Fact, One Authority**.

## 1. Configuration Authorities

| File | Schema | Responsibility |
|---|---|---|
| `agy.json` | `mindie-adapter-config/1` | Adapter binding: Python interpreter, runtime scripts directory, `agy.engine.json`, and `admission.sqlite3` path |
| `agy.engine.json` | `mindie-engine-config/1` | `mindie_knowledge` engine config: binds `transcript_adapter` (`agy_transcript.py`), `summary_command` (`organizer.py`), and `redactor_executable` |
| `mindie-community.json` | `mindie-community-config/1` | Shared community contribution authority (validated and locked via `mindie_knowledge.loop.settings.CommunityWriteContext`) |
| `mindie-consent.json` | `mindie-consent/1` | Unified contribution and fault-reporting consent authority (managed via `mindie_knowledge.consent_store.record_choice`) |

## 2. Cross-Session Persistence & Atomic Replacement

- All configuration writes use a sibling temporary file + `os.fsync` + `os.replace()`.
- Re-running `setup.py init` or upgrading the plugin preserves existing `mindie-community.json` and `mindie-consent.json` choices intact.
