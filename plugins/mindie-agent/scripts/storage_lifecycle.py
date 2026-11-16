"""[Role]
Post-switch storage reclamation and resident-generation leasing for the AGY
adapter: retires superseded `component-runtimes` generations and cross-harness
`agy-source` baggage after a verified component switch, pins a same-volume uv
cache for cold `agy-venv` bootstrap, and holds the shared `acquire_runtime_lease`
of the generation that serves a resident MCP process.

[Invariants]
- An active generation, its most recent rollback generation, a cross-plugin
  borrowed interpreter (sibling host config pin), a live lease/process, and any
  configured external user Git checkout are never retired. `mindie_knowledge`
  remains the only retire/liveness authority; this module only supplies bounded
  keep/forbidden sets and accounting.
- `reclaim_after_switch` is best-effort: it returns an accounting dict and
  never raises `OSError`/`PermissionError`, so a verified update keeps
  `status="updated"` and reports `cleanup_warning` instead of failing.
- A caller-supplied non-empty `UV_CACHE_DIR` is never overwritten.

[Timeouts & Locks]
- Every reclaim call carries one caller budget (default 5.0s) split between a
  prune phase and a sweep phase; the knowledge helpers own the deadline and
  atomic two-phase rename. Leases are non-blocking OS locks released by the
  kernel on process exit. No unbounded wait, network, or model call.

[Side-Effect Policy]
- Creates `<generation>/.inuse.lock` leases and delegates bounded retirement to
  `mindie_knowledge.storage`; reads host config pins and the process table only
  through those shared helpers.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import sys
import time

RETIREMENT_BUDGET_SECONDS = 5.0
PRUNE_BUDGET_SHARE = 0.6
KEEP_RECENT_VERIFIED = 2
UV_CACHE_DIRECTORY = ".uv-cache"


def _existing_ancestor(path: Path) -> Path | None:
    probe = Path(path)
    while True:
        try:
            os.stat(probe)
            return probe
        except FileNotFoundError:
            if probe.parent == probe:
                return None
            probe = probe.parent
        except TimeoutError:
            raise
        except OSError:
            return None


def _device(path: Path) -> int | None:
    ancestor = _existing_ancestor(path)
    if ancestor is None:
        return None
    try:
        return os.stat(ancestor).st_dev
    except TimeoutError:
        raise
    except OSError:
        return None


def _resolved(path: Path) -> Path:
    try:
        return Path(path).resolve()
    except TimeoutError:
        raise
    except OSError:
        return Path(path).absolute()


def _uv_default_cache(env: dict) -> Path:
    xdg = env.get("XDG_CACHE_HOME")
    if isinstance(xdg, str) and xdg.strip():
        return Path(xdg) / "uv"
    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA") \
            or str(Path.home() / "AppData" / "Local")
        return Path(base) / "uv" / "cache"
    home = Path.home()
    if sys.platform == "darwin":
        legacy = home / ".cache" / "uv"
        try:
            if legacy.exists() or legacy.is_symlink():
                return legacy
        except TimeoutError:
            raise
        except OSError:
            pass
        return home / "Library" / "Caches" / "uv"
    return home / ".cache" / "uv"


def _same_volume_cache_dir(target_dir: Path) -> Path:
    target = _resolved(target_dir)
    target_device = _device(target)
    for candidate in (target.parent / UV_CACHE_DIRECTORY,
                      target / UV_CACHE_DIRECTORY):
        if target_device is not None and _device(candidate) == target_device:
            return candidate
    return target.parent / UV_CACHE_DIRECTORY


def _stdlib_uv_cache(target_dir: Path, env: dict) -> dict:
    explicit = env.get("UV_CACHE_DIR")
    if isinstance(explicit, str) and explicit.strip():
        return env
    target = _resolved(target_dir)
    target_device = _device(target)
    if target_device is None:
        return env
    cache = _uv_default_cache(env)
    if target_device in {_device(cache), _device(_resolved(cache))}:
        return env
    chosen = _same_volume_cache_dir(target)
    if _device(chosen) != target_device:
        return env
    env["UV_CACHE_DIR"] = str(chosen)
    return env


def configure_uv_cache(target_dir: str | Path, env: dict) -> dict:
    """Pin uv's cache to the target volume when the platform default crosses it.

    Prefers the shared `mindie_knowledge.storage` helper; the stdlib mirror
    exists only because a cold `agy-venv` bootstrap runs in an interpreter where
    no MindIE component (and possibly no `mindie_knowledge.storage` module) is
    importable yet.
    """
    if not isinstance(env, dict):
        return env
    explicit = env.get("UV_CACHE_DIR")
    if isinstance(explicit, str) and explicit.strip():
        return env  # Never overwrite the caller's explicit cache choice.
    try:
        from mindie_knowledge.storage import configure_same_volume_uv_cache
    except ImportError:
        return _stdlib_uv_cache(target_dir, env)
    try:
        return configure_same_volume_uv_cache(Path(target_dir), env)
    except TimeoutError:
        raise
    except OSError:
        return _stdlib_uv_cache(target_dir, env)


def runtime_generation(executable: str | Path | None = None) -> Path | None:
    """The managed generation that owns `executable`, when one exists."""
    try:
        from mindie_knowledge.storage import owning_dir
    except ImportError:
        return None
    target = os.fspath(executable or sys.executable)
    try:
        owner = owning_dir(str(target))
    except TimeoutError:
        raise
    except Exception:  # noqa: BLE001 - an unmapped path is not an MCP startup error
        return None
    if owner is None:
        return None
    try:
        return Path(owner) if Path(owner).is_dir() else None
    except TimeoutError:
        raise
    except OSError:
        return None


@contextlib.contextmanager
def hold_runtime_lease(executable: str | Path | None = None):
    """Hold a shared lease on the serving generation for this process lifetime.

    Yields the lease (or `None` when the executable is not inside a managed
    generation or the lease cannot be created). A missing/read-only generation
    must never prevent the MCP server from starting.
    """
    generation = runtime_generation(executable)
    if generation is None:
        yield None
        return
    try:
        from mindie_knowledge.storage import acquire_runtime_lease
    except ImportError:
        yield None
        return
    try:
        lease = acquire_runtime_lease(generation)
    except TimeoutError:
        raise
    except Exception:  # noqa: BLE001 - degrade to no lease instead of failing MCP
        yield None
        return
    try:
        yield lease
    finally:
        try:
            lease.release()
        except TimeoutError:
            raise
        except OSError:
            pass


def _normalized(values) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        try:
            path = Path(os.fspath(value)).expanduser()
        except (TypeError, ValueError):
            continue
        if not path.is_absolute():
            continue
        try:
            key = os.path.normcase(str(path.resolve()))
        except TimeoutError:
            raise
        except OSError:
            key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def reclaim_after_switch(*, runtimes_root: str | Path, keep_python=(),
                         forbidden_roots=(),
                         budget_seconds: float = RETIREMENT_BUDGET_SECONDS,
                         keep_recent_verified: int = KEEP_RECENT_VERIFIED,
                         force_sweep: bool = False) -> dict:
    """Two-phase silent reclamation after a verified profile switch.

    Phase 1 prunes superseded `component-runtimes` generations; phase 2 sweeps
    cross-harness legacy baggage (`agy-source` generations, receipts, staging)
    through the library's canonical all-host discovery. The whole call is
    bounded by one budget and returns accounting only.
    """
    from mindie_knowledge.storage import (
        prune_component_runtimes,
        sweep_legacy_installations,
    )

    started = time.monotonic()
    budget = max(0.0, float(budget_seconds))
    limit = started + budget
    keep = _normalized(keep_python)
    forbidden = _normalized(forbidden_roots)
    report: dict = {
        "schema": "mindie-agy-storage-reclaim/1",
        "status": "completed",
        "prune": None,
        "sweep": None,
        "warnings": [],
        "elapsed_seconds": 0.0,
    }
    prune_limit = min(limit, time.monotonic() + budget * PRUNE_BUDGET_SHARE)
    try:
        report["prune"] = prune_component_runtimes(
            Path(runtimes_root),
            keep_paths=keep,
            keep_recent_verified=max(0, int(keep_recent_verified)),
            budget_seconds=max(0.0, prune_limit - time.monotonic()),
            forbidden_roots=forbidden,
            deadline=prune_limit,
        )
    except TimeoutError:
        report["status"] = "partial"
        report["warnings"].append("runtime prune budget exhausted")
    except Exception as exc:  # noqa: BLE001 - cleanup never fails a switched update
        report["status"] = "partial"
        report["warnings"].append("runtime prune " + type(exc).__name__)
    if time.monotonic() < limit:
        try:
            report["sweep"] = sweep_legacy_installations(
                budget_seconds=max(0.0, limit - time.monotonic()),
                force=force_sweep,
                deadline=limit,
            )
        except TimeoutError:
            report["status"] = "partial"
            report["warnings"].append("legacy sweep budget exhausted")
        except Exception as exc:  # noqa: BLE001 - cleanup never fails a switched update
            report["status"] = "partial"
            report["warnings"].append("legacy sweep " + type(exc).__name__)
    else:
        report["status"] = "partial"
        report["warnings"].append("legacy sweep budget exhausted")
    if (report["prune"] or {}).get("partial") or (report["sweep"] or {}).get("partial"):
        report["status"] = "partial"
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def cleanup_warning(report) -> str | None:
    """Compact honest warning for a switched profile whose GC was deferred."""
    if not isinstance(report, dict):
        return None
    prune = report.get("prune") or {}
    sweep = report.get("sweep") or {}
    deferred = int(prune.get("deferred_dirs") or 0) + int(sweep.get("deferred_dirs") or 0)
    warnings = [str(item) for item in report.get("warnings") or [] if item]
    if not deferred and not warnings and report.get("status") == "completed":
        return None
    reclaimed = int(prune.get("reclaimed_dirs") or 0) + int(sweep.get("reclaimed_dirs") or 0)
    detail = f"reclaimed={reclaimed}, deferred={deferred}"
    if warnings:
        detail += "; " + warnings[0][:160]
    return "post-update cleanup: " + detail


__all__ = [
    "KEEP_RECENT_VERIFIED",
    "PRUNE_BUDGET_SHARE",
    "RETIREMENT_BUDGET_SECONDS",
    "cleanup_warning",
    "configure_uv_cache",
    "hold_runtime_lease",
    "reclaim_after_switch",
    "runtime_generation",
]
