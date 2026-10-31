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
  bootstrap is bounded by `BOOTSTRAP_TIMEOUT = 540.0s`.

[Side-Effect Policy]
- Writes `agy.json`, `agy.engine.json`, `mindie-community.json`, and
  `mindie-consent.json` under `config_home()`, and stages `agy-source` /
  `agy-venv` under `data_home()` when bootstrapping.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
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
)

BOOTSTRAP_TIMEOUT = 540.0
_TEXT_SUFFIXES = {".py", ".json", ".md", ".toml", ".svg", ".txt", ".yml", ".yaml"}
_BASELINE_ITEMS = (
    "pyproject.toml",
    "product-contract.json",
    "harness-compatibility.json",
    "plugins",
)


def _discover_checkout_source() -> Path | None:
    """Locate a complete `mindie-agent-agy` checkout for source baseline retention."""
    candidates = [
        plugin_root().parent.parent,
        repo_root(),
        Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "mindie-agent-agy-install",
        Path("/tmp/mindie-agent-agy-install"),
        data_home() / "agy-source",
    ]
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


def stage_source_baseline(source_dir: str | Path | None = None, dest_dir: str | Path | None = None) -> Path:
    """Persist an LF-normalized adapter source baseline under `data_home()/agy-source`."""
    src = Path(source_dir).resolve() if source_dir is not None else _discover_checkout_source()
    dst = Path(dest_dir).resolve() if dest_dir is not None else (data_home() / "agy-source").resolve()
    if src is None:
        return dst if (dst / "pyproject.toml").is_file() else repo_root()
    if src == dst:
        for fpath in dst.rglob("*"):
            if fpath.is_file() and fpath.suffix in _TEXT_SUFFIXES and ".git" not in fpath.parts:
                raw = fpath.read_bytes()
                if b"\r\n" in raw:
                    fpath.write_bytes(raw.replace(b"\r\n", b"\n"))
        return dst

    dst.mkdir(mode=0o700, parents=True, exist_ok=True)
    for item_name in _BASELINE_ITEMS:
        item_src = src / item_name
        if not item_src.exists():
            continue
        item_dst = dst / item_name
        if item_src.is_file():
            _copy_lf_clean(item_src, item_dst)
        else:
            for sub in item_src.rglob("*"):
                if any(part in {"__pycache__", ".pytest_cache", ".git", ".venv"} for part in sub.parts):
                    continue
                if sub.is_file() and sub.suffix != ".pyc":
                    _copy_lf_clean(sub, item_dst / sub.relative_to(item_src))
    return dst


def ensure_runtime_python(
    source_root: str | Path | None = None,
    python_exe: str | None = None,
    *,
    timeout: float = BOOTSTRAP_TIMEOUT,
) -> str:
    """Return an interpreter with MindIE runtime components installed, bootstrapping `agy-venv` if needed."""
    if python_exe:
        return str(Path(python_exe).resolve())
    if importlib.util.find_spec("mindie_knowledge") is not None:
        return str(Path(sys.executable).resolve())

    venv_dir = data_home() / "agy-venv"
    venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    src_root = stage_source_baseline(source_root)
    if venv_py.is_file():
        probe = bounded_run(
            [str(venv_py), "-I", "-c", "import mindie_knowledge, remote_dev, mindie_diagnostics"],
            "",
            timeout=15.0,
        )
        if probe.returncode == 0:
            return str(venv_py.resolve())

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
    return str(venv_py.resolve())


def validate_contracts() -> dict:
    root = repo_root()
    contract_file = root / "product-contract.json"
    compat_file = root / "harness-compatibility.json"
    contract = read_json(contract_file) if contract_file.is_file() else {"native_version": "0.1.1"}
    compat = read_json(compat_file) if compat_file.is_file() else {"harness": "antigravity"}
    return dict(
        schema="mindie-candidate-check/1",
        status="validated",
        harness=compat.get("harness", "antigravity"),
        native_version=contract.get("native_version", "0.1.1"),
    )


def ensure_default_profile(
    *,
    config: str | Path | None = None,
    workspace_root: str | Path | None = None,
    domain: str = "vllm-ascend",
    python_exe: str | None = None,
) -> dict:
    apply_enterprise_transport_env()
    interpreter = ensure_runtime_python(python_exe=python_exe)

    from mindie_knowledge.loop import settings as settings_mod

    cfg_file = config_path(config)
    eng_file = engine_config_path(cfg_file)
    sdir = state_dir(cfg_file)
    adm_file = admission_path(cfg_file)
    comm_file = community_config_path(cfg_file)
    scripts_dir = Path(__file__).resolve().parent
    data_root = data_home() / "domains" / domain
    sdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    direct_root = plugin_root().parent.parent
    if not (direct_root / "pyproject.toml").is_file() or "mindie-agent-agy-install" in str(direct_root):
        resolved_source = stage_source_baseline()
    else:
        resolved_source = repo_root(cfg_file)

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
    engine_payload = dict(
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

    adapter_payload = dict(
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
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop import settings as settings_mod

    cfg_file = config_path(config)
    if not cfg_file.is_file():
        ensure_default_profile(config=cfg_file, workspace_root=project_root)
    comm_file = community_config_path(cfg_file)
    cfile = consent_path(cfg_file).resolve()
    consent_store.record_choice(cfile, "contribute" if enabled else "disabled")
    current = settings_mod.load(comm_file)
    roots = [str(p) for p in current.project_roots]
    if project_root is not None:
        resolved_root = str(Path(project_root).resolve())
        if resolved_root not in roots:
            roots.insert(0, resolved_root)
    resolved_fork = fork if fork is not None else current.raw.get("fork")
    resolved_account = (
        account
        if account is not None
        else current.raw.get("account")
        or (resolved_fork.split("/", 1)[0] if isinstance(resolved_fork, str) and "/" in resolved_fork else None)
    )
    updated = settings_mod.write(
        comm_file,
        enabled=bool(enabled),
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
    return updated.as_dict()


def main(argv: list[str] | None = None) -> int:
    apply_enterprise_transport_env()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        raw_argv = ["init"]
    if (
        importlib.util.find_spec("mindie_knowledge") is None
        and os.environ.get("_MINDIE_AGY_SETUP_BOOTSTRAPPED") != "1"
    ):
        staged_src = stage_source_baseline()
        target_py = ensure_runtime_python(staged_src)
        env = apply_enterprise_transport_env(dict(os.environ))
        env["_MINDIE_AGY_SETUP_BOOTSTRAPPED"] = "1"
        env["MINDIE_AGY_SOURCE_ROOT"] = str(staged_src)
        proc = subprocess.run([target_py, "-I", str(Path(__file__).resolve()), *raw_argv], env=env)
        return int(proc.returncode)

    parser = argparse.ArgumentParser(description="Configure MindIE Agent for Antigravity")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser("init")
    init_p.add_argument("--config", default=None)
    init_p.add_argument("--workspace-root", default=None)
    init_p.add_argument("--domain", default="vllm-ascend")

    share_p = sub.add_parser("sharing")
    share_p.add_argument("--config", default=None)
    share_p.add_argument("--enable", action="store_true")
    share_p.add_argument("--disable", action="store_true")
    share_p.add_argument("--fork", default=None)
    share_p.add_argument("--project-root", default=None)
    share_p.add_argument("--remote-project-root", default=None)
    share_p.add_argument("--visibility", default="public")

    args = parser.parse_args(raw_argv)
    if args.cmd == "init":
        result = ensure_default_profile(
            config=args.config,
            workspace_root=args.workspace_root,
            domain=args.domain,
        )
    else:
        enabled = True if args.enable else False if args.disable else True
        result = configure_community(
            config=args.config,
            enabled=enabled,
            fork=args.fork,
            project_root=args.project_root,
            remote_project_root=args.remote_project_root,
            visibility=args.visibility,
        )
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
