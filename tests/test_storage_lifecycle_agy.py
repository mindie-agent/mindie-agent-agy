"""Tier 1 storage lifecycle regressions for AGY component updates and MCP leases.

Covers silent reclamation of superseded `agy-state/component-runtimes`
generations, half-finished staging and multi-generation `agy-source` baggage,
external user Git checkout protection, the cross-process update mutex, a real
subprocess holding an `acquire_runtime_lease`, and the zero-half-delete
`PermissionError` backoff of NTFS retirement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "plugins" / "mindie-agent" / "scripts"

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "product-contract.json").is_file(),
    reason="storage lifecycle tests require the AGY plugin payload",
)


def _venv_python(generation: Path) -> Path:
    parts = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return generation / "runtime" / ".venv" / Path(*parts)


def _make_generation(root: Path, name: str, *, mtime: float | None = None,
                     manifest: bool = True, marker: str | None = None) -> Path:
    generation = root / name
    python = _venv_python(generation)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"stub interpreter\n")
    (generation / "runtime" / "site-packages").mkdir(parents=True, exist_ok=True)
    (generation / "runtime" / "site-packages" / "component.py").write_text(
        f"NAME = {marker or name!r}\n", encoding="utf-8")
    if manifest:
        (generation / "components.json").write_text(
            json.dumps({"generation": name, "runtime": {}}, sort_keys=True) + "\n",
            encoding="utf-8")
    if mtime is not None:
        for candidate in (generation, generation / "components.json"):
            if candidate.exists():
                os.utime(candidate, (mtime, mtime))
    return generation


def _write_profile(cfg_file: Path, *, python: Path, source_root: Path,
                   state_dir: Path) -> Path:
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    engine = cfg_file.with_name(cfg_file.stem + ".engine.json")
    engine.write_text(json.dumps({
        "schema": "mindie-engine-config/1",
        "harness": "agy",
        "state_dir": str(state_dir / "engine"),
        "summary_command": [str(python), "-I", "-X", "utf8", str(SCRIPTS / "organizer.py")],
    }, sort_keys=True) + "\n", encoding="utf-8")
    cfg_file.write_text(json.dumps({
        "schema": "mindie-adapter-config/1",
        "harness": "antigravity",
        "python": str(python),
        "engine_config": str(engine),
        "state_dir": str(state_dir),
        "source_root": str(source_root),
        "domain": "vllm-ascend",
    }, sort_keys=True) + "\n", encoding="utf-8")
    return engine


def _artifact(generation: Path) -> dict:
    return {
        "generation": str(generation),
        "source": str(REPO_ROOT),
        "python": str(_venv_python(generation)),
        "components": {
            "schema": "mindie-component-runtime/1",
            "adapter_source_sha256": "a" * 64,
            "lock_sha256": "b" * 64,
            "runtime": {
                "mindie-knowledge": {
                    "repository": "mindie-agent/knowledge",
                    "version": "0.13.22",
                    "revision": "c" * 40,
                }
            },
        },
        "installed": {
            "mindie-knowledge": {
                "repository": "mindie-agent/knowledge",
                "version": "0.13.22",
                "revision": "c" * 40,
            }
        },
    }


def _tree_digest(root: Path) -> list[tuple[str, bytes]]:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rows.append((path.relative_to(root).as_posix(), path.read_bytes()))
    return rows


def _freeze_process_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    """No live process references the synthetic generations; inspections complete."""
    import mindie_knowledge.storage.legacy as legacy
    import mindie_knowledge.storage.leases as leases

    monkeypatch.setattr(leases, "live_process_prefixes", lambda **_kw: (set(), True))
    monkeypatch.setattr(legacy, "live_process_prefixes", lambda **_kw: (set(), True))


def _wait_until(predicate, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _spawn(script: Path, *args: str, stdin=None) -> subprocess.Popen:
    kwargs: dict = dict(
        stdin=subprocess.DEVNULL if stdin is None else stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
    )
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([sys.executable, "-I", "-X", "utf8", str(script), *args], **kwargs)


def _stop(child: subprocess.Popen) -> None:
    from bounded import _kill_process_tree

    try:
        _kill_process_tree(child, None, wait_timeout=3.0)
    finally:
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()


@pytest.fixture
def upgrade_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import paths

    cfg_file = paths.config_path()
    data = paths.data_home()
    runtimes = data / "agy-state" / "component-runtimes"
    runtimes.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external-user-checkout"
    (external / ".git").mkdir(parents=True)
    (external / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (external / "pyproject.toml").write_text("[project]\nname='user-repo'\n", encoding="utf-8")
    victim = _make_generation(external / "component-runtimes", "user-victim",
                              mtime=time.time() - 9000, marker="user-victim")
    monkeypatch.setenv("MINDIE_AGY_SOURCE_ROOT", str(external))
    return dict(cfg_file=cfg_file, data=data, runtimes=runtimes,
                external=external, external_victim=victim)


def test_upgrade_silently_reclaims_legacy_baggage_and_protects_borrowed_and_external(
        upgrade_env, monkeypatch: pytest.MonkeyPatch):
    import component_update

    _freeze_process_inspection(monkeypatch)
    cfg_file = upgrade_env["cfg_file"]
    data = upgrade_env["data"]
    runtimes = upgrade_env["runtimes"]
    external = upgrade_env["external"]
    now = time.time()

    previous = _make_generation(runtimes, "gen-rollback", mtime=now - 500, marker="rollback")
    stale = [
        _make_generation(runtimes, f"gen-stale-{index}",
                         mtime=now - 5000 - index * 10, marker=f"stale-{index}")
        for index in range(5)
    ]
    borrowed = _make_generation(runtimes, "gen-borrowed", mtime=now - 9000, marker="borrowed")
    staging = runtimes / "gen-half-built"
    _make_generation(runtimes, "gen-half-built", mtime=now - 12000,
                     manifest=False, marker="staging")
    assert staging.is_dir()
    active = _make_generation(runtimes, "gen-active", mtime=now, marker="active")

    sources = data / "agy-source"
    sources.mkdir(parents=True, exist_ok=True)
    old_sources = []
    for index, version in enumerate(("0.1.9", "0.1.10", "0.1.11")):
        source = sources / f"{version}-{'d' * 40}"
        source.mkdir()
        (source / "pyproject.toml").write_text("[project]\nname='old'\n", encoding="utf-8")
        os.utime(source, (now - 8000 + index, now - 8000 + index))
        old_sources.append(source)
    active_source = sources / f"0.1.16-{'e' * 40}"
    active_source.mkdir()
    (active_source / "pyproject.toml").write_text("[project]\nname='active'\n", encoding="utf-8")
    retired_state_sources = []
    state_sources = data / "agy-state" / "agy-source"
    state_sources.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        source = state_sources / f"0.1.{index + 2}-{'f' * 40}"
        source.mkdir()
        (source / "pyproject.toml").write_text("[project]\nname='state'\n", encoding="utf-8")
        os.utime(source, (now - 9000 + index, now - 9000 + index))
        retired_state_sources.append(source)

    _write_profile(cfg_file, python=_venv_python(previous), source_root=external,
                   state_dir=data / "agy-state")
    borrowed_cfg = cfg_file.parent / "codex.json"
    borrowed_cfg.write_text(json.dumps({"python": str(_venv_python(borrowed))}) + "\n",
                            encoding="utf-8")
    monkeypatch.setattr(component_update, "prepare", lambda *a, **kw: _artifact(active))
    external_digest = _tree_digest(external)

    result = component_update.check(config=cfg_file, source=external)

    assert result["status"] == "updated"
    assert Path(json.loads(cfg_file.read_text(encoding="utf-8"))["python"]) == _venv_python(active)
    assert result["cleanup"]["prune"]["reclaimed_dirs"] >= 4
    assert result["cleanup"]["prune"]["deferred_dirs"] == 0
    assert result["cleanup"]["sweep"]["reclaimed_dirs"] >= 2
    assert not result.get("receipt_unconfirmed")
    assert result["cleanup"]["status"] == "completed"
    assert "cleanup_warning" not in result

    # Active generation, explicit rollback generation, borrowed sibling host
    # interpreter, the two-newest unprotected rollback window, and the newest
    # historical source survive.
    assert active.is_dir()
    assert previous.is_dir()
    assert borrowed.is_dir()
    assert active_source.is_dir()
    assert sum(1 for source in old_sources if source.is_dir()) == 1
    assert not retired_state_sources[0].exists()
    assert all(source.is_dir() for source in retired_state_sources[1:])
    # The explicit rollback pin plus the newest unprotected stale generation
    # occupy the two-generation retention window; older baggage is reclaimed.
    assert stale[0].is_dir()
    assert not any(gen.exists() for gen in stale[1:])
    assert not staging.exists()
    # The external user Git checkout is byte-for-byte untouched.
    assert _tree_digest(external) == external_digest
    assert upgrade_env["external_victim"].is_dir()

    # A reclaim rooted inside an external Git checkout refuses the whole tree.
    from storage_lifecycle import reclaim_after_switch

    refused = reclaim_after_switch(
        runtimes_root=external / "component-runtimes",
        forbidden_roots=[external],
        budget_seconds=2.0,
    )
    assert refused["prune"]["status"] == "protected_root"
    assert upgrade_env["external_victim"].is_dir()
    assert _tree_digest(external) == external_digest


def test_component_update_check_is_serialized_by_the_cross_process_mutex(
        upgrade_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import component_update
    import paths

    data = upgrade_env["data"]
    cfg_file = upgrade_env["cfg_file"]
    update_runtimes = data / "agy-state" / "component-runtimes"
    rollback = _make_generation(update_runtimes, "gen-rollback", mtime=time.time() - 100)
    active = _make_generation(update_runtimes, "gen-active", mtime=time.time())
    _write_profile(cfg_file, python=_venv_python(rollback), source_root=REPO_ROOT,
                   state_dir=data / "agy-state")

    ready = tmp_path / "lock-ready"
    holder = tmp_path / "hold_update_lock.py"
    holder.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import component_update\n"
        "with component_update.update_lock(Path(sys.argv[2]), timeout=30.0):\n"
        "    Path(sys.argv[3]).write_text('locked', encoding='utf-8')\n"
        "    time.sleep(60)\n",
        encoding="utf-8")
    child = _spawn(holder, str(SCRIPTS), str(paths.data_home() / component_update.UPDATE_LOCK_NAME),
                   str(ready))
    calls: list[str] = []

    def fail_prepare(*_args, **_kwargs):
        calls.append("prepare")
        raise AssertionError("a concurrent check must not build a second generation")

    monkeypatch.setattr(component_update, "prepare", fail_prepare)
    try:
        assert _wait_until(ready.exists), "the lock holder never became ready"
        busy = component_update.check(config=cfg_file, source=REPO_ROOT)
        assert busy["status"] == "busy"
        assert busy["update_lock"] == "held"
        assert busy["status"] != "up_to_date"
        assert calls == []
        assert not (data / "component-update-status.json").exists()
    finally:
        _stop(child)

    monkeypatch.setattr(component_update, "prepare", lambda *a, **kw: _artifact(active))
    updated = component_update.check(config=cfg_file, source=REPO_ROOT)
    assert updated["status"] == "updated"
    assert Path(json.loads(cfg_file.read_text(encoding="utf-8"))["python"]) == _venv_python(active)


def test_real_subprocess_lease_defers_retirement_until_the_process_tree_exits(
        upgrade_env, tmp_path: Path):
    from mindie_knowledge.storage import is_lease_held, prune_component_runtimes
    from mindie_knowledge.storage.leases import windows_lease_present

    root = upgrade_env["runtimes"]
    leased = _make_generation(root, "gen-leased", mtime=time.time() - 7200)
    sibling = _make_generation(root, "gen-sibling", mtime=time.time() - 7300)
    ready = tmp_path / "lease-ready"
    holder = tmp_path / "hold_lease.py"
    holder.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from mindie_knowledge.storage import acquire_runtime_lease\n"
        "with acquire_runtime_lease(Path(sys.argv[2])):\n"
        "    Path(sys.argv[3]).write_text('leased', encoding='utf-8')\n"
        "    time.sleep(60)\n",
        encoding="utf-8")
    child = _spawn(holder, str(SCRIPTS), str(leased), str(ready))

    def held() -> bool:
        return is_lease_held(leased) or windows_lease_present(leased)

    try:
        assert _wait_until(lambda: ready.exists() and held()), "the lease holder never became ready"
        outcome = prune_component_runtimes(
            root, keep_paths=(), keep_recent_verified=0,
            include_global_roots=False, staging_grace_seconds=0.0,
            live_paths=set(), inspection_complete=True, budget_seconds=3.0)
        assert leased.is_dir()
        assert (leased / "components.json").is_file()
        # POSIX reports the live shared lease as kept; Windows sees the private
        # non-share-delete handle as a deferred atomic rename.
        assert outcome["kept_dirs"] + outcome["deferred_dirs"] >= 1
        assert not sibling.exists()
    finally:
        _stop(child)
    assert _wait_until(lambda: not held())
    outcome = prune_component_runtimes(
        root, keep_paths=(), keep_recent_verified=0,
        include_global_roots=False, staging_grace_seconds=0.0,
        live_paths=set(), inspection_complete=True, budget_seconds=3.0)
    assert outcome["reclaimed_dirs"] >= 1
    assert not leased.exists()


def test_permission_error_rename_leaves_generation_intact_without_backoff(
        upgrade_env, monkeypatch: pytest.MonkeyPatch):
    from mindie_knowledge.storage import lifecycle, prune_component_runtimes

    root = upgrade_env["runtimes"]
    victim = _make_generation(root, "gen-in-use", mtime=time.time() - 30, marker="in-use")
    marker = victim / "runtime" / "site-packages" / "component.py"
    before = marker.read_bytes()

    class DeniedOs:
        def __init__(self, real, denied):
            self._real = real
            self._denied = os.path.normcase(os.fspath(denied))

        def __getattr__(self, name):
            return getattr(self._real, name)

        def replace(self, source, target):
            if os.path.normcase(os.fspath(source)) == self._denied:
                raise PermissionError(13, "generation is in use")
            return self._real.replace(source, target)

    monkeypatch.setattr(lifecycle, "os", DeniedOs(os, victim))
    started = time.monotonic()
    outcome = prune_component_runtimes(
        root, keep_paths=(), keep_recent_verified=0,
        include_global_roots=False, staging_grace_seconds=0.0,
        live_paths=set(), inspection_complete=True, budget_seconds=5.0)
    elapsed = time.monotonic() - started

    assert victim.is_dir()
    assert marker.read_bytes() == before
    assert outcome["deferred_dirs"] >= 1
    assert not any(entry.name.startswith(".trash-") for entry in root.iterdir())
    assert elapsed < 2.0  # No retry/backoff loop on a denied atomic rename.


def test_cleanup_permission_error_keeps_status_updated_with_cleanup_warning(
        upgrade_env, monkeypatch: pytest.MonkeyPatch):
    import component_update

    _freeze_process_inspection(monkeypatch)
    cfg_file = upgrade_env["cfg_file"]
    data = upgrade_env["data"]
    runtimes = upgrade_env["runtimes"]
    previous = _make_generation(runtimes, "gen-rollback", mtime=time.time() - 100)
    active = _make_generation(runtimes, "gen-active", mtime=time.time())
    _write_profile(cfg_file, python=_venv_python(previous), source_root=REPO_ROOT,
                   state_dir=data / "agy-state")
    monkeypatch.setattr(component_update, "prepare", lambda *a, **kw: _artifact(active))

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "generation is in use")

    monkeypatch.setattr("mindie_knowledge.storage.prune_component_runtimes", denied)
    result = component_update.check(config=cfg_file, source=REPO_ROOT)

    assert result["status"] == "updated"
    assert "PermissionError" in result["cleanup_warning"]
    assert not result.get("receipt_unconfirmed")
    status = json.loads((data / "component-update-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "updated"
    assert "PermissionError" in status["cleanup_warning"]
    assert Path(json.loads(cfg_file.read_text(encoding="utf-8"))["python"]) == _venv_python(active)


def test_resident_mcp_serve_holds_the_generation_lease(
        upgrade_env, tmp_path: Path):
    from mindie_knowledge.storage import is_lease_held, prune_component_runtimes
    from mindie_knowledge.storage.leases import windows_lease_present

    root = upgrade_env["runtimes"]
    generation = _make_generation(root, "gen-mcp", mtime=time.time() - 7200)
    holder = tmp_path / "run_mcp.py"
    holder.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "scripts, generation = sys.argv[1], Path(sys.argv[2])\n"
        "sys.path.insert(0, scripts)\n"
        "import storage_lifecycle\n"
        "storage_lifecycle.runtime_generation = lambda executable=None: generation\n"
        "import mcp_server\n"
        "sys.argv = ['mcp_server.py', 'tools', '--once']\n"
        "raise SystemExit(mcp_server.main())\n",
        encoding="utf-8")
    child = _spawn(holder, str(SCRIPTS), str(generation), stdin=subprocess.PIPE)

    def held() -> bool:
        return is_lease_held(generation) or windows_lease_present(generation)

    try:
        assert _wait_until(held), "the MCP server never held its generation lease"
        outcome = prune_component_runtimes(
            root, keep_paths=(), keep_recent_verified=0,
            include_global_roots=False, staging_grace_seconds=0.0,
            live_paths=set(), inspection_complete=True, budget_seconds=3.0)
        assert generation.is_dir()
        assert outcome["kept_dirs"] + outcome["deferred_dirs"] >= 1
    finally:
        _stop(child)
    assert _wait_until(lambda: not held())
    prune_component_runtimes(
        root, keep_paths=(), keep_recent_verified=0,
        include_global_roots=False, staging_grace_seconds=0.0,
        live_paths=set(), inspection_complete=True, budget_seconds=3.0)
    assert not generation.exists()


def test_resident_mcp_front_end_leases_before_dispatch(
        upgrade_env, monkeypatch: pytest.MonkeyPatch):
    import mcp_server
    import remote_dev.mcp.catalog_refresh as refresh
    import storage_lifecycle
    from mindie_knowledge.storage import is_lease_held
    from mindie_knowledge.storage.leases import windows_lease_present

    generation = _make_generation(upgrade_env["runtimes"], "gen-front", mtime=time.time() - 7200)
    seen: list[bool] = []

    def fake_serve_selected(select, **_kwargs):
        seen.append(is_lease_held(generation) or windows_lease_present(generation))
        return 0

    monkeypatch.setattr(storage_lifecycle, "runtime_generation",
                        lambda executable=None: generation)
    monkeypatch.setattr(refresh, "serve_selected", fake_serve_selected)
    assert mcp_server.main(["tools"]) == 0
    assert seen == [True]


def test_runtime_generation_maps_only_managed_generation_paths(upgrade_env, tmp_path: Path):
    import storage_lifecycle

    generation = _make_generation(upgrade_env["runtimes"], "gen-map", mtime=time.time())
    assert storage_lifecycle.runtime_generation(_venv_python(generation)) == generation
    plain = tmp_path / "plain-runtime" / "bin" / "python"
    plain.parent.mkdir(parents=True)
    plain.write_bytes(b"stub\n")
    assert storage_lifecycle.runtime_generation(plain) is None


def test_ensure_runtime_python_reuses_usable_profile_generation(monkeypatch, tmp_path):
    import component_update
    import paths
    import setup

    profile_python = tmp_path / "profile" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    profile_python.parent.mkdir(parents=True)
    profile_python.write_bytes(b"stub\n")
    cfg_file = paths.config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps({"python": str(profile_python)}) + "\n", encoding="utf-8")
    monkeypatch.setattr(setup.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(setup, "_imports_runtime", lambda *_a, **_k: True)
    monkeypatch.setattr(setup, "which_native",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("uv bootstrap")))
    monkeypatch.setattr(component_update, "compatible_runtime",
                        lambda python, **_kw: str(python))

    assert Path(setup.ensure_runtime_python(config=cfg_file)) == (
        profile_python.parent.resolve() / profile_python.name)
    assert not (paths.data_home() / "agy-venv").exists()


def test_cold_agy_venv_bootstrap_configures_the_same_volume_cache(
        monkeypatch, tmp_path):
    import paths
    import setup

    venv_dir = paths.data_home() / "agy-venv"
    seen: dict = {}
    calls: list[list[str]] = []

    def spy(target, env):
        seen["target"] = Path(target)
        seen["env"] = dict(env)
        return env

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "stub uv failure"

    monkeypatch.setattr(setup.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(setup, "_profile_python", lambda config: None)
    monkeypatch.setattr(setup, "stage_source_baseline", lambda *a, **k: tmp_path)
    monkeypatch.setattr(setup, "which_native", lambda name: "uv")
    monkeypatch.setattr(setup, "configure_uv_cache", spy)
    monkeypatch.setattr(setup, "bounded_run",
                        lambda argv, stdin, **kw: (calls.append(list(argv)), Failed())[1])

    with pytest.raises(RuntimeError, match="uv sync failed"):
        setup.ensure_runtime_python(config=tmp_path / "agy.json")
    assert seen["target"] == venv_dir
    assert seen["env"]["UV_PROJECT_ENVIRONMENT"] == str(venv_dir)
    assert calls and calls[0][:2] == ["uv", "sync"]


def test_configure_uv_cache_pins_cross_volume_bootstrap_only_when_unset(
        monkeypatch, tmp_path):
    import storage_lifecycle

    target = (tmp_path / "data" / "agy-venv").resolve()
    target.mkdir(parents=True)
    chosen = (target.parent / storage_lifecycle.UV_CACHE_DIRECTORY).resolve()

    def fake_device(path):
        probe = Path(path)
        try:
            probe = probe.resolve()
        except OSError:
            pass
        return 7 if probe in {target, chosen} else 9

    monkeypatch.setattr(storage_lifecycle, "_device", fake_device)
    env = storage_lifecycle._stdlib_uv_cache(target, {})
    assert env["UV_CACHE_DIR"] == str(chosen)

    explicit = {"UV_CACHE_DIR": "/caller/chosen/cache"}
    assert storage_lifecycle._stdlib_uv_cache(target, explicit)["UV_CACHE_DIR"] == "/caller/chosen/cache"
    assert storage_lifecycle.configure_uv_cache(target, dict(explicit))["UV_CACHE_DIR"] == "/caller/chosen/cache"


def test_repository_parent_has_no_upgrade_temp_residue():
    residue = sorted(Path(REPO_ROOT.parent).glob("mindie-upgrade-*"))
    assert residue == []
    assert sorted(REPO_ROOT.glob("mindie-upgrade-*")) == []
