"""Independent shared component runtime updater for the Antigravity (AGY) adapter.

[Role]
Discovers newer formal GitHub Releases of the shared MindIE runtime components
and builds an isolated generation under `state_dir()/component-runtimes`
without requiring a new `mindie-agent-agy` plugin release.

[Invariants]
- Monotonic Component Floor: `discover()` selects `max(installed, baseline, latest_formal_release)`
  and never downgrades an already-installed newer component.
- Atomic Profile Rebinding: `agy.json` / `agy.engine.json` are updated via
  `atomic_write_json` (`os.replace()`) only after lock and VCS verification.
- Background spawn writes `in_progress` with `pid` only after `Popen` returns;
  an unreaped zombie pid is reclaimed instead of blocking the next check.
  Spawn/status-write failure kills the untracked child; `read_inventory`
  failure publishes `status="failed"` rather than `up_to_date`.
- One cross-process update mutex (`<data_home>/agy-component-update.lock`)
  serializes `check()`; contention returns `status="busy"` after a bounded wait.
- After the atomic switch, a bounded best-effort cleanup retires superseded
  generations and legacy baggage, persisting `previous_python` and keeping
  `status="updated"` + `cleanup_warning`; failures keep a structured category.

[Timeouts & Locks]
- Enclosing budget defaults to `540.0s`: `15.0s` metadata read, `120.0s` release
  catalog discovery, `420.0s` `uv` runtime build with OS generation lock, and
  `5.0s` cleanup reserve (`outer_timeout >= sum(inner_budgets) + cleanup_grace`).
- The update mutex waits at most `UPDATE_LOCK_WAIT_SECONDS` (2.0s); the
  post-switch reclaim owns a separate `RETIREMENT_BUDGET_SECONDS` (5.0s).

[Side-Effect Policy]
- Writes candidate runtime generations under `state_dir()/component-runtimes`,
  atomically updates `agy.json` (including the `previous_python` rollback pin) /
  `agy.engine.json`, and delegates bounded silent reclamation of superseded
  generations to `mindie_knowledge.storage`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

_REAL_OS = os

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import run as bounded_run
from paths import (
    apply_enterprise_transport_env,
    atomic_write_json,
    community_config_path,
    config_path,
    data_home,
    engine_config_path,
    plugin_root,
    read_json,
    repo_root,
    state_dir,
)
from update_failures import (
    INTERNAL,
    MAX_OUTPUT_CHARS,
    UpdateFailure,
    attach_stage,
    child_failure,
    classify_exception,
    emit_failure_marker,
    stage_of,
)

UPDATE_CHECK_INTERVAL = 300.0
UPDATE_FAILURE_BACKOFF = 60.0
UPDATE_LOCK_NAME = "agy-component-update.lock"
UPDATE_LOCK_WAIT_SECONDS = 2.0


class UpdateLockBusy(TimeoutError):
    """Another process holds the cross-process component update mutex."""


def _record_background_update_failure(status_file: Path, checked_at: float, exc: BaseException) -> None:
    detail = f"{type(exc).__name__}: {exc}"
    print(f"MindIE background component update failed to start: {detail}", file=sys.stderr)
    try:
        atomic_write_json(
            status_file,
            {"checked_at": checked_at, "status": "failed", "error": detail},
        )
    except TimeoutError:
        raise
    except OSError as write_exc:
        print(
            f"MindIE background update status write failed: {type(write_exc).__name__}: {write_exc}",
            file=sys.stderr,
        )


def _pid_running(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        return _pid_running_windows(int(pid))
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except TimeoutError:
        raise
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except TimeoutError:
        raise
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_running_windows(pid: int) -> bool:
    """Return whether `pid` is alive. Query failure must not raise or fake 'dead'."""
    import ctypes
    from ctypes import wintypes

    error_access_denied = 5
    process_query_limited_information = 0x1000
    process_query_information = 0x0400
    still_active = 259
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            handle = kernel32.OpenProcess(process_query_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == error_access_denied
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return int(code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    except TimeoutError:
        return True
    except OSError:
        return True
    except Exception:
        return False


def _lock_descriptor(fd: int) -> None:
    """Acquire one non-blocking exclusive lock on an existing descriptor."""
    if _REAL_OS.name == "nt":
        import msvcrt

        if _REAL_OS.fstat(fd).st_size == 0:
            _REAL_OS.write(fd, b"\0")
        _REAL_OS.lseek(fd, 0, _REAL_OS.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except TimeoutError:
            raise
        except OSError as exc:
            # msvcrt reports contention as a plain OSError, never BlockingIOError.
            raise BlockingIOError(exc.errno or 13, "file lock is held") from None
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(fd: int) -> None:
    if _REAL_OS.name == "nt":
        import msvcrt

        _REAL_OS.lseek(fd, 0, _REAL_OS.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def _exclusive_file_lock(path: Path, *, timeout: float):
    """One bounded OS-lock acquisition; contention raises `TimeoutError`."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = _REAL_OS.open(str(path), _REAL_OS.O_CREAT | _REAL_OS.O_RDWR, 0o600)
    locked = False
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        while True:
            try:
                _lock_descriptor(fd)
                locked = True
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out acquiring {path.name}") from exc
                time.sleep(0.01)
        yield path
    finally:
        try:
            if locked:
                _unlock_descriptor(fd)
        finally:
            _REAL_OS.close(fd)


@contextlib.contextmanager
def _status_file_lock(status_file: Path, *, timeout: float = 2.0):
    """Synchronize `component-update-status.json` updates across parent and child."""
    with _exclusive_file_lock(status_file.with_suffix(".lock"), timeout=timeout) as held:
        yield held


@contextlib.contextmanager
def update_lock(path: str | Path | None = None, *, timeout: float = UPDATE_LOCK_WAIT_SECONDS):
    """Cross-process mutex serializing `check()` build/switch phases.

    Non-blocking attempts with one bounded wait; contention raises
    `UpdateLockBusy` (a `TimeoutError`) so the caller can report `busy`
    instead of racing a second build or double-switching `agy.json`.
    """
    target = Path(path) if path is not None else data_home() / UPDATE_LOCK_NAME
    holder = _exclusive_file_lock(target, timeout=timeout)
    try:
        holder.__enter__()
    except TimeoutError as exc:
        raise UpdateLockBusy(f"concurrent component update holds {target.name}") from exc
    try:
        yield target
    finally:
        holder.__exit__(None, None, None)


def trigger_background_update_if_due(*, config: str | Path | None = None) -> bool:
    """Spawn a non-blocking background `component_update.py` check if the interval has elapsed."""
    if os.environ.get("MINDIE_CONFIG_HOME") is not None:
        return False
    cfg_file = config_path(config)
    if not cfg_file.is_file():
        return False
    status_file = data_home() / "component-update-status.json"
    now = time.time()
    with _status_file_lock(status_file):
        if status_file.is_file():
            data = read_json(status_file)
            checked = float(data.get("checked_at") or 0.0)
            st = data.get("status")
            if st == "in_progress" and _pid_running(data.get("pid")):
                return False
            if st == "in_progress":
                print(
                    "MindIE background updater pid is not running; reclaiming leftover in_progress",
                    file=sys.stderr,
                )
            elif st == "failed" and now - checked < UPDATE_FAILURE_BACKOFF:
                return False
            elif st != "failed" and now - checked < UPDATE_CHECK_INTERVAL:
                return False
    try:
        adapter = read_json(cfg_file)
        py_exe = adapter.get("python") or sys.executable
        env = apply_enterprise_transport_env(dict(os.environ))
        kwargs: dict = dict(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
        )
        if os.name == "nt":
            # Do not combine DETACHED_PROCESS with CREATE_NO_WINDOW: Windows ignores
            # CREATE_NO_WINDOW when DETACHED_PROCESS is set, causing uv venv launcher
            # trampolines (Scripts\python.exe) and child git/uv processes to allocate
            # and flash a visible console window.
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            )
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [str(py_exe), "-I", "-X", "utf8", str(Path(__file__).resolve()), "--config", str(cfg_file)],
            **kwargs,
        )
    except TimeoutError as exc:
        _record_background_update_failure(status_file, now, exc)
        raise
    except Exception as exc:
        _record_background_update_failure(status_file, now, exc)
        raise
    try:
        with _status_file_lock(status_file):
            already_done = False
            if status_file.is_file():
                try:
                    cur = read_json(status_file)
                    if cur.get("status") in {"updated", "up_to_date"} and float(cur.get("checked_at") or 0.0) >= now:
                        already_done = True
                except (OSError, ValueError):
                    pass
            if not already_done:
                atomic_write_json(
                    status_file,
                    {"checked_at": now, "status": "in_progress", "pid": int(proc.pid)},
                )
    except TimeoutError as write_exc:
        _stop_untracked_updater(proc)
        _record_background_update_failure(status_file, now, write_exc)
        raise
    except OSError as write_exc:
        _stop_untracked_updater(proc)
        _record_background_update_failure(status_file, now, write_exc)
        return False
    return True


def _stop_untracked_updater(proc) -> None:
    from bounded import _kill_process_tree

    try:
        _kill_process_tree(proc, None, wait_timeout=0.5)
    except TimeoutError:
        raise
    except OSError as exc:
        print(
            f"MindIE untracked updater cleanup failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def _norm_python_path(py_exe: str | Path) -> str:
    p = Path(py_exe).expanduser()
    return str(p.parent.resolve() / p.name)


def compatible_runtime(python: str, *, source=None, config=None, timeout: float = 540.0) -> str:
    """Prepare a compatible generation without changing the active profile."""
    deadline = time.monotonic() + float(timeout)
    imports = ("import mindie_knowledge, remote_dev, mindie_diagnostics, mindie_profiler, "
               "mindie_sim, mindie_coordinator, mindie_debugger, npu_top; "
               "from remote_dev.mcp.outcome import operation_outcome")

    def probe(interpreter):
        budget = min(15.0, deadline - time.monotonic())
        if budget <= 0:
            raise TimeoutError("runtime compatibility probe exceeded its budget")
        return bounded_run([str(interpreter), "-I", "-X", "utf8", "-c", imports], "", timeout=budget).returncode == 0

    if probe(python):
        return _norm_python_path(python)
    remaining = deadline - time.monotonic()
    if remaining <= 20.0:
        raise TimeoutError("runtime preparation has no remaining build budget")
    # The configured interpreter may be older than this plugin. Its existing
    # component builder prepares the new baseline in a separate generation.
    command = [str(python), "-I", "-X", "utf8", str(Path(__file__).resolve()), "--prepare-only",
               "--python", str(python), "--source", str(source or repo_root(config)),
               "--config", str(config_path(config)), "--timeout", str(remaining - 20.0)]
    result = bounded_run(command, "", timeout=remaining - 15.0,
                         max_output=256 * 1024, env=apply_enterprise_transport_env(dict(os.environ)))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-1000:]
        raise child_failure(
            result.stdout, result.stderr, stage="prepare",
            message=f"runtime candidate preparation failed: {detail}")
    artifact = json.loads(result.stdout)
    candidate = artifact.get("python") if isinstance(artifact, dict) else None
    if not candidate or not probe(candidate):
        raise RuntimeError("runtime candidate does not provide the plugin APIs")
    return _norm_python_path(candidate)


def prepare(
    source: str | Path | None = None,
    python: str | Path | None = None,
    *,
    config: str | Path | None = None,
    force: bool = False,
    timeout: float = 540.0,
) -> dict | None:
    """Discover and build an updated component runtime generation if newer releases exist."""
    apply_enterprise_transport_env()
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
    source_root = Path(source or repo_root(cfg_file)).resolve()
    current_python = _norm_python_path(python or adapter.get("python") or sys.executable)

    required = required_components(adapter, timeout=min(15.0, remaining(5.0)))
    try:
        baseline = source_baseline(source_root)
    except BaseException as exc:
        raise attach_stage(exc, "discover")
    try:
        selected = discover(
            current_python,
            baseline["runtime"],
            force=force,
            required_components=required,
            catalog=Catalog(budget=min(120.0, remaining(20.0))),
        )
    except BaseException as exc:
        raise attach_stage(exc, "discover")
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

    class _TeeQueue:
        def __init__(self, inner, out_buf: bytearray, err_buf: bytearray):
            self._inner, self._out, self._err = inner, out_buf, err_buf

        def put(self, item, *args, **kwargs):
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
                sink = self._err if item[0] == "err" else self._out
                if len(sink) < MAX_OUTPUT_CHARS:
                    sink.extend(item[1][:MAX_OUTPUT_CHARS - len(sink)])
            return self._inner.put(item, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    import mindie_knowledge.loop.process as loop_process

    out_buf, err_buf = bytearray(), bytearray()
    orig_reader = loop_process._reader

    def tee_reader(stream, tag, chunks, cancel, errors):
        return orig_reader(stream, tag, _TeeQueue(chunks, out_buf, err_buf), cancel, errors)

    loop_process._reader = tee_reader
    try:
        artifact = build(
            source_root,
            state_dir(cfg_file) / "component-runtimes",
            baseline,
            selected["runtime"],
            python=current_python,
            timeout=min(420.0, remaining(5.0)),
            env=apply_enterprise_transport_env(dict(os.environ)),
            files=["pyproject.toml", "product-contract.json", "harness-compatibility.json"],
        )
    except BaseException as exc:
        if isinstance(exc, Exception) and (out_buf or err_buf) and classify_exception(exc)[0] == INTERNAL:
            rebuilt = child_failure(
                out_buf.decode("utf-8", errors="replace"),
                err_buf.decode("utf-8", errors="replace"),
                stage="uv-sync",
                message=str(exc),
            )
            if isinstance(rebuilt, UpdateFailure):
                raise rebuilt from exc
        raise attach_stage(exc, "build")
    finally:
        loop_process._reader = orig_reader
    return artifact


def check(
    *,
    config: str | Path | None = None,
    source: str | Path | None = None,
    force: bool = False,
    sync_feeds: bool = False,
    timeout: float = 540.0,
) -> dict:
    """Check for newer formal component releases under the cross-process update mutex."""
    apply_enterprise_transport_env()
    try:
        with update_lock():
            return _check_locked(
                config=config,
                source=source,
                force=force,
                sync_feeds=sync_feeds,
                timeout=timeout,
            )
    except UpdateLockBusy:
        return _busy_result(config)


def _busy_result(config: str | Path | None = None) -> dict:
    """Honest `busy` receipt: a peer owns the switch, so nothing was changed."""
    cfg_file = config_path(config)
    current = sys.executable
    try:
        if cfg_file.is_file():
            configured = read_json(cfg_file).get("python")
            if isinstance(configured, str) and configured:
                current = configured
    except TimeoutError:
        raise
    except (OSError, ValueError):
        pass
    return dict(
        schema="mindie-component-update/1",
        harness="antigravity",
        status="busy",
        python=_norm_python_path(current),
        installed={},
        update_lock="held",
    )


def _checkout_forbidden_roots(adapter, *, config, source) -> list:
    """External user checkouts that reclamation must treat as forbidden roots."""
    roots = []
    for value in (source, adapter.get("source_root"), adapter.get("runtime_scripts"),
                  repo_root(config), plugin_root()):
        if isinstance(value, (str, Path)) and str(value):
            roots.append(Path(value))
    return roots


def _reclaim_after_switch(cfg_file, *, adapter, previous_python, active_python, source):
    """Bounded silent GC after a verified switch; never raises into the update path."""
    try:
        from storage_lifecycle import cleanup_warning, reclaim_after_switch

        report = reclaim_after_switch(
            runtimes_root=state_dir(cfg_file) / "component-runtimes",
            keep_python=(previous_python, active_python,
                         adapter.get("previous_python")),
            data_home=data_home(),
            state_root=state_dir(cfg_file),
            forbidden_roots=_checkout_forbidden_roots(
                adapter, config=cfg_file, source=source),
        )
        return report, cleanup_warning(report)
    except TimeoutError:
        return ({"status": "partial", "prune": None, "sweep": None,
                 "warnings": ["cleanup budget exhausted"]},
                "post-update cleanup: budget exhausted")
    except Exception as exc:  # noqa: BLE001 - post-switch GC must never fail the update
        detail = "post-update cleanup unavailable: " + type(exc).__name__
        return ({"status": "unavailable", "prune": None, "sweep": None,
                 "warnings": [type(exc).__name__]}, detail)


def _check_locked(
    *,
    config: str | Path | None = None,
    source: str | Path | None = None,
    force: bool = False,
    sync_feeds: bool = False,
    timeout: float = 540.0,
) -> dict:
    """Check, build, switch, and reclaim while the update mutex is held."""
    from mindie_knowledge.component_bindings import rebind_commands
    from mindie_knowledge.component_runtime import read_inventory
    from setup import ensure_default_profile

    cfg_file = config_path(config)
    if not cfg_file.is_file():
        ensure_default_profile(config=cfg_file)
    adapter = read_json(cfg_file)
    eng_file = engine_config_path(cfg_file)
    cfg_py = adapter.get("python")
    cfg_py_norm = cfg_py.replace("\\", "/") if isinstance(cfg_py, str) else ""
    if not (cfg_py_norm and ("venv" in cfg_py_norm or ".venv" in cfg_py_norm) and "/uv/python/" not in cfg_py_norm):
        cfg_py = None
    current_python = cfg_py or sys.executable

    status_file = data_home() / "component-update-status.json"
    try:
        artifact = prepare(
            source=source or repo_root(cfg_file),
            python=current_python,
            config=cfg_file,
            force=force,
            timeout=timeout,
        )
    except Exception as exc:
        import diagnostic_support

        diag = diagnostic_support.failure("component.update", "prepare", "update", exception=exc, config=cfg_file)
        try:
            atomic_write_json(
                status_file,
                {
                    "checked_at": time.time(),
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "diagnostic": diag,
                },
            )
        except TimeoutError:
            raise
        except OSError:
            pass
        raise

    inventory_error = None
    inventory_diag = None
    post_promote_warn = None
    cleanup_report = None
    cleanup_warn = None
    if artifact is not None:
        new_python = _norm_python_path(artifact["python"])
        installed = artifact["installed"]
        updated_adapter = dict(adapter)
        updated_adapter["python"] = new_python
        updated_adapter["component_runtime"] = artifact["components"]
        updated_adapter["previous_python"] = _norm_python_path(current_python)
        if updated_adapter.get("component_commands"):
            updated_adapter["component_commands"] = rebind_commands(
                updated_adapter,
                new_python,
                artifact["components"]["runtime"],
            )
        atomic_write_json(cfg_file, updated_adapter)
        status = "updated"
        active_python = new_python
        try:
            if eng_file.is_file():
                eng_payload = read_json(eng_file)
                eng_payload["summary_command"] = [new_python, "-I", "-X", "utf8", str(HERE / "organizer.py")]
                atomic_write_json(eng_file, eng_payload)
        except (TimeoutError, OSError, ValueError, TypeError) as exc:
            post_promote_warn = f"{type(exc).__name__}: {exc}"
        # Both atomic switches are durable: reclaim superseded generations under
        # a separate bounded budget. A deferred GC is a warning, never a failure.
        cleanup_report, cleanup_warn = _reclaim_after_switch(
            cfg_file,
            adapter=updated_adapter,
            previous_python=_norm_python_path(current_python),
            active_python=new_python,
            source=source,
        )
    else:
        status = "up_to_date"
        active_python = _norm_python_path(current_python)
        try:
            installed = read_inventory(active_python, timeout=15.0)
        except TimeoutError as exc:
            inventory_error = f"{type(exc).__name__}: {exc}"
            try:
                atomic_write_json(
                    status_file,
                    {
                        "checked_at": time.time(),
                        "status": "failed",
                        "error": inventory_error,
                        "python": active_python,
                    },
                )
            except TimeoutError:
                raise
            except OSError:
                pass
            raise
        except Exception as exc:
            import diagnostic_support

            status = "failed"
            installed = {}
            inventory_error = f"{type(exc).__name__}: {exc}"
            inventory_diag = diagnostic_support.failure("component.update", "inventory", "update", exception=exc, config=cfg_file)

    result: dict = dict(
        schema="mindie-component-update/1",
        harness="antigravity",
        status=status,
        python=active_python,
        installed=installed,
    )
    if inventory_error is not None:
        result["inventory_error"] = inventory_error
        result["diagnostic"] = inventory_diag
    if post_promote_warn is not None:
        result["receipt_warning"] = post_promote_warn
        result["receipt_unconfirmed"] = True
    if cleanup_report is not None:
        result["cleanup"] = cleanup_report
        if cleanup_warn is not None:
            result["cleanup_warning"] = cleanup_warn
    if sync_feeds:
        try:
            if community_config_path(cfg_file).is_file() and eng_file.is_file():
                from mindie_knowledge.loop.cli import sync_feeds as run_sync_feeds

                result["feed_sync"] = run_sync_feeds(str(eng_file))
        except Exception as exc:
            if isinstance(exc, TimeoutError) and status != "updated":
                raise
            err_msg = f"{type(exc).__name__}: {exc}"
            result["feed_sync_error"] = err_msg
            if status == "updated":
                result.setdefault("receipt_warning", err_msg)
                result["receipt_unconfirmed"] = True
    try:
        status_payload = {
            "checked_at": time.time(),
            "status": status,
            "python": active_python,
        }
        if inventory_error is not None:
            status_payload["error"] = inventory_error
            status_payload["diagnostic"] = inventory_diag
        elif result.get("receipt_unconfirmed"):
            status_payload["receipt_warning"] = result.get("receipt_warning")
            status_payload["receipt_unconfirmed"] = True
        if cleanup_warn is not None:
            status_payload["cleanup_warning"] = cleanup_warn
        with _status_file_lock(status_file):
            atomic_write_json(status_file, status_payload)
    except (TimeoutError, OSError) as exc:
        if isinstance(exc, TimeoutError) and status != "updated":
            raise
        if status == "updated":
            result.setdefault("receipt_warning", f"{type(exc).__name__}: {exc}")
            result["receipt_unconfirmed"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update MindIE shared runtime components for Antigravity")
    parser.add_argument("--config", default=None, help="Path to agy.json")
    parser.add_argument("--source", default=None, help="Path to mindie-agent-agy checkout")
    parser.add_argument("--prepare-only", action="store_true", help="Build a candidate without activating it")
    parser.add_argument("--python", default=None, help="Interpreter whose installed components must be preserved")
    parser.add_argument("--force", action="store_true", help="Bypass release catalog cache")
    parser.add_argument("--sync-feeds", action="store_true", help="Also synchronize configured knowledge feeds")
    parser.add_argument("--timeout", type=float, default=540.0, help="Total update timeout in seconds")
    args = parser.parse_args(argv)
    if args.prepare_only:
        try:
            payload = prepare(source=args.source, python=args.python or sys.executable,
                              config=args.config, force=args.force, timeout=args.timeout)
        except BaseException as exc:
            emit_failure_marker(exc, stage=stage_of(exc, "prepare"))
            raise
        sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0
    try:
        payload = check(
            config=args.config,
            source=args.source,
            force=args.force,
            sync_feeds=args.sync_feeds,
            timeout=args.timeout,
        )
    except BaseException as exc:
        emit_failure_marker(exc, stage=stage_of(exc, "update"))
        raise
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0 if payload.get("status") in {"updated", "up_to_date"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
