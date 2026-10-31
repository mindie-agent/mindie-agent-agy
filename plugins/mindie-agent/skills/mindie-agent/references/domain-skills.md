# Domain Tooling & NPU Discipline

## 1. Graded Knowledge Tools (`knowledge_cli.py`)

- `knowledge_query`: Searches graded `L1–L4` units and task blocks. Returns bounded `cards` and deduplicated `foundation_cards`.
- `knowledge_explain`: Reads exact `mindie://<domain>/units/<id>@<rev>` references and expands `requires` once.
- `knowledge_feedback`: Records `discovered`, `loaded`, `applied`, or `evaluated` (`supported` | `refuted` | `inconclusive`) usage events.
- `knowledge_distill`: Distills verified milestone (`task_state="verified_milestone"`) or completed (`task_state="completed"`) evidence into graded `L1–L4` units. `L4` mutators require a verified leave-one-out ablation receipt.

## 2. Remote NPU Execution & Coordinator Leases

- Acquire NPU and port leases through `coordinator` before running device workloads (`vram_free_mb >= Expected VRAM`; respect Ascend A3 910C dual-Die `Phy-ID 0..15` package boundaries).
- Track long-running jobs in `PROGRESS.md` under `in_flight_runs` before yielding or ending the turn.
