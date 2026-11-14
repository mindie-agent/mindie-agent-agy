"""Tier 0 & Tier 1 tests for AGY background updater PID tracking, sharing status, and launcher re-exec budgets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_background_update_spawn_failure_records_failed_not_in_progress(tmp_path, monkeypatch):
    import json as json_mod
    import subprocess
    import sys
    import component_update
    import paths

    cfg_file = tmp_path / "agy.json"
    cfg_file.write_text(json_mod.dumps({"python": sys.executable}), encoding="utf-8")
    monkeypatch.delenv("MINDIE_CONFIG_HOME", raising=False)

    def boom(*_args, **_kwargs):
        raise OSError("cannot spawn updater")

    monkeypatch.setattr(subprocess, "Popen", boom)
    with pytest.raises(OSError, match="cannot spawn updater"):
        component_update.trigger_background_update_if_due(config=cfg_file)
    status = json_mod.loads((paths.data_home() / "component-update-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["status"] != "in_progress"
    assert "OSError" in status["error"]
    assert "cannot spawn updater" in status["error"]


def test_stale_in_progress_without_live_pid_is_reclaimed(tmp_path, monkeypatch):
    import json as json_mod
    import subprocess
    import sys
    import time
    import component_update
    import paths

    cfg_file = tmp_path / "agy.json"
    cfg_file.write_text(json_mod.dumps({"python": sys.executable}), encoding="utf-8")
    monkeypatch.delenv("MINDIE_CONFIG_HOME", raising=False)
    status_file = paths.data_home() / "component-update-status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    win_kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"], **win_kw)
    dead.wait()
    status_file.write_text(
        json_mod.dumps({"checked_at": time.time(), "status": "in_progress", "pid": dead.pid}),
        encoding="utf-8",
    )
    spawned: list[int] = []

    class FakeProc:
        def __init__(self, argv, **kwargs):
            self.pid = 4242
            spawned.append(self.pid)

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    assert component_update.trigger_background_update_if_due(config=cfg_file) is True
    assert spawned == [4242]
    updated = json_mod.loads(status_file.read_text(encoding="utf-8"))
    assert updated["status"] == "in_progress"
    assert updated["pid"] == 4242

    live_status = {"checked_at": time.time(), "status": "in_progress", "pid": os.getpid()}
    status_file.write_text(json_mod.dumps(live_status), encoding="utf-8")
    spawned.clear()
    assert component_update.trigger_background_update_if_due(config=cfg_file) is False
    assert spawned == []
    still = json_mod.loads(status_file.read_text(encoding="utf-8"))
    assert still["status"] == "in_progress"
    assert still["pid"] == os.getpid()


@pytest.mark.skipif(os.name == "nt", reason="POSIX zombie waitpid reap")
def test_zombie_in_progress_pid_is_reaped_and_allows_retry(tmp_path, monkeypatch):
    import json as json_mod
    import subprocess
    import sys
    import time
    import component_update
    import paths

    cfg_file = tmp_path / "agy.json"
    cfg_file.write_text(json_mod.dumps({"python": sys.executable}), encoding="utf-8")
    monkeypatch.delenv("MINDIE_CONFIG_HOME", raising=False)
    status_file = paths.data_home() / "component-update-status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    zombie = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    deadline = time.monotonic() + 2.0
    saw_zombie = False
    while time.monotonic() < deadline:
        proc_status = Path(f"/proc/{zombie.pid}/status")
        if proc_status.is_file():
            text = proc_status.read_text(encoding="utf-8")
            if "\nState:\tZ" in text:
                saw_zombie = True
                break
        else:
            try:
                os.kill(zombie.pid, 0)
                time.sleep(0.05)
                saw_zombie = True
                break
            except ProcessLookupError:
                break
        time.sleep(0.02)
    if not saw_zombie:
        pytest.skip("could not observe unreaped child")
    os.kill(zombie.pid, 0)
    status_file.write_text(
        json_mod.dumps(
            {
                "checked_at": time.time() - 86400,
                "status": "in_progress",
                "pid": zombie.pid,
            }
        ),
        encoding="utf-8",
    )
    spawned: list[int] = []

    class FakeProc:
        def __init__(self, argv, **kwargs):
            self.pid = 4243
            spawned.append(self.pid)

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    assert component_update.trigger_background_update_if_due(config=cfg_file) is True
    assert spawned == [4243]
    updated = json_mod.loads(status_file.read_text(encoding="utf-8"))
    assert updated["status"] == "in_progress"
    assert updated["pid"] == 4243


def test_status_write_timeout_after_spawn_kills_untracked_updater(tmp_path, monkeypatch):
    import json as json_mod
    import subprocess
    import sys
    import component_update
    import paths

    cfg_file = tmp_path / "agy.json"
    cfg_file.write_text(json_mod.dumps({"python": sys.executable}), encoding="utf-8")
    monkeypatch.delenv("MINDIE_CONFIG_HOME", raising=False)
    spawn_kwargs: dict = {}
    if os.name == "nt":
        spawn_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        spawn_kwargs["stdin"] = subprocess.DEVNULL
        spawn_kwargs["stdout"] = subprocess.DEVNULL
        spawn_kwargs["stderr"] = subprocess.DEVNULL
    else:
        spawn_kwargs["start_new_session"] = True
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **spawn_kwargs)

    class WrapProc:
        def __init__(self, argv, **kwargs):
            self.pid = live.pid
            self.returncode = None

        def poll(self):
            rc = live.poll()
            self.returncode = rc
            return rc

        def kill(self):
            live.kill()
            self.returncode = live.poll()

        def wait(self, timeout=None):
            rc = live.wait(timeout=timeout)
            self.returncode = rc
            return rc

    monkeypatch.setattr(subprocess, "Popen", WrapProc)
    writes = {"n": 0}
    real_write = component_update.atomic_write_json

    def gated_write(*args, **kwargs):
        writes["n"] += 1
        if writes["n"] == 1:
            raise TimeoutError("status write timed out")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(component_update, "atomic_write_json", gated_write)
    try:
        with pytest.raises(TimeoutError, match="status write timed out"):
            component_update.trigger_background_update_if_due(config=cfg_file)
        assert live.poll() is not None
        status = json_mod.loads((paths.data_home() / "component-update-status.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert status["status"] != "in_progress"
    finally:
        if live.poll() is None:
            live.kill()
            live.wait(timeout=1)


def test_pid_running_on_windows_does_not_use_os_kill_signal_zero(monkeypatch):
    import ctypes
    import component_update

    monkeypatch.setattr(component_update.os, "name", "nt")

    def boom_kill(*_args, **_kwargs):
        raise ValueError("invalid signal")

    monkeypatch.setattr(component_update.os, "kill", boom_kill)
    opened: list[int] = []

    class FakeApi:
        def __init__(self, fn):
            self.fn = fn
            self.argtypes = None
            self.restype = None

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

    class FakeKernel32:
        def __init__(self):
            self.last_error = 0
            self.OpenProcess = FakeApi(self._open)
            self.GetExitCodeProcess = FakeApi(self._exit)
            self.CloseHandle = FakeApi(lambda _h: 1)

        def _open(self, _access, _inherit, pid):
            opened.append(pid)
            return 42

        def _exit(self, _handle, out):
            out._obj.value = 259
            return 1

    fake = FakeKernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: fake, raising=False)
    assert component_update._pid_running(4242) is True
    assert opened == [4242]


def test_pid_running_windows_access_denied_counts_as_alive(monkeypatch):
    import ctypes
    import component_update

    monkeypatch.setattr(component_update.os, "name", "nt")

    class FakeApi:
        def __init__(self, fn):
            self.fn = fn
            self.argtypes = None
            self.restype = None

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

    class FakeKernel32:
        def __init__(self):
            self.OpenProcess = FakeApi(lambda *_a, **_k: 0)
            self.GetExitCodeProcess = FakeApi(lambda *_a, **_k: 0)
            self.CloseHandle = FakeApi(lambda *_a, **_k: 1)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: FakeKernel32(), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    assert component_update._pid_running(4242) is True


def test_pid_running_windows_query_timeout_or_oserror_assumes_alive(monkeypatch):
    import ctypes
    import component_update

    monkeypatch.setattr(component_update.os, "name", "nt")

    class FakeApi:
        def __init__(self, fn):
            self.fn = fn
            self.argtypes = None
            self.restype = None

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

    class TimeoutKernel:
        def __init__(self):
            self.OpenProcess = FakeApi(lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("OpenProcess query timed out")))
            self.GetExitCodeProcess = FakeApi(lambda *_a, **_k: 0)
            self.CloseHandle = FakeApi(lambda *_a, **_k: 1)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: TimeoutKernel(), raising=False)
    assert component_update._pid_running(4242) is True

    class OsErrorKernel:
        def __init__(self):
            self.OpenProcess = FakeApi(lambda *_a, **_k: (_ for _ in ()).throw(OSError("OpenProcess query failed")))
            self.GetExitCodeProcess = FakeApi(lambda *_a, **_k: 0)
            self.CloseHandle = FakeApi(lambda *_a, **_k: 1)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: OsErrorKernel(), raising=False)
    assert component_update._pid_running(4242) is True


def test_inventory_read_failure_is_failed_not_up_to_date(tmp_path, monkeypatch):
    import component_update
    import setup

    cfg_file = tmp_path / "config" / "agy.json"
    setup.ensure_default_profile(config=cfg_file, workspace_root=tmp_path)
    monkeypatch.setattr(component_update, "prepare", lambda *a, **kw: None)

    def boom(*_args, **_kwargs):
        raise OSError("inventory sqlite locked")

    monkeypatch.setattr("mindie_knowledge.component_runtime.read_inventory", boom)
    result = component_update.check(config=cfg_file, source=REPO_ROOT)
    assert result["status"] == "failed"
    assert result["status"] != "up_to_date"
    assert "OSError" in result["inventory_error"]
    assert "inventory sqlite locked" in result["inventory_error"]
    status = json.loads((Path(os.environ["MINDIE_DATA_HOME"]) / "component-update-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert "inventory sqlite locked" in status["error"]


def test_inventory_timeout_is_failed_and_reraised(tmp_path, monkeypatch):
    import component_update
    import setup

    cfg_file = tmp_path / "config" / "agy.json"
    setup.ensure_default_profile(config=cfg_file, workspace_root=tmp_path)
    monkeypatch.setattr(component_update, "prepare", lambda *a, **kw: None)
    monkeypatch.setattr(
        "mindie_knowledge.component_runtime.read_inventory",
        lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("inventory read timed out")),
    )
    with pytest.raises(TimeoutError, match="inventory read timed out"):
        component_update.check(config=cfg_file, source=REPO_ROOT)
    status = json.loads((Path(os.environ["MINDIE_DATA_HOME"]) / "component-update-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert "TimeoutError" in status["error"]


def test_sharing_status_marks_corrupt_configs_as_repair_required(tmp_path, monkeypatch):
    import setup

    cfg = Path(os.environ["MINDIE_AGENT_CONFIG"])
    comm = cfg.parent / "mindie-community.json"
    diag = Path(os.environ["MINDIE_DIAGNOSTICS_CONFIG"])
    comm.write_text("{not-json", encoding="utf-8")
    status = setup.sharing_status()
    assert status["community_state"] == "corrupt"
    assert status["repair_required"] is True
    assert status["sharing_enabled"] is None
    assert status["community_enabled"] is None
    assert status["sharing_enabled"] is not False
    assert status["repository"] is None
    assert status["needs_onboarding_prompt"] is False

    comm.unlink()
    diag.write_text("{not-json", encoding="utf-8")
    diag_status = setup.sharing_status()
    assert diag_status["diagnostics_state"] == "corrupt"
    assert diag_status["repair_required"] is True
    assert diag_status["reporting_enabled"] is None
    assert diag_status["reporting_enabled"] is not False

    diag.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(setup, "read_json", lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("config read timed out")))
    with pytest.raises(TimeoutError, match="config read timed out"):
        setup.sharing_status()


def test_configure_community_does_not_swallow_reporting_activation(tmp_path, monkeypatch):
    import mindie_knowledge.consent_store as consent_store
    import setup

    monkeypatch.setattr(
        consent_store,
        "activate_reporting",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("reporting backend down")),
    )
    with pytest.raises(RuntimeError, match="reporting backend down"):
        setup.configure_community(
            enabled=True,
            fork="user/knowledge-vllm-ascend",
            project_root=tmp_path,
        )


def test_hook_reexec_passes_exec_budget_timeout_and_mcp_stays_unbounded(monkeypatch):
    import subprocess
    import mindie_launch

    seen: list[dict] = []

    class FakeProc:
        def __init__(self, argv, **kwargs):
            self.pid = 2_000_000_001
            self.returncode = 0
            seen.append({"argv": list(argv), "kwargs": dict(kwargs), "mode": "popen"})

        def wait(self, timeout=None):
            seen[-1]["wait_timeout"] = timeout
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def fake_run(argv, **kwargs):
        seen.append({"argv": list(argv), "timeout": kwargs.get("timeout"), "mode": "run"})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: "/tmp/other-python")
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mindie_launch.main(["hook", "session-start"]) == 0
    assert seen[0]["mode"] == "popen"
    assert seen[0]["wait_timeout"] == mindie_launch.HOOK_EXEC_BUDGET
    assert seen[0]["wait_timeout"] != mindie_launch.HOOK_TOTAL
    if os.name == "nt":
        flags = seen[0]["kwargs"].get("creationflags") or 0
        assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        assert seen[0]["kwargs"].get("start_new_session") is True
    assert mindie_launch.main(["mcp", "tools"]) == 0
    assert seen[1]["mode"] == "run"
    assert seen[1]["timeout"] is None
    assert mindie_launch.main(["setup", "init"]) == 0
    assert seen[2]["mode"] == "popen"
    import setup as setup_mod

    inner_setup = setup_mod.BOOTSTRAP_TIMEOUT + setup_mod.BOOTSTRAP_CLEANUP_GRACE
    default_outer = mindie_launch._setup_outer_budget(mindie_launch.BOOTSTRAP_TOTAL)
    assert seen[2]["wait_timeout"] == default_outer
    assert seen[2]["wait_timeout"] > inner_setup
    assert mindie_launch.main(["setup", "--timeout", "700", "init"]) == 0
    assert seen[3]["mode"] == "popen"
    prep = 700.0
    nested = 700.0 + setup_mod.BOOTSTRAP_CLEANUP_GRACE
    old_single_segment = 700.0 + (2 * mindie_launch.BOOTSTRAP_CLEANUP_GRACE)
    assert seen[3]["wait_timeout"] == mindie_launch._setup_outer_budget(700.0)
    assert seen[3]["wait_timeout"] >= prep + nested
    assert seen[3]["wait_timeout"] > old_single_segment
    assert mindie_launch.main(["update", "--timeout", "700"]) == 0
    assert seen[4]["wait_timeout"] == 700 + (2 * mindie_launch.BOOTSTRAP_CLEANUP_GRACE)


def test_setup_inner_reexec_uses_cli_timeout(monkeypatch):
    import importlib.util
    import subprocess
    import sys
    import setup as setup_mod

    seen: list[float | None] = []
    real_find = importlib.util.find_spec

    def gated_find(name, *args, **kwargs):
        if name == "mindie_knowledge":
            return None
        return real_find(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", gated_find)
    monkeypatch.setattr(
        setup_mod,
        "ensure_runtime_python",
        lambda *a, **k: seen.append(k.get("timeout")) or sys.executable,
    )
    monkeypatch.setattr(setup_mod, "stage_source_baseline", lambda *_a, **_k: Path("/tmp/agy-source"))

    def fake_run(argv, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("_MINDIE_AGY_SETUP_BOOTSTRAPPED", raising=False)
    assert setup_mod.main(["--timeout", "700", "init"]) == 0
    assert seen[0] == 700
    assert seen[1] == 700 + setup_mod.BOOTSTRAP_CLEANUP_GRACE
    assert seen[1] != setup_mod.BOOTSTRAP_TIMEOUT + setup_mod.BOOTSTRAP_CLEANUP_GRACE


def test_setup_outer_budget_covers_prep_plus_nested_under_timeout_700(monkeypatch):
    import subprocess
    import mindie_launch
    import setup as setup_mod

    prep = 700.0
    nested = 700.0 + setup_mod.BOOTSTRAP_CLEANUP_GRACE
    old_outer = 700.0 + (2 * mindie_launch.BOOTSTRAP_CLEANUP_GRACE)
    work = prep + nested
    seen: list[float | None] = []

    class SequentialProc:
        def __init__(self, argv, **kwargs):
            self.pid = 2_000_000_002
            self.returncode = 0

        def wait(self, timeout=None):
            seen.append(timeout)
            if timeout is None or timeout < work:
                raise subprocess.TimeoutExpired(cmd=["python"], timeout=timeout)
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: "/tmp/other-python")
    monkeypatch.setattr(subprocess, "Popen", SequentialProc)
    assert mindie_launch.main(["setup", "--timeout", "700", "init"]) == 0
    assert seen == [mindie_launch._setup_outer_budget(700.0)]
    assert seen[0] >= work
    assert seen[0] > old_outer
    assert old_outer < work


def test_hook_reexec_timeout_emits_host_readable_json_and_exit_0(monkeypatch, capsys):
    import subprocess
    import mindie_launch

    class TimeoutProc:
        def __init__(self, argv, **kwargs):
            self.pid = 2_000_000_001
            self.returncode = None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd=["python"], timeout=timeout)

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: "/tmp/other-python")
    monkeypatch.setattr(subprocess, "Popen", TimeoutProc)

    assert mindie_launch.main(["hook", "pre-tool-use"]) == 0
    denied = json.loads(capsys.readouterr().out)
    assert denied["decision"] == "deny"
    assert denied.get("decision") != "allow"
    assert "TimeoutExpired" in denied["reason"]

    assert mindie_launch.main(["hook", "stop"]) == 0
    stopped = json.loads(capsys.readouterr().out)
    assert stopped.get("decision") != "continue"
    assert "injectSteps" in stopped
    assert "TimeoutExpired" in stopped["injectSteps"][0]["ephemeralMessage"]


def test_hook_reexec_timeout_error_emits_host_readable_json_and_exit_0(monkeypatch, capsys):
    import subprocess
    import mindie_launch

    class TimeoutProc:
        def __init__(self, argv, **kwargs):
            self.pid = 2_000_000_001
            self.returncode = None

        def wait(self, timeout=None):
            raise TimeoutError("reexec wait timed out")

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(mindie_launch, "_preferred_python", lambda: "/tmp/other-python")
    monkeypatch.setattr(subprocess, "Popen", TimeoutProc)

    assert mindie_launch.main(["hook", "pre-tool-use"]) == 0
    denied = json.loads(capsys.readouterr().out)
    assert denied["decision"] == "deny"
    assert denied.get("decision") != "allow"
    assert "TimeoutError" in denied["reason"]
    assert "TimeoutExpired" not in denied["reason"]
