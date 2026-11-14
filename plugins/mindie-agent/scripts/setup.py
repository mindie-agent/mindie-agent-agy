"""MindIE Agent profile provisioner and runtime bootstrapper for Antigravity (`setup.py`).

[Role]
Initializes and updates the MindIE-owned Antigravity adapter profile (`agy.json`,
`agy.engine.json`, `mindie-community.json`, `mindie-consent.json`), bootstrapping
the persistent component virtualenv (`agy-venv`) and LF-normalized source baseline
(`agy-source`) when invoked from a bare Python interpreter.

[Invariants]
- Passive Default: `ensure_default_profile` and `configure_community` write
  MindIE-owned JSON configs atomically via `os.replace()` and never activate a
  session lease or start the background knowledge loop.
- Never writes into `~/.gemini`.

[Timeouts & Locks]
- Local config validation/writes are immediate; first-time `uv sync` runtime
  bootstrap is bounded by `--timeout` (default `BOOTSTRAP_TIMEOUT = 540.0s`).
  Interpreter re-exec uses `timeout=timeout + BOOTSTRAP_CLEANUP_GRACE`.

[Side-Effect Policy]
- Writes `agy.json`, `agy.engine.json`, `mindie-community.json`, and
  `mindie-consent.json` under `config_home()`, and stages `agy-source` /
  `agy-venv` under `data_home()` when bootstrapping.
- `--help` / `-h` and `--dry-run` are read-only: they do not stage source,
  create `agy-venv`, import `mindie_knowledge`, or write config directories.
  Unique abbreviations that argparse accepts (`--dry`, `--dr`, and similar)
  take that same read-only path and never record consent.
"""

from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import run as bounded_run
from paths import (
    admission_path,
    apply_enterprise_transport_env,
    atomic_write_json,
    community_config_path,
    config_path,
    consent_path,
    data_home,
    engine_config_path,
    plugin_root,
    read_json,
    repo_root,
    state_dir,
    which_native,
)
import setup_sharing

BOOTSTRAP_TIMEOUT = 540.0
BOOTSTRAP_CLEANUP_GRACE = 5.0
_TEXT_SUFFIXES = {".py", ".json", ".md", ".toml", ".svg", ".txt", ".yml", ".yaml"}
_BASELINE_ITEMS = (
    "pyproject.toml",
    "product-contract.json",
    "harness-compatibility.json",
    "plugins",
)


def _discover_checkout_source(workspace_root: str | Path | None = None) -> Path | None:
    """Locate a complete `mindie-agent-agy` checkout for source baseline retention."""
    candidates: list[Path] = [HERE.parent.parent.parent, plugin_root().parent.parent]
    if workspace_root is not None:
        ws = Path(workspace_root).expanduser()
        candidates.extend([ws, ws / "mindie-agent-agy"])
    cwd = Path.cwd()
    candidates.extend(
        [
            cwd,
            cwd / "mindie-agent-agy",
            repo_root(),
            Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "mindie-agent-agy-install",
            Path("/tmp/mindie-agent-agy-install"),
            data_home() / "agy-source",
        ]
    )
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except TimeoutError:
            raise
        except OSError:
            continue
        if (resolved / "pyproject.toml").is_file() and (resolved / "product-contract.json").is_file():
            return resolved
    return None


def _copy_lf_clean(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = src.read_bytes()
    if src.suffix in _TEXT_SUFFIXES and b"\r\n" in raw:
        raw = raw.replace(b"\r\n", b"\n")
    dst.write_bytes(raw)


def _payload_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def stage_source_baseline(
    source_dir: str | Path | None = None,
    dest_dir: str | Path | None = None,
    *,
    workspace_root: str | Path | None = None,
) -> Path:
    """Publish an immutable payload generation before its profile can activate."""
    src = (Path(source_dir).resolve() if source_dir is not None
           else _discover_checkout_source(workspace_root=workspace_root))
    base = (data_home() / "agy-source").resolve()
    if src is None:
        return base if (base / "pyproject.toml").is_file() else repo_root()
    data_home().mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".source-stage-", dir=data_home()) as folder:
        staged = Path(folder)
        for item_name in _BASELINE_ITEMS:
            item = src / item_name
            if not item.exists():
                continue
            files = [item] if item.is_file() else sorted(item.rglob("*"))
            for path in files:
                if (path.is_file() and path.suffix != ".pyc"
                        and not any(part in {"__pycache__", ".pytest_cache", ".git", ".venv"}
                                    for part in path.relative_to(src).parts)):
                    _copy_lf_clean(path, staged / path.relative_to(src))
        digest = _payload_digest(staged)
        if dest_dir is not None:
            dst = Path(dest_dir).resolve()
            dst.mkdir(parents=True, exist_ok=True)
            for path in staged.rglob("*"):
                if path.is_file():
                    _copy_lf_clean(path, dst / path.relative_to(staged))
            return dst
        else:
            version = read_json(staged / "plugins/mindie-agent/plugin.json")["version"]
            if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                raise ValueError("plugin payload requires a formal version")
            dst = base / f"{version}-{digest}"
        if dst.is_dir():
            if _payload_digest(dst) != digest:
                raise ValueError("retained plugin payload content changed")
            return dst
        dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.replace(staged, dst)
        except TimeoutError:
            raise
        except OSError:
            if not dst.is_dir() or _payload_digest(dst) != digest:
                raise
            # Concurrent publication of the same content identity is complete.
        return dst


def _norm_python_path(py_exe: str | Path) -> Path:
    """Normalize a Python executable path while preserving virtualenv `bin/python` symlinks."""
    p = Path(py_exe).expanduser()
    return p.parent.resolve() / p.name


def ensure_runtime_python(
    source_root: str | Path | None = None,
    python_exe: str | None = None,
    *,
    config: str | Path | None = None,
    timeout: float = BOOTSTRAP_TIMEOUT,
) -> str:
    """Return an interpreter with MindIE runtime components installed, bootstrapping `agy-venv` if needed."""
    from component_update import compatible_runtime

    def verified(interpreter):
        return compatible_runtime(str(interpreter), source=source_root or _discover_checkout_source() or repo_root(config),
                                  config=config, timeout=timeout)

    if python_exe:
        return verified(_norm_python_path(python_exe))
    if all(
        importlib.util.find_spec(mod) is not None
        for mod in (
            "mindie_knowledge",
            "remote_dev",
            "mindie_diagnostics",
        )
    ):
        return verified(_norm_python_path(sys.executable))

    venv_dir = data_home() / "agy-venv"
    venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_py.is_file():
        probe = bounded_run(
            [
                str(venv_py),
                "-I",
                "-X",
                "utf8",
                "-c",
                "import mindie_knowledge, remote_dev, mindie_diagnostics",
            ],
            "",
            timeout=15.0,
        )
        if probe.returncode == 0:
            return verified(_norm_python_path(venv_py))

    src_root = stage_source_baseline(source_root)
    uv = which_native("uv")
    if uv is None:
        raise RuntimeError("uv is required to bootstrap the MindIE Antigravity runtime")

    env = apply_enterprise_transport_env(dict(os.environ))
    env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)
    proc = bounded_run(
        [
            uv,
            "sync",
            "--project",
            str(src_root),
            "--all-extras",
            "--no-dev",
            "--python",
            "3.13",
        ],
        "",
        timeout=timeout,
        max_output=256 * 1024,
        env=env,
    )
    if proc.returncode != 0 or not venv_py.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()[-1000:]
        raise RuntimeError(f"uv sync failed while bootstrapping agy-venv (exit {proc.returncode}): {detail}")
    return verified(_norm_python_path(venv_py))


def validate_contracts() -> dict:
    root = _discover_checkout_source() or repo_root()
    contract_file = root / "product-contract.json"
    compat_file = root / "harness-compatibility.json"
    contract = read_json(contract_file)
    compat = read_json(compat_file)
    return dict(
        schema="mindie-candidate-check/1",
        status="validated",
        harness=compat.get("harness", "antigravity"),
        native_version=contract["native_version"],
    )


def _is_temp_checkout_path(path: Path) -> bool:
    raw = str(path).replace("\\", "/")
    if "mindie-agent-agy-install" in raw or raw.startswith(("/tmp/", "/private/tmp/")):
        return True
    try:
        return Path(tempfile.gettempdir()).resolve() in path.resolve().parents
    except TimeoutError:
        raise
    except OSError:
        return False


def _discover_gitleaks() -> str | None:
    found = which_native("gitleaks")
    if found:
        return found
    expected_name = "gitleaks.exe" if os.name == "nt" else "gitleaks"
    cache_roots = [
        Path.home() / ".cache" / "mindie-agent" / "gitleaks",
        Path.home() / ".cache" / "mindie-knowledge",
    ]
    if os.name == "nt":
        local_app = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        cache_roots.append(local_app / "mindie-knowledge")
    for cache_dir in cache_roots:
        if cache_dir.is_dir():
            for candidate in sorted(cache_dir.rglob(expected_name)):
                if candidate.is_file() and candidate.name == expected_name:
                    return str(candidate.resolve())
    return None


def ensure_default_profile(
    *,
    config: str | Path | None = None,
    workspace_root: str | Path | None = None,
    domain: str = "vllm-ascend",
    python_exe: str | None = None,
) -> dict:
    apply_enterprise_transport_env()
    interpreter = ensure_runtime_python(python_exe=python_exe, config=config)

    from mindie_knowledge.loop import settings as settings_mod

    cfg_file = config_path(config)
    eng_file = engine_config_path(cfg_file)
    sdir = state_dir(cfg_file)
    adm_file = admission_path(cfg_file)
    comm_file = community_config_path(cfg_file)
    scripts_dir = Path(__file__).resolve().parent
    data_root = data_home() / "domains"
    sdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    resolved_source = stage_source_baseline(workspace_root=workspace_root)
    scripts_dir = (resolved_source / "plugins/mindie-agent/scripts").resolve()

    validation = validate_contracts()
    gitleaks = _discover_gitleaks()

    cfile = consent_path(cfg_file)
    engine_payload = dict(read_json(eng_file) if eng_file.is_file() else {},
        schema="mindie-engine-config/1",
        harness="agy",
        domain=domain,
        root=str(data_root),
        state_dir=str(sdir / "engine"),
        admission_path=str(adm_file),
        community_config=str(comm_file),
        settings_path=str(comm_file),
        transcript_adapter=str(scripts_dir / "agy_transcript.py"),
        summary_command=[interpreter, "-I", "-X", "utf8", str(scripts_dir / "organizer.py")],
        redactor_executable=gitleaks,
        product_validation=validation,
    )
    atomic_write_json(eng_file, engine_payload)

    adapter_payload = dict(read_json(cfg_file) if cfg_file.is_file() else {},
        schema="mindie-adapter-config/1",
        harness="antigravity",
        python=interpreter,
        runtime_scripts=str(scripts_dir),
        source_root=str(resolved_source),
        engine_config=str(eng_file),
        state_dir=str(sdir),
        admission_path=str(adm_file),
        domain=domain,
        product_validation=validation,
    )
    atomic_write_json(cfg_file, adapter_payload)

    if not comm_file.is_file():
        roots = [str(Path(workspace_root).resolve())] if workspace_root else []
        with settings_mod.CommunityWriteContext(comm_file) as ctx:
            ctx.configure(
                comm_file,
                dict(
                    enabled=False,
                    repository="mindie-agent/knowledge-vllm-ascend",
                    project_roots=roots,
                    branch="main",
                    idle_seconds=300,
                    consent_config=str(cfile.resolve()),
                ),
            )

    return adapter_payload


def sharing_status(config: str | Path | None = None) -> dict:
    """Return current community sharing and fault reporting status using stdlib only."""
    return setup_sharing.sharing_status(config, read_json_fn=read_json)


def configure_community(
    *,
    config: str | Path | None = None,
    enabled: bool,
    fork: str | None = None,
    account: str | None = None,
    project_root: str | Path | None = None,
    remote_project_root: str | None = None,
    visibility: str = "public",
    repository: str = "mindie-agent/knowledge-vllm-ascend",
) -> dict:
    return setup_sharing.configure_community(
        ensure_profile_fn=ensure_default_profile,
        config=config,
        enabled=enabled,
        fork=fork,
        account=account,
        project_root=project_root,
        remote_project_root=remote_project_root,
        visibility=visibility,
        repository=repository,
    )


def _requests_help(argv: list[str]) -> bool:
    return any(arg in {"-h", "--help"} or arg.startswith("--help=") for arg in argv)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure MindIE Agent for Antigravity",
        epilog=(
            "Read-only: --help and --dry-run print usage or a plan and exit. "
            "Unique abbreviations argparse accepts, such as --dry, are the same "
            "read-only plan. They do not bootstrap agy-venv, record consent, "
            "or write config directories."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the setup plan and exit without bootstrapping a runtime or writing directories",
    )
    parser.add_argument("--timeout", type=float, default=BOOTSTRAP_TIMEOUT, help="runtime bootstrap timeout in seconds")
    sub = parser.add_subparsers(dest="cmd", required=False)

    init_p = sub.add_parser("init", help="write the passive default adapter profile")
    init_p.add_argument("--config", default=None)
    init_p.add_argument("--workspace-root", default=None)
    init_p.add_argument("--domain", default="vllm-ascend")
    # SUPPRESS so a parent flag before the subcommand is kept. store_true's
    # default False would otherwise clear `--dry-run init` and `--dry sharing`.
    init_p.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print the init plan and exit without bootstrapping or writing files",
    )

    share_p = sub.add_parser("sharing", help="record or inspect the community sharing choice")
    share_p.add_argument("--config", default=None)
    mode = share_p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--status", action="store_true", help="print read-only sharing status JSON")
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    share_p.add_argument("--fork", default=None)
    share_p.add_argument("--account", default=None)
    share_p.add_argument("--project-root", default=None)
    share_p.add_argument("--remote-project-root", default=None)
    share_p.add_argument("--visibility", default="public")
    share_p.add_argument("--repository", default="mindie-agent/knowledge-vllm-ascend")
    share_p.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print the sharing plan and exit without recording consent",
    )
    return parser


def _emit_json(payload: dict) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _dry_run_payload(args: argparse.Namespace) -> dict:
    """Describe what a real invocation would touch. Performs no writes."""
    cfg = config_path(getattr(args, "config", None))
    knowledge_present = importlib.util.find_spec("mindie_knowledge") is not None
    venv_name = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    cmd = args.cmd or "init"
    payload = dict(
        schema="mindie-agy-setup-plan/1",
        status="dry-run",
        cmd=cmd,
        harness="antigravity",
        python=sys.version.split()[0],
        uv=which_native("uv"),
        mindie_knowledge_importable=knowledge_present,
        would_bootstrap_runtime=not knowledge_present,
        writes=[],
        paths=dict(
            config_file=str(cfg),
            engine_config=str(cfg.with_name(cfg.stem + ".engine.json")),
            community_config=str(cfg.parent / "mindie-community.json"),
            consent_config=str(cfg.parent / "mindie-consent.json"),
            data_home=str(data_home()),
            source_baseline=str(data_home() / "agy-source"),
            venv_python=str(data_home() / "agy-venv" / venv_name),
        ),
    )
    if cmd == "init":
        payload["domain"] = getattr(args, "domain", "vllm-ascend")
        payload["workspace_root"] = getattr(args, "workspace_root", None)
        payload["contracts"] = validate_contracts()
    else:
        payload["sharing"] = dict(
            status=bool(getattr(args, "status", False)),
            enable=bool(getattr(args, "enable", False)),
            disable=bool(getattr(args, "disable", False)),
            fork=getattr(args, "fork", None),
            account=getattr(args, "account", None),
            project_root=getattr(args, "project_root", None),
            remote_project_root=getattr(args, "remote_project_root", None),
            visibility=getattr(args, "visibility", "public"),
            repository=getattr(args, "repository", "mindie-agent/knowledge-vllm-ascend"),
            applies_changes=False,
        )
    return payload


def _reexec_stdio(stream):
    try:
        if os.name == "nt":
            import msvcrt

            if msvcrt.get_osfhandle(stream.fileno()) in (-1, -2, 0):
                return subprocess.DEVNULL
        else:
            stream.fileno()
        return stream
    except Exception:
        return subprocess.DEVNULL


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        raw_argv = ["init"]
    if _requests_help(raw_argv):
        _build_parser().parse_args(raw_argv)
        return 0

    # Parse before bootstrap so usage errors cannot stage agy-source or agy-venv.
    # Unique --dry-run prefixes are read-only. Subparser SUPPRESS keeps parent flags.
    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    if args.cmd == "sharing" and not (args.status or args.enable or args.disable):
        parser.error("sharing requires one of --status, --enable, or --disable")
    if args.dry_run:
        _emit_json(_dry_run_payload(args))
        return 0
    if args.cmd == "sharing" and args.status:
        _emit_json(sharing_status(args.config))
        return 0

    apply_enterprise_transport_env()
    if (
        importlib.util.find_spec("mindie_knowledge") is None
        and os.environ.get("_MINDIE_AGY_SETUP_BOOTSTRAPPED") != "1"
    ):
        source = _discover_checkout_source() or repo_root(args.config)
        target_py = ensure_runtime_python(source, config=args.config, timeout=args.timeout)
        staged_src = stage_source_baseline(source)
        env = apply_enterprise_transport_env(dict(os.environ))
        env["_MINDIE_AGY_SETUP_BOOTSTRAPPED"] = "1"
        env["MINDIE_AGY_SOURCE_ROOT"] = str(staged_src)
        run_kwargs: dict = {"env": env, "timeout": args.timeout + BOOTSTRAP_CLEANUP_GRACE}
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            run_kwargs["stdin"] = _reexec_stdio(sys.stdin)
            run_kwargs["stdout"] = _reexec_stdio(sys.stdout)
            run_kwargs["stderr"] = _reexec_stdio(sys.stderr)
        try:
            proc = subprocess.run([target_py, "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_argv], **run_kwargs)
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            print(
                f"MindIE setup reexec exceeded timeout={run_kwargs['timeout']}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        return int(proc.returncode)

    if args.cmd == "init":
        result = ensure_default_profile(
            config=args.config,
            workspace_root=args.workspace_root,
            domain=args.domain,
        )
    elif args.cmd == "sharing":
        result = configure_community(
            config=args.config,
            enabled=bool(args.enable),
            fork=args.fork,
            account=args.account,
            project_root=args.project_root,
            remote_project_root=args.remote_project_root,
            visibility=args.visibility,
            repository=args.repository,
        )
    else:
        parser.error("a command is required: init | sharing")
    _emit_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
