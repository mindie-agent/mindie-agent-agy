"""Public stdio JSON-RPC 2.0 MCP server for Antigravity (`remote` and `tools` surfaces).

[Role]
Serves MCP discovery (`tools/list`) and tool execution (`tools/call`) for
`mindie-remote-dev` and `mindie-tools` (profiler/sim/coordinator/debugger/npu_top/
diagnostics/mindie_status + default-enabled `knowledge_*` tools).

[Invariants]
- While the Antigravity plugin is enabled, `mindie-tools` exposes both deterministic
  component tools and `knowledge_*` tools (`knowledge_query`, `knowledge_explain`,
  `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`); conversations muted
  via `/mindie-agent deactivate` reject `knowledge_*` calls with `isError=True`.
- All `mindie-remote-dev` and `mindie-tools` operations work over standard MCP
  `tools/call` with zero pre-call session binding (`PreToolUse` binds and
  `AGY_CONVERSATION_ID` are opportunistic hints, falling back to `_CONNECTION_SESSION_ID`).
- Receipt Honesty: once a primary remote/component/knowledge operation succeeds,
  a local SQLite `finish`/`finish_bind` `OSError` or `sqlite3.Error` keeps
  `isError=False` and attaches `receipt_warning` + `receipt_unconfirmed=True`.
- Non-empty UTF-8/JSON parse failures return JSON-RPC `-32700` Parse error.
  Parsed non-object frames return `-32600` Invalid Request (never dropped).
- Background updater spawn failure never aborts `tools/call` or the stdio loop.
- The resident stdio server holds a shared `acquire_runtime_lease` on the
  managed generation owning `sys.executable`.

[Timeouts & Locks]
- SQLite WAL mode with `busy_timeout=5000` (`timeout=5.0`) for `RemoteReceipts`.
- Enforces `remote_timeout(name, args) = max(minimum, inner_wait + 60.0s grace)`.

[Side-Effect Policy]
- `initialize`/`tools/list` are read-only; serving creates only the shared
  generation lease files owned by `mindie_knowledge.storage`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import sysconfig
import threading
import time
from typing import BinaryIO
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from admission import status_facts
from bounded import run as bounded_run
from component_update import trigger_background_update_if_due
from identity import claim_bind, finish_bind, require_session
import knowledge_cli
from knowledge_cli import KNOWLEDGE_NAMES, KNOWLEDGE_TOOL_SCHEMAS
from paths import bind_db_path, config_path, diagnostics_config_path, read_json, remote_receipts_path, state_dir, which_native

REMOTE_GRACE_SECONDS = 60.0
REMOTE_MIN_BUDGET_SECONDS = 180.0
COMPONENT_CAPTURE_LIMIT_BYTES = 16 * 1024 * 1024
REMOTE_NATIVE_MAX_WAIT_MS = {
    "remote_write_stdin": 300000,
    "remote_job_stdin": 300000,
    "remote_bash": 600000,
    "remote_run_command": 600000,
    "remote_exec_command": 30000,
}

COMPONENT_DESCRIPTIONS = {
    "profiler": (
        "Inspect and analyze Ascend NPU profiling captures using mindie-profiler. "
        "Pass CLI arguments as an argv array, e.g. ['inventory', '--root', '/capture'] "
        "or ['analyze', '--root', '/capture', '--full-range']. Commands: inventory, analyze, "
        "correlate, compare, sql, camodel, and kernels. Use ['--help'] to inspect options "
        "and --output for large reports."
        " Offline analysis of existing captures requires no device lease or managed task binding."
    ),
    "sim": (
        "Compute Ascend hardware bounds (A2/A3/A5), operator/graph/serving latency, HBM capacity "
        "(including async_scheduling delayed release), Roofline efficiency, SLA concurrency "
        "frontiers, and P/D disaggregation using mindie-sim. Pass CLI arguments as an argv "
        "array: synthesize a case from model/hardware IDs or HF config (e.g. ['compose', "
        "'--model', 'deepseek-v2-lite', '--hardware', 'Ascend910B2C', '--tp', '8', "
        "'--async-scheduling', '--output', '/case.json']) and evaluate it (e.g. ['predict', "
        "'/case.json', '--scope', 'body', '--phase', 'decode', '--sequences', '32', "
        "'--output', '/report.json'], ['efficiency', '/case.json'], "
        "['sla', '/case.json', '--sla-ms', '50'], ['sweep', '/case.json', '--mode', 'pd'], "
        "['skew', '/case.json'], or ['graph', '/case.json', '--phase', 'prefill', '--tokens', "
        "'32', '--output', '/graph.json']). Use ['--help'] to inspect options and --output "
        "for large reports."
        " Simulation calculations require no device allocation or managed task binding."
    ),
    "coordinator": (
        "Manage Ascend host inventory, probe CPU/RAM/NPU capacity, bind containers, "
        "and acquire/inspect/release NPU and service-port leases through "
        "mindie-coordinator. Pass CLI argv, e.g. ['hosts', '--action', 'list'] "
        "or ['probe', '--json', '{\"action\":\"hosts\"}']. Use ['--help'] to inspect options."
        " Host inventory, probes, containers and bare-metal leases need no managed "
        "task binding or knowledge activation. Omit context_file for these independent "
        "operations; inspect resources before acquiring a lease."
    ),
    "npu_top": (
        "Observe live Ascend NPU fleet telemetry, per-Die HBM/utilization, and active "
        "processes across A2/A3/A5 servers using npu-top (observation-only)."
    ),
    "debugger": (
        "Ascend NPU fault triage (`triage`, `decode-code`), ACLGraph/NPUGraph capture/replay & buffer-address "
        "debugger (`aclgraph`: `guard`, `inspect`), and operator precision/memory-stomp toolkit (`precision`: "
        "`check-call`, `replay-call`, `inspect-capsule`, `compare-traces`, `watch`) via mindie-debugger. "
        "Error-code decoding (`['decode-code', '507015']`) or inline stderr triage (`['triage', '--text', '<err>']`) "
        "runs locally. Since full CANN `plog` directories (`$ASCEND_PROCESS_LOG_PATH` or `~/ascend/log/debug/plog`) "
        "and dynamic ACLGraph/precision runtime hooks reside inside the remote NPU container, call `['remote-guide']` "
        "(or `['triage']` / `['aclgraph', ...]` / `['precision', ...]`) to stage the pure-Python `mindie_debugger` "
        "bundle locally and get the exact `remote-dev` (`remote_artifact_push` + `remote_bash`) commands to triage "
        "CANN `plog`s in-place (`python3 -m mindie_debugger.cli triage`) or run `ACLGraphCaptureGuard`, "
        "`ACLGraphAddressTracker`, `ACLGraphDumper`, `ACLGraphVllmInspector`, and `precision` inside the NPU container."
    ),
}


def remote_timeout(
    name: str,
    args: dict | None = None,
    *,
    native_wait_ms: int | float | None = None,
    grace: float = REMOTE_GRACE_SECONDS,
    minimum: float = REMOTE_MIN_BUDGET_SECONDS,
) -> float:
    """Compute `outer_timeout >= max(minimum, inner_wait + grace)`."""
    if isinstance(grace, bool) or not isinstance(grace, (int, float)) or not math.isfinite(grace) or grace <= 0:
        raise ValueError("remote grace must be a positive finite number")
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("remote minimum budget must be a positive finite number")
    if native_wait_ms is None:
        native_wait_ms = REMOTE_NATIVE_MAX_WAIT_MS.get(name, 0)
    explicit_ms = 0.0
    if isinstance(args, dict):
        for key in ("yield_time_ms", "timeout_ms", "WaitMsBeforeAsync"):
            value = args.get(key)
            if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
                explicit_ms = max(explicit_ms, float(value))
    inner_seconds = max(float(native_wait_ms) / 1000.0, explicit_ms / 1000.0)
    return max(float(minimum), inner_seconds + float(grace))


class RemoteReceipts:
    """Lightweight SQLite WAL store tracking in-flight and completed remote calls."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or remote_receipts_path())
        self._init_lock = threading.Lock()
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized and self.path.is_file():
            return
        with self._init_lock:
            if self._initialized and self.path.is_file():
                return
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            init_db = sqlite3.connect(self.path, timeout=5.0)
            try:
                init_db.execute("PRAGMA busy_timeout=5000;")
                init_db.execute("PRAGMA journal_mode=WAL;")
                init_db.execute("PRAGMA synchronous=NORMAL;")
                init_db.execute(
                    "CREATE TABLE IF NOT EXISTS receipts("
                    "call_id TEXT PRIMARY KEY, "
                    "session TEXT NOT NULL, "
                    "tool TEXT NOT NULL, "
                    "args_sha256 TEXT NOT NULL, "
                    "status TEXT NOT NULL, "
                    "started REAL NOT NULL, "
                    "updated REAL NOT NULL)"
                )
                init_db.commit()
            finally:
                init_db.close()
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        self._ensure_initialized()
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000;")
        db.execute("PRAGMA synchronous=NORMAL;")
        return db

    def begin(self, call_id: str, session: str, tool: str, args: dict) -> None:
        digest = hashlib.sha256(
            json.dumps(args or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        now = time.time()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM receipts WHERE call_id=?", (call_id,)).fetchone() is not None:
                    raise ValueError("Duplicate MCP request; replay rejected")
                db.execute(
                    "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                    (call_id, session, tool, digest, "running", now, now),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        finally:
            db.close()

    def set_session(self, call_id: str, session: str) -> None:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("UPDATE receipts SET session=? WHERE call_id=?", (session, call_id))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        finally:
            db.close()

    def finish(self, call_id: str, status: str) -> None:
        now = time.time()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "UPDATE receipts SET status=?, updated=? WHERE call_id=?",
                    (status, now, call_id),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        finally:
            db.close()


def tools_catalog() -> list[dict]:
    """Return the `mindie-tools` MCP catalog (deterministic tools + default-enabled `knowledge_*`)."""
    limits = {
        "timeout_ms": dict(type="integer", minimum=1, maximum=300000, default=120000,
                           description="Execution deadline in milliseconds; includes inner budgets and cleanup grace."),
        "max_output_tokens": dict(type="integer", minimum=1, maximum=262144, default=16384,
                                  description="Output byte budget divided by four."),
    }
    items: list[dict] = [
        dict(
            name="mindie_status",
            description=(
                "Read factual MindIE control-plane status for the current Antigravity session. "
                "Does not start background services or emit onboarding prompts."
            ),
            inputSchema=dict(type="object", properties={}, additionalProperties=False),
            annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        )
    ]
    for name, description in COMPONENT_DESCRIPTIONS.items():
        items.append(dict(
            name=name,
            description=description,
            inputSchema=dict(
                type="object",
                additionalProperties=False,
                required=["argv"],
                properties=dict(
                    argv=dict(type="array", items=dict(type="string"),
                              description="CLI arguments array; passed without shell expansion."),
                    **limits,
                ),
            ),
            annotations=dict(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
        ))
    items.append(dict(
        name="diagnostics_status",
        description="Inspect local MindIE diagnostic/reporting status without starting a worker or contacting GitHub.",
        inputSchema=dict(type="object", additionalProperties=False, properties=dict(config=dict(type="string"), **limits)),
        annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    ))
    items.append(dict(
        name="diagnostics_bundle",
        description="Export a bounded, sanitized local diagnostic support bundle without uploading it.",
        inputSchema=dict(
            type="object",
            additionalProperties=False,
            required=[],
            properties=dict(root=dict(type="string"), operation_id=dict(type="string"),
                            incident_id=dict(type="string"), output=dict(type="string"), **limits),
        ),
        annotations=dict(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    ))
    for schema in KNOWLEDGE_TOOL_SCHEMAS:
        kname = schema["name"]
        items.append(dict(
            **schema,
            annotations=dict(
                readOnlyHint=kname in {"knowledge_query", "knowledge_explain"},
                destructiveHint=False,
                openWorldHint=(kname == "knowledge_sync"),
            ),
        ))
    return items


def remote_catalog() -> list[dict]:
    """Return the `mindie-remote-dev` MCP catalog."""
    from remote_dev.mcp.tools import list_tools

    return list_tools("canonical")


def _resolve_component_command(component: str, *, config=None) -> list[str]:
    cfg_file = config_path(config)
    adapter = read_json(cfg_file) if cfg_file.is_file() else {}
    configured = adapter.get("component_commands", {}).get(component)
    if isinstance(configured, list) and configured and all(isinstance(x, str) and x for x in configured):
        return configured
    python_exe = adapter.get("python") or sys.executable
    if component == "diagnostics":
        return [str(python_exe), "-I", "-X", "utf8", "-m", "mindie_diagnostics.cli"]
    module_map = {
        "profiler": "mindie_profiler",
        "sim": "mindie_sim",
        "coordinator": "mindie_coordinator.cli",
        "debugger": "mindie_debugger.cli",
        "npu_top": "npu_top",
    }
    bin_name = "npu-top" if component == "npu_top" else f"mindie-{component}"
    runtime_dir = str(Path(python_exe).expanduser().parent)
    search_path = os.pathsep.join([runtime_dir, sysconfig.get_path("scripts"), os.environ.get("PATH", "")])
    exe = which_native(bin_name, path=search_path)
    if exe:
        if os.name == "nt" and component in module_map and exe.lower().endswith(".exe"):
            # Windows 11 Smart App Control (WinError 4551) blocks unsigned pip/uv
            # console-script .exe trampolines; invoke via PSF-signed python.exe instead.
            return [str(python_exe), "-I", "-X", "utf8", "-m", module_map[component]]
        return [exe]
    return [str(python_exe), "-I", "-X", "utf8", "-m", module_map[component]]


_CONNECTION_SESSION_ID = os.environ.get("MINDIE_MCP_CONNECTION_ID") or f"agy-mcp-{os.getpid()}-{uuid.uuid4().hex[:12]}"


def _rpc_receipt_id(surface: str, rpc_id) -> str:
    if rpc_id is None:
        return str(uuid.uuid4())
    return f"{_CONNECTION_SESSION_ID}:{surface}:rpc:{json.dumps(rpc_id, sort_keys=True)}"


def _resolve_session(surface: str, tool_name: str, arguments: dict, *, config=None) -> tuple[str, str | None]:
    """Resolve optional session identity from `PreToolUse`, `AGY_CONVERSATION_ID`, or connection scope."""
    if bind_db_path(config).is_file():
        try:
            bound = claim_bind(surface, tool_name, arguments, config=config)
            return bound["session"], bound["bind_id"]
        except TimeoutError:
            raise
        except ValueError as exc:
            if "no active PreToolUse bind" not in str(exc) and "no native PreToolUse bind" not in str(exc):
                raise
    raw_env = os.environ.get("AGY_CONVERSATION_ID")
    if isinstance(raw_env, str) and raw_env.strip():
        return require_session(raw_env), None
    return _CONNECTION_SESSION_ID, None


def _admission_failure(exc):
    return dict(isError=True, structuredContent=dict(
        code="admission_unavailable", execution="not_started", automatic_retry=False,
        error_type=type(exc).__name__), content=[dict(type="text", text=
        "This MindIE call did not start because local admission is unavailable. "
        "Continue independent task steps or use equivalent authorized host-native tools; "
        "preserve task identity and resource ownership. Do not repeat this call unchanged.")])


def _attach_accounting_warning(result: dict, warning_key: str, exc: Exception) -> dict:
    structured = dict(result.get("structuredContent") or {})
    msg = f"{type(exc).__name__}: {exc}"
    structured[warning_key] = msg
    structured.setdefault("receipt_warning", msg)
    structured["receipt_unconfirmed"] = True
    result["structuredContent"] = structured
    content = list(result.get("content") or [])
    content.append(
        dict(
            type="text",
            text=(
                f"Operation completed, but local {warning_key} failed ({type(exc).__name__}). "
                "Do not repeat the completed operation."
            ),
        )
    )
    result["content"] = content
    return result


def call_tools_surface(
    name: str,
    arguments: dict | None = None,
    *,
    rpc_id=None,
    config=None,
    receipts: RemoteReceipts | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    args = dict(arguments or {})
    receipt_store = receipts if receipts is not None or rpc_id is not None else None
    if receipt_store is None and rpc_id is not None:
        receipt_store = RemoteReceipts(remote_receipts_path(config))
    call_id = _rpc_receipt_id("tools", rpc_id)
    reserved = False
    try:
        if receipt_store is not None:
            receipt_store.begin(call_id, _CONNECTION_SESSION_ID, name, args)
            reserved = True
        session, bind_id = _resolve_session("tools", name, args, config=config)
        if receipt_store is not None and session != _CONNECTION_SESSION_ID and hasattr(receipt_store, "set_session"):
            receipt_store.set_session(call_id, session)
    except Exception as exc:
        if reserved and receipt_store is not None:
            try:
                receipt_store.finish(call_id, "failed")
            except TimeoutError:
                raise
            except (OSError, sqlite3.Error):
                pass
        return _admission_failure(exc)
    succeeded = False
    try:
        if name == "mindie_status":
            facts = status_facts(
                session if session != "agy-default-session" else None,
                config=config,
            )
            succeeded = True
            result = dict(
                content=[dict(type="text", text=json.dumps(facts, ensure_ascii=False))],
                structuredContent=facts,
                isError=False,
            )
        elif name in KNOWLEDGE_NAMES:
            try:
                result = knowledge_cli.invoke(name, args, session=session, config=config)
            except Exception as exc:
                result = knowledge_cli._format_failure(exc)
            succeeded = not bool(result.get("isError"))
        elif name in COMPONENT_DESCRIPTIONS or name in {"diagnostics_status", "diagnostics_bundle"}:
            if name in COMPONENT_DESCRIPTIONS:
                component = name
                argv = args.get("argv")
                if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
                    raise ValueError("argv must be a list of strings")
            elif name == "diagnostics_status":
                component, argv = "diagnostics", ["reporting", "status"]
                diag_cfg = args.get("config") if isinstance(args.get("config"), str) else str(diagnostics_config_path(config))
                argv += ["--config", diag_cfg]
            else:
                import diagnostic_support

                component = "diagnostics"
                root_arg = args.get("root")
                if root_arg is not None and (not isinstance(root_arg, str) or not root_arg):
                    raise ValueError("diagnostics_bundle 'root' must be a non-empty string")
                argv = ["bundle", "--root", root_arg or diagnostic_support.default_diagnostics_root()]
                op_id = args.get("operation_id") if isinstance(args.get("operation_id"), str) else args.get("incident_id")
                if isinstance(op_id, str) and op_id:
                    argv += ["--operation-id", op_id]
                if isinstance(args.get("output"), str) and args["output"]:
                    argv += ["--output", args["output"]]

            budget = args.get("max_output_tokens", 16384)
            if type(budget) is not int or not 1 <= budget <= 262144:
                raise ValueError("max_output_tokens must be an integer between 1 and 262144")
            timeout_ms = args.get("timeout_ms", 120000)
            if type(timeout_ms) is not int or not 1 <= timeout_ms <= 300000:
                raise ValueError("timeout_ms must be an integer between 1 and 300000")

            child_env = dict(os.environ, AGY_CONVERSATION_ID=session,
                             MINDIE_DIAGNOSTICS_CONFIG=str(diagnostics_config_path(config)))
            invocation_token = os.urandom(16).hex() if name in COMPONENT_DESCRIPTIONS else None
            if invocation_token is not None:
                child_env["MINDIE_DIAGNOSTICS_INVOCATION_TOKEN"] = invocation_token
            cmd = _resolve_component_command(component, config=config) + argv
            proc = bounded_run(
                cmd,
                "",
                timeout=timeout_ms / 1000.0,
                max_output=COMPONENT_CAPTURE_LIMIT_BYTES,
                env=child_env,
                cancel=cancel,
            )
            reply_limit = budget * 4
            stdout_bytes = proc.stdout.encode("utf-8", errors="replace")
            stderr_bytes = proc.stderr.encode("utf-8", errors="replace")
            truncated = len(stdout_bytes) + len(stderr_bytes) > reply_limit
            err_budget = min(len(stderr_bytes), reply_limit // 2)
            out_budget = min(len(stdout_bytes), reply_limit - err_budget)
            err_budget = min(len(stderr_bytes), reply_limit - out_budget)
            payload = dict(
                component=component,
                exit_code=proc.returncode,
                stdout=stdout_bytes[:out_budget].decode("utf-8", errors="replace"),
                stderr=stderr_bytes[:err_budget].decode("utf-8", errors="replace"),
                output_truncated=truncated,
            )
            if not truncated:
                try:
                    payload["result"] = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    pass
            if name in COMPONENT_DESCRIPTIONS and proc.returncode not in (0, 2):
                import diagnostic_support

                diag = diagnostic_support.record_component_crash(
                    component, argv, proc.returncode, proc.stderr,
                    parsed_result=payload.get("result"), config=config, executable=cmd[0],
                    invocation_token=invocation_token,
                )
                if diag is not None:
                    payload["diagnostic"] = diag
            succeeded = proc.returncode == 0
            result = dict(
                content=[dict(type="text", text=json.dumps(payload, ensure_ascii=False))],
                structuredContent=payload,
                isError=proc.returncode != 0,
            )
        else:
            raise ValueError(f"unknown mindie-tools tool: {name}")
    except Exception as exc:
        import diagnostic_support

        err_struct = dict(error=str(exc), error_type=type(exc).__name__)
        if diagnostic_support.is_internal_mcp_exception(exc):
            err_struct["diagnostic"] = diagnostic_support.failure(
                "mcp.tools", str(name or "dispatch"), "internal", exception=exc, config=config
            )
        result = dict(
            content=[dict(type="text", text=f"{type(exc).__name__}: {exc}")],
            structuredContent=err_struct,
            isError=True,
        )

    if receipt_store is not None:
        try:
            receipt_store.finish(call_id, "succeeded" if succeeded else "failed")
        except (TimeoutError, OSError, sqlite3.Error) as exc:
            if not succeeded and isinstance(exc, TimeoutError):
                raise
            if succeeded:
                result = _attach_accounting_warning(result, "receipt_warning", exc)

    if bind_id is not None:
        try:
            finish_bind(bind_id, succeeded, config=config)
        except (TimeoutError, OSError, sqlite3.Error) as exc:
            if not succeeded and isinstance(exc, TimeoutError):
                raise
            if succeeded:
                result = _attach_accounting_warning(result, "bind_warning", exc)
    return result


def call_remote_surface(
    name: str,
    arguments: dict | None = None,
    *,
    rpc_id=None,
    config=None,
    receipts: RemoteReceipts | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    args = dict(arguments or {})
    reserved = False
    try:
        receipt_store = receipts or RemoteReceipts(remote_receipts_path(config))
        call_id = _rpc_receipt_id("remote", rpc_id)
        receipt_store.begin(call_id, _CONNECTION_SESSION_ID, name, args)
        reserved = True
        session, bind_id = _resolve_session("remote", name, args, config=config)
        if session != _CONNECTION_SESSION_ID and hasattr(receipt_store, "set_session"):
            receipt_store.set_session(call_id, session)
    except Exception as exc:
        if reserved:
            try:
                receipt_store.finish(call_id, "failed")
            except TimeoutError:
                raise
            except (OSError, sqlite3.Error):
                pass
        return _admission_failure(exc)

    os.environ.setdefault("MINDIE_DIAGNOSTICS_CONFIG", str(diagnostics_config_path(config)))
    explicit_remote = os.environ.get("REMOTE_DEV_STATE_DIR")
    remote_root = Path(explicit_remote) if explicit_remote else (state_dir(config) / "remote")
    remote_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    succeeded = False
    outcome = "failed"
    try:
        from contextlib import nullcontext
        from host_adapter import bind_remote_session
        from remote_dev.core.cancellation import request_context
        _had_remote_session_env = "REMOTE_DEV_SESSION_ID" in os.environ
        from remote_dev.mcp.server import tool_text
        if not _had_remote_session_env and os.environ.get("REMOTE_DEV_SESSION_ID", "").startswith("mcp-"):
            os.environ.pop("REMOTE_DEV_SESSION_ID", None)
        from remote_dev.mcp.tools import call_tool
        from remote_dev.mcp.outcome import operation_outcome

        ctx_mgr = request_context(cancel) if cancel is not None else nullcontext()
        with bind_remote_session(session, state_root_dir=remote_root), ctx_mgr:
            raw = call_tool(name, args, profile="canonical")
        outcome = operation_outcome(name, raw)
        is_error = outcome != "succeeded"
        succeeded = not is_error
        blocks = [dict(type="text", text=tool_text(raw))]
        native_blocks = raw.get("content") if isinstance(raw, dict) else None
        if isinstance(native_blocks, list):
            blocks.extend(
                item for item in native_blocks if isinstance(item, dict) and isinstance(item.get("type"), str)
            )
        result = dict(
            content=blocks,
            structuredContent=raw if isinstance(raw, dict) else dict(result=raw),
            isError=is_error,
        )
    except Exception as exc:
        from remote_dev.core.errors import error_details, safe_auth_error_details

        details = error_details(exc)
        outcome = "unknown" if details.get("submission_state") == "uncertain" else "failed"
        auth = safe_auth_error_details(details)
        result = dict(
            content=[dict(type="text", text=auth["hint"] if auth else f"{type(exc).__name__}: {exc}")],
            structuredContent=(dict(ok=False, error_details=auth) if auth else
                               dict(ok=False, error=str(exc), error_type=type(exc).__name__)),
            isError=True,
        )

    try:
        receipt_store.finish(call_id, outcome)
    except (TimeoutError, OSError, sqlite3.Error) as exc:
        if not succeeded and isinstance(exc, TimeoutError):
            raise
        if succeeded:
            result = _attach_accounting_warning(result, "receipt_warning", exc)

    if bind_id is not None:
        try:
            finish_bind(bind_id, succeeded, config=config)
        except (TimeoutError, OSError, sqlite3.Error) as exc:
            if not succeeded and isinstance(exc, TimeoutError):
                raise
            if succeeded:
                result = _attach_accounting_warning(result, "bind_warning", exc)
    return result


def handle_rpc(
    request: dict,
    surface: str,
    *,
    config=None,
    receipts: RemoteReceipts | None = None,
    cancel: threading.Event | None = None,
) -> dict | None:
    if not isinstance(request, dict):
        return dict(
            jsonrpc="2.0",
            id=None,
            error=dict(code=-32600, message="Invalid Request: MCP frame must be a JSON object"),
        )
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}

    if method == "notifications/initialized" or req_id is None:
        return None
    if method == "ping":
        return dict(jsonrpc="2.0", id=req_id, result={})
    if method == "initialize":
        return dict(
            jsonrpc="2.0",
            id=req_id,
            result=dict(
                protocolVersion=params.get("protocolVersion") or "2024-11-05",
                capabilities=dict(tools=dict(listChanged=True)),
                serverInfo=dict(name=f"mindie-agy-{surface}",
                                version=read_json(Path(__file__).parent.parent / "plugin.json")["version"]),
            ),
        )
    if method == "tools/list":
        tools = remote_catalog() if surface == "remote" else tools_catalog()
        return dict(jsonrpc="2.0", id=req_id, result=dict(tools=tools))
    if method == "tools/call":
        try:
            trigger_background_update_if_due(config=config)
        except Exception as exc:
            print(
                f"MindIE background updater spawn failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not isinstance(name, str) or not name:
            return dict(
                jsonrpc="2.0",
                id=req_id,
                error=dict(code=-32602, message="MCP tools/call requires a tool name"),
            )
        if surface == "remote":
            outcome = call_remote_surface(
                name, arguments, rpc_id=req_id, config=config, receipts=receipts, cancel=cancel
            )
        else:
            outcome = call_tools_surface(
                name, arguments, rpc_id=req_id, config=config, receipts=receipts, cancel=cancel
            )
        return dict(jsonrpc="2.0", id=req_id, result=outcome)
    return dict(
        jsonrpc="2.0",
        id=req_id,
        error=dict(code=-32601, message=f"Method not found: {method}"),
    )


def serve(
    surface: str,
    *,
    config=None,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
) -> int:
    if surface not in {"remote", "tools"}:
        raise ValueError(f"unsupported MCP surface: {surface}")
    from host_adapter import serve_concurrent_stdio
    from storage_lifecycle import hold_runtime_lease

    receipts = RemoteReceipts(remote_receipts_path(config))
    with hold_runtime_lease():
        return serve_concurrent_stdio(
            surface=surface,
            rpc_handler=lambda req, cancel_ev: handle_rpc(
                req, surface, config=config, receipts=receipts, cancel=cancel_ev
            ),
            watched_files=(config_path(config),),
            stdin=stdin,
            stdout=stdout,
        )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    positional = [arg for arg in argv if arg != "--once"]
    surface = positional[0] if positional else "tools"
    from storage_lifecycle import hold_runtime_lease

    with hold_runtime_lease():
        return serve(surface)


if __name__ == "__main__":
    raise SystemExit(main())
