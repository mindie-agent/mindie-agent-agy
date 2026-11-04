"""Stable entrypoint invoked by Antigravity `hooks.json` and `mcp_config.json`.

[Role]
Dispatches Antigravity lifecycle hooks (`hook session-start`, `hook pre-invocation`,
`hook pre-tool-use`, `hook stop`), stdio MCP servers (`mcp remote`, `mcp tools`),
and setup commands (`setup ...`) under explicit timeout budgets.

[Invariants]
- Enforces `HOOK_TOTAL = 10.0s >= HOOK_EXEC_BUDGET (8.0s) + CLEANUP_GRACE (2.0s)`.
- If the configured profile interpreter differs from `sys.executable` on MCP
  launch, re-executes into the configured interpreter so `mindie_knowledge`,
  `remote_dev`, and `mindie_diagnostics` imports resolve deterministically.

[Timeouts & Locks]
- `HOOK_TOTAL = 10.0s`, `HOOK_LOCK_BUDGET = 2.0s`, `BOOTSTRAP_TOTAL = 540.0s`.

[Side-Effect Policy]
- Delegates hook and MCP execution to `bridge.py`, `mcp_server.py`, and `setup.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import apply_enterprise_transport_env, config_path, data_home, read_json

HOOK_TOTAL = 10.0
HOOK_LOCK_BUDGET = 2.0
BOOTSTRAP_TOTAL = 540.0


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
    import subprocess

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


def main(argv: list[str] | None = None) -> int:
    apply_enterprise_transport_env()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("mindie_launch requires a subcommand: hook | mcp | setup | update")

    target_py = _preferred_python()
    if (
        target_py is not None
        and _norm_python_path(sys.executable) != _norm_python_path(target_py)
        and os.environ.get("_MINDIE_AGY_REEXEC") != "1"
    ):
        env = dict(os.environ)
        env["_MINDIE_AGY_REEXEC"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        import subprocess

        run_kwargs: dict = {"env": env}
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            run_kwargs["stdin"] = _reexec_stdio(sys.stdin)
            run_kwargs["stdout"] = _reexec_stdio(sys.stdout)
            run_kwargs["stderr"] = _reexec_stdio(sys.stderr)
        proc = subprocess.run([target_py, "-I", str(Path(__file__).resolve()), *argv], **run_kwargs)
        return int(proc.returncode)

    role = argv[0]
    rest = argv[1:]
    if role == "hook":
        import bridge

        try:
            return bridge.main(rest)
        except Exception as exc:
            print(f"MindIE hook warning: {type(exc).__name__}: {exc}", file=sys.stderr)
            sys.stdout.buffer.write(b"{}\n")
            sys.stdout.buffer.flush()
            return 0
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
