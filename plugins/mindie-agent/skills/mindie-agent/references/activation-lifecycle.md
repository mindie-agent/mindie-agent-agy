# Activation & Capture Lifecycle in Antigravity

- **Manual Entry Only**: Only an explicit `/mindie-agent` or `$mindie-agent` prompt in `PreInvocation` grants `entry_enabled = 1` in `admission.sqlite3`.
- **Deactivation**: `/mindie-agent deactivate` revokes the conversation's lease across all registered domains; deterministic tools remain available.
- **Turn-End `Stop` Capture**: `bridge.py stop` checks `Admission.knowledge_lease(conversationId)`, community sharing consent (`mindie-community.json`), and project scope, then hands off `transcript_full.jsonl` (or `transcript.jsonl`) to `mindie_knowledge.loop.cli.capture_hook` without blocking or forcing model continuation.
