"""Frozen-contract, seed, compatibility, and thin-script invariant tests."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "mindie-agent"
SCRIPTS = PLUGIN / "scripts"
HOOK_SHA = "33b564c78b439205fd4acfe095c83b15770043ff462d636e2a89f14620cdfc3f"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _symbol_exists(relative: str, root: Path) -> bool:
    file_part, _, symbol = relative.partition("::")
    if not symbol:
        file_part, _, symbol = file_part.partition(":")
    path = root / file_part
    if not path.is_file():
        return False
    if not symbol:
        return True
    node: object = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name in symbol.split("."):
        node = next(
            (child for child in ast.iter_child_nodes(node)
             if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name),
            None,
        )
        if node is None:
            return False
    return True


def test_plugin_manifest_and_root_hooks_layout() -> None:
    hooks = (PLUGIN / "hooks.json").read_bytes()
    assert len(hooks) == 979
    assert hashlib.sha256(hooks).hexdigest() == HOOK_SHA
    assert not (PLUGIN / "hooks" / "hooks.json").exists()
    manifest = _load(PLUGIN / "plugin.json")
    assert manifest["name"] == "mindie-agent"
    assert manifest["version"] == "0.1.22"
    contract = _load(ROOT / "product-contract.json")
    assert contract["native_version"] == "0.1.22"
    assert contract["components"]["mindie-harness"] == "0.1.6"
    assert contract["components"]["mindie-debugger"] == "0.1.4"
    assert contract["components"]["npu-top"] == "0.3.5"
    assert len(contract["components"]) == 9
    assert 'version = "0.1.22"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (PLUGIN / "runtime-requirements.txt").is_file()
    assert len((PLUGIN / "runtime-requirements.txt").read_text(encoding="utf-8").splitlines()) == 10


def test_mcp_config_uses_agy_profile_and_clean_script_args() -> None:
    servers = _load(PLUGIN / "mcp_config.json")["mcpServers"]
    remote, tools = servers["mindie-remote-dev"], servers["mindie-tools"]
    assert remote["env"]["REMOTE_DEV_HARNESS_PROFILE"] == "agy"
    assert "-c" not in remote["args"] and "-c" not in tools["args"]
    assert remote["args"][-3:] == ["scripts/mindie_launch.py", "mcp", "remote"]
    assert tools["args"][-3:] == ["scripts/mindie_launch.py", "mcp", "tools"]


def test_seed_bundle_verifies_and_rejects_tampering(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import mindie_launch

    lock = mindie_launch.verify_seed_bundle(PLUGIN)
    assert lock["schema"] == "mindie-harness-seed/1"
    assert lock["version"] == "0.1.6"
    copied = tmp_path / "plugin"
    shutil.copytree(PLUGIN / "assets", copied / "assets")
    pyz = copied / "assets" / "mindie-harness-seed.pyz"
    pyz.write_bytes(pyz.read_bytes() + b"tamper")
    with pytest.raises(ValueError):
        mindie_launch.verify_seed_bundle(copied)


def test_harness_compatibility_symbols_and_ast() -> None:
    from mindie_knowledge.releases import validate_compatibility

    compatibility = _load(ROOT / "harness-compatibility.json")
    validate_compatibility(compatibility, harness_name="antigravity")
    assert compatibility["harness"] == "antigravity"
    assert "summary.structured-output" not in compatibility["used_interfaces"]
    assert set(compatibility["used_interfaces"]) == set(compatibility["interface_registry"])
    for entry in compatibility["interface_registry"].values():
        for code_path in entry["code_paths"]:
            assert _symbol_exists(code_path, ROOT), code_path
        for test_id in entry["test_ids"]:
            assert _symbol_exists(test_id, ROOT), test_id


def test_thin_script_invariants() -> None:
    scripts = sorted(SCRIPTS.glob("*.py"))
    assert len(scripts) == 6
    required = ("[Role]", "[Invariants]", "[Timeouts & Locks]", "[Side-Effect Policy]")
    total = 0
    for path in scripts:
        text = path.read_text(encoding="utf-8")
        total += len(text.splitlines())
        assert "\r" not in text, path
        if path.name != "__init__.py":
            header = "\n".join(text.splitlines()[:30])
            assert all(section in header for section in required), path
            assert len(text.splitlines()) < 800, path
    assert total < 800, total
    assert len((SCRIPTS / "mcp_server.py").read_text(encoding="utf-8").splitlines()) < 50
    for path in scripts:
        source_text = path.read_text(encoding="utf-8")
        assert "rpc_knowledge" not in source_text, path
        assert "knowledge_unavailable" not in source_text, path
        tree = ast.parse(source_text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Call,)) and isinstance(node.func, ast.Attribute) and node.func.attr in {"run", "Popen"}:
                assert any(keyword.arg == "creationflags" for keyword in node.keywords), (path, node.lineno)
            if isinstance(node, ast.Try):
                had_timeout = False
                for handler in node.handlers:
                    caught = ast.unparse(handler.type) if handler.type is not None else "BaseException"
                    if "TimeoutError" in caught:
                        had_timeout = True
                    if ("OSError" in caught or caught == "Exception") and not had_timeout:
                        pytest.fail(f"{path}:{handler.lineno} catches {caught} without a preceding TimeoutError")
        forbidden = ("CREATE_" + "NEW_CONSOLE", "DETACHED_" + "PROCESS")
        assert not any(token in path.read_text(encoding="utf-8") for token in forbidden)
