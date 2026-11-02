# MindIE Agent for Google Antigravity `v0.1.3` Release Notes

<a href="mindie-agent-agy-0.1.3.md">中文</a> | <b>English</b> · <a href="../README.en.md">Back to Documentation Index</a>

`v0.1.3` upgrades `mindie-agent-agy` to the latest Ascend AI Infra shared component baseline (`knowledge 0.13.10`, `remote-dev 0.11.4`, `diagnostics 0.5.5`, `debugger 0.1.0`, `profiler 0.5.6`, `sim 0.9.2`, `coordinator 0.7.3`, and `npu-top 0.3.1`), ensures forward-compatible Antigravity host version matching, and adds automated GitHub Release publication with reproducible CPU microbenchmarks.

## Highlights (Human-Readable Summary)

1. **Forward-Compatible Host Matching & Clearer Remote CANN Log Triage**:
   - `harness-compatibility.json` covers `2.0.0..2.99.99` (`include_prerelease: true`) so routine Antigravity IDE/CLI updates never break plugin startup or background updates.
   - Adds `mindie-debugger 0.1.0` to `pyproject.toml` and `product-contract.json`, and clarifies the remote NPU container `plog` and ACLGraph triage workflow in the `debugger` MCP tool description.
2. **Upgraded Shared Component Baseline (Including Ascend 950PR / A5 Simulation)**:
   - `mindie-knowledge 0.13.10` (`7936a4fda67f6808d8fa73824363a4d7345766e5`)
   - `remote-dev 0.11.4` (`7c67876d716feb3949e4dbf33eb5e06980ff0d36`)
   - `mindie-diagnostics 0.5.5` (`f2f2f946c172afc99342a84c0c1ed244f6d44e01`)
   - `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`)
   - `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`)
   - `mindie-sim 0.9.2` (`ced2dba5741af2d98f3a75b2a9cad77b9b94c40d`, adding Ascend 950PR / A5 hardware Roofline and superpod topology calibration)
   - `mindie-coordinator 0.7.3` (`79a8b28eef72d57f965a01c6660849f024fbb1c0`)
   - `npu-top 0.3.1` (`8f0cb3ec66393812e90e118cd73704070c6027a2`)
3. **Automated Release Pipeline & Reproducible Benchmark Gate**:
   - Adds `.github/workflows/release.yml` and `benchmarks/release.json` to attach verified `SHA256SUMS` and public-transcript extraction latency benchmarks to every formal release.
