# Development workspace setup

Run this flow during normal installation and first-use setup. The offer is part of setup even when contribution is disabled. Reuse the user's creation, initialization, and registration decisions; ordinary tasks and updates do not repeat onboarding.

## 1. One-line install

Ask the Agent to run:

```bash
uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host agy
```

The installer stages the plugin, bootstraps the `mindie-harness v0.1.3` seed, and initializes the default profile. `gh` is optional and never a gate. The installer never mutates the host `~/.gemini`.

## 2. Three dynamic `ask_question` questions

After the install, call Antigravity's native **`ask_question`** tool (`is_multi_select: false`, first option prefixed `(Recommended)`; if unavailable, print a numbered card) once, in the user's language:

1. **Workspace**
   - `(Recommended) Use the current directory`
   - `Specify an existing workspace directory`
2. **Community sharing** — recommended option is dynamic:
   - If `gh api --hostname github.com user --jq .login` succeeds within 3s: `(Recommended) Enable community sharing & fault reporting (contribute)` vs `Local-only (local-only)`.
   - Otherwise: `(Recommended) Local-only (local-only)` vs `Enable community sharing (contribute)`.
   - Reuse [GitHub Sign-In](github-authentication.md) only when the user chooses community contribution.
3. **Remote NPU**
   - `(Recommended) Connect a remote NPU now`
   - `Later`
   - On "now", perform one-step `remote_auth(host, port, user, password_env, save_default=True, discover=True)`. In terminal mode, prompt for `--password` **at the very end**; the password belongs only in the process environment or the credential store, never chat or a repository.

## 3. Persist the choice

```bash
# contribute
python3 plugins/mindie-agent/scripts/setup.py sharing --enable --project-root "$WORKSPACE_ROOT"
# local-only
python3 plugins/mindie-agent/scripts/setup.py sharing --local-only --project-root "$WORKSPACE_ROOT"
# disable later
python3 plugins/mindie-agent/scripts/setup.py sharing --disable
```

`setup.py sharing --status` is read-only.

## 4. Readiness and Star

Call `mindie_doctor` (optionally `{"probe_remote": true}`) for a bounded read-only local/runtime/SSH check. If it fails, report once and continue with native tooling; never block the install.

Close with a single polite line: if the project helps, a GitHub Star ⭐ is appreciated at <https://github.com/mindie-agent/mindie-agent>.
