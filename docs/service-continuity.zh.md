# 服务身份与统一授权连续性

<b>中文</b> | <a href="service-continuity.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 通过 [`setup.py`](../plugins/mindie-agent/scripts/setup.py) 与 `mindie_harness.paths.ProfileRef` 管理持久化配置，避免依赖宿主易失缓存，遵循 **One Fact, One Authority**。

## 1. 配置权威

| 文件 | Schema | 职责 |
|---|---|---|
| `agy.json` | `mindie-adapter-config/1` | 适配器绑定：解释器、`runtime_scripts`、`engine_config`、`source_root` 与 `admission.sqlite3` 路径 |
| `agy.engine.json` | `mindie-engine-config/1` | `mindie_knowledge` 引擎配置：绑定 `transcript_adapter`（`agy_transcript.py`）、`redactor_executable`、`root` 与 `admission_path` |
| `mindie-community.json` | `mindie-community-config/1` | 社区贡献权威（经 `mindie_knowledge.loop.settings.CommunityWriteContext` 校验并加锁） |
| `mindie-consent.json` | `mindie-consent/1` | 统一贡献与故障上报授权（`contribute`、`local-only`、`disabled`） |
| `state/agy/pending-update-check.json` | `mindie-pending-update/1` | `< 5ms` SessionStart 更新握手标记，由 `mindie_harness.runtime.activation.write_pending_update_check` 消费 |

## 2. 跨会话持久化与原子替换

- 全部配置写入使用 `mindie_harness.atomic.atomic_write_json`（同目录临时文件 + `os.fsync` + `os.replace()`）。
- 重复执行 `setup.py init` 或升级插件会保留既有 `mindie-community.json` 与 `mindie-consent.json` 选择。
- 代际激活在 `mindie_harness` 锁内**最后**写入 `agy.json`；显式撤销的 admission 租约绝不会被 `Stop` 自动重新激活。
