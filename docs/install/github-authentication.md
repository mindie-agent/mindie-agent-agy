# GitHub sign-in during setup

`gh` authentication is an **optional enhancement**, never an installation gate. The one-line installer works without `gh`. Require a working `gh` channel only for the `contribute` tier (knowledge PRs and sanitized fault reporting).

1. Probe with a bounded wait: `gh api --hostname github.com user --jq .login` (3s). Reuse an existing working account, including `GH_TOKEN`, and skip the rest of this page on success.
2. If login is needed, prefer browser login (`gh auth login --hostname github.com --git-protocol https --web`) on a normal network. On enterprise proxies or custom CAs, prefer a token supplied through the environment; keep TLS verification enabled.
3. Receive tokens through `gh` or the environment, never through chat output, logs, or a tracked file. Do not print proxy credentials.
4. After login, verify the account and continue the install. If authentication remains unavailable, select `local-only` and continue; do not block plugin activation, remote NPU development, or knowledge reads.

See also [Development workspace setup](workspace-setup.md).
