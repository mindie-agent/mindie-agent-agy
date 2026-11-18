"""Antigravity lifecycle hooks and knowledge RPC fallback (`SessionStart`, `PreInvocation`, `PreToolUse`, `Stop`).
[Role]
Implements the frozen `hooks.json` contracts and provides `rpc_knowledge` when
`mindie_harness` reports `knowledge_unavailable` for an in-process checkout.
[Invariants]
- Stop returns `{}` and NEVER emits `"decision": "continue"`; an `inactive`
  admission lease is never reactivated.
- PreToolUse needs zero `_meta`; forged identity keys or malformed `session_id` deny.
- Receipt Honesty: a successful RPC whose `Admission.finish` raises
  `OSError` / `sqlite3.Error` keeps `isError=False` with `receipt_warning`.
[Timeouts & Locks]
- `capture_hook` is bounded to `6.0s`; knowledge RPC uses `35s` reads and `110s` sync/distill.
[Side-Effect Policy]
- Writes the state marker on SessionStart and captures only for an authorized
  in-scope Stop. Never mutates the host Antigravity data directory.
"""
from __future__ import annotations
import json, os, re, sqlite3, sys
from pathlib import Path
from typing import Any
from mindie_harness.api import ProfileRef
from mindie_harness.mcp.receipts import apply_receipt_honesty
from mindie_harness.mcp.router import RequestContext
from mindie_harness.runtime.activation import write_pending_update_check
from mindie_harness.sharing import sharing_status
from agy_transcript import capture_source
MAX_HOOK_BYTES = 1024 * 1024
SESSION_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
def _event(event: dict | None = None) -> dict:
    if event is not None:
        if not isinstance(event, dict): raise ValueError("native hook payload must be a JSON object")
        return event
    raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
    if len(raw) > MAX_HOOK_BYTES: raise ValueError("native hook payload exceeds 1 MiB")
    if not raw.strip(): return {}
    parsed = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(parsed, dict): raise ValueError("native hook payload must be a JSON object")
    return parsed
def _profile(config: str | Path | None = None) -> ProfileRef:
    return ProfileRef.resolve("agy", plugin_root=Path(__file__).resolve().parent.parent, config_path=config or os.environ.get("MINDIE_AGENT_CONFIG"))
def _session(event: dict) -> str | None:
    raw = event.get("conversationId") or event.get("conversation_id") or event.get("session_id")
    return raw.strip().lower() if isinstance(raw, str) and SESSION_RE.fullmatch(raw.strip()) else None
def _workspace(event: dict) -> str | None:
    values = [event.get(key) for key in ("workspaceRoot", "workspace_root", "cwd", "directory", "project_root")] + list(event.get("workspacePaths") or [])
    for value in values:
        if isinstance(value, str) and os.path.isabs(value): return str(Path(value).expanduser().resolve())
    return None
def _transcript(event: dict, session: str) -> str | None:
    direct = event.get("transcriptPath") or event.get("transcript_path")
    if isinstance(direct, str) and os.path.isabs(direct): return str(Path(direct))
    app_data = event.get("appDataDir") or event.get("app_data_dir") or os.environ.get("AGY_APP_DATA_DIR")
    if not isinstance(app_data, str) or not os.path.isabs(app_data): return None
    logs = Path(app_data) / "brain" / session / ".system_generated" / "logs"
    for name in ("transcript_full.jsonl", "transcript.jsonl"):
        if (logs / name).is_file(): return str(logs / name)
    return None
def _tool(event: dict) -> tuple[str, dict]:
    call = event.get("toolCall") or event.get("tool_call")
    if not isinstance(call, dict): return "", {}
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else call.get("args")
    args = dict(args) if isinstance(args, dict) else {}
    name = call.get("name")
    if name == "call_mcp_tool":
        name = args.get("ToolName") or args.get("tool_name") or args.get("tool") or ""
        inner = args.get("Arguments") or args.get("arguments")
        args = dict(inner) if isinstance(inner, dict) else args
    return name if isinstance(name, str) else "", args
def _read_json(path: Path) -> dict:
    try: raw = Path(path).read_bytes() if Path(path).is_file() else None
    except TimeoutError: raise
    except OSError: return {}
    try: doc = json.loads(raw.decode("utf-8-sig")) if raw is not None else None
    except (UnicodeDecodeError, ValueError): return {}
    return doc if isinstance(doc, dict) else {}
def _engine(profile: ProfileRef) -> Path:
    raw = _read_json(profile.config_path).get("engine_config")
    return Path(raw) if isinstance(raw, str) and os.path.isabs(raw) else profile.config_path.with_name(profile.config_path.stem + ".engine.json")
def _error(reason: str, message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "structuredContent": {"reason": reason, "message": message, "retryable": False}, "isError": True}
def rpc_knowledge(ctx: RequestContext, arguments: dict, profile: ProfileRef) -> dict | None:
    """Fallback RPC bridge to `mindie_knowledge.loop.transport` with Receipt Honesty."""
    try:
        from mindie_knowledge.loop import transport
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop.cli import ensure_service
    except TimeoutError: raise
    except Exception: return None
    method, payload = ctx.tool_name.removeprefix("knowledge_"), dict(arguments or {})
    payload["_session_id"] = ctx.session_id
    admission, token = Admission(str(profile.admission_db)), None
    try:
        inspect_fn = getattr(admission, "inspect", None)
        if callable(inspect_fn) and inspect_fn(ctx.session_id).get("status") == "inactive":
            return _error("knowledge_inactive", "MindIE knowledge is explicitly deactivated for this session.")
        lease = admission.active_lease(ctx.session_id)
        if isinstance(lease, dict): token = lease.get("token")
        if method == "distill" and not token:
            scope = str((ctx.cwd or profile.plugin_root).resolve())
            token = admission.activate(ctx.session_id, project_root=scope, root_session=ctx.session_id, not_before=0.0).get("token")
    except TimeoutError: raise
    except (OSError, sqlite3.Error, ValueError):
        if method == "distill": return None
    if method == "distill": payload.pop("_session_verified", None); payload["_activation"] = token
    else: payload.pop("_activation", None); payload["_session_verified"] = True
    try:
        value = transport.rpc(ensure_service(str(_engine(profile))), method, payload, timeout=110.0 if method in {"sync", "distill"} else 35.0)
        is_error = False
        if method == "distill":
            from mindie_knowledge.graded.distill import result_view
            value, is_error = result_view(value)
        result: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}],
                                  "structuredContent": value if isinstance(value, dict) else {"feeds": value}, "isError": bool(is_error)}
    except TimeoutError: raise
    except Exception as exc: return _error("knowledge_rpc_failed", f"mindie_knowledge RPC failed ({type(exc).__name__}): {exc}")
    if token:
        try: admission.finish(ctx.session_id, token, not is_error)
        except TimeoutError as exc:
            if not is_error: result = apply_receipt_honesty(result, exc)
        except Exception as exc:
            if not is_error: result = apply_receipt_honesty(result, exc)
    return result
def _in_scope(candidate: str, roots: list) -> bool:
    try: target = Path(candidate).expanduser().resolve()
    except TimeoutError: raise
    except (OSError, ValueError): return False
    for root in roots:
        if not isinstance(root, str) or not os.path.isabs(root): continue
        try:
            if target.is_relative_to(Path(root).expanduser().resolve()): return True
        except TimeoutError: raise
        except (OSError, ValueError): continue
    return False
def handle_session_start(event: dict | None = None, *, config: str | Path | None = None) -> dict:
    _ = _event(event)
    write_pending_update_check(_profile(config))
    return {}
def handle_pre_invocation(event: dict | None = None, *, config: str | Path | None = None) -> dict:
    _ = (_event(event), config)
    return {}
def handle_pre_tool_use(event: dict | None = None, *, config: str | Path | None = None) -> dict:
    _ = config
    tool, args = _tool(_event(event))
    if any(key in args for key in ("_session_id", "_activation", "mindie_activation")):
        return {"decision": "deny", "reason": "forged session identity fields are not accepted"}
    session = args.get("session_id")
    if "knowledge_" in tool and session is not None and (not isinstance(session, str) or not session.strip() or len(session) > 256):
        return {"decision": "deny", "reason": "malformed session identity"}
    return {"decision": "allow"}
def handle_stop(event: dict | None = None, *, config: str | Path | None = None) -> dict:
    event = _event(event)
    session, workspace = _session(event), _workspace(event)
    if session is None or workspace is None: return {}
    profile = _profile(config)
    if not Path(profile.config_path).is_file(): return {}
    status = sharing_status(profile, cwd=workspace)
    if status.get("tier") not in {"contribute", "local-only"} or status.get("cwd_in_scope") is not True: return {}
    transcript = _transcript(event, session)
    if transcript is None: return {}
    try:
        from mindie_knowledge.loop.activation import Admission
        admission = Admission(str(profile.admission_db))
        if admission.inspect(session).get("status") == "inactive": return {}
        lease = admission.activate(session, project_root=workspace, root_session=session, not_before=0.0)
        source = capture_source(transcript, session, binding=lease)
        engine = _engine(profile)
        if engine.is_file():
            from mindie_knowledge.loop.handoff import prepare_schema
            prepare_schema(str(engine))
        forwarded = {"hook_event_name": "Stop", "identity_kind": "turn", "session_id": session,
                     "turn_id": f"{session}:exec:{event.get('executionNum', 1)}:{event.get('terminationReason', 'NO_TOOL_CALL')}",
                     "mindie_activation": lease.get("token"), "harness": "agy", "transcript_path": source["transcript_path"]}
        from mindie_knowledge.loop.cli import capture_hook
        capture_hook(str(engine), forwarded, timeout=6.0)
    except TimeoutError: raise
    except (OSError, ImportError, sqlite3.Error, ValueError, RuntimeError) as exc:
        print(f"MindIE Stop capture skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
    return {}
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    handlers = {"session-start": handle_session_start, "pre-invocation": handle_pre_invocation, "pre-tool-use": handle_pre_tool_use, "stop": handle_stop}
    payload: dict = {}
    try:
        if argv and argv[0] in handlers: payload = handlers[argv[0]]()
    except TimeoutError: raise
    except Exception as exc: print(f"MindIE hook failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")); sys.stdout.buffer.flush()
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
