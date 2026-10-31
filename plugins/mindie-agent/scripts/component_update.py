"""Independent shared component runtime updater for the Antigravity (AGY) adapter.

[Role]
Discovers newer formal GitHub Releases of shared MindIE runtime components
(`mindie-knowledge`, `remote-dev`, `mindie-diagnostics`, `mindie-profiler`,
`mindie-sim`, `npu-top`, `mindie-coordinator`) and builds an isolated component
generation under `state_dir()/component-runtimes` without requiring a new
`mindie-agent-agy` plugin release.

[Invariants]
- Monotonic Component Floor: `discover()` selects `max(installed, baseline, latest_formal_release)`
  and never downgrades an already-installed newer component.
- Atomic Profile Rebinding: `agy.json` and `agy.engine.json` are updated via
  `atomic_write_json` (`os.replace()`) only after the candidate generation passes
  `runtime_inventory` lock and VCS receipt verification.
- Never writes into `~/.gemini` and never activates a session lease.

[Timeouts & Locks]
- Enclosing budget defaults to `540.0s`: `15.0s` metadata read, `120.0s` release
  catalog discovery, `420.0s` `uv` runtime build with OS generation lock, and
  `5.0s` cleanup reserve (`outer_timeout >= sum(inner_budgets) + cleanup_grace`).

[Side-Effect Policy]
- Writes candidate runtime generations under `state_dir()/component-runtimes`
  and atomically updates `agy.json` / `agy.engine.json` when a newer component
  release is activated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import (
    atomic_write_json,
    community_config_path,
    config_path,
    engine_config_path,
    read_json,
    repo_root,
    state_dir,
)


def prepare(
    source: str | Path | None = None,
    python: str | Path | None = None,
    *,
    config: str | Path | None = None,
    force: bool = False,
    timeout: float = 540.0,
) -> dict | None:
    """Discover and build an updated component runtime generation if newer releases exist."""
    from mindie_knowledge.component_bindings import rebind_commands, required_components
    from mindie_knowledge.component_releases import Catalog
    from mindie_knowledge.component_runtime import build, discover, read_inventory, source_baseline

    deadline = time.monotonic() + float(timeout)

    def remaining(reserve: float = 0.0) -> float:
        avail = deadline - time.monotonic() - reserve
        if avail <= 0:
            raise TimeoutError("AGY component update exceeded its total budget")
        return avail

    cfg_file = config_path(config)
    adapter = read_json(cfg_file) if cfg_file.is_file() else {}
    source_root = Path(source or repo_root()).resolve()
    current_python = str(Path(python or adapter.get("python") or sys.executable).resolve())

    required = required_components(adapter, timeout=min(15.0, remaining(5.0)))
    baseline = source_baseline(source_root)
    selected = discover(
        current_python,
        baseline["runtime"],
        force=force,
        required_components=required,
        catalog=Catalog(budget=min(120.0, remaining(20.0))),
    )
    needs_binding = bool(adapter.get("component_commands")) and (
        rebind_commands(adapter, current_python, selected["runtime"]) != adapter.get("component_commands")
    )
    installed = selected["installed"]
    needs_build = installed != selected["runtime"]
    if not needs_build and not needs_binding:
        return None
    if (
        not needs_binding
        and {name: row["revision"] for name, row in selected["runtime"].items()} == baseline["runtime"]
        and not selected["changed"]
    ):
        return None

    artifact = build(
        source_root,
        state_dir(cfg_file) / "component-runtimes",
        baseline,
        selected["runtime"],
        python=current_python,
        timeout=min(420.0, remaining(5.0)),
    )
    return artifact


def check(
    *,
    config: str | Path | None = None,
    source: str | Path | None = None,
    force: bool = False,
    sync_feeds: bool = False,
    timeout: float = 540.0,
) -> dict:
    """Check for newer formal component releases, switch profile interpreter, and optionally sync feeds."""
    from mindie_knowledge.component_bindings import rebind_commands
    from mindie_knowledge.component_runtime import read_inventory
    from setup import ensure_default_profile

    cfg_file = config_path(config)
    if not cfg_file.is_file():
        ensure_default_profile(config=cfg_file)
    adapter = read_json(cfg_file)
    eng_file = engine_config_path(cfg_file)
    current_python = adapter.get("python") or sys.executable

    artifact = prepare(
        source=source or repo_root(),
        python=current_python,
        config=cfg_file,
        force=force,
        timeout=timeout,
    )
    if artifact is not None:
        new_python = str(Path(artifact["python"]).resolve())
        updated_adapter = dict(adapter)
        updated_adapter["python"] = new_python
        updated_adapter["component_runtime"] = artifact["components"]
        if updated_adapter.get("component_commands"):
            updated_adapter["component_commands"] = rebind_commands(
                updated_adapter,
                new_python,
                artifact["components"]["runtime"],
            )
        atomic_write_json(cfg_file, updated_adapter)
        if eng_file.is_file():
            eng_payload = read_json(eng_file)
            eng_payload["summary_command"] = [new_python, "-I", str(HERE / "organizer.py")]
            atomic_write_json(eng_file, eng_payload)
        status = "updated"
        active_python = new_python
        installed = artifact["installed"]
    else:
        status = "up_to_date"
        active_python = str(Path(current_python).resolve())
        try:
            installed = read_inventory(active_python, timeout=15.0)
        except Exception:
            installed = {}

    result: dict = dict(
        schema="mindie-component-update/1",
        harness="antigravity",
        status=status,
        python=active_python,
        installed=installed,
    )
    if sync_feeds and community_config_path(cfg_file).is_file() and eng_file.is_file():
        try:
            from mindie_knowledge.loop.cli import sync_feeds as run_sync_feeds

            result["feed_sync"] = run_sync_feeds(str(eng_file))
        except Exception as exc:
            result["feed_sync_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update MindIE shared runtime components for Antigravity")
    parser.add_argument("--config", default=None, help="Path to agy.json")
    parser.add_argument("--source", default=None, help="Path to mindie-agent-agy checkout")
    parser.add_argument("--force", action="store_true", help="Bypass release catalog cache")
    parser.add_argument("--sync-feeds", action="store_true", help="Also synchronize configured knowledge feeds")
    parser.add_argument("--timeout", type=float, default=540.0, help="Total update timeout in seconds")
    args = parser.parse_args(argv)
    payload = check(
        config=args.config,
        source=args.source,
        force=args.force,
        sync_feeds=args.sync_feeds,
        timeout=args.timeout,
    )
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
