"""Thin Antigravity profile, sharing, doctor, update, and release-metadata CLI.
[Role] Initializes `agy.json` and 9-domain knowledge routes via `ensure_knowledge_binding`,
persists sharing consent, delegates `doctor`/`update`, and validates compatibility ASTs.
[Invariants] `--help`/`-h` and `--dry-run`/`--dry` are read-only; `init` holds `.agy.json.update.lock`
once (`already_locked=True`), preserves generation/interpreter coherence, and fails closed
unless `status == "configured"`; `release-metadata` resolves all `code_paths`/`test_ids` via `ast`.
[Timeouts & Locks] `.agy.json.update.lock` waits `<= 5.0s`; runtime prep uses `--timeout` (`540s`).
[Side-Effect Policy] Only `init`, `sharing`, and `update` write, and only harness-owned configs.
"""
from __future__ import annotations
import ast, importlib.util, json, os, re, shutil, site, sys
from pathlib import Path
SCRIPTS = Path(__file__).resolve().parent
PLUGIN_ROOT, REPOSITORY, SHA_RE = SCRIPTS.parent, "mindie-agent/mindie-agent-agy", re.compile(r"[0-9a-f]{40}\Z")
COMMANDS = {"init", "sharing", "doctor", "update", "release-metadata"}
SPECS: dict[str, dict[str, bool | str]] = {
    "init": {"--config": False, "--python": False, "--timeout": False, "--workspace-root": False, "--domain": False, "--dry-run": True},
    "sharing": {"--config": False, "--status": True, "--enable": True, "--local-only": True, "--disable": True, "--project-root": "list", "--repository": False, "--fork": False, "--account": False, "--cwd": False, "--dry-run": True},
    "doctor": {"--config": False, "--probe-remote": True, "--cwd": False, "--timeout": False, "--dry-run": True},
    "update": {"--config": False, "--force": True, "--offline": True, "--channel-base-url": False, "--background": True, "--dry-run": True},
    "release-metadata": {"--config": False, "--source": False, "--tests": False, "--revision": False, "--dry-run": True},
}
def _read_json(path: str | Path) -> dict | None:
    try: raw = Path(path).read_bytes() if Path(path).is_file() else None
    except TimeoutError: raise
    except OSError: return None
    try: doc = json.loads(raw.decode("utf-8-sig")) if raw is not None else None
    except (UnicodeDecodeError, ValueError): return None
    return doc if isinstance(doc, dict) else None
def _source_root(plugin_root: Path) -> Path:
    return next((c for c in (Path(plugin_root).parents[1], Path(plugin_root)) if (c / "harness-compatibility.json").is_file()), Path(plugin_root))
def _ensure_local_site(root: Path) -> None:
    if importlib.util.find_spec("mindie_harness") is not None: return
    for path in (root / ".venv/Lib/site-packages", *sorted((root / ".venv/lib").glob("python*/site-packages"))):
        if path.is_dir(): site.addsitedir(str(path)); break
def _profile(config: str | None = None):
    _ensure_local_site(_source_root(PLUGIN_ROOT))
    from mindie_harness.paths import ProfileRef
    return ProfileRef.resolve("agy", plugin_root=PLUGIN_ROOT, config_path=config or os.environ.get("MINDIE_AGENT_CONFIG"))
def _emit(payload: dict) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8")); sys.stdout.buffer.flush()
def _version(root: Path = PLUGIN_ROOT) -> str:
    for path in (root / "plugin.json", root / "plugins/mindie-agent/plugin.json"):
        if doc := _read_json(path): return str(doc.get("version") or "0.0.0")
    return "0.0.0"
def _usable_py(raw: object) -> Path | None:
    if not isinstance(raw, (str, Path)) or not str(raw).strip() or "\\microsoft\\windowsapps\\" in str(raw).replace("/", "\\").lower(): return None
    cand = Path(raw).expanduser()
    try: st = cand.stat()
    except TimeoutError: raise
    except OSError: return None
    return (cand.parent.resolve() / cand.name) if cand.is_file() and (os.name != "nt" or st.st_size > 0) else None
def _generation_py(prior: dict) -> Path | None:
    rel = prior.get("component_release")
    if isinstance(rel, str) and rel.strip():
        base = Path(rel).expanduser()
        doc = _read_json(base / "runtime.json" if base.is_dir() else base) or {}
        if isinstance(doc.get("validation"), dict) and doc["validation"].get("status") == "validated" and (py := _usable_py(doc.get("python"))): return py
    return _usable_py(prior.get("python"))
def _runtime_python(profile, timeout: float) -> Path:
    if importlib.util.find_spec("mindie_harness") is not None and (py := _usable_py(sys.executable)): return py
    from mindie_harness.api import prepare_runtime
    prepared = prepare_runtime(profile, deadline=float(timeout))
    if prepared.status not in {"validated", "reused"}: raise RuntimeError(f"runtime preparation was {prepared.status}")
    return Path(prepared.python_path)
def _init(profile, flags: dict) -> dict:
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.locks import FileLock
    from mindie_harness.runtime.knowledge_binding import ensure_knowledge_binding
    plugin_root, version = Path(profile.plugin_root), _version(Path(profile.plugin_root))
    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    lock.acquire()
    try:
        prior = dict(_read_json(profile.config_path) or {})
        gen_py = _generation_py(prior)
        if flags.get("--python") is not None:
            python = _usable_py(flags["--python"])
            if python is None: raise ValueError("invalid --python interpreter")
            if gen_py is not None and os.path.normcase(str(python)) != os.path.normcase(str(gen_py)):
                prior.pop("component_release", None); prior.pop("product_validation", None)
        else:
            python = gen_py or _runtime_python(profile, float(flags.get("--timeout", 540.0)))
        res = ensure_knowledge_binding(profile, plugin_root=plugin_root, python_executable=python, install_redactor=False, retire_previous=True, already_locked=True)
        if res.get("status") != "configured" or not isinstance(res.get("binding_fields"), dict):
            raise RuntimeError(f"knowledge binding {res.get('status')}: {res.get('reason') or 'unavailable'}")
        fields, domain = dict(res["binding_fields"]), str(flags.get("--domain") or prior.get("domain") or "vllm-ascend")
        validation = dict(prior.get("product_validation")) if isinstance(prior.get("product_validation"), dict) else {}
        validation.setdefault("schema", "mindie-candidate-check/1"); validation.setdefault("status", "validated")
        validation.update(harness="antigravity", native_version=version, python=str(python))
        adapter = {**prior, **fields, "schema": str(prior.get("schema") or "mindie-adapter-config/1"), "harness": "antigravity",
                   "python": str(python), "runtime_scripts": str(SCRIPTS), "source_root": str(plugin_root), "domain": domain,
                   "state_dir": str(profile.state_dir), "product_validation": validation}
        atomic_write_json(profile.config_path, adapter)
    finally: lock.release()
    return {"status": "configured", "version": version, "config": str(profile.config_path), "engine_config": fields["engine_config"],
            "domain_configs": fields["domain_configs"], "python": str(python),
            "transcript_adapter": str(res.get("transcript_adapter") or (SCRIPTS / "agy_transcript.py")), "domain": domain}
def _sharing(profile, flags: dict) -> dict:
    from mindie_harness.sharing import configure_sharing, sharing_status
    modes = [key for key in ("--enable", "--local-only", "--disable") if flags.get(key)]
    if len(modes) > 1: raise ValueError(f"conflicting sharing flags: {', '.join(modes)}")
    if flags.get("--status") or not modes: return sharing_status(profile, cwd=flags.get("--cwd"))
    choice = "contribute" if flags.get("--enable") else "local-only" if flags.get("--local-only") else "disabled"
    return configure_sharing(profile, choice=choice, project_roots=list(flags.get("--project-root") or []), repository=flags.get("--repository"), fork=flags.get("--fork"), account=flags.get("--account"))
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
        node = next((c for c in ast.iter_child_nodes(node) if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and c.name == name), None)
        if node is None: return False
    return True
def _release(root: Path, flags: dict) -> dict:
    source_root = Path(flags["--source"]).resolve() if flags.get("--source") else root
    _ensure_local_site(source_root)
    from mindie_knowledge.releases import validate_compatibility
    tests_arg, revision = flags.get("--tests"), flags.get("--revision")
    tests_root = (Path(tests_arg) if Path(tests_arg).is_absolute() else source_root / tests_arg).resolve() if tests_arg else None
    if revision is not None and not SHA_RE.fullmatch(revision): raise ValueError("revision must be a 40-character lowercase SHA")
    compatibility = _read_json(source_root / "harness-compatibility.json") or {}
    validate_compatibility(compatibility, harness_name="antigravity")
    for entry in compatibility["interface_registry"].values():
        if any(not _has(path, source_root) for path in entry["code_paths"]): raise ValueError("unresolved code_paths")
        if any(not (_has(test, source_root) or (tests_root is not None and _has(test.removeprefix("tests/"), tests_root))) for test in entry["test_ids"]):
            raise ValueError("unresolved test_ids")
    return {"schema": "mindie-plugin-release/1", "version": _version(source_root), "source": {"repository": REPOSITORY, "sha": revision or "0" * 40}, "compatibility": compatibility}
def _plan(profile, cmd: str, flags: dict) -> dict:
    cfg, roots = Path(profile.config_path), list(flags.get("--project-root") or [])
    plan = {"schema": "mindie-agy-setup-plan/1", "status": "dry-run", "cmd": cmd, "harness": "antigravity", "writes": [],
            "paths": {"config_file": str(cfg), "engine_dir": str(cfg.parent / "engines" / profile.host), "community_config": str(cfg.parent / "mindie-community.json"),
                      "consent_config": str(profile.consent_path), "data_home": str(profile.data_dir), "state_dir": str(profile.state_dir)}}
    if cmd == "init":
        plan.update({"domain": flags.get("--domain", "vllm-ascend"), "workspace_root": flags.get("--workspace-root"),
                     "runtime_python": str(sys.executable) if importlib.util.find_spec("mindie_harness") is not None else None,
                     "redactor_from_env": os.environ.get("MINDIE_REDACTOR_EXECUTABLE"), "gitleaks": shutil.which("gitleaks"), "version": _version(Path(profile.plugin_root))})
    else:
        plan["sharing"] = {"status": bool(flags.get("--status")), "enable": bool(flags.get("--enable")), "local_only": bool(flags.get("--local-only")),
                           "disable": bool(flags.get("--disable")), "project_roots": roots, "project_root": roots[-1] if roots else None,
                           "repository": flags.get("--repository"), "fork": flags.get("--fork"), "account": flags.get("--account"), "applies_changes": False}
    return plan
def _store_flag(values: dict, spec: dict[str, bool | str], key: str, val: str) -> None:
    if spec[key] == "list": values.setdefault(key, []).append(val)
    else: values[key] = val
def _flags(argv: list[str], spec: dict[str, bool | str]) -> dict:
    values: dict = {}; index = 0
    while index < len(argv):
        token = "--dry-run" if argv[index].startswith("--dry") and "--dry-run".startswith(argv[index]) else argv[index]
        if token == "--": index += 1; continue
        if "=" in token and token.startswith("--"):
            key, val = token.split("=", 1)
            if key in spec and spec[key] is not True: _store_flag(values, spec, key, val); index += 1; continue
        if token in spec:
            if spec[token] is True: values[token] = True; index += 1
            elif index + 1 < len(argv) and not argv[index + 1].startswith("-"): _store_flag(values, spec, token, argv[index + 1]); index += 2
            else: raise SystemExit(f"{token} requires a value")
        elif token.startswith("-"): raise SystemExit(f"unknown option: {token}")
        else: values.setdefault("_args", []).append(token); index += 1
    return values
def _usage() -> None: print("usage: setup.py [init|sharing|doctor|update|release-metadata] [--dry-run] [options]", file=sys.stderr)
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv) or ["init"]
    if any(token in {"-h", "--help"} or token.startswith("--help=") for token in argv): _usage(); return 0
    pos = next((idx for idx, tok in enumerate(argv) if tok in COMMANDS), None)
    cmd, rest = (argv[pos], argv[:pos] + argv[pos + 1:]) if pos is not None else ("init", argv)
    try: flags = _flags(rest, SPECS[cmd])
    except SystemExit as exc: print(str(exc), file=sys.stderr); return 2
    try:
        if cmd == "release-metadata" and not flags.get("--dry-run"): _emit(_release(_source_root(PLUGIN_ROOT), flags)); return 0
        profile = _profile(flags.get("--config"))
        if flags.get("--dry-run"): _emit(_plan(profile, cmd, flags)); return 0
        if cmd == "init": _emit(_init(profile, flags))
        elif cmd == "sharing": _emit(_sharing(profile, flags))
        elif cmd == "doctor": return _doctor(profile, flags)
        else: _emit(_update(profile, flags))
    except TimeoutError: raise
    except Exception as exc: print(f"setup {cmd} failed: {type(exc).__name__}: {exc}", file=sys.stderr); return 1
    return 0
if __name__ == "__main__": raise SystemExit(main())
