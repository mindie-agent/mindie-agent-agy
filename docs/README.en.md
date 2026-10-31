# MindIE Agent for Google Antigravity · Documentation Index

<a href="README.md">中文</a> | <b>English</b> · <a href="../README.en.md">Back to Repository Home</a>

This directory contains the Harness interface verification records, public transcript capture pipeline, service continuity and consent authority design, failure diagnostics and timeout arithmetic, framework stability, and testing contract specifications for `mindie-agent-agy`. Every document is available in both English and Chinese.

## Technical Guides

| English Document | 中文 | Summary |
|---|---|---|
| [Harness Interface Evidence](harness-interfaces.md) | [Antigravity Harness 接口兼容性证据](harness-interfaces.zh.md) | Registered interfaces in `harness-compatibility.json`, plugin-root `hooks.json` layout, `PreInvocation` activation, `PreToolUse` one-shot bind, and subagent `conversationId` isolation |
| [Public Transcript Capture](public-transcript.md) | [公开对话采集与本地脱敏](public-transcript.zh.md) | Automatic promotion from `transcript.jsonl` to `transcript_full.jsonl`, stripping `thinking` / `tool_calls` / `<ADDITIONAL_METADATA>`, and isolated indexing |
| [Service Identity & Consent Continuity](service-continuity.md) | [服务身份与统一授权连续性](service-continuity.zh.md) | `agy.json` / `agy.engine.json` bindings and persistent `mindie-community-config/1` & `mindie-consent/1` authorities |
| [Failure Diagnostics & Receipt Honesty](failure-diagnostics.md) | [故障诊断、超时算术与回执诚实性](failure-diagnostics.zh.md) | `660s >= 600s + 60s` timeout arithmetic, `receipt_unconfirmed` remote receipt preservation, `outcome_unknown` replay prevention, and bounded process tree cleanup |
| [Framework Stability](framework-stability.md) | [框架稳定性与持久化配置](framework-stability.zh.md) | Passive default guarantees, cross-platform atomic file replacement, and configuration isolation |
| [Test Boundaries & Contracts](testing-contracts.md) | [测试边界与契约校验](testing-contracts.zh.md) | Tier-0 contract test matrix, `harness-compatibility.json` verification, and LF format gate |
| [Release v0.1.0 Notes](releases/mindie-agent-agy-0.1.0.en.md) | [Release v0.1.0 发布说明](releases/mindie-agent-agy-0.1.0.md) | Initial `v0.1.0` capabilities, supported interfaces, and pinned component versions |
