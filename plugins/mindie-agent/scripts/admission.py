"""Session lease admission and factual control-plane status for Antigravity.

[Role]
Wraps `mindie_knowledge.loop.activation.Admission` for default-enabled session
leases, explicit `/mindie-agent deactivate` muting (`is_deactivated`,
`ensure_default_lease`, `activate`, `deactivate`, `knowledge_lease`), and
read-only `status_facts`.

[Invariants]
- While the Antigravity plugin is enabled, conversations are active by default
  unless explicitly muted via `/mindie-agent deactivate` (`enabled == 0`).
- `knowledge_lease`, `is_deactivated`, and `status_facts` are strictly read-only
  and never create `admission.sqlite3` or onboard/start background services.
- Damaged community config yields `community_sharing_enabled=None`, never
  `False`. Corrupt/unreadable sharing files set `repair_required=True`.

[Timeouts & Locks]
- Uses `Admission`'s WAL + `5.0s` SQLite busy timeout (`open_database`).

[Side-Effect Policy]
- `activate`, `ensure_default_lease`, and `deactivate` mutate `admission_path`;
  `knowledge_lease`, `is_deactivated`, and `status_facts` are side-effect free.
"""

from __future__ import annotations

import os
from pathlib import Path

from identity import require_session_or_scope
from paths import (
    admission_path,
    community_config_path,
    config_path,
    consent_path,
    engine_config_path,
    read_json,
)


def gate(config: str | Path | None = None):
    from mindie_knowledge.loop.activation import Admission

    return Admission(str(admission_path(config)))


def knowledge_lease(session: str, *, config: str | Path | None = None) -> dict | None:
    session = require_session_or_scope(session)
    path = admission_path(config)
    if not path.is_file():
        return None
    return gate(config).knowledge_lease(session)


def is_deactivated(session: str, *, config: str | Path | None = None) -> bool:
    session = require_session_or_scope(session)
    path = admission_path(config)
    if not path.is_file():
        return False
    info = gate(config).inspect(session)
    return info.get("status") == "inactive"


def ensure_default_lease(
    session: str,
    project_root: str | Path | None = None,
    *,
    not_before: float | None = 0.0,
    config: str | Path | None = None,
) -> dict:
    session = require_session_or_scope(session)
    if is_deactivated(session, config=config):
        raise ValueError(
            "MindIE knowledge is deactivated for this conversation; send /mindie-agent to re-enable"
        )
    existing = knowledge_lease(session, config=config)
    if existing is not None:
        return existing
    root = str(Path(project_root or os.getcwd()).resolve())
    adm = gate(config)
    adm.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lease = adm.activate(session, project_root=root, root_session=session, not_before=not_before)
    return dict(
        lease,
        enabled=int(bool(lease.get("enabled", 1))),
        entry_enabled=int(bool(lease.get("entry_enabled", 1))),
    )


def activate(
    session: str,
    project_root: str | Path,
    *,
    domain: str | None = None,
    config: str | Path | None = None,
) -> dict:
    session = require_session_or_scope(session)
    root = str(Path(project_root).resolve())
    if not os.path.isabs(root):
        raise ValueError("project_root must be an absolute path")
    cfg_file = config_path(config)
    if domain is not None and cfg_file.is_file():
        adapter = read_json(cfg_file)
        from mindie_knowledge.loop.domain_routes import activate_selected_domain

        selected = activate_selected_domain(
            adapter["engine_config"],
            domain=domain,
            domain_configs=adapter.get("domain_configs"),
            session=session,
            project_root=root,
            root_session=session,
        )
        lease = selected["lease"]
        return dict(
            status="active",
            domain=selected["domain"],
            engine_config=selected["engine_config"],
            generation=selected["generation"],
            mindie_session_id=lease["session"],
            mindie_activation=lease["token"],
            activated_at=lease["activated_at"],
            project_root=lease["project_root"],
        )
    adm = gate(config)
    adm.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lease = adm.activate(session, project_root=root, root_session=session)
    return dict(
        status="active",
        mindie_session_id=lease["session"],
        mindie_activation=lease["token"],
        activated_at=lease["activated_at"],
        project_root=lease["project_root"],
    )


def deactivate(
    session: str,
    *,
    config: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict:
    session = require_session_or_scope(session)
    root = str(Path(project_root or os.getcwd()).resolve())
    adm = gate(config)
    adm.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if adm.inspect(session).get("status") == "missing":
        adm.activate(session, project_root=root, root_session=session)
    adm.deactivate(session)
    cfg_file = config_path(config)
    if cfg_file.is_file():
        adapter = read_json(cfg_file)
        domain_configs = adapter.get("domain_configs")
        if isinstance(domain_configs, dict) and domain_configs:
            from mindie_knowledge.loop.domain_routes import deactivate_registered_domains

            return deactivate_registered_domains(
                adapter["engine_config"],
                domain_configs=domain_configs,
                session=session,
            )
    return dict(status="inactive", session=session)


def status_facts(session: str | None = None, *, config: str | Path | None = None) -> dict:
    """Return factual control-plane status without onboarding prompts or activation."""
    try:
        return _status_facts_body(session, config=config)
    except TimeoutError:
        raise
    except OSError:
        cfg = ""
        try:
            cfg = str(config_path(config))
        except TimeoutError:
            raise
        except OSError:
            pass
        return dict(
            harness="antigravity",
            configured=False,
            config_path=cfg,
            engine_config_path="",
            activated=False,
            session_id=session,
            project_root=None,
            activated_at=None,
            consent_choice="invalid",
            community_sharing_enabled=None,
            community_state="unreadable",
            diagnostics_state="unreadable",
            repair_required=True,
        )


def _status_facts_body(session: str | None = None, *, config: str | Path | None = None) -> dict:
    cfg_file = config_path(config)
    try:
        configured = cfg_file.is_file()
    except TimeoutError:
        raise
    except OSError:
        configured = False
    lease = None
    activated = True
    norm_session = session
    if session is not None:
        try:
            norm_session = require_session_or_scope(session)
            activated = not is_deactivated(norm_session, config=config)
            lease = knowledge_lease(norm_session, config=config)
        except TimeoutError:
            raise
        except (ValueError, OSError):
            activated = False
            lease = None
    consent_state = "unconfigured"
    cpath = consent_path(config)
    try:
        consent_present = cpath.is_file()
    except TimeoutError:
        raise
    except OSError:
        consent_state = "invalid"
        consent_present = False
    if consent_present:
        try:
            cdata = read_json(cpath)
            consent_state = str(cdata.get("choice") or cdata.get("status") or "configured")
        except TimeoutError:
            raise
        except FileNotFoundError:
            consent_state = "unconfigured"
        except (ValueError, OSError):
            consent_state = "invalid"
    community_enabled = False
    comm_path = community_config_path(config)
    try:
        community_present = comm_path.is_file()
    except TimeoutError:
        raise
    except OSError:
        community_enabled = None
        community_present = False
    if community_present:
        try:
            comm_data = read_json(comm_path)
            community_enabled = bool(comm_data.get("enabled"))
        except TimeoutError:
            raise
        except FileNotFoundError:
            community_enabled = False
        except (ValueError, OSError):
            community_enabled = None
    from setup import sharing_status

    try:
        share = sharing_status(config)
    except TimeoutError:
        raise
    except (ValueError, TypeError, OSError, KeyError) as exc:
        share = {
            "community_state": "unreadable",
            "diagnostics_state": "unreadable",
            "repair_required": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if share.get("community_state") not in {"ok", "missing"}:
        community_enabled = None
    try:
        engine_path = str(engine_config_path(config))
    except TimeoutError:
        raise
    except (ValueError, OSError):
        engine_path = str(cfg_file.with_name(cfg_file.stem + ".engine.json"))
    return dict(
        harness="antigravity",
        configured=configured,
        config_path=str(cfg_file),
        engine_config_path=engine_path,
        activated=bool(activated),
        session_id=lease["session"] if lease else norm_session,
        project_root=lease.get("project_root") if lease else None,
        activated_at=lease.get("activated_at") if lease else None,
        consent_choice=consent_state,
        community_sharing_enabled=community_enabled,
        community_state=share["community_state"],
        diagnostics_state=share["diagnostics_state"],
        repair_required=bool(share["repair_required"]),
    )
