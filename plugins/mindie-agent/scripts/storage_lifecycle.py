"""[Role]
Post-switch storage reclamation and resident generation leasing for AGY:
retires superseded `component-runtimes` generations and cross-harness
`agy-source` baggage after a verified switch, pins a same-volume uv cache for
cold `agy-venv` bootstrap, and holds the shared runtime lease of the resident
MCP generation.

[Invariants]
- Active, rollback, cross-plugin borrowed, live-leased and external Git
  checkout roots are never retired; `mindie_knowledge` stays the only
  retire/liveness authority and this module only supplies bounded sets.
- Both phases run under one temporary `acquire_runtime_lease` per protected
  owning directory, so the legacy sweep keeps what Phase 1 kept. The switch
  persists the rollback interpreter as `previous_python` for later ticks.
- Phase 1 refuses when an ancestor between the data/state root and
  `component-runtimes` is a link, or when either the unresolved or resolved
  root is a Git checkout, forbidden root, or configured source tree. Phase 2
  is never narrowed to one config/data root: sibling pins stay visible.
- `reclaim_after_switch` is best-effort: it returns accounting and never
  raises `OSError`/`PermissionError`, so a verified update keeps
  `status="updated"` and reports `cleanup_warning`.
- A caller-supplied non-empty `UV_CACHE_DIR` is never overwritten.

[Timeouts & Locks]
- One caller budget (5.0s) is split across prune and sweep; knowledge helpers
  own the deadline and atomic two-phase rename. Leases are non-blocking OS
  locks. No unbounded wait, network, or model call.

[Side-Effect Policy]
- Creates `<generation>/.inuse.lock` only inside reachable MindIE-owned roots
  (never through a link into a foreign tree) and delegates bounded retirement
  to `mindie_knowledge.storage`.
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
HEALTHY_PHASE_STATUSES = frozenset({"completed", "absent", "skipped_cooldown"})


def _is_link(path: Path) -> bool:
    """Symlink/junction detection that also works without `mindie_knowledge`."""
    probe = Path(path)
    try:
        if probe.is_symlink():
            return True
    except TimeoutError:
        raise
    except OSError:
        return True
    checker = getattr(os.path, "isjunction", None)
    if checker is not None:
        try:
            if checker(probe):
                return True
        except TimeoutError:
            raise
        except (OSError, ValueError):
            return True
    if os.name != "nt":
        return False
    try:
        from mindie_knowledge.storage.lifecycle import is_junction
    except ImportError:
        return False
    try:
        return bool(is_junction(probe))
    except TimeoutError:
        raise
    except OSError:
        return True


def _first_link(anchor: Path, target: Path) -> Path | None:
    """First link component between `anchor` (inclusive) and `target`."""
    start, destination = Path(anchor), Path(target)
    try:
        relative = destination.relative_to(start)
    except ValueError:
        return None
    if _is_link(start):
        return start
    current = start
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            return current
    return None


def _skipped_prune(root: Path, status: str, warnings: list[str]) -> dict:
    """Honest non-completed Phase-1 accounting without touching the tree."""
    return {
        "status": status,
        "root": str(root),
        "reclaimed_dirs": 0,
        "deferred_dirs": 0,
        "kept_dirs": 0,
        "reclaimed_trash": 0,
        "warnings": list(warnings),
        "inspection_complete": False,
        "partial": True,
    }


def _root_anchor(component_root: Path, data_home, state_root) -> Path:
    """Nearest configured storage root that contains the runtimes root."""
    for candidate in (data_home, state_root):
        if candidate is None:
            continue
        try:
            if component_root.is_relative_to(Path(candidate)):
                return Path(candidate)
        except TimeoutError:
            raise
        except (OSError, ValueError):
            continue
    return component_root.parent


def _component_root_guard(component_root: Path, *, data_home, state_root,
                          forbidden, deadline) -> dict | None:
    """Refuse Phase 1 when the runtimes root escapes through a link or Git tree."""
    from mindie_knowledge.storage.lifecycle import has_git_ancestor, is_within_roots

    root = Path(component_root)
    anchor = _root_anchor(root, data_home, state_root)
    link = _first_link(anchor, root)
    if link is not None:
        return _skipped_prune(root, "skipped_link", [
            "component root crosses a link: " + link.name[:32]])
    resolved = _resolved(root)
    if has_git_ancestor(root):
        return _skipped_prune(root, "protected_root", [
            "component root is inside a Git checkout"])
    if has_git_ancestor(resolved):
        return _skipped_prune(root, "protected_root", [
            "resolved component root is inside a Git checkout"])
    if is_within_roots(root, forbidden) or is_within_roots(resolved, forbidden):
        return _skipped_prune(root, "protected_root", [
            "component root is inside a forbidden root"])
    try:
        from mindie_knowledge.storage.legacy_roots import protected_source_roots
    except ImportError:
        sources = set()
    else:
        sources = protected_source_roots(deadline=deadline)
    if sources and (is_within_roots(root, sources)
                    or is_within_roots(resolved, sources)):
        return _skipped_prune(root, "protected_root", [
            "component root is inside a configured source root"])
    return None


def _protected_owning_dirs(keep, *, anchor: Path, known_roots) -> list[Path]:
    """Managed generation directories a keep pin references, safely reachable."""
    from mindie_knowledge.storage import owning_dir

    directories: list[Path] = []
    seen: set[str] = set()
    for path in keep:
        try:
            owner = owning_dir(str(path))
        except TimeoutError:
            raise
        except Exception:  # noqa: BLE001 - an unmapped pin protects nothing extra
            owner = None
        if owner is None:
            continue
        try:
            if not owner.is_dir():
                continue
        except TimeoutError:
            raise
        except OSError:
            continue
        # Never create a lease inside a foreign tree reached through a link,
        # and never write a lease into an unrelated user directory.
        if _first_link(anchor, owner) is not None:
            continue
        if not any(_is_within(owner, item) for item in known_roots):
            continue
        key = os.path.normcase(str(_resolved(owner)))
        if key in seen:
            continue
        seen.add(key)
        directories.append(owner)
    return directories


def _is_within(path: Path, root: Path) -> bool:
    try:
        return Path(path).is_relative_to(Path(root))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return False


@contextlib.contextmanager
def _held_protected_leases(directories, *, runtimes_root: Path, report: dict):
    """Hold temporary `RuntimeLease`s so BOTH reclaim phases keep each owner."""
    from mindie_knowledge.storage import acquire_runtime_lease

    with contextlib.ExitStack() as stack:
        for directory in directories:
            try:
                stack.enter_context(acquire_runtime_lease(directory))
            except TimeoutError:
                raise
            except Exception as exc:  # noqa: BLE001 - cleanup never fails a switch
                if _is_within(directory, runtimes_root):
                    report["warnings"].append("lease " + type(exc).__name__)
        yield


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
                         data_home: str | Path | None = None,
                         state_root: str | Path | None = None,
                         budget_seconds: float = RETIREMENT_BUDGET_SECONDS,
                         keep_recent_verified: int = KEEP_RECENT_VERIFIED,
                         force_sweep: bool = False) -> dict:
    """Two-phase silent reclamation after a verified profile switch.

    Phase 1 prunes superseded `component-runtimes` generations; phase 2 sweeps
    cross-harness legacy baggage (`agy-source` generations, receipts, staging)
    through the library's canonical all-host discovery. The whole call is
    bounded by one budget and returns accounting only.

    Every protected owning directory is leased before either phase starts, so
    the rollback generation an explicit `keep_python` pin names survives the
    legacy sweep even when it has no live process and newer candidates fill the
    retention window. All-host discovery is never narrowed to one config/data
    root: sibling harness pins must stay visible to both phases.
    """
    from mindie_knowledge.storage import (
        prune_component_runtimes,
        sweep_legacy_installations,
    )

    started = time.monotonic()
    budget = max(0.0, float(budget_seconds))
    limit = started + budget
    root = Path(runtimes_root)
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
    anchor = _root_anchor(root, data_home, state_root)
    known_roots = [root] + [Path(item) for item in (data_home, state_root) if item is not None]
    leased = _protected_owning_dirs(keep, anchor=anchor, known_roots=known_roots)
    with _held_protected_leases(leased, runtimes_root=root, report=report):
        guard = _component_root_guard(
            root, data_home=data_home, state_root=state_root,
            forbidden=forbidden, deadline=prune_limit)
        if guard is not None:
            report["prune"] = guard
            report["warnings"].extend(guard["warnings"])
        else:
            try:
                report["prune"] = prune_component_runtimes(
                    root,
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
    prune = report["prune"] or {}
    sweep = report["sweep"] or {}
    if prune.get("partial") or sweep.get("partial"):
        report["status"] = "partial"
    degraded = [item.get("status") for item in (prune, sweep)
                if isinstance(item, dict)
                and item.get("status") not in HEALTHY_PHASE_STATUSES]
    if degraded and report["status"] == "completed":
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
    for phase in (prune, sweep):
        if isinstance(phase, dict):
            warnings.extend(str(item) for item in phase.get("warnings") or [] if item)
    warnings = list(dict.fromkeys(warnings))
    statuses = [item for item in (prune.get("status"), sweep.get("status"))
                if isinstance(item, str) and item not in HEALTHY_PHASE_STATUSES]
    if report.get("status") not in (None, "completed"):
        statuses.append(str(report["status"]))
    if not deferred and not warnings and not statuses:
        return None
    reclaimed = int(prune.get("reclaimed_dirs") or 0) + int(sweep.get("reclaimed_dirs") or 0)
    detail = f"reclaimed={reclaimed}, deferred={deferred}"
    if statuses:
        detail += "; status=" + ",".join(sorted(set(statuses)))
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
