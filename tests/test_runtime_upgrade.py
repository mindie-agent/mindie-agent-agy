"""Setup read-only guarantees, profile lifecycle, and harness generation rebinding."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "mindie-agent"
SCRIPTS = PLUGIN / "scripts"


def _config() -> Path:
    return Path(os.environ["MINDIE_AGENT_CONFIG"])


def test_setup_help_and_dry_run_are_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import setup

    monkeypatch.setenv("MINDIE_REDACTOR_EXECUTABLE", str(tmp_path / "bin" / "gitleaks"))
    for argv in (["--help"], ["-h"], ["--dry-run", "init"], ["--dry", "init"], ["--dry-run", "sharing", "--enable"]):
        assert setup.main(argv) == 0
    assert not _config().exists()
    assert not _config().with_name("agy.engine.json").exists()
    assert not (_config().parent / "mindie-consent.json").exists()
    assert not (_config().parent / "mindie-community.json").exists()


def test_setup_init_sharing_and_release_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import setup

    monkeypatch.setenv("MINDIE_REDACTOR_EXECUTABLE", str(tmp_path / "bin" / "gitleaks"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert setup.main(["init", "--domain", "vllm-ascend"]) == 0
    adapter = json.loads(_config().read_text(encoding="utf-8"))
    engine = json.loads(_config().with_name("agy.engine.json").read_text(encoding="utf-8"))
    assert adapter["harness"] == "antigravity"
    assert adapter["python"] == sys.executable
    assert adapter["runtime_scripts"] == str(SCRIPTS)
    assert adapter["source_root"] == str(PLUGIN)
    assert adapter["product_validation"]["status"] == "validated"
    assert engine["transcript_adapter"] == str(SCRIPTS / "agy_transcript.py")
    assert engine["redactor_executable"] == str(tmp_path / "bin" / "gitleaks")

    assert setup.main(["sharing", "--status"]) == 0
    assert setup.main(["sharing", "--local-only", "--project-root", str(workspace)]) == 0
    consent = json.loads((_config().parent / "mindie-consent.json").read_text(encoding="utf-8"))
    community = json.loads((_config().parent / "mindie-community.json").read_text(encoding="utf-8"))
    assert consent["choice"] == "local-only" and community["local_only"] is True
    assert setup.main(["sharing", "--enable", "--project-root", str(workspace)]) == 0
    consent = json.loads((_config().parent / "mindie-consent.json").read_text(encoding="utf-8"))
    assert consent["choice"] == "contribute"
    assert json.loads((_config().parent / "mindie-community.json").read_text(encoding="utf-8"))["enabled"] is True
    assert setup.main(["sharing", "--disable"]) == 0
    assert json.loads((_config().parent / "mindie-consent.json").read_text(encoding="utf-8"))["choice"] == "disabled"
    assert json.loads((_config().parent / "mindie-community.json").read_text(encoding="utf-8"))["enabled"] is False
    assert setup.main(["release-metadata"]) == 0


def test_harness_generation_activation_and_native_rebinding(tmp_path: Path) -> None:
    from mindie_harness.api import activate_runtime
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.mcp.gateway import rebind_native_plugin_if_needed
    from mindie_harness.paths import ProfileRef
    from mindie_harness.runtime.builder import PreparedGeneration

    profile = ProfileRef.resolve("agy", plugin_root=PLUGIN, config_path=str(_config()), data_dir=os.environ.get("MINDIE_DATA_HOME"))
    generation = profile.generations_dir / "gen-1"
    generation.mkdir(parents=True)
    atomic_write_json(generation / "runtime.json", {"schema": "mindie-candidate-check/1", "validation": {"status": "validated"}, "python": sys.executable})
    prepared = PreparedGeneration(
        status="validated", generation_key="gen-1", generation_dir=generation,
        python_path=Path(sys.executable), receipt_path=generation / "runtime.json", components={},
    )
    activated = activate_runtime(profile, prepared, run_gc=False)
    assert activated.status == "active"
    adapter = json.loads(_config().read_text(encoding="utf-8"))
    assert adapter["component_release"] == str(generation)
    assert adapter["python"] == sys.executable

    adapter["product_validation"] = {"native_version": "0.1.20"}
    adapter["runtime_scripts"] = str(tmp_path / "old-scripts")
    adapter["component_commands"] = {"profiler": [sys.executable, "-I", "-X", "utf8", "-m", "mindie_profiler"]}
    atomic_write_json(_config(), adapter)
    rebound = rebind_native_plugin_if_needed(profile, native_version="0.1.21", runtime_scripts=SCRIPTS)
    assert rebound["status"] == "rebound"
    updated = json.loads(_config().read_text(encoding="utf-8"))
    assert updated["product_validation"]["native_version"] == "0.1.21"
    assert updated["runtime_scripts"] == str(SCRIPTS)
    assert updated["source_root"] == str(PLUGIN)
    assert updated["component_commands"]["profiler"] == [sys.executable, "-I", "-X", "utf8", "-m", "mindie_profiler"]
