"""Antigravity (AGY) lifecycle hook handlers (`SessionStart`, `PreInvocation`, `PreToolUse`, `Stop`).

[Role]
Implements Antigravity's native hook contracts (`hooks.json`), bridging
`SessionStart`, `PreInvocation` (`/mindie-agent` manual gesture), `PreToolUse`
(one-shot MCP call binding), and `Stop` (turn-end redacted transcript handoff).

[Invariants]
- Passive Default: `SessionStart` and ordinary `PreInvocation` prompts without
  `/mindie-agent` or `$mindie-agent` never activate a lease, start the knowledge
  service, or capture transcripts (`PreInvocation` returns `{}` immediately).
- Lease Honesty: if `Admission.activate` succeeds on `/mindie-agent` in
  `PreInvocation` but background service readiness fails, the active lease is
  preserved (`status="degraded"` warning injected) rather than revoked.
- `Stop` never returns `"decision": "continue"` (never forces model
  continuation). Unactivated sessions short-circuit with `{}` before reading
  sharing settings or transcripts.

[Timeouts & Locks]
- Runs within `mindie_launch.py`'s `HOOK_TOTAL = 10.0s` budget; SQLite WAL
  writes use `5.0s` busy timeout.

[Side-Effect Policy]
- Mutates `admission.sqlite3` on `/mindie-agent`, writes one-shot binds to
  `mcp-binds.sqlite3` on `PreToolUse`, and hands off eligible `Stop` events to
  `mindie_knowledge.loop.cli.capture_hook`.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from admission import activate, deactivate, knowledge_lease, status_facts
from agy_transcript import capture_source
from identity import (
    parse_agy_mcp_tool,
    parse_hook,
    parse_mindie_gesture,
    publish_bind,
    session_from_hook,
    transcript_from_hook,
    workspace_from_hook,
)
from paths import (
    community_config_path,
    config_path,
    engine_config_path,
    plugin_root,
    read_json,
)


def _emit(payload: dict) -> int:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def _read_stdin_event() -> dict:
    raw = sys.stdin.buffer.read()
    return parse_hook(raw)


def handle_session_start(event: dict | None = None, *, config=None) -> dict:
    """`SessionStart` hook: passive by default; never activates a knowledge lease."""
    event = _read_stdin_event() if event is None else event
    try:
        session = session_from_hook(event)
    except ValueError:
        return {}
    lease = knowledge_lease(session, config=config)
    if lease is None:
        return {}
    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": (
                        f"MindIE Agent knowledge lease is active for conversation {session} "
                        f"(project_root={lease.get('project_root')})."
                    )
                }
            }
        ]
    }


def handle_pre_invocation(event: dict | None = None, *, config=None) -> dict:
    """`PreInvocation` hook: handles explicit `/mindie-agent` or `$mindie-agent` gestures."""
    event = _read_stdin_event() if event is None else event
    is_gesture, remainder = parse_mindie_gesture(event)
    if not is_gesture:
        return {}
    session = session_from_hook(event)
    workspace_root = workspace_from_hook(event)
    action = remainder.split()[0].lower() if remainder else "activate"

    if action == "deactivate":
        result = deactivate(session, config=config)
        if result.get("status") != "inactive":
            msg = (
                "MindIE session deactivation is incomplete; inspect per-domain status: "
                + json.dumps(result, ensure_ascii=False)
            )
        else:
            msg = (
                "MindIE knowledge is deactivated for this conversation. "
                "Deterministic tools (remote-dev, profiler, sim, coordinator, diagnostics) remain available."
            )
        return {"injectSteps": [{"ephemeralMessage": msg}]}

    if action == "status":
        facts = status_facts(session, config=config)
        return {
            "injectSteps": [
                {
                    "ephemeralMessage": "MindIE factual status: "
                    + json.dumps(facts, ensure_ascii=False)
                }
            ]
        }

    lease_info = activate(session, workspace_root, config=config)
    scripts_dir = Path(__file__).resolve().parent
    skill_path = plugin_root() / "skills" / "mindie-agent" / "SKILL.md"
    knowledge_cli = scripts_dir / "knowledge_cli.py"

    readiness_warning = ""
    cfg_file = config_path(config)
    if not cfg_file.is_file():
        try:
            from setup import ensure_default_profile

            ensure_default_profile(config=cfg_file, workspace_root=workspace_root)
        except Exception as exc:
            lease_info["status"] = "degraded"
            readiness_warning = (
                f" Note: default profile initialization degraded ({type(exc).__name__}); "
                "the session lease remains active."
            )

    msg = (
        f"The user explicitly enabled MindIE Agent for conversation `{session}` "
        f"(project_root=`{lease_info['project_root']}`, status=`{lease_info['status']}`). "
        f"Read the MindIE Agent skill instructions at `{skill_path}` and invoke private knowledge tools "
        f"via `python -I \"{knowledge_cli}\" describe` / `python -I \"{knowledge_cli}\" <tool_name>` "
        f"with `AGY_CONVERSATION_ID={session}`.{readiness_warning}"
    )
    return {"injectSteps": [{"ephemeralMessage": msg}]}


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
        print(f"MindIE PreToolUse bind warning: {type(exc).__name__}", file=sys.stderr)
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
    lease = knowledge_lease(session, config=config)
    if lease is None:
        return {}
    if not config_path(config).is_file():
        return {}
    allowed, reason = _allows_community_capture(lease["project_root"], config=config)
    if not allowed:
        return {}
    transcript = transcript_from_hook(event)
    if not transcript:
        return {}
    try:
        source = capture_source(transcript, session, binding=lease)
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return {}

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
        print(f"MindIE Stop capture handoff warning: {type(exc).__name__}", file=sys.stderr)
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
