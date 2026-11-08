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
  bootstrap is bounded by `BOOTSTRAP_TIMEOUT = 540.0s`. Interpreter re-exec
  uses `timeout=BOOTSTRAP_TIMEOUT + BOOTSTRAP_CLEANUP_GRACE`.

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
    diagnostics_config_path,
    engine_config_path,
    plugin_root,
    read_json,
    repo_root,
    state_dir,
)

BOOTSTRAP_TIMEOUT = 540.0
BOOTSTRAP_CLEANUP_GRACE = 5.0
_REPO_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
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
                "-c",
                "import mindie_knowledge, remote_dev, mindie_diagnostics",
            ],
            "",
            timeout=15.0,
        )
        if probe.returncode == 0:
            return verified(_norm_python_path(venv_py))

    src_root = stage_source_baseline(source_root)
    uv = shutil.which("uv")
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
    except OSError:
        return False


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
    gitleaks = shutil.which("gitleaks")
    if not gitleaks:
        cache_dir = Path.home() / ".cache" / "mindie-agent" / "gitleaks"
        if cache_dir.is_dir():
            for candidate in sorted(cache_dir.rglob("gitleaks*"), reverse=True):
                if candidate.is_file() and candidate.name in {"gitleaks", "gitleaks.exe"}:
                    gitleaks = str(candidate.resolve())
                    break

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
        summary_command=[interpreter, "-I", str(scripts_dir / "organizer.py")],
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


def _read_optional_json(path: Path) -> tuple[str, dict]:
    if not path.exists():
        return "missing", {}
    try:
        return "ok", read_json(path)
    except TimeoutError:
        raise
    except FileNotFoundError:
        return "missing", {}
    except ValueError:
        return "corrupt", {}
    except OSError:
        return "unreadable", {}


def sharing_status(config: str | Path | None = None) -> dict:
    """Return current community sharing and fault reporting status using stdlib only."""
    cfg_file = config_path(config)
    cfile = consent_path(cfg_file)
    comm_file = community_config_path(cfg_file)
    diag_file = diagnostics_config_path(cfg_file)

    consent_state, consent_data = _read_optional_json(cfile)
    community_state, comm_data = _read_optional_json(comm_file)
    diagnostics_state, diag_data = _read_optional_json(diag_file)

    choice = consent_data.get("choice") if consent_state == "ok" else None
    if consent_state == "ok" and choice not in (None, "contribute", "read-only", "later", "disabled"):
        consent_state = "corrupt"
        choice = None
    choice_revision = consent_data.get("choice_revision") if consent_state == "ok" else None
    repair_required = any(
        state not in {"ok", "missing"}
        for state in (consent_state, community_state, diagnostics_state)
    )
    sharing_enabled = (
        community_state == "ok"
        and bool(comm_data.get("enabled", False))
        and choice == "contribute"
    )
    reporting_sub = diag_data.get("reporting") if isinstance(diag_data.get("reporting"), dict) else {}
    reporting_enabled = diagnostics_state == "ok" and bool(
        reporting_sub.get("enabled", False)
        or diag_data.get("decision") == "enabled"
        or diag_data.get("enabled") is True
    )
    needs_onboarding_prompt = (not repair_required) and (choice not in {"contribute", "disabled"})
    return {
        "schema": "mindie-sharing-status/1",
        "harness": "antigravity",
        "consent_state": consent_state,
        "community_state": community_state,
        "diagnostics_state": diagnostics_state,
        "consent_choice": choice,
        "choice_revision": choice_revision,
        "sharing_enabled": sharing_enabled,
        "community_enabled": sharing_enabled,
        "reporting_enabled": reporting_enabled,
        "repository": comm_data.get("repository") if community_state == "ok" else None,
        "fork": comm_data.get("fork") if community_state == "ok" else None,
        "account": comm_data.get("account") if community_state == "ok" else None,
        "project_roots": list(comm_data.get("project_roots") or []) if community_state == "ok" else [],
        "needs_onboarding_prompt": needs_onboarding_prompt,
        "repair_required": repair_required,
    }


def _validate_sharing_inputs(
    *,
    repository: str,
    visibility: str,
    account: str | None,
    fork: str | None,
    project_root: str | Path | None,
) -> Path | None:
    if not isinstance(repository, str) or not _REPO_SLUG_RE.fullmatch(repository):
        raise ValueError("invalid repository format; expected owner/repo")
    if visibility != "public":
        raise ValueError("community sharing requires visibility='public'")
    if account is not None and (
        not isinstance(account, str) or not account.strip() or "/" in account or any(c.isspace() for c in account)
    ):
        raise ValueError("invalid GitHub account name")
    if fork is not None and (not isinstance(fork, str) or not _REPO_SLUG_RE.fullmatch(fork)):
        raise ValueError("invalid fork format; expected owner/repo")
    if project_root is not None:
        return Path(project_root).expanduser().resolve()
    return None


def _resolve_github_fork(repository: str, account: str | None) -> tuple[str | None, str | None]:
    gh = shutil.which("gh")
    if not gh:
        return account, None
    env = apply_enterprise_transport_env(dict(os.environ))
    resolved_account = account
    if not resolved_account:
        proc = bounded_run(
            [gh, "api", "--hostname", "github.com", "user", "--jq", ".login"],
            "",
            timeout=10.0,
            env=env,
        )
        login = (proc.stdout or "").strip()
        if proc.returncode == 0 and login and "/" not in login and not any(c.isspace() for c in login):
            resolved_account = login
    if not resolved_account:
        return None, None
    repo_name = repository.split("/", 1)[1]
    candidate_fork = f"{resolved_account}/{repo_name}"
    probe = bounded_run(
        [gh, "api", "--hostname", "github.com", f"repos/{candidate_fork}", "--jq", ".full_name"],
        "",
        timeout=10.0,
        env=env,
    )
    if probe.returncode == 0 and (probe.stdout or "").strip():
        return resolved_account, candidate_fork
    fork_proc = bounded_run(
        [gh, "repo", "fork", repository, "--clone=false"],
        "",
        timeout=15.0,
        env=env,
    )
    if fork_proc.returncode == 0:
        return resolved_account, candidate_fork
    return resolved_account, None


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
    resolved_root_path = _validate_sharing_inputs(
        repository=repository,
        visibility=visibility,
        account=account,
        fork=fork,
        project_root=project_root,
    )

    from mindie_knowledge import consent_store
    from mindie_knowledge.loop import settings as settings_mod

    cfg_file = config_path(config)
    if not cfg_file.is_file():
        ensure_default_profile(config=cfg_file, workspace_root=resolved_root_path)
    comm_file = community_config_path(cfg_file)
    cfile = consent_path(cfg_file).resolve()
    consent_info = consent_store.read(cfile)
    if consent_info["state"] in ("corrupt", "unreadable"):
        raise consent_store.ConsentError(
            f"consent file is {consent_info['state']}; explicit user repair is required",
            state=consent_info["state"],
        )

    current = settings_mod.load(comm_file)
    roots = [str(p) for p in current.project_roots]
    if resolved_root_path is not None:
        resolved_root = str(resolved_root_path)
        if resolved_root not in roots:
            roots.insert(0, resolved_root)
    resolved_fork = fork if fork is not None else current.raw.get("fork")
    resolved_account = (
        account
        if account is not None
        else current.raw.get("account")
        or (resolved_fork.split("/", 1)[0] if isinstance(resolved_fork, str) and "/" in resolved_fork else None)
    )
    if enabled and resolved_fork is None and shutil.which("gh"):
        resolved_account, resolved_fork = _resolve_github_fork(repository, resolved_account)

    if enabled:
        updated = settings_mod.write(
            comm_file,
            enabled=True,
            repository=repository,
            project_roots=roots,
            consent_config=str(cfile),
            fork=resolved_fork,
            account=resolved_account,
            visibility=visibility,
            remote_project_root=(
                remote_project_root
                if remote_project_root is not None
                else current.raw.get("remote_project_root")
            ),
        )
        consent_store.record_choice(cfile, "contribute")
    else:
        consent_store.record_choice(cfile, "disabled")
        updated = settings_mod.write(
            comm_file,
            enabled=False,
            repository=repository,
            project_roots=roots,
            consent_config=str(cfile),
            fork=resolved_fork,
            account=resolved_account,
            visibility=visibility,
            remote_project_root=(
                remote_project_root
                if remote_project_root is not None
                else current.raw.get("remote_project_root")
            ),
        )

    diag_file = diagnostics_config_path(cfg_file)
    if hasattr(consent_store, "activate_reporting"):
        consent_store.activate_reporting(
            cfile,
            config=diag_file,
            roots=roots or None,
            ensure=False,
        )
    return updated.as_dict()


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
        uv=shutil.which("uv"),
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

    # Parse before any runtime bootstrap so usage errors cannot stage
    # agy-source or create agy-venv. argparse accepts unique prefixes of
    # --dry-run (--dry, --dr, and similar). Those parsed options are dry-runs
    # and return before consent or config writes. Subparser defaults use
    # SUPPRESS so a parent flag placed before the subcommand is not cleared.
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
        target_py = ensure_runtime_python(source, config=args.config)
        staged_src = stage_source_baseline(source)
        env = apply_enterprise_transport_env(dict(os.environ))
        env["_MINDIE_AGY_SETUP_BOOTSTRAPPED"] = "1"
        env["MINDIE_AGY_SOURCE_ROOT"] = str(staged_src)
        run_kwargs: dict = {"env": env, "timeout": BOOTSTRAP_TIMEOUT + BOOTSTRAP_CLEANUP_GRACE}
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            run_kwargs["stdin"] = _reexec_stdio(sys.stdin)
            run_kwargs["stdout"] = _reexec_stdio(sys.stdout)
            run_kwargs["stderr"] = _reexec_stdio(sys.stderr)
        try:
            proc = subprocess.run([target_py, "-I", str(Path(__file__).resolve()), *raw_argv], **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            print(
                f"MindIE setup reexec exceeded timeout={run_kwargs['timeout']}: {exc}",
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
