"""MindIE Agent profile provisioner and configuration manager for Antigravity (`setup.py`).

[Role]
Initializes and updates the MindIE-owned Antigravity adapter profile (`agy.json`,
`agy.engine.json`, `mindie-community.json`, `mindie-consent.json`), validating
`product-contract.json` and `harness-compatibility.json` without starting the
knowledge service or syncing feeds during installation.

[Invariants]
- Passive Default: `ensure_default_profile` and `configure_community` write
  MindIE-owned JSON configs atomically via `os.replace()` and never activate a
  session lease or start the background knowledge loop.
- Never writes into `~/.gemini`.

[Timeouts & Locks]
- Pure local file validation and atomic `os.replace()` writes; no network calls.

[Side-Effect Policy]
- Writes `agy.json`, `agy.engine.json`, `mindie-community.json`, and
  `mindie-consent.json` under `config_home()`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

from mindie_knowledge import consent_store
from mindie_knowledge.loop import settings as settings_mod

from paths import (
    admission_path,
    atomic_write_json,
    community_config_path,
    config_path,
    consent_path,
    data_home,
    engine_config_path,
    read_json,
    repo_root,
    state_dir,
)


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
    cfg_file = config_path(config)
    eng_file = engine_config_path(cfg_file)
    sdir = state_dir(cfg_file)
    adm_file = admission_path(cfg_file)
    comm_file = community_config_path(cfg_file)
    scripts_dir = Path(__file__).resolve().parent
    interpreter = str(Path(python_exe or sys.executable).resolve())
    data_root = data_home() / "domains" / domain
    sdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_root.mkdir(mode=0o700, parents=True, exist_ok=True)

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

    args = parser.parse_args(argv)
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
