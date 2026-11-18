"""Thin resident Antigravity MCP delegator over `mindie_harness` (`< 50` lines).
[Role] Serves the 12-tool `remote` (`agy` profile) and deterministic `tools` surfaces.
[Invariants] Zero pre-call session binding; `REMOTE_DEV_HARNESS_PROFILE=agy` is pinned.
[Timeouts & Locks] Concurrency, framing, and deadlines are owned by `mindie_harness`.
[Side-Effect Policy] Dispatches tools in-process via `ManagedMCPAdapter`; never touches host state.
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from typing import Any
from mindie_harness.api import ProfileRef, serve_mcp
from mindie_harness.mcp.gateway import ManagedMCPAdapter, rebind_native_plugin_if_needed
from mindie_harness.mcp.knowledge_schemas import KNOWLEDGE_NAMES
from mindie_harness.mcp.router import RequestContext
from bridge import rpc_knowledge
class AGYManagedAdapter(ManagedMCPAdapter):
    host = "agy"
    def __init__(self, host: str = "agy", *, profile: ProfileRef | None = None, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        if profile is not None: self._set_defaults(profile)
    @staticmethod
    def _set_defaults(profile: ProfileRef) -> None:
        os.environ.update(REMOTE_DEV_STATE_DIR=str(profile.state_dir / "remote"), REMOTE_DEV_HARNESS_PROFILE="agy")
    def mcp_tools(self, surface: str, profile: ProfileRef) -> list[dict[str, Any]]:
        self._set_defaults(profile)
        return super().mcp_tools(surface, profile)
    def dispatch_tool(self, ctx: RequestContext, arguments: dict[str, Any], profile: ProfileRef) -> dict[str, Any]:
        self._set_defaults(profile)
        result = super().dispatch_tool(ctx, arguments, profile)
        if ctx.tool_name in KNOWLEDGE_NAMES and result.get("isError") and (result.get("structuredContent") or {}).get("reason") == "knowledge_unavailable":
            return rpc_knowledge(ctx, arguments, profile) or result
        return result
def serve(surface: str, *, config: str | Path | None = None) -> int:
    if surface not in {"remote", "tools"}: raise ValueError(f"unsupported MCP surface: {surface}")
    profile = ProfileRef.resolve("agy", plugin_root=Path(__file__).resolve().parent.parent, config_path=config or os.environ.get("MINDIE_AGENT_CONFIG"))
    adapter = AGYManagedAdapter(profile=profile, plugin_root=str(profile.plugin_root), config_path=str(profile.config_path), data_dir=str(profile.data_dir))
    try: rebind_native_plugin_if_needed(profile)
    except TimeoutError: raise
    except Exception: pass
    return serve_mcp(surface, profile=profile, adapter=adapter, managed=True)
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    surface = argv[0] if argv else "tools"
    if surface not in {"remote", "tools"}: print(f"unsupported MCP surface: {surface}", file=sys.stderr); return 2
    return serve(surface)
if __name__ == "__main__":
    raise SystemExit(main())
