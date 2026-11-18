# Domain Tooling & NPU Discipline

## 1. Graded Knowledge Tools (`mindie-tools` MCP)

- `knowledge_query`: Searches graded `L1–L4` units and task blocks (`token_budget` `1..64000`, default `8000` in standard/reuse mode and `24000` in `mode="explore"`). Returns bounded `cards` and deduplicated `foundation_cards`.
- `knowledge_explain`: Reads exact `mindie://<domain>/units/<id>@<rev>` references (`ref` or `refs`) and expands `requires` once (`token_budget` `1..64000`, default `16000` in reuse/diagnose and `24000` in `mode="explore"`).
- `knowledge_feedback`: Records `discovered`, `loaded`, `applied`, or `evaluated` (`supported` | `refuted` | `inconclusive`) usage events.
- `knowledge_distill`: Distills verified milestone (`task_state="verified_milestone"`) or completed (`task_state="completed"`) evidence into graded `L1–L4` units. `L4` mutators require a verified leave-one-out ablation receipt on the target operator or runtime surface.
- `knowledge_sync`: Synchronizes public graded knowledge feeds for the active domain.

## 2. Antigravity `remote-dev` Profile (12 Native Tools)

`mindie-remote-dev` runs with `REMOTE_DEV_HARNESS_PROFILE=agy` and exposes exactly:

`remote_read_file`, `remote_write_file`, `remote_edit_file`, `remote_run_command`, `remote_manage_task`, `remote_find_by_name`, `remote_grep_search`, `remote_list_dir`, `remote_auth`, `remote_probe`, `remote_artifact_pull`, `remote_artifact_push`.

- **One-step SSH connection**: call `remote_auth(host=..., port=..., user=..., password_env=..., save_default=True, discover=True)`. The password stays in the process environment / credential store; never put it in chat or a tracked file.
- **Task continuity**: if a remote tool is unavailable, continue with native `ssh` / `scp` / `git` after reporting the failure once. Reconcile `unknown` writes before retrying.

## 3. `mindie_doctor` Readiness Check

Call `mindie_doctor` (`{"probe_remote": true}` for bounded SSH probing) when install, runtime, credentials, or NPU readiness is uncertain. It is read-only: it does not start the knowledge loop, mutate consent, upload diagnostics, or install components. Continue the task with native tooling if the doctor itself fails.

## 4. Remote NPU Execution & Coordinator Leases

- Acquire NPU and port leases through `coordinator` before running device workloads (`vram_free_mb >= Expected VRAM`; respect Ascend A3 910C dual-Die `Phy-ID 0..15` package boundaries).
- Address background `remote-dev` jobs by `job_id` and `coordinator` runs by `execution_id`; inspect live handle status before replacing or stopping an in-flight run.

## 5. Consent & Sharing Commands

- `setup.py sharing --status` is read-only.
- `setup.py sharing --enable --project-root <ABS>` selects the `contribute` tier (community PRs + sanitized fault reporting).
- `setup.py sharing --local-only --project-root <ABS>` captures locally without reporting.
- `setup.py sharing --disable` stops new capture and reporting.
