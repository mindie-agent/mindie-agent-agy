"""Pure-stdlib entrypoint for Antigravity hooks, MCP, setup, update, and doctor.
[Role] Dispatches `hook`, `mcp remote|tools`, and `setup`/`update`/`doctor`/`sharing`
with pure-stdlib hook fast-paths and verified `mindie-harness-seed.pyz` cold handoff.
[Invariants] Zero third-party imports at import time; `hook stop` NEVER emits
`"decision": "continue"`; rejects `WindowsApps` 0-byte Python stubs; streams seed
in 64 KiB chunks; removes SessionStart sibling temp file in `finally`.
[Timeouts & Locks] Hook re-exec `15s`; cold seed `300s` (`stdin=DEVNULL`); MCP unbounded.
[Side-Effect Policy] Writes only `state/agy/pending-update-check.json`; never touches `~/.gemini`.
"""
from __future__ import annotations
import hashlib, importlib.util, io, json, os, subprocess, sys, tempfile, time
from pathlib import Path
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path: sys.path.insert(0, str(SCRIPTS_DIR))
PLUGIN_ROOT, MAX_HOOK_BYTES, HOOK_TIMEOUT, SEED_TIMEOUT = SCRIPTS_DIR.parent, 1024 * 1024, 15.0, 300.0
def _home(config: bool) -> Path:
    raw = os.environ.get("MINDIE_CONFIG_HOME" if config else "MINDIE_DATA_HOME")
    if raw: return Path(raw).expanduser()
    base = Path(os.environ.get("APPDATA" if config else "LOCALAPPDATA") or Path.home() / ("AppData/Roaming" if config else "AppData/Local")) if os.name == "nt" else Path(os.environ.get("XDG_CONFIG_HOME" if config else "XDG_DATA_HOME") or Path.home() / (".config" if config else ".local/share"))
    return base / "mindie-agent"
def cfg_path() -> Path:
    raw = os.environ.get("MINDIE_AGENT_CONFIG")
    return Path(raw).expanduser() if raw else _home(True) / "agy.json"
def state_dir() -> Path:
    return _home(False) / "state" / "agy"
def _read_json(path: Path) -> dict | None:
    try: raw = Path(path).read_bytes() if Path(path).is_file() else None
    except TimeoutError: raise
    except OSError: return None
    try: doc = json.loads(raw.decode("utf-8-sig")) if raw is not None else None
    except (UnicodeDecodeError, ValueError): return None
    return doc if isinstance(doc, dict) else None
def _norm(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.parent.resolve() / path.name
def verify_seed_bundle(plugin_dir: str | Path | None = None) -> dict:
    """Verify `assets/mindie-harness-seed.pyz` against its `mindie-harness-seed/1` lock."""
    root = Path(plugin_dir).expanduser() if plugin_dir is not None else PLUGIN_ROOT
    if not (root / "assets").is_dir() and (root.parent / "assets").is_dir(): root = root.parent
    try: lock = json.loads((root / "assets/seed-lock.json").read_bytes().decode("utf-8-sig"))
    except TimeoutError: raise
    except (OSError, ValueError) as exc: raise ValueError(f"Invalid seed lock file: {root}") from exc
    if not isinstance(lock, dict) or lock.get("schema") != "mindie-harness-seed/1":
        raise ValueError("Invalid mindie-harness-seed/1 lock document")
    sha, size = str(lock.get("sha256") or "").lower(), lock.get("size_bytes")
    if len(sha) != 64 or type(size) is not int or size <= 0:
        raise ValueError("Invalid mindie-harness-seed/1 lock document")
    digest, total, pyz = hashlib.sha256(), 0, root / "assets/mindie-harness-seed.pyz"
    try:
        with pyz.open("rb") as stream:
            while chunk := stream.read(65536):
                digest.update(chunk); total += len(chunk)
    except TimeoutError: raise
    except OSError as exc: raise ValueError(f"Seed archive is unreadable: {pyz}") from exc
    if total != size or digest.hexdigest() != sha: raise ValueError(f"Seed integrity mismatch for {pyz}")
    return lock
def _explicit_cfg(argv: list[str]) -> Path | None:
    for idx, tok in enumerate(argv):
        if tok == "--config" and idx + 1 < len(argv) and not argv[idx + 1].startswith("-"): return Path(argv[idx + 1]).expanduser()
        if tok.startswith("--config="): return Path(tok.split("=", 1)[1]).expanduser()
    return None
def _cli_cfg(argv: list[str]) -> Path:
    return _explicit_cfg(argv) or cfg_path()
def _readonly_cli(role: str, argv: list[str]) -> bool:
    if any(tok in {"-h", "--help"} or tok.startswith("--help=") or (tok.startswith("--dry") and "--dry-run".startswith(tok)) for tok in argv): return True
    if argv[:2] == ["setup", "release-metadata"]: return True
    return (role == "sharing" or argv[:2] == ["setup", "sharing"]) and ("--status" in argv or not any(f in argv for f in ("--enable", "--local-only", "--disable")))
def _usable_py(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip() or "\\microsoft\\windowsapps\\" in raw.replace("/", "\\").lower(): return None
    cand = Path(raw).expanduser()
    try: st = cand.stat()
    except TimeoutError: raise
    except OSError: return None
    return str(_norm(cand)) if cand.is_file() and (os.name != "nt" or st.st_size > 0) else None
def _active_python(target_cfg: Path | None = None) -> str | None:
    cfg = _read_json(target_cfg or cfg_path()) or {}
    if py := _usable_py(cfg.get("python")): return py
    release = cfg.get("component_release")
    if isinstance(release, str) and release:
        base = Path(release).expanduser()
        receipt = _read_json(base / "runtime.json" if base.is_dir() else base) or {}
        validation = receipt.get("validation")
        if isinstance(validation, dict) and validation.get("status") == "validated" and (py := _usable_py(receipt.get("python"))): return py
    try: importable = importlib.util.find_spec("mindie_harness") is not None
    except (ImportError, AttributeError, ValueError): importable = False
    return sys.executable if importable and _usable_py(sys.executable) else None
def _emit(payload: dict) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")); sys.stdout.buffer.flush()
def _read_hook() -> tuple[dict, bytes]:
    raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
    if len(raw) > MAX_HOOK_BYTES: raise ValueError("native hook payload exceeds 1 MiB")
    if not raw.strip(): return {}, raw
    event = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(event, dict): raise ValueError("native hook payload must be a JSON object")
    return event, raw
def _workspace(event: dict) -> str | None:
    extra = event.get("workspacePaths")
    for value in [event.get(key) for key in ("workspaceRoot", "workspace_root", "cwd", "directory", "project_root")] + (extra if isinstance(extra, list) else []):
        if isinstance(value, str) and os.path.isabs(value): return str(Path(value).expanduser().resolve())
    return None
def _in_scope(candidate: str, roots: list) -> bool:
    try: target = os.path.normcase(str(Path(candidate).expanduser().resolve()))
    except TimeoutError: raise
    except (OSError, ValueError): return False
    for root in roots:
        if not isinstance(root, str) or not os.path.isabs(root): continue
        try:
            root_norm = os.path.normcase(str(Path(root).expanduser().resolve()))
            if os.path.splitdrive(target)[0] == os.path.splitdrive(root_norm)[0] and os.path.commonpath([target, root_norm]) == root_norm: return True
        except TimeoutError: raise
        except (OSError, ValueError): continue
    return False
def _passive_stop(event: dict) -> bool:
    cfg = _read_json(cfg_path())
    if cfg is None: return True
    base, consent = cfg_path().parent, cfg.get("consent_config")
    consent = _read_json(Path(consent) if isinstance(consent, str) and os.path.isabs(consent) else base / "mindie-consent.json") or {}
    if consent.get("choice") not in {"contribute", "local-only"}: return True
    community, comm_raw = None, cfg.get("community_config")
    community = _read_json(Path(comm_raw) if isinstance(comm_raw, str) and os.path.isabs(comm_raw) else base / "mindie-community.json") or {}
    roots, candidate = community.get("project_roots"), _workspace(event)
    if community.get("enabled") is not True or not isinstance(roots, list) or not roots or candidate is None: return True
    return not _in_scope(candidate, roots)
def _tool_call(event: dict) -> tuple[str, dict]:
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
def _forged(tool: str, args: dict) -> bool:
    if any(key in args for key in ("_session_id", "_activation", "mindie_activation")): return True
    session = args.get("session_id")
    return "knowledge_" in tool and session is not None and (not isinstance(session, str) or not session.strip() or len(session) > 256)
def _write_pending() -> None:
    target = state_dir() / "pending-update-check.json"; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{target.name}.tmp.", dir=str(target.parent)); temp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps({"schema": "mindie-pending-update/1", "host": "agy", "reason": "hook_handoff", "requested_at": time.time()}, sort_keys=True).encode("utf-8") + b"\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
def _bridge(rest: list[str], raw: bytes | None = None) -> int:
    import bridge
    previous = sys.stdin
    if raw is not None: sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    try: return int(bridge.main(rest))
    finally: sys.stdin = previous
def _reexec(python: str, argv: list[str], raw: bytes | None = None) -> int:
    command = [python, "-I", "-X", "utf8", str(Path(__file__).resolve()), *argv]
    env = dict(os.environ, _MINDIE_AGY_REEXEC="1")
    try: sys.stdin.fileno(); sys.stdout.fileno(); sys.stderr.fileno(); io_kw = {"stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr}
    except TimeoutError: raise
    except (AttributeError, OSError, ValueError): io_kw = {}
    try:
        if raw is not None: return int(subprocess.run(command, input=raw, env=env, timeout=HOOK_TIMEOUT if argv and argv[0] == "hook" else None, creationflags=CREATE_NO_WINDOW).returncode)
        if argv and argv[0] == "hook": return int(subprocess.run(command, **io_kw, env=env, timeout=HOOK_TIMEOUT, creationflags=CREATE_NO_WINDOW).returncode)
        return int(subprocess.run(command, **io_kw, env=env, creationflags=CREATE_NO_WINDOW).returncode)
    except subprocess.TimeoutExpired:
        print(f"MindIE {argv[0]} re-exec exceeded {HOOK_TIMEOUT}s", file=sys.stderr)
        _emit({"decision": "deny"} if argv[:2] == ["hook", "pre-tool-use"] else {}); return 0
    except TimeoutError: raise
    except OSError as exc:
        print(f"MindIE {argv[0]} re-exec failed: {type(exc).__name__}", file=sys.stderr); return 1
def _provision(argv: list[str]) -> int:
    verify_seed_bundle(PLUGIN_ROOT)
    target_cfg = _cli_cfg(argv)
    parent = PLUGIN_ROOT.parents[1]
    provision_root = parent if (parent / "pyproject.toml").is_file() else PLUGIN_ROOT
    try:
        proc = subprocess.run([sys.executable, str(PLUGIN_ROOT / "assets/mindie-harness-seed.pyz"), "--host", "agy", "--plugin-root", str(provision_root), "--config", str(target_cfg)], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SEED_TIMEOUT, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        print("MindIE cold provisioning exceeded its time budget", file=sys.stderr); return 1
    except TimeoutError: raise
    except OSError as exc:
        print(f"MindIE cold provisioning failed: {type(exc).__name__}", file=sys.stderr); return 1
    if proc.returncode != 0:
        print(f"MindIE cold provisioning exited {proc.returncode}", file=sys.stderr); return 1
    lines = (proc.stdout or "").strip().splitlines()
    try: payload = json.loads(lines[-1]) if lines else {}
    except ValueError: payload = {}
    python = _usable_py(payload.get("python")) if isinstance(payload, dict) else None
    if python is None: python = _active_python(target_cfg)
    if python is None:
        print("MindIE cold provisioning reported no interpreter", file=sys.stderr); return 1
    return _reexec(python, argv)
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: mindie_launch.py {hook|mcp|setup|update|doctor|sharing} ...", file=sys.stderr); return 2
    role, rest = argv[0], argv[1:]
    if role == "hook":
        op = rest[0] if rest else ""
        try:
            if op == "pre-invocation": _read_hook(); _emit({}); return 0
            if op == "pre-tool-use":
                tool, args = _tool_call(_read_hook()[0])
                _emit({"decision": "deny", "reason": "forged session identity"} if _forged(tool, args) else {"decision": "allow"}); return 0
            if op == "session-start": _read_hook(); _write_pending(); _emit({}); return 0
            if op == "stop":
                event, raw = _read_hook()
                if event.get("stop_hook_active", event.get("stopHookActive", False)) is not False or _passive_stop(event): _emit({}); return 0
                python = _active_python(_cli_cfg(rest))
                if python is not None and _norm(python) != _norm(sys.executable): return _reexec(python, ["hook", "stop"], raw)
                return _bridge(["stop"], raw)
        except TimeoutError: raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"; print(f"MindIE hook skipped: {detail}", file=sys.stderr)
            _emit({"decision": "deny", "reason": detail} if op == "pre-tool-use" else {}); return 0
        return _bridge(rest)
    python = _active_python(_cli_cfg(argv))
    if python is None and not (role in {"setup", "update", "doctor", "sharing"} and _readonly_cli(role, argv)):
        return _provision(argv)
    if python is not None and _norm(python) != _norm(sys.executable): return _reexec(python, argv)
    if role == "mcp":
        pos = [rest[i] for i in range(len(rest)) if not rest[i].startswith("-") and (i == 0 or rest[i - 1] != "--config")]
        if pos and pos[0] not in {"remote", "tools"}:
            print(f"unsupported MCP surface: {pos[0]}", file=sys.stderr); return 2
        import mcp_server
        return mcp_server.serve(pos[0] if pos else "tools", config=_explicit_cfg(argv))
    if role in {"setup", "update", "doctor", "sharing"}:
        import setup
        return setup.main(argv if role == "setup" else [role, *rest])
    print(f"unknown mindie_launch role: {role}", file=sys.stderr); return 2
if __name__ == "__main__":
    raise SystemExit(main())
