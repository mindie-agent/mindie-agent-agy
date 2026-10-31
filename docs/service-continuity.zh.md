# 服务身份与统一授权连续性

<b>中文</b> | <a href="service-continuity.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-agy` 通过 [`setup.py`](../plugins/mindie-agent/scripts/setup.py) 与 [`paths.py`](../plugins/mindie-agent/scripts/paths.py) 管理独立于宿主易失缓存的持久化配置文件，遵循 **One Fact, One Authority（单一事实源）** 原则。

## 1. 配置文件职责划分

| 文件名 | Schema | 核心职责 |
|---|---|---|
| `agy.json` | `mindie-adapter-config/1` | 适配器主配置：绑定 Python 解释器、运行时脚本目录、`agy.engine.json` 与 `admission.sqlite3` 路径 |
| `agy.engine.json` | `mindie-engine-config/1` | `mindie_knowledge` 引擎配置：声明 `transcript_adapter`（`agy_transcript.py`）、`summary_command`（`organizer.py`）与 `redactor_executable` |
| `mindie-community.json` | `mindie-community-config/1` | 社区知识共享策略权威（复用 `mindie_knowledge.loop.settings` 唯一校验器与 `CommunityWriteContext` 文件锁） |
| `mindie-consent.json` | `mindie-consent/1` | 统一贡献与故障上报授权权威（复用 `mindie_knowledge.consent_store.record_choice`） |

## 2. 跨会话持久化与原子更新

- 所有配置文件写入均采用同目录唯一临时文件 + `os.fsync` + `os.replace()` 原子替换。
- 插件升级或重新执行 `setup.py init` 绝不覆盖已保存的 `mindie-community.json` 与 `mindie-consent.json` 授权状态。
