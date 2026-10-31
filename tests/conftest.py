"""Pytest isolation fixtures for `mindie-agent-agy` (Tier 0 & Tier 1)."""

from __future__ import annotations

from pathlib import Path
import sys
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "plugins" / "mindie-agent" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def isolated_mindie_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Strictly isolate all MindIE and Antigravity paths per test."""
    cfg_home = tmp_path / "config"
    data_home = tmp_path / "data"
    agy_home = tmp_path / "gemini" / "antigravity"
    cfg_home.mkdir(parents=True, exist_ok=True)
    data_home.mkdir(parents=True, exist_ok=True)
    agy_home.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(data_home))
    monkeypatch.setenv("MINDIE_AGENT_CONFIG", str(cfg_home / "agy.json"))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_CONFIG", str(cfg_home / "diagnostics.json"))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_ROOT", str(data_home / "diagnostics"))
    monkeypatch.setenv("AGY_APP_DATA_DIR", str(agy_home))
    monkeypatch.setenv("_MINDIE_AGY_REEXEC", "1")
    monkeypatch.delenv("AGY_CONVERSATION_ID", raising=False)
    return dict(
        config_home=cfg_home,
        data_home=data_home,
        agy_home=agy_home,
        config_path=cfg_home / "agy.json",
    )
