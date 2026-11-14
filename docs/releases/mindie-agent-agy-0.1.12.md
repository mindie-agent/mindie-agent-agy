# mindie-agent-agy 0.1.12

[English](mindie-agent-agy-0.1.12.en.md)

- **Windows 11（CP936 / PowerShell 5.1 / Smart App Control）与 WSL2 全链路兼容加固及三端 CI 门禁（`paths.py`、`bounded.py`、`mindie_launch.py`、`knowledge_cli.py`、`organizer.py`）**：新增 `which_native` 过滤 WSL2 `/mnt/<drive>` Windows 互操作二进制与 Windows `WindowsApps` 0 字节存根并自动发现 WinGet `uv.exe` 与用户级 Git/GitHub CLI/Gitleaks；使用标准库 `ctypes` Win32 Job Object（为 `hook` 会话保留后台自动更新进程存活并在超时场景完整回收子进程树）替换失效的 `pywin32` 分支；全链路子进程补充 `-I -X utf8`、`PYTHONUTF8=1` 与 `CREATE_NO_WINDOW`；`knowledge_cli.py` 与 `bridge.py` 改用跨平台 `--session` / `--input-file` 参数消除 POSIX shell 前缀依赖；修复 UNC `file://` 路径解析、Windows 盘符大小写归一化与 HTTPS 发送前后的状态诚实分类，并将 `ubuntu-latest`、`macos-latest`、`windows-latest` 全量接入 CI 门禁。
