"""Pytest isolation fixtures for `mindie-agent-agy` (Tier 0 & Tier 1)."""

from __future__ import annotations

from pathlib import Path
import sys
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "plugins" / "mindie-agent" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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
