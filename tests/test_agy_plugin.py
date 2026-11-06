"""Tier 0 tests for Antigravity native plugin layout, contracts, and engineering invariants."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "mindie-agent"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def test_plugin_manifest_and_root_hooks_layout():
    plugin_json = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert plugin_json["name"] == "mindie-agent"
    assert (PLUGIN_ROOT / plugin_json["logo"]).is_file()

    # Antigravity native plugins MUST place hooks.json at the plugin root, not hooks/hooks.json
    hooks_file = PLUGIN_ROOT / "hooks.json"
    assert hooks_file.is_file()
    assert not (PLUGIN_ROOT / "hooks" / "hooks.json").exists()

    hooks_cfg = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert "mindie-agent" in hooks_cfg
    events = hooks_cfg["mindie-agent"]
    assert set(events) == {"SessionStart", "PreInvocation", "PreToolUse", "Stop"}

    mcp_cfg = json.loads((PLUGIN_ROOT / "mcp_config.json").read_text(encoding="utf-8"))
    servers = mcp_cfg["mcpServers"]
    assert set(servers) == {"mindie-remote-dev", "mindie-tools"}
    for srv in servers.values():
        assert isinstance(srv["command"], str)
        assert isinstance(srv["args"], list)


def test_product_and_harness_compatibility_contracts_and_module_invariants():
    product = json.loads((REPO_ROOT / "product-contract.json").read_text(encoding="utf-8"))
    assert product["schema"] == "mindie-product-contract/1"
    assert product["harness"] == "antigravity"
    assert set(product["components"]) == {
        "mindie-knowledge",
        "remote-dev",
        "mindie-diagnostics",
        "mindie-coordinator",
        "mindie-debugger",
        "mindie-profiler",
        "mindie-sim",
        "npu-top",
    }
    assert (PLUGIN_ROOT / "skills" / "mindie-agent" / "SKILL.md").is_file()
    assert (SCRIPTS_DIR / "organizer.py").is_file()

    compat = json.loads((REPO_ROOT / "harness-compatibility.json").read_text(encoding="utf-8"))
    assert compat["schema"] == "mindie-harness-compatibility/1"
    assert set(compat["used_interfaces"]) == set(compat["interface_registry"])
    from mindie_knowledge.releases import supports_harness, validate_compatibility

    validate_compatibility(compat, harness_name="antigravity")
    assert supports_harness(compat, harness_name="antigravity", harness_version="2.0.0")

    for iface_id, entry in compat["interface_registry"].items():
        assert entry["code_paths"], f"{iface_id} missing code_paths"
        assert entry["test_ids"], f"{iface_id} missing test_ids"
        for code_ref in entry["code_paths"]:
            rel_path = code_ref.split(":", 1)[0]
            assert (REPO_ROOT / rel_path).is_file(), f"Missing code path {rel_path}"
        for test_id in entry["test_ids"]:
            test_rel, func_name = test_id.split("::", 1)
            test_file = REPO_ROOT / test_rel
            assert test_file.is_file(), f"Missing test file {test_rel}"
            assert f"def {func_name}(" in test_file.read_text(encoding="utf-8")

    required_sections = ("[Role]", "[Invariants]", "[Timeouts & Locks]", "[Side-Effect Policy]")
    for script in sorted(SCRIPTS_DIR.glob("*.py")):
        text = script.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) < 800, f"{script.name} exceeds 800 lines ({len(lines)})"
        for section in required_sections:
            assert section in text, f"{script.name} missing mandatory header section {section}"

    for fpath in REPO_ROOT.rglob("*"):
        if (
            fpath.is_file()
            and fpath.suffix in {".py", ".json", ".md", ".toml", ".svg"}
            and not any(part in {".pytest_cache", ".venv", ".git"} for part in fpath.parts)
        ):
            raw = fpath.read_bytes()
            assert b"\r\n" not in raw, f"CRLF line endings found in {fpath.relative_to(REPO_ROOT)}"


def test_flexible_component_update_baseline_and_atomic_rebind(tmp_path, monkeypatch):
    from mindie_knowledge.component_runtime import source_baseline
    import component_update
    import setup

    baseline = source_baseline(REPO_ROOT)
    assert set(baseline["runtime"]) == {
        "mindie-knowledge",
        "remote-dev",
        "mindie-diagnostics",
        "mindie-coordinator",
        "mindie-debugger",
        "mindie-profiler",
        "mindie-sim",
        "npu-top",
    }
    assert all(len(rev) == 40 for rev in baseline["runtime"].values())

    cfg_file = tmp_path / "config" / "agy.json"
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(tmp_path / "data"))
    setup.ensure_default_profile(config=cfg_file, workspace_root=tmp_path)

    fake_py = tmp_path / "new-venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.write_text("#!/bin/sh\n", encoding="utf-8")

    fake_artifact = {
        "generation": str(tmp_path / "gen-1"),
        "source": str(REPO_ROOT),
        "python": str(fake_py),
        "components": {
            "schema": "mindie-component-runtime/1",
            "adapter_source_sha256": baseline["source_sha256"],
            "runtime": {
                "mindie-knowledge": {
                    "repository": "mindie-agent/knowledge",
                    "version": "0.13.99",
                    "revision": "a" * 40,
                }
            },
            "lock_sha256": "b" * 64,
        },
        "installed": {
            "mindie-knowledge": {
                "repository": "mindie-agent/knowledge",
                "version": "0.13.99",
                "revision": "a" * 40,
            }
        },
    }
    monkeypatch.setattr(component_update, "prepare", lambda *a, **kw: fake_artifact)
    res = component_update.check(config=cfg_file, source=REPO_ROOT)
    assert res["status"] == "updated"
    assert Path(res["python"]).resolve() == fake_py.resolve()

    rebound = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert Path(rebound["python"]).resolve() == fake_py.resolve()
    assert rebound["component_runtime"]["runtime"]["mindie-knowledge"]["version"] == "0.13.99"


def test_enterprise_transport_env_and_staged_source_baseline(tmp_path, monkeypatch):
    import paths
    import setup

    env = paths.apply_enterprise_transport_env({})
    assert env["UV_SYSTEM_CERTS"] == "true"
    assert env["UV_NATIVE_TLS"] == "true"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_0"] == "core.longpaths"
    assert env["GIT_CONFIG_VALUE_0"] == "true"
    assert env["GIT_CONFIG_KEY_1"] == "core.autocrlf"
    assert env["GIT_CONFIG_VALUE_1"] == "false"

    crlf_src = tmp_path / "crlf-src"
    crlf_src.mkdir()
    for name in ("pyproject.toml", "product-contract.json", "harness-compatibility.json"):
        raw = (REPO_ROOT / name).read_bytes().replace(b"\n", b"\r\n")
        (crlf_src / name).write_bytes(raw)
    staged_dst = tmp_path / "data" / "agy-source"
    result_dir = setup.stage_source_baseline(crlf_src, staged_dst)
    assert result_dir == staged_dst.resolve()
    for name in ("pyproject.toml", "product-contract.json", "harness-compatibility.json"):
        assert b"\r\n" not in (staged_dst / name).read_bytes()

    mounted_plugin = tmp_path / "gemini" / "config" / "plugins" / "mindie-agent"
    mounted_plugin.mkdir(parents=True)
    monkeypatch.setenv("AGY_PLUGIN_ROOT", str(mounted_plugin))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(tmp_path / "config"))
    assert paths.repo_root() == staged_dst.resolve()


def test_windows_subprocess_creationflags_prevent_console_window(tmp_path, monkeypatch):
    import subprocess
    import sys
    import bounded
    import component_update

    seen_bounded: list[dict] = []
    real_popen = subprocess.Popen

    def spy_popen(argv, **kwargs):
        seen_bounded.append(dict(kwargs))
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy_popen)
    out = bounded.run([sys.executable, "-c", "print('ok')"], timeout=5.0)
    assert out.returncode == 0
    assert seen_bounded
    if bounded.os.name == "nt":
        flags = seen_bounded[-1]["creationflags"]
        assert flags & getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    cfg_file = tmp_path / "agy.json"
    cfg_file.write_text(json.dumps({"python": sys.executable}), encoding="utf-8")
    monkeypatch.delenv("MINDIE_CONFIG_HOME", raising=False)
    monkeypatch.setattr(component_update, "os", type("NT", (), {"name": "nt", "environ": {}}))
    bg_calls: list[dict] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda argv, **kwargs: bg_calls.append(dict(kwargs)),
    )
    assert component_update.trigger_background_update_if_due(config=cfg_file) is True
    assert len(bg_calls) == 1
    bg_flags = bg_calls[0]["creationflags"]
    assert bg_flags & getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert bg_flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    assert not (bg_flags & getattr(subprocess, "DETACHED_PROCESS", 0x00000008))


def _hide_mindie_knowledge(monkeypatch):
    import setup

    real_find = setup.importlib.util.find_spec

    def missing_knowledge(name, package=None):
        if name == "mindie_knowledge":
            return None
        return real_find(name, package)

    monkeypatch.setattr(setup.importlib.util, "find_spec", missing_knowledge)
    monkeypatch.delenv("_MINDIE_AGY_SETUP_BOOTSTRAPPED", raising=False)
    return setup


def test_help_and_dry_run_do_not_bootstrap_runtime(tmp_path, monkeypatch, capsys):
    import setup

    setup_mod = _hide_mindie_knowledge(monkeypatch)
    boot: list[str] = []

    def fail_stage(*_args, **_kwargs):
        boot.append("stage")
        raise AssertionError("stage_source_baseline")

    def fail_runtime(*_args, **_kwargs):
        boot.append("runtime")
        raise AssertionError("ensure_runtime_python")

    def fail_run(*_args, **_kwargs):
        boot.append("subprocess")
        raise AssertionError("subprocess.run")

    monkeypatch.setattr(setup_mod, "stage_source_baseline", fail_stage)
    monkeypatch.setattr(setup_mod, "ensure_runtime_python", fail_runtime)
    monkeypatch.setattr(setup_mod.subprocess, "run", fail_run)

    for help_argv in (["--help"], ["-h"], ["init", "--help"], ["sharing", "--help"]):
        with pytest.raises(SystemExit) as exc:
            setup_mod.main(help_argv)
        assert exc.value.code in (0, None)
    with pytest.raises(SystemExit) as exc:
        setup_mod.main(["--not-a-flag"])
    assert exc.value.code == 2
    help_out = capsys.readouterr().out
    assert "--dry-run" in help_out
    assert "agy-venv" in help_out

    assert setup_mod.main(["--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["status"] == "dry-run"
    assert dry["cmd"] == "init"
    assert dry["harness"] == "antigravity"
    assert dry["would_bootstrap_runtime"] is True
    assert dry["writes"] == []
    assert dry["contracts"]["harness"] == "antigravity"

    assert setup_mod.main(
        ["init", "--dry-run", "--workspace-root", str(tmp_path), "--domain", "vllm-ascend"]
    ) == 0
    dry_init = json.loads(capsys.readouterr().out)
    assert dry_init["cmd"] == "init"
    assert dry_init["domain"] == "vllm-ascend"
    assert dry_init["workspace_root"] == str(tmp_path)
    assert dry_init["writes"] == []

    assert setup_mod.main(["sharing", "--dry-run", "--disable"]) == 0
    dry_share = json.loads(capsys.readouterr().out)
    assert dry_share["cmd"] == "sharing"
    assert dry_share["writes"] == []
    assert dry_share["sharing"]["disable"] is True
    assert dry_share["sharing"]["applies_changes"] is False

    data = Path(os.environ["MINDIE_DATA_HOME"])
    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    assert not (data / "agy-venv").exists()
    assert not (data / "agy-source").exists()
    assert not cfg.exists()
    assert boot == []


def test_init_still_bootstraps_when_knowledge_is_missing(monkeypatch):
    import subprocess
    import sys

    setup_mod = _hide_mindie_knowledge(monkeypatch)
    seen: dict = {}

    def fake_stage(*_args, **_kwargs):
        seen["stage"] = True
        return Path(os.environ["MINDIE_DATA_HOME"]) / "agy-source"

    def fake_runtime(*_args, **_kwargs):
        seen["runtime"] = True
        return sys.executable

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["bootstrapped"] = kwargs.get("env", {}).get("_MINDIE_AGY_SETUP_BOOTSTRAPPED")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(setup_mod, "stage_source_baseline", fake_stage)
    monkeypatch.setattr(setup_mod, "ensure_runtime_python", fake_runtime)
    monkeypatch.setattr(setup_mod.subprocess, "run", fake_run)

    assert setup_mod.main(["init", "--workspace-root", "/tmp/workspace"]) == 0
    assert seen["stage"] is True
    assert seen["runtime"] is True
    assert seen["bootstrapped"] == "1"
    assert seen["argv"][-3:] == ["init", "--workspace-root", "/tmp/workspace"]


def test_abbreviated_dry_run_never_writes_consent_or_config(tmp_path, monkeypatch, capsys):
    """Argparse treats --dry as --dry-run. That preview must not bootstrap or record consent."""
    import setup

    calls: list[str] = []

    def reject(name):
        def _inner(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(name)

        return _inner

    monkeypatch.setattr(setup, "configure_community", reject("configure_community"))
    monkeypatch.setattr(setup, "ensure_default_profile", reject("ensure_default_profile"))
    monkeypatch.setattr(setup, "stage_source_baseline", reject("stage_source_baseline"))
    monkeypatch.setattr(setup, "ensure_runtime_python", reject("ensure_runtime_python"))
    monkeypatch.setattr(setup.subprocess, "run", reject("subprocess.run"))

    accepted = (
        ["--dry"],
        ["--dr"],
        ["--dry-r"],
        ["--dry-ru"],
        ["--d"],
        ["--dry-run", "init"],
        ["--dry", "init", "--domain", "vllm-ascend"],
        ["init", "--dry", "--workspace-root", str(tmp_path), "--domain", "demo"],
        ["init", "--dr"],
        ["sharing", "--dry", "--enable"],
        ["sharing", "--dry-r", "--enable"],
        ["sharing", "--dr", "--enable", "--fork", "user/knowledge-vllm-ascend"],
        ["sharing", "--enable", "--dry"],
        ["--dry", "sharing", "--enable"],
        ["--dr", "sharing", "--disable"],
        ["--dry-run", "sharing", "--enable"],
    )
    for argv in accepted:
        assert setup.main(list(argv)) == 0, argv
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "dry-run", argv
        assert payload["writes"] == [], argv
        if "sharing" in argv:
            assert payload["cmd"] == "sharing", argv
            assert payload["sharing"]["applies_changes"] is False
            if "--enable" in argv:
                assert payload["sharing"]["enable"] is True
            if "--disable" in argv:
                assert payload["sharing"]["disable"] is True
        elif "init" not in argv:
            assert payload["cmd"] == "init", argv

    for argv in (
        ["init", "--d"],
        ["sharing", "--d", "--enable"],
        ["sharing", "--dry=true", "--enable"],
        ["init", "--domain", "--dry"],
        ["sharing", "--fork", "--dry"],
    ):
        with pytest.raises(SystemExit) as exc:
            setup.main(argv)
        assert exc.value.code == 2, argv
        capsys.readouterr()

    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    data = Path(os.environ["MINDIE_DATA_HOME"])
    assert calls == []
    assert not cfg.exists()
    assert not (cfg.parent / "mindie-consent.json").exists()
    assert not (cfg.parent / "mindie-community.json").exists()
    assert not (cfg.parent / "agy.engine.json").exists()
    assert not (data / "agy-venv").exists()
    assert not (data / "agy-source").exists()

    def record_sharing(**kwargs):
        calls.append("configure_community")
        return {"schema": "mindie-community-config/1", "enabled": kwargs["enabled"]}

    monkeypatch.setattr(setup, "configure_community", record_sharing)
    monkeypatch.setenv("_MINDIE_AGY_SETUP_BOOTSTRAPPED", "1")
    assert setup.main(["sharing", "--disable"]) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["enabled"] is False
    assert calls == ["configure_community"]
    assert not (cfg.parent / "mindie-consent.json").exists()

    _hide_mindie_knowledge(monkeypatch)
    monkeypatch.setattr(setup, "configure_community", reject("configure_community"))
    monkeypatch.setattr(setup, "stage_source_baseline", reject("stage_source_baseline"))
    monkeypatch.setattr(setup, "ensure_runtime_python", reject("ensure_runtime_python"))
    calls.clear()
    assert setup.main(["sharing", "--dry", "--enable"]) == 0
    hidden = json.loads(capsys.readouterr().out)
    assert hidden["status"] == "dry-run"
    assert hidden["would_bootstrap_runtime"] is True
    assert hidden["writes"] == []
    assert hidden["sharing"]["enable"] is True
    assert hidden["sharing"]["applies_changes"] is False
    assert calls == []
    assert not cfg.exists()
    assert not (cfg.parent / "mindie-consent.json").exists()


def test_git_config_count_composition_and_sharing_contract_safety(tmp_path: Path, monkeypatch, capsys):
    import paths
    import setup

    pre_env = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.proxy",
        "GIT_CONFIG_VALUE_0": "http://proxy.internal:8080",
        "GIT_CONFIG_KEY_1": "core.longpaths",
        "GIT_CONFIG_VALUE_1": "false",
    }
    composed = paths.apply_enterprise_transport_env(pre_env)
    assert composed["GIT_CONFIG_COUNT"] == "4"
    assert composed["GIT_CONFIG_KEY_0"] == "http.proxy"
    assert composed["GIT_CONFIG_VALUE_0"] == "http://proxy.internal:8080"
    assert composed["GIT_CONFIG_KEY_1"] == "core.longpaths"
    assert composed["GIT_CONFIG_VALUE_1"] == "false"
    assert composed["GIT_CONFIG_KEY_2"] == "core.autocrlf"
    assert composed["GIT_CONFIG_VALUE_2"] == "false"
    assert composed["GIT_CONFIG_KEY_3"] == "credential.https://github.com.provider"
    assert composed["GIT_CONFIG_VALUE_3"] == "github"

    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    consent_file = cfg.parent / "mindie-consent.json"

    for bad_argv in (["sharing"], ["sharing", "--enable", "--disable"], ["sharing", "--status", "--enable"]):
        with pytest.raises(SystemExit) as exc:
            setup.main(bad_argv)
        assert exc.value.code == 2
        capsys.readouterr()
    assert not consent_file.exists()

    # sharing --status runs in pure stdlib without bootstrapping runtime
    setup_mod = _hide_mindie_knowledge(monkeypatch)
    monkeypatch.setattr(setup_mod, "ensure_runtime_python", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("no bootstrap")))
    assert setup_mod.main(["sharing", "--status"]) == 0
    status_out = json.loads(capsys.readouterr().out)
    assert status_out["schema"] == "mindie-sharing-status/1"
    assert status_out["harness"] == "antigravity"
    assert status_out["consent_choice"] is None
    assert status_out["sharing_enabled"] is False
    assert status_out["needs_onboarding_prompt"] is True
    assert not consent_file.exists()

    # Restore find_spec and verify invalid repository fails before writing consent
    monkeypatch.undo()
    with pytest.raises(ValueError, match="invalid repository format"):
        setup.configure_community(enabled=True, repository="invalid-repo", project_root=tmp_path)
    assert not consent_file.exists()

    with pytest.raises(ValueError, match="visibility='public'"):
        setup.configure_community(enabled=True, visibility="private", project_root=tmp_path)
    assert not consent_file.exists()


def test_temp_checkout_script_paths_rebind_to_agy_source(tmp_path: Path, monkeypatch):
    import shutil
    import setup

    temp_checkout = tmp_path / "mindie-agent-agy-install"
    temp_scripts = temp_checkout / "plugins" / "mindie-agent" / "scripts"
    temp_scripts.mkdir(parents=True)
    for name in ("pyproject.toml", "product-contract.json", "harness-compatibility.json"):
        shutil.copy2(REPO_ROOT / name, temp_checkout / name)
    for script in SCRIPTS_DIR.glob("*.py"):
        shutil.copy2(script, temp_scripts / script.name)

    monkeypatch.setenv("AGY_PLUGIN_ROOT", str(temp_checkout / "plugins" / "mindie-agent"))
    monkeypatch.setattr(setup, "__file__", str(temp_scripts / "setup.py"))

    cfg_file = tmp_path / "config" / "agy.json"
    prof = setup.ensure_default_profile(config=cfg_file, workspace_root=tmp_path)
    eng_data = json.loads((tmp_path / "config" / "agy.engine.json").read_text(encoding="utf-8"))

    assert "mindie-agent-agy-install" not in prof["runtime_scripts"]
    assert "mindie-agent-agy-install" not in prof["source_root"]
    assert "mindie-agent-agy-install" not in eng_data["transcript_adapter"]
    assert all("mindie-agent-agy-install" not in part for part in eng_data["summary_command"])
    version = json.loads((Path(prof["source_root"]) / "plugins/mindie-agent/plugin.json").read_text(encoding="utf-8"))["version"]
    assert Path(prof["source_root"]).parent.resolve() == (Path(os.environ["MINDIE_DATA_HOME"]) / "agy-source").resolve()
    assert Path(prof["source_root"]).name.startswith(version + "-")




