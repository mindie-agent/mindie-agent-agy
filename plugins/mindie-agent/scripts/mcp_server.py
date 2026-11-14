"""Public stdio JSON-RPC 2.0 MCP server for Antigravity (`remote` and `tools` surfaces).

[Role]
Serves public MCP discovery (`tools/list`) and tool execution (`tools/call`) for
`mindie-remote-dev` (`remote_*` tools + Antigravity aliases) and `mindie-tools`
(`profiler`, `sim`, `coordinator`, `diagnostics_status`, `diagnostics_bundle`,
and factual `mindie_status`).

[Invariants]
- Default MCP discovery (`tools/list`) NEVER exposes `knowledge_*` tool names,
  descriptions, or schemas—even if a session has activated `/mindie-agent`.
- Deterministic tools (`remote_*`, `profiler`, `sim`, `coordinator`,
  `diagnostics_*`, `mindie_status`) work in ordinary sessions without requiring
  a knowledge lease.
- Receipt Honesty Invariant: once a remote or component operation succeeds, if
  local SQLite receipt persistence (`RemoteReceipts.finish`) or `finish_bind`
  raises an `OSError` or `sqlite3.Error`, `isError` stays `False` and
  `receipt_warning` + `receipt_unconfirmed=True` are attached to
  `structuredContent` so the caller never blindly repeats a completed mutation.
- Non-empty UTF-8/JSON parse failures return JSON-RPC `-32700` Parse error.
  Parsed non-object frames return `-32600` Invalid Request (never dropped).
- Background updater spawn failure never aborts `tools/call` or the stdio loop.

[Timeouts & Locks]
- Uses SQLite WAL mode with `busy_timeout=5000` (`timeout=5.0`) for
  `RemoteReceipts` (`remote-receipts.sqlite3`).
- Enforces `remote_timeout(name, args) = max(minimum, inner_wait + 60.0s grace)`.

[Side-Effect Policy]
- `initialize` and `tools/list` read the selected release without knowledge
  authorization or service admission.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from admission import status_facts
from bounded import run as bounded_run
from component_update import trigger_background_update_if_due
from identity import claim_bind, finish_bind, require_session
from paths import bind_db_path, config_path, diagnostics_config_path, read_json, remote_receipts_path, which_native

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

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000;")
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
        db.execute(
            "CREATE TABLE IF NOT EXISTS receipts("
            "call_id TEXT PRIMARY KEY, "
            "session TEXT NOT NULL, "
            "tool TEXT NOT NULL, "
            "args_sha256 TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "started REAL NOT NULL, "
            "updated REAL NOT NULL)"
        )
        return db

    def begin(self, call_id: str, session: str, tool: str, args: dict) -> None:
        digest = hashlib.sha256(
            json.dumps(args or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        now = time.time()
        db = self._connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?,?,?)",
                    (call_id, session, tool, digest, "running", now, now),
                )
        finally:
            db.close()

    def finish(self, call_id: str, status: str) -> None:
        now = time.time()
        db = self._connect()
        try:
            with db:
                db.execute(
                    "UPDATE receipts SET status=?, updated=? WHERE call_id=?",
                    (status, now, call_id),
                )
        finally:
            db.close()


def tools_catalog() -> list[dict]:
    """Return the deterministic `mindie-tools` MCP catalog (zero knowledge tools)."""
    limits = {
        "timeout_ms": dict(
            type="integer",
            minimum=1,
            maximum=300000,
            default=120000,
            description="Execution deadline in milliseconds; includes inner budgets and cleanup grace.",
        ),
        "max_output_tokens": dict(
            type="integer",
            minimum=1,
            maximum=262144,
            default=16384,
            description="Output byte budget divided by four.",
        ),
    }
    items: list[dict] = [
        dict(
            name="mindie_status",
            description=(
                "Read factual MindIE control-plane status for the current Antigravity session. "
                "Does not activate a knowledge lease, start background services, or emit onboarding prompts."
            ),
            inputSchema=dict(type="object", properties={}, additionalProperties=False),
            annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        )
    ]
    for name, description in COMPONENT_DESCRIPTIONS.items():
        items.append(
            dict(
                name=name,
                description=description,
                inputSchema=dict(
                    type="object",
                    additionalProperties=False,
                    required=["argv"],
                    properties=dict(
                        argv=dict(
                            type="array",
                            items=dict(type="string"),
                            description="CLI arguments array; passed without shell expansion.",
                        ),
                        **limits,
                    ),
                ),
                annotations=dict(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
            )
        )
    items.append(
        dict(
            name="diagnostics_status",
            description="Inspect local MindIE diagnostic/reporting status without starting a worker or contacting GitHub.",
            inputSchema=dict(
                type="object",
                additionalProperties=False,
                properties=dict(config=dict(type="string"), **limits),
            ),
            annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        )
    )
    items.append(
        dict(
            name="diagnostics_bundle",
            description="Export a bounded, sanitized local diagnostic support bundle without uploading it.",
            inputSchema=dict(
                type="object",
                additionalProperties=False,
                required=[],
                properties=dict(
                    root=dict(type="string"),
                    operation_id=dict(type="string"),
                    incident_id=dict(type="string"),
                    output=dict(type="string"),
                    **limits,
                ),
            ),
            annotations=dict(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
        )
    )
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
        return [python_exe, "-I", "-X", "utf8", "-m", "mindie_diagnostics.cli"]
    if component == "npu_top":
        exe = which_native("npu-top")
        if exe:
            return [exe]
        return [python_exe, "-I", "-X", "utf8", "-m", "npu_top.cli"]
    module_map = {
        "profiler": "mindie_profiler",
        "sim": "mindie_sim",
        "coordinator": "mindie_coordinator.cli",
        "debugger": "mindie_debugger.cli",
    }
    exe = which_native("mindie-" + component)
    if exe:
        return [exe]
    return [python_exe, "-I", "-X", "utf8", "-m", module_map[component]]


def _resolve_session(surface: str, tool_name: str, arguments: dict, *, config=None) -> tuple[str, str | None]:
    """Resolve session identity from a `PreToolUse` bind or `AGY_CONVERSATION_ID`."""
    if bind_db_path(config).is_file():
        bound = claim_bind(surface, tool_name, arguments, config=config)
        return bound["session"], bound["bind_id"]
    return require_session(os.environ.get("AGY_CONVERSATION_ID")), None


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
    config=None,
    receipts: RemoteReceipts | None = None,
) -> dict:
    args = dict(arguments or {})
    try:
        session, bind_id = _resolve_session("tools", name, args, config=config)
    except Exception as exc:
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
    config=None,
    receipts: RemoteReceipts | None = None,
) -> dict:
    args = dict(arguments or {})
    try:
        session, bind_id = _resolve_session("remote", name, args, config=config)
        receipt_store = receipts or RemoteReceipts(remote_receipts_path(config))
        call_id = str(uuid.uuid4())
        receipt_store.begin(call_id, session, name, args)
    except Exception as exc:
        return _admission_failure(exc)

    os.environ.setdefault("MINDIE_DIAGNOSTICS_CONFIG", str(diagnostics_config_path(config)))
    os.environ["REMOTE_DEV_SESSION_ID"] = session
    succeeded = False
    outcome = "failed"
    try:
        from remote_dev.mcp.server import tool_text
        from remote_dev.mcp.tools import call_tool
        from remote_dev.mcp.outcome import operation_outcome

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


def handle_rpc(request: dict, surface: str, *, config=None, receipts: RemoteReceipts | None = None) -> dict | None:
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
                capabilities=dict(tools=dict(listChanged=False)),
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
            outcome = call_remote_surface(name, arguments, config=config, receipts=receipts)
        else:
            outcome = call_tools_surface(name, arguments, config=config, receipts=receipts)
        return dict(jsonrpc="2.0", id=req_id, result=outcome)
    return dict(
        jsonrpc="2.0",
        id=req_id,
        error=dict(code=-32601, message=f"Method not found: {method}"),
    )


def serve(surface: str, *, config=None) -> int:
    if surface not in {"remote", "tools"}:
        raise ValueError(f"unsupported MCP surface: {surface}")
    receipts = RemoteReceipts(remote_receipts_path(config))
    for raw_line in sys.stdin.buffer:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(
                f"MindIE MCP parse error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            sys.stdout.buffer.write(
                (
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {
                                "code": -32700,
                                "message": f"Parse error: {type(exc).__name__}: {exc}",
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            sys.stdout.buffer.flush()
            continue
        resp = handle_rpc(req, surface, config=config, receipts=receipts)
        if resp is not None:
            sys.stdout.buffer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    surface = argv[0] if argv else "tools"
    if "--once" not in argv:
        from remote_dev.mcp.catalog_refresh import profile_selection, serve_selected
        return serve_selected(lambda: profile_selection(
            config_path(), Path(__file__).resolve().parent, surface,
            config_env="MINDIE_AGENT_CONFIG", plugin_env="AGY_PLUGIN_ROOT",
        ))
    return serve(surface)


if __name__ == "__main__":
    raise SystemExit(main())
