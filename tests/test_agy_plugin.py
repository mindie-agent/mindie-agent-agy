"""Tier 0 tests for Antigravity native plugin layout, contracts, and engineering invariants."""

from __future__ import annotations

import json
from pathlib import Path


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
        if fpath.is_file() and fpath.suffix in {".py", ".json", ".md", ".toml", ".svg"} and ".pytest_cache" not in fpath.parts:
            raw = fpath.read_bytes()
            assert b"\r\n" not in raw, f"CRLF line endings found in {fpath.relative_to(REPO_ROOT)}"


def test_flexible_component_update_baseline_and_atomic_rebind(tmp_path, monkeypatch):
    from mindie_knowledge.component_runtime import source_baseline
    import component_update
    import setup

    baseline = source_baseline(REPO_ROOT)
    assert set(baseline["runtime"]) == {"mindie-knowledge", "remote-dev", "mindie-diagnostics"}
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

