# mindie-agent-agy 0.1.13

[English](mindie-agent-agy-0.1.13.en.md)

- **Windows 11 Smart App Control（`WinError 4551`）组件调用兼容与 `npu_top` 模块入口修正（`mcp_server.py`、`paths.py`、`bounded.py`、`component_update.py`、`tests/test_runtime_upgrade.py`）**：`mcp_server._resolve_component_command` 在优先检索活动虚拟环境 `Scripts`/`bin` 目录后，针对 Windows 平台未签名 `pip`/`uv` `.exe` 入口跳板（触发 `WinError 4551`）统一回退为官方签名的 `python -I -X utf8 -m <module>` 直接执行，并将 `npu_top` 模块名修正为标准 `npu_top`；`paths.which_native` 在 WSL2 下升级为逐目录检测并精准拦截 LF npm `cmd-shim`、CRLF `#!` 与 PE 二进制；`bounded.run` 增加流式输出上限熔断与 `taskkill` 状态校验；`component_update.py` 补齐父子进程跨平台状态文件互斥锁与 `TimeoutError` 透传，并兼容 Python 3.11 的 `test_runtime_upgrade.py` 只读目录清理。
