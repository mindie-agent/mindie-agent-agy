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
    """Strictly isolate all MindIE, Windows AppData, XDG, and Antigravity paths per test."""
    cfg_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    appdata = tmp_path / "appdata" / "Roaming"
    localappdata = tmp_path / "appdata" / "Local"
    agy_home = tmp_path / "gemini" / "antigravity"
    home = tmp_path / "home"
    for folder in (cfg_home, data_home, state_home, cache_home, appdata, localappdata, agy_home, home):
        folder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
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
        config_path=cfg_home / "agy.json",
    )
