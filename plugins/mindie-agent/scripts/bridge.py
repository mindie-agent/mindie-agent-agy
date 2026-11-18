"""Antigravity (AGY) lifecycle hook handlers (`SessionStart`, `PreInvocation`, `PreToolUse`, `Stop`).

[Role]
Implements Antigravity's native hook contracts (`hooks.json`), bridging
`SessionStart` (passive background update check), `PreInvocation` (zero-I/O `{}`
pass-through), `PreToolUse` (one-shot MCP call binding), and `Stop` (opt-in
redacted transcript capture when community sharing is enabled).

[Invariants]
- Default-Enabled Plugin & Zero-I/O Hooks: `SessionStart` and `PreInvocation`
  perform zero SQLite I/O and return `{}` immediately.
- Cold/Disabled `Stop` Fast-Path: when unconfigured, community sharing is
  disabled, or the workspace is outside authorized `project_roots`, `Stop`
  returns `{}` in `< 100ms` before opening `admission.sqlite3`. `Stop` never
  returns `"decision": "continue"`.
- `PreToolUse` bind failures, including `TimeoutError`, return
  `"decision": "deny"` with exit 0 so the host consumes stdout (never fail-open).

[Timeouts & Locks]
- Runs within `mindie_launch.py`'s `HOOK_TOTAL = 10.0s` budget; SQLite WAL
  writes use `5.0s` busy timeout.

[Side-Effect Policy]
- Mutates `admission.sqlite3` only on eligible `Stop` captures, writes one-shot
  binds to `mcp-binds.sqlite3` on `PreToolUse`, and hands off eligible `Stop`
  events to `mindie_knowledge.loop.cli.capture_hook`.
"""

from __future__ import annotations

import json
import sys

from admission import ensure_default_lease
from agy_transcript import capture_source
from component_update import trigger_background_update_if_due
from identity import (
    parse_agy_mcp_tool,
    parse_hook,
    publish_bind,
    session_from_hook,
    transcript_from_hook,
    workspace_from_hook,
)
from paths import community_config_path, config_path, engine_config_path


def _emit(payload: dict, *, exit_code: int = 0) -> int:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return exit_code


def _hook_failure_text(prefix: str, exc: BaseException) -> str:
    return f"{prefix}: {type(exc).__name__}: {exc}"


def _hook_warning_steps(detail: str) -> dict:
    return {"injectSteps": [{"ephemeralMessage": detail}]}


def _read_stdin_event() -> dict:
    raw = sys.stdin.buffer.read()
    return parse_hook(raw)


def handle_session_start(event: dict | None = None, *, config=None) -> dict:
    """`SessionStart` hook: passive background update check; zero SQLite I/O."""
    _ = _read_stdin_event() if event is None else event
    try:
        trigger_background_update_if_due(config=config)
    except Exception as exc:
        detail = _hook_failure_text("background updater spawn failed", exc)
        print(f"MindIE {detail}", file=sys.stderr)
        return _hook_warning_steps(detail)
    return {}


def handle_pre_invocation(event: dict | None = None, *, config=None) -> dict:
    """`PreInvocation` hook: pure zero-I/O `{}` pass-through."""
    parsed = _read_stdin_event() if event is None else event
    if not isinstance(parsed, dict):
        raise ValueError("native hook payload must be a JSON object")
    _ = config
    return {}


def handle_pre_tool_use(event: dict | None = None, *, config=None) -> dict:
    """`PreToolUse` hook: records a one-shot bind for MindIE MCP tool calls."""
    event = _read_stdin_event() if event is None else event
    tool_call = event.get("toolCall")
    parsed = parse_agy_mcp_tool(tool_call) if isinstance(tool_call, dict) else None
    if parsed is None:
        return {"decision": "allow"}
    surface, local_tool, arguments = parsed
    try:
        session = session_from_hook(event)
        workspace_root = None
        try:
            workspace_root = workspace_from_hook(event)
        except ValueError:
            pass
        step_idx = event.get("stepIdx")
        publish_bind(
            session,
            surface,
            local_tool,
            arguments,
            step_idx=step_idx if isinstance(step_idx, int) else 0,
            workspace_root=workspace_root,
            transcript_path=transcript_from_hook(event),
            config=config,
        )
    except Exception as exc:
        detail = _hook_failure_text("PreToolUse bind failed", exc)
        print(f"MindIE {detail}", file=sys.stderr)
        return {"decision": "deny", "reason": detail}
    return {"decision": "allow"}


def _allows_community_capture(project_root: str, *, config=None) -> tuple[bool, str]:
    from mindie_knowledge.loop import settings as settings_mod

    comm_file = community_config_path(config)
    if not comm_file.is_file():
        return False, "community_unconfigured"
    settings = settings_mod.load(comm_file)
    if not settings.allows_capture():
        return False, settings.capture_block_kind() or "disabled"
    if not settings.in_scope(project_root):
        return False, "out_of_scope"
    return True, "ok"


def handle_stop(event: dict | None = None, *, config=None) -> dict:
    """`Stop` hook: never forces continuation (`decision != 'continue'`)."""
    event = _read_stdin_event() if event is None else event
    try:
        session = session_from_hook(event)
    except ValueError:
        return {}
    if not config_path(config).is_file():
        return {}
    try:
        workspace_root = workspace_from_hook(event)
    except ValueError:
        return {}
    allowed, _reason = _allows_community_capture(workspace_root, config=config)
    if not allowed:
        return {}
    transcript = transcript_from_hook(event)
    if not transcript:
        return {}
    try:
        lease = ensure_default_lease(session, workspace_root, not_before=0.0, config=config)
        source = capture_source(transcript, session, binding=lease)
    except Exception as exc:
        detail = _hook_failure_text("Stop transcript capture failed", exc)
        print(f"MindIE {detail}", file=sys.stderr)
        return _hook_warning_steps(detail)

    exec_num = event.get("executionNum", 1)
    term_reason = event.get("terminationReason", "NO_TOOL_CALL")
    turn_id = f"{session}:exec:{exec_num}:{term_reason}"
    forwarded = dict(
        hook_event_name="Stop",
        identity_kind="turn",
        session_id=session,
        turn_id=turn_id,
        mindie_activation=lease.get("token"),
        harness="agy",
        transcript_path=source["transcript_path"],
    )
    try:
        from mindie_knowledge.loop.cli import capture_hook

        capture_hook(str(engine_config_path(config)), forwarded)
    except Exception as exc:
        detail = _hook_failure_text("Stop capture handoff failed", exc)
        print(f"MindIE {detail}", file=sys.stderr)
        return _hook_warning_steps(detail)
    return {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _emit({})
    op = argv[0]
    if op == "session-start":
        return _emit(handle_session_start())
    if op == "pre-invocation":
        return _emit(handle_pre_invocation())
    if op == "pre-tool-use":
        return _emit(handle_pre_tool_use())
    if op == "stop":
        return _emit(handle_stop())
    return _emit({})


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
