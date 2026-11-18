# Public Transcript Capture & Local Redaction (Antigravity Brain JSONL)

<a href="public-transcript.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Back to Documentation Index</a>

`mindie-agent-agy` implements the `public-transcript` capture contract in [`agy_transcript.py`](../plugins/mindie-agent/scripts/agy_transcript.py), exporting `FileIdentity`, `identify`, `capture_source`, `read_material`, and `read_increment`.

## 1. Automatic Promotion from `transcript.jsonl` to `transcript_full.jsonl`

Antigravity stores conversation logs under `<appDataDir>/brain/<conversationId>/.system_generated/logs/`:
- `transcript.jsonl` is the compact log and may carry `"truncated_fields": ["content"]`;
- `transcript_full.jsonl` stores the complete, untruncated JSONL records.

Whenever `capture_source()` receives `transcript.jsonl` and a sibling `transcript_full.jsonl` exists, it automatically promotes the source path to `transcript_full.jsonl`.

## 2. Strict Whitelist Filtering Rules

| Record Type | Capture Behavior |
|---|---|
| `type == "USER_INPUT"` and `source == "USER_EXPLICIT"` | Extracts text inside `<USER_REQUEST>...</USER_REQUEST>` while stripping `<ADDITIONAL_METADATA>`, `<USER_SETTINGS_CHANGE>`, `<SYSTEM_MESSAGE>`, and `<EPHEMERAL_MESSAGE>` blocks |
| `type == "PLANNER_RESPONSE"` and `source == "MODEL"` | Extracts only visible `content`; **strictly excludes** `thinking` (internal chain-of-thought) and `tool_calls` |
| All other step types (`GENERIC` tool outputs, system steps) | Ignored and never exported |
| Lease Timestamp Boundary (`not_before`) | Steps recorded before the lease's `activated_at` boundary (`0.0` by default) are skipped |

## 3. Incremental Cursor & Local Redaction

- `FileIdentity` hashes the first 512 bytes of the JSONL file and tracks byte length so incremental reads (`offset > 0`) detect truncation or file replacement immediately.
- `read_material` computes exact `[start, end)` SHA-256 accounting and returns `unchanged`, `replaced`, `wrong-task`, `invalid-boundary`, or `unknown-format` honestly.
- `bridge.handle_stop` hands the authorized transcript to `mindie_knowledge.loop.cli.capture_hook`; `mindie_knowledge` performs deterministic redaction, Gitleaks scanning, navigation indexing, and C6 distillation. No separate summary worker is shipped in `v0.1.21`.
