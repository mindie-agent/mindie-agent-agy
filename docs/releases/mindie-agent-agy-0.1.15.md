# mindie-agent-agy 0.1.15：发布附件缓存审查补修

- 使用共享 knowledge 0.13.21，组件校验和与基准 JSON 附件在大小、摘要和内容验证通过后才进入缓存；旧坏附件可恢复，强制刷新覆盖附件。
- 非法 Content-Length 在 HTTP 边界分类，本地解析与附件完整性故障继续可报告。
- Astra 独立复审通过；实际原生宿主会话未切换，不修改产品历史通道或用户授权。

关联诊断：[mindie-agent#354](https://github.com/mindie-agent/mindie-agent/issues/354)。本版追加修复，不覆盖已发布的 0.1.14。
