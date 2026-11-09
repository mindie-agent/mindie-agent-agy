"""Stable entrypoint invoked by Antigravity `hooks.json` and `mcp_config.json`.

[Role]
Dispatches Antigravity lifecycle hooks (`hook session-start`, `hook pre-invocation`,
`hook pre-tool-use`, `hook stop`), stdio MCP servers (`mcp remote`, `mcp tools`),
and setup commands (`setup ...`) under explicit timeout budgets.

[Invariants]
- Enforces `HOOK_TOTAL = 10.0s >= HOOK_EXEC_BUDGET (8.0s) + CLEANUP_GRACE (2.0s)`.
  Direct-dispatch hooks (no interpreter re-exec) are still bounded by
  `HOOK_EXEC_BUDGET`; over-budget work emits host JSON and exits 0.
- Hook failures never emit `{}` with exit 0. `PreToolUse` bind errors deny the
  call. Hook re-exec `TimeoutExpired` and `TimeoutError` emit host-readable
  JSON and exit 0 (`PreToolUse` deny; other hooks `injectSteps.ephemeralMessage`);
  exit 1 with empty stdout would fail-open. Other hook exceptions propagate.
- If the configured profile interpreter differs from `sys.executable` on MCP
  launch, re-executes into the configured interpreter so `mindie_knowledge`,
  `remote_dev`, and `mindie_diagnostics` imports resolve deterministically.

[Timeouts & Locks]
- `HOOK_EXEC_BUDGET = 8.0s`, `HOOK_CLEANUP_GRACE = 2.0s`, `HOOK_TOTAL = 10.0s`,
  `HOOK_LOCK_BUDGET = 2.0s`, `HOOK_UV_STARTUP = 5.0s`.
- Hook re-exec uses `timeout=HOOK_EXEC_BUDGET` (not `HOOK_TOTAL`) so cleanup
  can emit JSON; setup outer re-exec covers sequential prep + nested re-exec
  (`inner + (inner + CLEANUP_GRACE) + CLEANUP_GRACE`); update stays
  `inner + 2 * CLEANUP_GRACE`. MCP stdio has no timeout. Timed re-exec kills
  the process tree (POSIX group / Windows Job) then emits JSON so
  grandchildren are not left as zombies.
- Outer `hooks.json` timeout is `HOOK_OUTER_SECONDS = HOOK_TOTAL + HOOK_UV_STARTUP`.

[Side-Effect Policy]
- Delegates hook and MCP execution to `bridge.py`, `mcp_server.py`, and `setup.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import apply_enterprise_transport_env, config_path, data_home, read_json

HOOK_EXEC_BUDGET = 8.0
HOOK_CLEANUP_GRACE = 2.0
HOOK_TOTAL = HOOK_EXEC_BUDGET + HOOK_CLEANUP_GRACE
HOOK_LOCK_BUDGET = 2.0
HOOK_UV_STARTUP = 5.0
HOOK_OUTER_SECONDS = int(HOOK_TOTAL + HOOK_UV_STARTUP)
BOOTSTRAP_TOTAL = 540.0
BOOTSTRAP_CLEANUP_GRACE = 5.0


def _norm_python_path(py_exe: str | Path) -> Path:
    p = Path(py_exe).expanduser()
    return p.parent.resolve() / p.name


def _is_venv_python(py: str) -> bool:
    return bool(
        isinstance(py, str)
        and ("venv" in py or ".venv" in py)
        and "/uv/python/" not in py
        and "/Cellar/python" not in py
        and Path(py).is_file()
    )


def _preferred_python() -> str | None:
    cfg = config_path()
    if cfg.is_file():
        try:
            py = read_json(cfg).get("python")
            if _is_venv_python(py):
                return str(_norm_python_path(py))
        except ValueError:
            pass
    agy_venv = data_home() / "agy-venv"
    for candidate in (agy_venv / "Scripts" / "python.exe", agy_venv / "bin" / "python"):
        if candidate.is_file():
            return str(_norm_python_path(candidate))
    ws_venv = HERE.parent.parent.parent.parent / ".venv"
    for candidate in (ws_venv / "Scripts" / "python.exe", ws_venv / "bin" / "python"):
        if candidate.is_file():
            return str(_norm_python_path(candidate))
    search_dirs = [cfg.parent]
    host_cfg_dir = Path.home() / ".config" / "mindie-agent"
    if host_cfg_dir not in search_dirs:
        search_dirs.append(host_cfg_dir)
    for cdir in search_dirs:
        for sibling_cfg in ("agy.json", "wb.json", "codex.json", "cc.json", "kimi.json"):
            scfg = cdir / sibling_cfg
            if scfg.is_file():
                try:
                    py = read_json(scfg).get("python")
                    if _is_venv_python(py):
                        return str(_norm_python_path(py))
                except ValueError:
                    pass
    return None


def _reexec_stdio(stream):
    try:
        if os.name == "nt":
            import msvcrt

            if msvcrt.get_osfhandle(stream.fileno()) in (-1, -2, 0):
                return subprocess.DEVNULL
        else:
            stream.fileno()
        return stream
    except Exception:
        return subprocess.DEVNULL


def _parse_cli_timeout(argv: list[str]) -> float | None:
    for index, token in enumerate(argv):
        raw = None
        if token == "--timeout" and index + 1 < len(argv):
            raw = argv[index + 1]
        elif token.startswith("--timeout="):
            raw = token.split("=", 1)[1]
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 0 else None
    return None


def _setup_outer_budget(inner: float) -> float:
    """Wall-clock for sequential `ensure_runtime_python` then nested setup re-exec."""
    nested = inner + BOOTSTRAP_CLEANUP_GRACE
    return inner + nested + BOOTSTRAP_CLEANUP_GRACE


def _timeout_role_budget(role: str, argv: list[str] | None = None) -> float | None:
    if role == "hook":
        return HOOK_EXEC_BUDGET
    requested = _parse_cli_timeout(argv or [])
    inner = BOOTSTRAP_TOTAL if requested is None else requested
    if role == "setup":
        return _setup_outer_budget(inner)
    if role == "update":
        return inner + (2 * BOOTSTRAP_CLEANUP_GRACE)
    return None


def _run_hook_with_exec_budget(argv: list[str], rest: list[str]) -> int:
    """Bound in-process hook dispatch to HOOK_EXEC_BUDGET (POSIX SIGALRM)."""
    import signal
    import bridge

    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return bridge.main(rest)

    def _on_alarm(_signum, _frame):
        raise TimeoutError(f"hook exec exceeded HOOK_EXEC_BUDGET={HOOK_EXEC_BUDGET}s")

    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _on_alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, float(HOOK_EXEC_BUDGET))
        try:
            return int(bridge.main(rest))
        except TimeoutError as exc:
            return _emit_reexec_timeout(argv, exc)
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
        finally:
            signal.signal(signal.SIGALRM, previous)


def _emit_reexec_timeout(argv: list[str], exc: BaseException) -> int:
    detail = f"{argv[0]} reexec exceeded timeout: {type(exc).__name__}: {exc}"
    print(f"MindIE {detail}", file=sys.stderr)
    if argv[0] == "hook":
        op = argv[1] if len(argv) > 1 else ""
        payload = (
            {"decision": "deny", "reason": detail}
            if op == "pre-tool-use"
            else {"injectSteps": [{"ephemeralMessage": detail}]}
        )
        sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0
    return 1


def _reexec(target_py: str, target_script: Path, argv: list[str], env: dict) -> int:
    from bounded import _attach_windows_job, _kill_process_tree

    timeout = _timeout_role_budget(argv[0], argv)
    cmd = [target_py, "-I", str(target_script), *argv]
    popen_kwargs: dict = {"env": env}
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if timeout is not None:
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs["creationflags"] = flags
        popen_kwargs["stdin"] = _reexec_stdio(sys.stdin)
        popen_kwargs["stdout"] = _reexec_stdio(sys.stdout)
        popen_kwargs["stderr"] = _reexec_stdio(sys.stderr)
    elif timeout is not None:
        popen_kwargs["start_new_session"] = True
    try:
        if timeout is None:
            return int(subprocess.run(cmd, **popen_kwargs).returncode)
        proc = subprocess.Popen(cmd, **popen_kwargs)
        job = _attach_windows_job(proc)
        try:
            return int(proc.wait(timeout=timeout))
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            _kill_process_tree(proc, job, wait_timeout=min(0.5, HOOK_CLEANUP_GRACE))
            return _emit_reexec_timeout(argv, exc)
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        return _emit_reexec_timeout(argv, exc)


def main(argv: list[str] | None = None) -> int:
    apply_enterprise_transport_env()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("mindie_launch requires a subcommand: hook | mcp | setup | update")

    try:
        target_py = _preferred_python()
        target_script = Path(__file__).resolve()
        if argv[0] in {"hook", "mcp"} and config_path().is_file():
            # The profile activates interpreter and payload together. A newly
            # downloaded native entry must keep serving the previous generation
            # until installation has prepared and activated its replacement.
            active = read_json(config_path())
            active_python = active.get("python")
            active_scripts = active.get("runtime_scripts")
            validation = active.get("product_validation") or {}
            expected_version = validation.get("native_version")
            retained = active.get("source_root")
            directories = ([Path(retained) / "plugins/mindie-agent/scripts"]
                           if isinstance(retained, str) else [])
            if isinstance(active_scripts, str):
                directories.append(Path(active_scripts))
            if isinstance(active_python, str) and Path(active_python).is_file():
                for directory in directories:
                    manifest = directory.parent / "plugin.json"
                    paired_script = directory / "mindie_launch.py"
                    if (manifest.is_file() and paired_script.is_file()
                            and read_json(manifest).get("version") == expected_version):
                        target_py = str(_norm_python_path(active_python))
                        target_script = paired_script.resolve()
                        break
                else:
                    raise RuntimeError("active plugin payload does not match its recorded version")
        if (
            target_py is not None
            and (_norm_python_path(sys.executable) != _norm_python_path(target_py)
                 or target_script != Path(__file__).resolve())
        ):
            env = dict(os.environ)
            env["_MINDIE_AGY_REEXEC"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            if argv[0] in {"hook", "mcp"}:
                env["AGY_PLUGIN_ROOT"] = str(target_script.parent.parent)
            if argv[0] == "hook":
                env["_MINDIE_AGY_HOOK_BUDGETED"] = "1"
            return _reexec(target_py, target_script, argv, env)
    except TimeoutError as exc:
        return _emit_reexec_timeout(argv, exc)

    role = argv[0]
    rest = argv[1:]
    if role == "hook":
        import bridge

        budgeted = os.environ.get("_MINDIE_AGY_HOOK_BUDGETED") == "1"
        if os.name == "nt" and not budgeted and os.environ.get("_MINDIE_AGY_REEXEC") != "1":
            env = dict(os.environ)
            env["_MINDIE_AGY_HOOK_BUDGETED"] = "1"
            env["_MINDIE_AGY_REEXEC"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            return _reexec(sys.executable, Path(__file__).resolve(), argv, env)
        if os.name != "nt" and not budgeted:
            return _run_hook_with_exec_budget(argv, rest)
        return bridge.main(rest)
    if role == "mcp":
        import mcp_server

        return mcp_server.main(rest)
    if role == "setup":
        import setup

        return setup.main(rest)
    if role == "update":
        import component_update

        return component_update.main(rest)
    raise SystemExit(f"unknown mindie_launch role: {role}")


if __name__ == "__main__":
    raise SystemExit(main())
