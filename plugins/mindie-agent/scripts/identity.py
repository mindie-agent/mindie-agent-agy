"""Antigravity (AGY) native hook payload validator and one-shot MCP bind store.

[Role]
Validates Antigravity camelCase hook payloads (`conversationId`, `workspacePaths`,
`transcriptPath`, `artifactDirectoryPath`, `lastUserInput`, `toolCall`, `stepIdx`,
`executionNum`, `terminationReason`) and manages one-shot SQLite WAL binds
connecting `PreToolUse` to `mcp_server.py`.

[Invariants]
- Native task identity comes strictly from `conversationId` in hook payloads or
  `AGY_CONVERSATION_ID` in the environment—never from model-controlled tool
  arguments.
- `PreToolUse` binds `(server, local_tool, args_digest)` to `(conversationId,
  stepIdx, workspace_root, transcriptPath)` with a 120s TTL; `claim_bind`
  consumes each bind at most once under `BEGIN IMMEDIATE`.

[Timeouts & Locks]
- Opens `mcp-binds.sqlite3` with `PRAGMA journal_mode=WAL` and
  `PRAGMA busy_timeout=5000` (`timeout=5.0`) once per connection; no `.owner`
  `lstat` or schema reflection on hot paths.

[Side-Effect Policy]
- Mutates only `state_dir() / "mcp-binds.sqlite3"`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from paths import bind_db_path

SESSION_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
GESTURE_RE = re.compile(r"^\s*(?:/mindie-agent|\$mindie-agent(?::mindie-agent)?)(?=\s|$)")
MCP_EAGER_RE = re.compile(
    r"^mcp_(?:mindie[-_]agent[-_])?(mindie[-_]remote[-_]dev|mindie[-_]tools)_([A-Za-z0-9_.]+)\Z"
)
BIND_TTL_SECONDS = 120.0
MAX_HOOK_BYTES = 32 * 1024 * 1024


def parse_hook(raw: bytes) -> dict:
    if len(raw) > MAX_HOOK_BYTES:
        raise ValueError("native hook payload exceeds 32 MiB byte budget")
    if not raw.strip():
        return {}
    event = json.loads(raw.decode("utf-8"))
    if not isinstance(event, dict):
        raise ValueError("native hook payload must be a JSON object")
    return event


def require_session(value) -> str:
    if not isinstance(value, str) or not SESSION_RE.fullmatch(value.strip()):
        raise ValueError("native Antigravity conversationId is missing or invalid")
    return value.strip().lower()


def session_from_hook(event: dict) -> str:
    return require_session(event.get("conversationId") or event.get("session_id"))


def workspace_from_hook(event: dict) -> str:
    paths = event.get("workspacePaths")
    if isinstance(paths, list) and paths:
        first = paths[0]
        if isinstance(first, str) and os.path.isabs(first):
            return str(Path(first).resolve())
    cwd = event.get("cwd")
    if isinstance(cwd, str) and os.path.isabs(cwd):
        return str(Path(cwd).resolve())
    raise ValueError("native hook payload has no absolute workspacePaths entry")


def transcript_from_hook(event: dict) -> str | None:
    value = event.get("transcriptPath") or event.get("transcript_path")
    if isinstance(value, str) and os.path.isabs(value):
        return str(Path(value))
    return None


def parse_mindie_gesture(event: dict) -> tuple[bool, str]:
    """Return `(is_gesture, subcommand_or_body)` from `lastUserInput`."""
    prompt = event.get("lastUserInput")
    if not isinstance(prompt, str):
        prompt = event.get("prompt")
    if not isinstance(prompt, str):
        return False, ""
    match = GESTURE_RE.match(prompt)
    if match is None:
        return False, ""
    remainder = prompt[match.end():].strip()
    return True, remainder


def parse_agy_mcp_tool(tool_call: dict) -> tuple[str, str, dict] | None:
    """Resolve an Antigravity `toolCall` object to `(surface, local_tool, args)`.

    Supports both eager MCP tool names (`mcp_mindie-remote-dev_remote_read`) and
    lazy `call_mcp_tool` invocations (`ServerName="mindie-remote-dev"`).
    """
    if not isinstance(tool_call, dict):
        return None
    name = tool_call.get("name")
    args = tool_call.get("args")
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    if name == "call_mcp_tool":
        server = args.get("ServerName")
        tool_name = args.get("ToolName")
        inner_args = args.get("Arguments")
        if not isinstance(server, str) or not isinstance(tool_name, str):
            return None
        norm_server = server.replace("_", "-")
        if norm_server.startswith("mindie-agent-"):
            norm_server = norm_server[len("mindie-agent-"):]
        if norm_server == "mindie-remote-dev":
            return "remote", tool_name, inner_args if isinstance(inner_args, dict) else {}
        if norm_server == "mindie-tools":
            return "tools", tool_name, inner_args if isinstance(inner_args, dict) else {}
        return None
    match = MCP_EAGER_RE.match(name)
    if match is None:
        return None
    raw_server, local_tool = match.group(1), match.group(2)
    surface = "remote" if "remote" in raw_server else "tools"
    return surface, local_tool, args


def canonical_args(arguments: dict | None) -> str:
    return json.dumps(
        arguments if isinstance(arguments, dict) else {},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def args_digest(arguments: dict | None) -> str:
    return hashlib.sha256(canonical_args(arguments).encode("utf-8")).hexdigest()


def _connect(path: Path, *, create: bool) -> sqlite3.Connection:
    if not path.exists() and not create:
        raise FileNotFoundError(str(path))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5.0)
    db.execute("PRAGMA busy_timeout=5000;")
    if create:
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
    return db


def _ensure_binds(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS binds("
        "bind_id TEXT PRIMARY KEY, "
        "session TEXT NOT NULL, "
        "surface TEXT NOT NULL, "
        "local_tool TEXT NOT NULL, "
        "args_digest TEXT NOT NULL, "
        "step_idx INTEGER NOT NULL, "
        "workspace_root TEXT, "
        "transcript_path TEXT, "
        "status TEXT NOT NULL, "
        "created REAL NOT NULL, "
        "updated REAL NOT NULL)"
    )


def publish_bind(
    session_id: str,
    surface: str,
    local_tool: str,
    arguments: dict,
    *,
    step_idx: int = 0,
    workspace_root: str | None = None,
    transcript_path: str | None = None,
    config=None,
) -> str:
    """Record a one-shot `PreToolUse` bind for an Antigravity MCP tool call."""
    session_id = require_session(session_id)
    if surface not in {"remote", "tools"}:
        raise ValueError("unknown MindIE MCP surface")
    if not isinstance(local_tool, str) or not local_tool:
        raise ValueError("local_tool must be a nonempty string")
    adigest = args_digest(arguments)
    step = int(step_idx) if isinstance(step_idx, int) and not isinstance(step_idx, bool) else 0
    bind_id = hashlib.sha256(
        f"{session_id}|{surface}|{local_tool}|{adigest}|{step}".encode("utf-8")
    ).hexdigest()
    now = time.time()
    db = _connect(bind_db_path(config), create=True)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            _ensure_binds(db)
            db.execute(
                "INSERT OR REPLACE INTO binds VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    bind_id,
                    session_id,
                    surface,
                    local_tool,
                    adigest,
                    step,
                    workspace_root,
                    transcript_path,
                    "bound",
                    now,
                    now,
                ),
            )
            return bind_id
    finally:
        db.close()


def claim_bind(
    surface: str,
    local_tool: str,
    arguments: dict,
    *,
    config=None,
) -> dict:
    """Atomically claim the freshest unexpired `PreToolUse` bind matching `(surface, local_tool, args)`."""
    if surface not in {"remote", "tools"}:
        raise ValueError("unknown MindIE MCP surface")
    adigest = args_digest(arguments)
    path = bind_db_path(config)
    if not path.is_file():
        raise ValueError("no native PreToolUse bind database exists; call rejected")
    now = time.time()
    cutoff = now - BIND_TTL_SECONDS
    db = _connect(path, create=False)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            _ensure_binds(db)
            row = db.execute(
                "SELECT bind_id, session, workspace_root, transcript_path, step_idx "
                "FROM binds "
                "WHERE surface=? AND local_tool=? AND args_digest=? AND status='bound' AND created>=? "
                "ORDER BY created DESC, step_idx DESC LIMIT 1",
                (surface, local_tool, adigest, cutoff),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"no active PreToolUse bind found for {surface}.{local_tool}; "
                    "unauthorized or replayed MCP tool call"
                )
            bind_id, session, workspace_root, transcript_path, step_idx = row
            db.execute(
                "UPDATE binds SET status='claimed', updated=? WHERE bind_id=?",
                (now, bind_id),
            )
            return dict(
                bind_id=bind_id,
                session=session,
                workspace_root=workspace_root,
                transcript_path=transcript_path,
                step_idx=step_idx,
                surface=surface,
                local_tool=local_tool,
            )
    finally:
        db.close()


def finish_bind(bind_id: str, succeeded: bool, *, config=None) -> None:
    """Mark a claimed bind as `succeeded` or `failed`."""
    if not isinstance(bind_id, str) or not bind_id:
        return
    path = bind_db_path(config)
    if not path.is_file():
        return
    db = _connect(path, create=False)
    try:
        with db:
            db.execute(
                "UPDATE binds SET status=?, updated=? WHERE bind_id=? AND status='claimed'",
                ("succeeded" if succeeded else "failed", time.time(), bind_id),
            )
    finally:
        db.close()
