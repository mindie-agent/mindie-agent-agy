"""Antigravity lifecycle hooks (`SessionStart`, `PreInvocation`, `PreToolUse`, `Stop`).
[Role] Implements the frozen `hooks.json` contracts and delegates authorized `Stop`
transcript capture to `mindie_harness.runtime.knowledge_binding.run_stop_capture`.
[Invariants] Stop returns `{}` in `< 30ms` when passive/disabled/out-of-scope with zero
SQLite, NEVER emits `"decision": "continue"`, never reactivates a revoked lease, and uses
`"identity_kind": "notification"` unless a native turn/execution ID is supplied.
[Timeouts & Locks] `run_stop_capture` is bounded to `6.0s` around one monotonic deadline.
[Side-Effect Policy] Writes `pending-update-check.json` on SessionStart; never touches `~/.gemini`.
"""
from __future__ import annotations
import json, os, re, sqlite3, sys
from pathlib import Path
from mindie_harness.api import ProfileRef
from mindie_harness.runtime.activation import write_pending_update_check
from mindie_harness.runtime.knowledge_binding import run_stop_capture
from mindie_harness.sharing import sharing_status
PLUGIN_ROOT, MAX_HOOK_BYTES = Path(__file__).resolve().parent.parent, 1024 * 1024
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
    return ProfileRef.resolve("agy", plugin_root=PLUGIN_ROOT, config_path=config or os.environ.get("MINDIE_AGENT_CONFIG"))
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
    if isinstance(direct, str) and os.path.isabs(direct):
        candidate = Path(direct)
        if candidate.name == "transcript.jsonl" and candidate.with_name("transcript_full.jsonl").is_file():
            candidate = candidate.with_name("transcript_full.jsonl")
        return str(candidate) if candidate.is_file() else None
    app_data = event.get("appDataDir") or event.get("app_data_dir") or os.environ.get("AGY_APP_DATA_DIR")
    if not isinstance(app_data, str) or not os.path.isabs(app_data): return None
    logs = Path(app_data) / "brain" / session / ".system_generated" / "logs"
    for name in ("transcript_full.jsonl", "transcript.jsonl"):
        if (logs / name).is_file(): return str(logs / name)
    return None
def _clean_id(val: object) -> str | None:
    return val.strip() if isinstance(val, str) and val.strip() and "\x00" not in val and len(val.strip()) <= 256 else None
def _stop_identity(event: dict, session: str, transcript: str) -> dict[str, str]:
    if turn := _clean_id(event.get("turnId") or event.get("turn_id")): return {"identity_kind": "turn", "turn_id": turn}
    if exec_id := _clean_id(event.get("executionId") or event.get("execution_id")): return {"identity_kind": "turn", "turn_id": f"exec:{exec_id}"[:256]}
    num = event.get("executionNum") if "executionNum" in event else event.get("execution_num")
    if (type(num) is int and num >= 0) or _clean_id(num): return {"identity_kind": "turn", "turn_id": f"exec:{str(num).strip()}"[:256]}
    if evt := _clean_id(event.get("eventId") or event.get("event_id")): return {"identity_kind": "notification", "event_id": evt}
    stat = Path(transcript).stat()
    return {"identity_kind": "notification", "event_id": f"{session}:eof:{stat.st_size}:{stat.st_mtime_ns}"[:256]}
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
        payload = {"hook_event_name": "Stop", "harness": "agy", "session_id": session, "cwd": workspace, "transcript_path": transcript, **_stop_identity(event, session, transcript)}
        if "stop_hook_active" in event or "stopHookActive" in event:
            payload["stop_hook_active"] = event["stop_hook_active"] if "stop_hook_active" in event else event["stopHookActive"]
        run_stop_capture(profile, payload, plugin_root=PLUGIN_ROOT, timeout_sec=6.0)
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
