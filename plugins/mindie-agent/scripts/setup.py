"""Thin Antigravity profile, sharing, doctor, update, and release-metadata CLI.
[Role]
Initializes `agy.json` / `agy.engine.json`, persists sharing consent through
`mindie_harness.sharing`, delegates `doctor` and `update` to `mindie_harness`,
and validates `harness-compatibility.json` code paths and test IDs.
[Invariants]
- `--help` / `-h` and `--dry-run` (including unique prefixes such as `--dry`)
  are read-only: zero directories, runtimes, consent records, or downloads.
- `init` binds `transcript_adapter` -> `agy_transcript.py`, `harness="antigravity"`,
  an absolute runtime Python, `runtime_scripts`, `source_root`, and
  `product_validation`; it never mutates the host Antigravity data directory.
- `release-metadata` resolves every `code_paths` symbol and `test_ids`
  function through `ast`.
[Timeouts & Locks]
- Runtime preparation uses `--timeout` (default `540s`); doctor/update
  deadlines are owned by `mindie_harness`; config writes are atomic.
[Side-Effect Policy]
- Only `init`, `sharing`, and `update` write, and only harness-owned configs.
"""
from __future__ import annotations
import ast, importlib.util, json, os, re, shutil, sys
from pathlib import Path
SCRIPTS = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS.parent
REPOSITORY = "mindie-agent/mindie-agent-agy"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
COMMANDS = {"init", "sharing", "doctor", "update", "release-metadata"}
SPECS = {
    "init": {"--config": False, "--timeout": False, "--workspace-root": False, "--domain": False, "--dry-run": True},
    "sharing": {"--config": False, "--status": True, "--enable": True, "--local-only": True, "--disable": True, "--project-root": False, "--repository": False, "--fork": False, "--account": False, "--cwd": False, "--dry-run": True},
    "doctor": {"--config": False, "--probe-remote": True, "--cwd": False, "--timeout": False, "--dry-run": True}, "update": {"--config": False, "--force": True, "--offline": True, "--channel-base-url": False, "--background": True, "--dry-run": True},
    "release-metadata": {"--config": False, "--revision": False, "--dry-run": True},
}
def _read_json(path: str | Path) -> dict | None:
    try: raw = Path(path).read_bytes() if Path(path).is_file() else None
    except TimeoutError: raise
    except OSError: return None
    try: doc = json.loads(raw.decode("utf-8-sig")) if raw is not None else None
    except (UnicodeDecodeError, ValueError): return None
    return doc if isinstance(doc, dict) else None
def _profile(config: str | None = None):
    from mindie_harness.paths import ProfileRef
    return ProfileRef.resolve("agy", plugin_root=PLUGIN_ROOT, config_path=config or os.environ.get("MINDIE_AGENT_CONFIG"))
def _emit(payload: dict) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8")); sys.stdout.buffer.flush()
def _version(root: Path = PLUGIN_ROOT) -> str:
    for path in (root / "plugin.json", root / "plugins/mindie-agent/plugin.json"):
        if doc := _read_json(path): return str(doc.get("version") or "0.0.0")
    return "0.0.0"
def _source_root(plugin_root: Path) -> Path:
    for candidate in (Path(plugin_root).parents[1], Path(plugin_root)):
        if (candidate / "harness-compatibility.json").is_file(): return candidate
    return Path(plugin_root)
def _validation(version: str) -> dict:
    return {"schema": "mindie-candidate-check/1", "status": "validated", "harness": "antigravity", "native_version": version}
def _runtime_python(profile, timeout: float) -> Path:
    if importlib.util.find_spec("mindie_harness") is not None: return Path(sys.executable)
    from mindie_harness.api import prepare_runtime
    prepared = prepare_runtime(profile, deadline=float(timeout))
    if prepared.status not in {"validated", "reused"}: raise RuntimeError(f"runtime preparation was {prepared.status}")
    return Path(prepared.python_path)
def _redactor(profile) -> str:
    if explicit := os.environ.get("MINDIE_REDACTOR_EXECUTABLE"): return str(Path(explicit).expanduser().absolute())
    try:
        from mindie_knowledge.loop.transcript_redaction import install_scanner
        return str(Path(install_scanner()).absolute())
    except TimeoutError: raise
    except Exception: pass
    if found := shutil.which("gitleaks"): return str(Path(found).absolute())
    return str((Path(profile.data_dir) / "bin" / ("gitleaks.exe" if os.name == "nt" else "gitleaks")).absolute())
def _init(profile, flags: dict) -> dict:
    from mindie_harness.atomic import atomic_write_json
    python = _runtime_python(profile, float(flags.get("--timeout", 540.0)))
    version, validation = _version(Path(profile.plugin_root)), _validation(_version(Path(profile.plugin_root)))
    engine_path = Path(profile.config_path).with_name(Path(profile.config_path).stem + ".engine.json")
    community = Path(profile.config_path).parent / "mindie-community.json"
    engine = {
        "schema": "mindie-engine-config/1", "harness": "agy", "domain": flags.get("--domain", "vllm-ascend"),
        "root": str(Path(os.environ.get("MINDIE_KNOWLEDGE_ROOT") or (Path(profile.data_dir) / "knowledge"))),
        "state_dir": str(Path(profile.state_dir) / "engine"), "admission_path": str(profile.admission_db),
        "community_config": str(community), "settings_path": str(community),
        "transcript_adapter": str(SCRIPTS / "agy_transcript.py"), "redactor_executable": _redactor(profile),
        "product_validation": validation,
    }
    adapter = dict(_read_json(profile.config_path) or {})
    adapter.update({"schema": "mindie-adapter-config/1", "harness": "antigravity", "python": str(python), "engine_config": str(engine_path),
                    "runtime_scripts": str(SCRIPTS), "source_root": str(Path(profile.plugin_root)), "domain": engine["domain"],
                    "state_dir": str(profile.state_dir), "admission_path": str(profile.admission_db), "product_validation": validation})
    atomic_write_json(engine_path, engine); atomic_write_json(profile.config_path, adapter)
    return {"status": "configured", "version": version, "config": str(profile.config_path), "engine_config": str(engine_path), "python": str(python), "transcript_adapter": engine["transcript_adapter"], "domain": engine["domain"]}
def _sharing(profile, flags: dict) -> dict:
    from mindie_harness.sharing import configure_sharing, sharing_status
    modes = [key for key in ("--enable", "--local-only", "--disable") if flags.get(key)]
    if len(modes) > 1: raise ValueError(f"conflicting sharing flags: {', '.join(modes)}")
    if flags.get("--status") or not modes:
        return sharing_status(profile, cwd=flags.get("--cwd"))
    choice = "contribute" if flags.get("--enable") else "local-only" if flags.get("--local-only") else "disabled"
    return configure_sharing(profile, choice=choice, project_roots=[flags["--project-root"]] if flags.get("--project-root") else [], repository=flags.get("--repository"), fork=flags.get("--fork"), account=flags.get("--account"))
def _doctor(profile, flags: dict) -> int:
    from mindie_harness.api import run_host
    forwarded = ["--host", "agy", "--plugin-root", str(profile.plugin_root), "--config", str(profile.config_path), "--data-dir", str(profile.data_dir), "doctor"]
    if flags.get("--probe-remote"): forwarded.append("--probe-remote")
    if flags.get("--cwd"): forwarded.extend(["--cwd", flags["--cwd"]])
    if flags.get("--timeout") is not None: forwarded.extend(["--timeout", str(flags["--timeout"])])
    return run_host(forwarded)
def _update(profile, flags: dict) -> dict:
    from mindie_harness.runtime.wheelhouse import update_runtime
    return update_runtime(profile, force=bool(flags.get("--force")), offline=bool(flags.get("--offline")), channel_base_url=flags.get("--channel-base-url"), background=bool(flags.get("--background")))
def _has(relative: str, root: Path) -> bool:
    file_part, _, symbol = relative.partition("::")
    if not symbol: file_part, _, symbol = file_part.partition(":")
    path = root / file_part
    if not path.is_file(): return False
    if not symbol: return True
    try: node: object = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except TimeoutError: raise
    except (OSError, SyntaxError): return False
    for name in symbol.split("."):
        node = next((child for child in ast.iter_child_nodes(node) if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name), None)
        if node is None: return False
    return True
def _release(root: Path, revision: str | None) -> dict:
    from mindie_knowledge.releases import validate_compatibility
    if revision is not None and not SHA_RE.fullmatch(revision): raise ValueError("revision must be a 40-character lowercase SHA")
    compatibility = _read_json(root / "harness-compatibility.json") or {}
    validate_compatibility(compatibility, harness_name="antigravity")
    for entry in compatibility["interface_registry"].values():
        if any(not _has(path, root) for path in entry["code_paths"]): raise ValueError("unresolved code_paths")
        if any(not _has(test, root) for test in entry["test_ids"]): raise ValueError("unresolved test_ids")
    return {"schema": "mindie-plugin-release/1", "version": _version(root), "source": {"repository": REPOSITORY, "sha": revision or "0" * 40}, "compatibility": compatibility}
def _plan(profile, cmd: str, flags: dict) -> dict:
    cfg = Path(profile.config_path)
    plan = {"schema": "mindie-agy-setup-plan/1", "status": "dry-run", "cmd": cmd, "harness": "antigravity", "writes": [],
            "paths": {"config_file": str(cfg), "engine_config": str(cfg.with_name(cfg.stem + ".engine.json")), "community_config": str(cfg.parent / "mindie-community.json"),
                      "consent_config": str(profile.consent_path), "data_home": str(profile.data_dir), "state_dir": str(profile.state_dir)}}
    if cmd == "init":
        plan.update({"domain": flags.get("--domain", "vllm-ascend"), "workspace_root": flags.get("--workspace-root"),
                     "runtime_python": str(sys.executable) if importlib.util.find_spec("mindie_harness") is not None else None,
                     "redactor_from_env": os.environ.get("MINDIE_REDACTOR_EXECUTABLE"), "gitleaks": shutil.which("gitleaks"), "version": _version(Path(profile.plugin_root))})
    else:
        plan["sharing"] = {"status": bool(flags.get("--status")), "enable": bool(flags.get("--enable")), "local_only": bool(flags.get("--local-only")),
                           "disable": bool(flags.get("--disable")), "project_root": flags.get("--project-root"), "repository": flags.get("--repository"),
                           "fork": flags.get("--fork"), "account": flags.get("--account"), "applies_changes": False}
    return plan
def _flags(argv: list[str], spec: dict[str, bool]) -> dict:
    values, index = {}, 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--dry") and "--dry-run".startswith(token): token = "--dry-run"
        if token == "--": index += 1; continue
        if "=" in token and token.startswith("--"):
            key, value = token.split("=", 1)
            if key in spec and not spec[key]: values[key] = value; index += 1; continue
        if token in spec:
            if spec[token]: values[token] = True; index += 1
            elif index + 1 < len(argv) and not argv[index + 1].startswith("-"): values[token] = argv[index + 1]; index += 2
            else: raise SystemExit(f"{token} requires a value")
        elif token.startswith("-"): raise SystemExit(f"unknown option: {token}")
        else: values.setdefault("_args", []).append(token); index += 1
    return values
def _usage() -> None: print("usage: setup.py [init|sharing|doctor|update|release-metadata] [--dry-run] [options]", file=sys.stderr)
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv: argv = ["init"]
    if any(token in {"-h", "--help"} or token.startswith("--help=") for token in argv): _usage(); return 0
    position = next((index for index, token in enumerate(argv) if token in COMMANDS), None)
    cmd = argv[position] if position is not None else "init"
    rest = argv[:position] + argv[position + 1:] if position is not None else argv
    try: flags = _flags(rest, SPECS[cmd])
    except SystemExit as exc: print(str(exc), file=sys.stderr); return 2
    profile = _profile(flags.get("--config"))
    if flags.get("--dry-run"): _emit(_plan(profile, cmd, flags)); return 0
    if cmd == "init": _emit(_init(profile, flags))
    elif cmd == "sharing": _emit(_sharing(profile, flags))
    elif cmd == "doctor": return _doctor(profile, flags)
    elif cmd == "update": _emit(_update(profile, flags))
    else: _emit(_release(_source_root(Path(profile.plugin_root)), flags.get("--revision")))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
