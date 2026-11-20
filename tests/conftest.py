"""Pytest isolation fixtures for `mindie-agent-agy` (Tier 0 & Tier 1)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import types
from typing import Any, Mapping
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "plugins" / "mindie-agent" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _install_pre_015_knowledge_binding_shim() -> None:
    """Provide a faithful `mindie_harness.runtime.knowledge_binding` shim only on `mindie-harness < 0.1.5`."""
    if importlib.util.find_spec("mindie_harness") is None:
        return
    if importlib.util.find_spec("mindie_harness.runtime.knowledge_binding") is not None:
        return

    import mindie_harness.mcp.gateway as gateway
    import mindie_harness.mcp.knowledge_rpc as knowledge_rpc
    import mindie_harness.runtime as runtime_pkg
    from mindie_harness.atomic import atomic_write_json, read_json_safe
    from mindie_harness.mcp.receipts import apply_receipt_honesty
    from mindie_harness.paths import ProfileRef
    from mindie_harness.sharing import sharing_status

    host_adapters = {
        "codex": "codex_transcript.py",
        "cc": "transcript.py",
        "claude-code": "transcript.py",
        "kimi": "kimi_transcript.py",
        "kimi-code": "kimi_transcript.py",
        "dsh": "dsh_transcript.py",
        "agy": "agy_transcript.py",
        "antigravity": "agy_transcript.py",
        "wb": "wb_transcript.py",
        "workbuddy": "wb_transcript.py",
        "oc": "opencode_transcript.py",
        "opencode": "opencode_transcript.py",
    }

    def _resolve_transcript_adapter(profile: ProfileRef, plugin_root: Path | None) -> Path:
        root = Path(plugin_root or profile.plugin_root)
        script_name = host_adapters.get(profile.host, "agy_transcript.py")
        for candidate in (
            root / "scripts" / script_name,
            root / script_name,
            root / "plugins" / "mindie-agent" / "scripts" / script_name,
        ):
            if candidate.is_file():
                return candidate.resolve()
        noop = Path(profile.state_dir) / "noop_transcript_adapter.py"
        if not noop.is_file():
            noop.parent.mkdir(parents=True, exist_ok=True)
            noop.write_text(
                "from dataclasses import dataclass\n"
                "@dataclass(frozen=True)\nclass FileIdentity:\n    path: str = ''\n"
                "def identify(path): return None\n"
                "def read_material(path, start, **kw): return {'status': 'unchanged', 'start': start, 'end': start}\n",
                encoding="utf-8",
            )
        return noop.resolve()

    def _resolve_redactor(profile: ProfileRef, *, install_redactor: bool) -> str:
        explicit = os.environ.get("MINDIE_REDACTOR_EXECUTABLE")
        if explicit:
            return str(Path(explicit).expanduser().absolute())
        if install_redactor:
            from mindie_knowledge.loop.transcript_redaction import install_scanner

            return str(Path(install_scanner()).absolute())
        found = shutil.which("gitleaks")
        if found:
            return str(Path(found).absolute())
        binary = "gitleaks.exe" if os.name == "nt" else "gitleaks"
        return str((Path(profile.data_dir) / "cache" / "gitleaks" / "8.30.1" / binary).absolute())

    def ensure_knowledge_binding(
        profile: ProfileRef,
        *,
        plugin_root: Path | None = None,
        python_executable: str | Path | None = None,
        install_redactor: bool = False,
        retire_previous: bool = False,
        include_default_feed: bool = False,
        already_locked: bool = False,
    ) -> dict[str, Any]:
        from contextlib import closing
        from mindie_knowledge.loop import domain_catalog
        from mindie_knowledge.loop.store import Store

        cfg_path = Path(profile.config_path)
        previous = dict(read_json_safe(cfg_path) or {})
        default_domain = "vllm-ascend"
        knowledge_root = Path(os.environ.get("MINDIE_KNOWLEDGE_ROOT") or (Path(profile.data_dir) / "knowledge")).resolve()
        transcript_path = _resolve_transcript_adapter(profile, plugin_root)
        redactor_path = _resolve_redactor(profile, install_redactor=install_redactor)
        community_path = (cfg_path.parent / "mindie-community.json").resolve()
        consent_path = Path(profile.consent_path).resolve()
        admission_path = Path(profile.admission_db).resolve()
        config_dir = (cfg_path.parent / "engines" / profile.host).resolve()
        authority_dir = (cfg_path.parent / "authorities" / profile.host).resolve()

        default_engine: dict[str, Any] = {
            "root": str(knowledge_root),
            "domain": default_domain,
            "domain_catalog": domain_catalog.SCHEMA,
            "admission_path": str(admission_path),
            "community_config": str(community_path),
            "capture_mode": "public-transcript",
            "transcript_adapter": str(transcript_path),
            "redactor_executable": redactor_path,
            "product_validation": {
                "schema": "mindie-candidate-check/1",
                "status": "validated",
                "python": str(python_executable or sys.executable),
            },
        }
        if include_default_feed:
            default_engine["feeds"] = [dict(domain_catalog.get_domain_config(default_domain).to_feed())]

        default_bytes = (json.dumps(default_engine, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        default_path = config_dir / hashlib.sha256(default_bytes).hexdigest()[:20] / f"{default_domain}.engine.json"
        default_path.parent.mkdir(parents=True, exist_ok=True)
        if not default_path.is_file() or default_path.read_bytes() != default_bytes:
            atomic_write_json(default_path, default_engine)
        with closing(Store(str(knowledge_root), default_domain)) as store:
            store.db.assert_authority()

        existing_routes = {
            dom: route
            for dom, route in dict(previous.get("domain_configs") or {}).items()
            if isinstance(route, str) and dom != default_domain and Path(route).is_file()
        }
        candidate_adapter = dict(previous)
        candidate_adapter["engine_config"] = str(default_path)
        candidate_adapter["domain_configs"] = existing_routes
        prepared = domain_catalog.prepare(
            candidate_adapter,
            default_engine,
            config_directory=str(config_dir),
            authority_directory=str(authority_dir),
            consent_config=str(consent_path),
        )
        routes = dict(prepared["domain_configs"])
        routes[default_domain] = str(default_path)
        binding_fields = {
            "host": profile.host,
            "engine_config": str(default_path),
            "domain_configs": routes,
            "community_config": str(community_path),
            "consent_config": str(consent_path),
            "admission_path": str(admission_path),
        }

        if retire_previous and previous:
            clean_prev: dict[str, Any] = {}
            old_default = previous.get("engine_config")
            if isinstance(old_default, str) and Path(old_default).is_file():
                clean_prev["engine_config"] = old_default
            clean_prev["domain_configs"] = existing_routes
            if clean_prev.get("engine_config") or clean_prev["domain_configs"]:
                try:
                    domain_catalog.retire_changed(clean_prev, {"engine_config": str(default_path), "domain_configs": routes}, include_default=bool(clean_prev.get("engine_config")))
                except TimeoutError:
                    raise
                except Exception:
                    pass

        if not already_locked:
            merged_cfg = dict(previous)
            if python_executable is not None:
                merged_cfg["python"] = str(python_executable)
            merged_cfg.update(binding_fields)
            atomic_write_json(cfg_path, merged_cfg)

        return {
            "status": "configured",
            "engine_config": str(default_path),
            "domain_configs": routes,
            "domains": sorted(routes),
            "transcript_adapter": str(transcript_path),
            "redactor_executable": redactor_path,
            "binding_fields": binding_fields,
            "reportable": False,
        }

    def run_stop_capture(
        profile: ProfileRef,
        event: Mapping[str, Any],
        *,
        plugin_root: Path | None = None,
        timeout_sec: float = 6.0,
    ) -> dict[str, Any]:
        if event.get("stop_hook_active", False) is not False:
            return {"stage": "inert", "reason": "recursive-stop"}
        cfg = read_json_safe(profile.config_path)
        if not isinstance(cfg, dict):
            return {"stage": "inert", "reason": "unconfigured"}
        cwd = str(event.get("cwd") or "")
        session_id = str(event.get("session_id") or "")
        if not session_id or not cwd:
            return {"stage": "inert", "reason": "missing-context"}
        status = sharing_status(profile, cwd=cwd)
        if status.get("tier") not in {"contribute", "local-only"} or status.get("cwd_in_scope") is not True:
            return {"stage": "inert", "reason": "sharing-disabled"}

        from mindie_knowledge.loop import handoff
        from mindie_knowledge.loop.activation import Admission

        admission = Admission(str(profile.admission_db))
        if admission.inspect(session_id).get("status") == "inactive":
            return {"stage": "inert", "reason": "revoked"}
        lease = admission.active_lease(session_id)
        if not isinstance(lease, dict) or not lease.get("token"):
            lease = admission.activate(session_id, project_root=cwd, root_session=session_id, not_before=0.0)

        engine_raw = cfg.get("engine_config")
        engine_path = Path(engine_raw) if isinstance(engine_raw, str) and os.path.isabs(engine_raw) else profile.config_path.with_name(f"{profile.config_path.stem}.engine.json")
        if engine_path.is_file():
            try:
                handoff.prepare_schema(str(engine_path))
            except TimeoutError:
                raise
            except Exception:
                pass
        forwarded = dict(event)
        forwarded.setdefault("hook_event_name", "Stop")
        forwarded.setdefault("harness", profile.host)
        if isinstance(lease, dict) and lease.get("token"):
            forwarded.setdefault("mindie_activation", lease["token"])
        return handoff.accept_stop(str(engine_path), forwarded, timeout=float(timeout_sec))

    def _shim_invoke_knowledge_tool(
        operation: str,
        arguments: Mapping[str, Any] | None,
        *,
        profile: ProfileRef,
        session_id: str,
        cwd: Path | None = None,
        deadline: Any = None,
    ) -> dict[str, Any]:
        from mindie_knowledge.graded.distill import result_view
        from mindie_knowledge.loop import cli, transport
        from mindie_knowledge.loop.activation import Admission

        if operation not in knowledge_rpc.KNOWLEDGE_NAMES:
            return knowledge_rpc._error_result("unknown_operation", f"Unknown knowledge operation {operation!r}.")
        payload = dict(arguments or {})
        method = operation.removeprefix("knowledge_")
        cfg = read_json_safe(profile.config_path) or {}
        domain = str(payload.pop("domain", None) or cfg.get("domain") or "vllm-ascend")
        routes = cfg.get("domain_configs") if isinstance(cfg.get("domain_configs"), Mapping) else {}
        engine_raw = routes.get(domain) or cfg.get("engine_config")
        if not isinstance(engine_raw, str) or not Path(engine_raw).is_file():
            bound = ensure_knowledge_binding(profile, plugin_root=profile.plugin_root, install_redactor=False)
            engine_raw = bound["domain_configs"].get(domain) or bound["engine_config"]

        admission = Admission(str(profile.admission_db))
        inspect_fn = getattr(admission, "inspect", None)
        if callable(inspect_fn) and inspect_fn(session_id).get("status") == "inactive":
            return knowledge_rpc._error_result("knowledge_inactive", "MindIE knowledge is explicitly deactivated for this session.")

        token: str | None = None
        if method == "distill":
            lease = admission.active_lease(session_id) if callable(getattr(admission, "active_lease", None)) else None
            if isinstance(lease, Mapping):
                token = lease.get("token")
            if not token:
                scope = str((cwd or profile.plugin_root).resolve())
                share = sharing_status(profile, cwd=scope)
                if share.get("tier") not in {"contribute", "local-only"} or share.get("cwd_in_scope") is not True:
                    return knowledge_rpc._error_result("sharing_disabled", "Distillation requires local-only or contribute consent for this workspace.")
                token = admission.activate(session_id, project_root=scope, root_session=session_id, not_before=0.0).get("token")
            payload.pop("_session_verified", None)
            payload["_activation"] = token
        else:
            payload.pop("_activation", None)
            payload["_session_verified"] = True
        payload["_session_id"] = session_id

        try:
            conn = cli.ensure_service(str(engine_raw))
            value = transport.rpc(conn, method, payload, timeout=110.0 if method in {"sync", "distill"} else 35.0)
            is_error = False
            if method == "distill":
                value, is_error = result_view(value)
            result: dict[str, Any] = {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}],
                "structuredContent": value if isinstance(value, dict) else {"feeds": value},
                "isError": bool(is_error),
            }
        except TimeoutError:
            raise
        except Exception as exc:
            return knowledge_rpc._error_result("knowledge_rpc_failed", f"mindie_knowledge RPC failed ({type(exc).__name__}): {exc}")

        if method == "distill" and token and callable(getattr(admission, "finish", None)):
            try:
                admission.finish(session_id, token, not is_error)
            except TimeoutError:
                raise
            except (OSError, sqlite3.Error) as exc:
                if not is_error:
                    result = apply_receipt_honesty(result, exc)
        return result

    shim_mod = types.ModuleType("mindie_harness.runtime.knowledge_binding")
    shim_mod.ensure_knowledge_binding = ensure_knowledge_binding  # type: ignore[attr-defined]
    shim_mod.run_stop_capture = run_stop_capture  # type: ignore[attr-defined]
    shim_mod.__all__ = ["ensure_knowledge_binding", "run_stop_capture"]  # type: ignore[attr-defined]
    sys.modules["mindie_harness.runtime.knowledge_binding"] = shim_mod
    setattr(runtime_pkg, "knowledge_binding", shim_mod)
    knowledge_rpc.invoke_knowledge_tool = _shim_invoke_knowledge_tool  # type: ignore[assignment]
    gateway.invoke_knowledge_tool = _shim_invoke_knowledge_tool  # type: ignore[assignment]


_install_pre_015_knowledge_binding_shim()

# Every inherited host-home variable that a legacy sweep, a launcher, or `uv`
# may consult. The autouse fixture below replaces all of them with fresh
# `tmp_path` children so a developer's real `~/.codex`, `~/.claude`, `~/.kimi`,
# `.dsh`, `.workbuddy`, `.opencode`, temp, or uv cache is never scanned.
HOST_HOME_ENV_VARS = (
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_PLUGIN_DATA",
    "KIMI_SHARE_DIR",
    "KIMI_CODE_HOME",
    "DSH_HOME",
    "OPENCODE_CONFIG_DIR",
    "WORKBUDDY_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "LOCALAPPDATA",
    "APPDATA",
    "TMPDIR",
    "TEMP",
    "TMP",
    "UV_CACHE_DIR",
)


@pytest.fixture(autouse=True)
def isolated_mindie_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Strictly isolate all MindIE, Windows AppData, XDG, host-harness and Antigravity paths per test."""
    return isolate_environment(tmp_path, monkeypatch)


def isolate_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Point every host-home variable at `tmp_path` (shared by tests and fixture)."""
    cfg_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    appdata = tmp_path / "appdata" / "Roaming"
    localappdata = tmp_path / "appdata" / "Local"
    agy_home = tmp_path / "gemini" / "antigravity"
    home = tmp_path / "home"
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    claude_plugin_data = tmp_path / "claude-plugin-data"
    kimi_home = tmp_path / "kimi"
    kimi_code_home = tmp_path / "kimi-code"
    dsh_home = tmp_path / "dsh"
    opencode_home = tmp_path / "opencode"
    workbuddy_home = tmp_path / "workbuddy"
    temp_home = tmp_path / "tmp"
    uv_cache = tmp_path / "uv-cache"
    for folder in (cfg_home, data_home, state_home, cache_home, appdata, localappdata,
                   agy_home, home, codex_home, claude_home, claude_plugin_data,
                   kimi_home, kimi_code_home, dsh_home, opencode_home,
                   workbuddy_home, temp_home, uv_cache):
        folder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(claude_plugin_data))
    monkeypatch.setenv("KIMI_SHARE_DIR", str(kimi_home))
    monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_code_home))
    monkeypatch.setenv("DSH_HOME", str(dsh_home))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(opencode_home))
    monkeypatch.setenv("WORKBUDDY_HOME", str(workbuddy_home))
    monkeypatch.setenv("TMPDIR", str(temp_home))
    monkeypatch.setenv("TEMP", str(temp_home))
    monkeypatch.setenv("TMP", str(temp_home))
    monkeypatch.setenv("UV_CACHE_DIR", str(uv_cache))
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(data_home))
    monkeypatch.setenv("REMOTE_DEV_STATE_DIR", str(data_home / "remote"))
    monkeypatch.setenv("REMOTE_DEV_AUTH_DIR", str(data_home / "remote-auth"))
    monkeypatch.setenv("MINDIE_AGENT_CONFIG", str(cfg_home / "agy.json"))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_CONFIG", str(cfg_home / "diagnostics.json"))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_ROOT", str(data_home / "diagnostics"))
    monkeypatch.setenv("AGY_APP_DATA_DIR", str(agy_home))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini-cli"))
    monkeypatch.setenv("_MINDIE_AGY_REEXEC", "1")
    monkeypatch.delenv("AGY_CONVERSATION_ID", raising=False)
    return dict(
        config_home=cfg_home,
        data_home=data_home,
        agy_home=agy_home,
        codex_home=codex_home,
        temp_home=temp_home,
        uv_cache=uv_cache,
        config_path=cfg_home / "agy.json",
    )
