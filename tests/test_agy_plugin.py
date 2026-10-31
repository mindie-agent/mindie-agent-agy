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
    assert "mindie-knowledge" in product["components"]
    assert "remote-dev" in product["components"]
    assert (PLUGIN_ROOT / "skills" / "mindie-agent" / "SKILL.md").is_file()
    assert (SCRIPTS_DIR / "organizer.py").is_file()

    compat = json.loads((REPO_ROOT / "harness-compatibility.json").read_text(encoding="utf-8"))
    assert compat["schema"] == "mindie-harness-compatibility/1"
    assert set(compat["used_interfaces"]) == set(compat["interface_registry"])

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
