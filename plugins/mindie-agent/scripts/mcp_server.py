"""Thin resident Antigravity MCP delegator over `mindie_harness` (`< 50` lines).
[Role] Serves the 12-tool `remote` (`agy` profile) and deterministic `tools` surfaces.
[Invariants] Zero pre-call session binding; `REMOTE_DEV_HARNESS_PROFILE=agy` is pinned;
rejects `WindowsApps` 0-byte Python stubs and delegates `knowledge_*` to `ManagedMCPAdapter`.
[Timeouts & Locks] Concurrency, framing, and deadlines are owned by `mindie_harness`.
[Side-Effect Policy] Dispatches tools in-process via `ManagedMCPAdapter`; never touches host state.
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from typing import Any
from mindie_harness.api import ProfileRef, serve_mcp
from mindie_harness.atomic import read_json_safe
from mindie_harness.mcp.gateway import ManagedMCPAdapter, rebind_native_plugin_if_needed
from mindie_harness.mcp.router import RequestContext
def _usable_py(raw: object) -> bool:
    if not isinstance(raw, str) or not raw.strip() or "\\microsoft\\windowsapps\\" in raw.replace("/", "\\").lower(): return False
    try: st = os.stat(raw); return Path(raw).is_file() and (os.name != "nt" or st.st_size > 0)
    except TimeoutError: raise
    except OSError: return False
class AGYManagedAdapter(ManagedMCPAdapter):
    host = "agy"
    def __init__(self, host: str = "agy", *, profile: ProfileRef | None = None, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        if profile is not None: self._set_defaults(profile)
    @staticmethod
    def _set_defaults(profile: ProfileRef) -> None:
        os.environ.update(REMOTE_DEV_STATE_DIR=str(profile.state_dir / "remote"), REMOTE_DEV_HARNESS_PROFILE="agy", MINDIE_AGENT_CONFIG=str(profile.config_path))
    def mcp_tools(self, surface: str, profile: ProfileRef) -> list[dict[str, Any]]: self._set_defaults(profile); return super().mcp_tools(surface, profile)
    def dispatch_tool(self, ctx: RequestContext, arguments: dict[str, Any], profile: ProfileRef) -> dict[str, Any]: self._set_defaults(profile); return super().dispatch_tool(ctx, arguments, profile)
def serve(surface: str, *, config: str | Path | None = None) -> int:
    if surface not in {"remote", "tools"}: raise ValueError(f"unsupported MCP surface: {surface}")
    profile = ProfileRef.resolve("agy", plugin_root=Path(__file__).resolve().parent.parent, config_path=config or os.environ.get("MINDIE_AGENT_CONFIG"))
    adapter = AGYManagedAdapter(profile=profile, plugin_root=str(profile.plugin_root), config_path=str(profile.config_path), data_dir=str(profile.data_dir))
    try:
        if _usable_py((read_json_safe(profile.config_path) or {}).get("python")): rebind_native_plugin_if_needed(profile)
    except TimeoutError: raise
    except Exception: pass
    return serve_mcp(surface, profile=profile, adapter=adapter, managed=True)
def main(argv: list[str] | None = None) -> int:
    args, cfg, surface, idx = list(sys.argv[1:] if argv is None else argv), None, None, 0
    while idx < len(args):
        if args[idx] == "--config" and idx + 1 < len(args): cfg = args[idx + 1]; idx += 2
        elif args[idx].startswith("--config="): cfg = args[idx].split("=", 1)[1]; idx += 1
        else: surface = surface or args[idx]; idx += 1
    if (surface := surface or "tools") not in {"remote", "tools"}: print(f"unsupported MCP surface: {surface}", file=sys.stderr); return 2
    return serve(surface, config=cfg)
if __name__ == "__main__": raise SystemExit(main())
