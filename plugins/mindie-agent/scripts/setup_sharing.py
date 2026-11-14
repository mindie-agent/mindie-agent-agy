"""Community knowledge sharing and fault-reporting configuration for Antigravity (`setup_sharing.py`).

[Role]
Implements stdlib-only sharing status inspection (`sharing_status`), GitHub fork
discovery/provisioning (`resolve_github_fork`), and consent-backed community
sharing configuration (`configure_community`) for `setup.py`.

[Invariants]
- `sharing_status` is strictly read-only and never bootstraps a virtualenv or
  imports third-party packages; corrupt consent/community/diagnostics files set
  `repair_required=True` and `sharing_enabled=None` rather than pretending disabled.
- `configure_community` validates repository/account/visibility inputs before
  touching disk and refuses to overwrite corrupt or unreadable consent files.
- Explicitly re-raises `TimeoutError` before any `except OSError:` branch.

[Timeouts & Locks]
- `gh api` / `gh repo fork` subprocess calls use `bounded.run` (`10.0s`–`15.0s`).

[Side-Effect Policy]
- `sharing_status` performs zero writes; `configure_community` writes
  `mindie-community.json`, `mindie-consent.json`, and `diagnostics.json`.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Callable

from bounded import run as bounded_run
from paths import (
    apply_enterprise_transport_env,
    community_config_path,
    config_path,
    consent_path,
    diagnostics_config_path,
    read_json,
    which_native,
)

_REPO_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _read_optional_json(path: Path, *, read_json_fn: Callable = read_json) -> tuple[str, dict]:
    try:
        path.stat()
        return "ok", read_json_fn(path)
    except TimeoutError:
        raise
    except FileNotFoundError:
        return "missing", {}
    except ValueError:
        return "corrupt", {}
    except OSError:
        return "unreadable", {}


def sharing_status(config: str | Path | None = None, *, read_json_fn: Callable = read_json) -> dict:
    """Return current community sharing and fault reporting status using stdlib only."""
    cfg_file = config_path(config)
    cfile = consent_path(cfg_file)
    comm_file = community_config_path(cfg_file)
    diag_file = diagnostics_config_path(cfg_file)

    consent_state, consent_data = _read_optional_json(cfile, read_json_fn=read_json_fn)
    community_state, comm_data = _read_optional_json(comm_file, read_json_fn=read_json_fn)
    diagnostics_state, diag_data = _read_optional_json(diag_file, read_json_fn=read_json_fn)

    choice = consent_data.get("choice") if consent_state == "ok" else None
    if consent_state == "ok" and choice not in (None, "contribute", "read-only", "later", "disabled"):
        consent_state = "corrupt"
        choice = None
    choice_revision = consent_data.get("choice_revision") if consent_state == "ok" else None
    repair_required = any(
        state not in {"ok", "missing"}
        for state in (consent_state, community_state, diagnostics_state)
    )
    if community_state == "ok":
        sharing_enabled = bool(comm_data.get("enabled", False)) and choice == "contribute"
    elif community_state == "missing":
        sharing_enabled = False
    else:
        sharing_enabled = None
    reporting_sub = diag_data.get("reporting") if isinstance(diag_data.get("reporting"), dict) else {}
    if diagnostics_state == "ok":
        reporting_enabled = bool(
            reporting_sub.get("enabled", False)
            or diag_data.get("decision") == "enabled"
            or diag_data.get("enabled") is True
        )
    elif diagnostics_state == "missing":
        reporting_enabled = False
    else:
        reporting_enabled = None
    raw_roots = comm_data.get("project_roots") if community_state == "ok" else []
    if isinstance(raw_roots, list) and all(isinstance(item, str) for item in raw_roots):
        project_roots = raw_roots
    elif community_state == "ok" and raw_roots not in (None, []):
        community_state = "corrupt"
        sharing_enabled = None
        repair_required = True
        project_roots = []
    else:
        project_roots = []
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
        "project_roots": project_roots,
        "needs_onboarding_prompt": needs_onboarding_prompt,
        "repair_required": repair_required,
    }


def validate_sharing_inputs(
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


def resolve_github_fork(repository: str, account: str | None) -> tuple[str | None, str | None]:
    gh = which_native("gh")
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
    ensure_profile_fn: Callable,
    config: str | Path | None = None,
    enabled: bool,
    fork: str | None = None,
    account: str | None = None,
    project_root: str | Path | None = None,
    remote_project_root: str | None = None,
    visibility: str = "public",
    repository: str = "mindie-agent/knowledge-vllm-ascend",
) -> dict:
    resolved_root_path = validate_sharing_inputs(
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
        ensure_profile_fn(config=cfg_file, workspace_root=resolved_root_path)
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
    if enabled and resolved_fork is None and which_native("gh"):
        resolved_account, resolved_fork = resolve_github_fork(repository, resolved_account)

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
