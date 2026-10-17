# 更新日志 / Changelog

本项目的所有重要变更记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划中

- 根据 v0.1 的早期使用反馈补充扫描/轨迹规则与最小修复模式。
- 在更多真实 LangGraph/LangChain 组合故障上校准误报率，并公开可复现的测试样例。
- 评估对更多 LangGraph 版本与常见编排封装的兼容性。

## [0.1.0] - 2026-10-17

首个公开版本。项目从内部评测档案重新定位为面向公开开发者的
**LangGraph/LangChain 组合故障诊断工具**。

### Added

- 新增 `acr` 命令行入口，提供五组命令：
  - `acr scan PATH [--json] [--fail-on high|review|never]`：基于 Python AST 静态扫描 LangGraph 构图代码。
  - `acr trace TRACE [--json]`：读取 JSON 数组、JSONL 或 LangGraph debug stream，定位运行期组合故障。
  - `acr report --scan FILE --trace FILE -o report.md`：合并扫描与轨迹结果，生成单页 Markdown 报告。
  - `acr demo`：运行内置的 LangGraph 子图输出泄漏示例，演示 broken → 捕获错误 → 扫描定位 → 最小修复的完整链路。
  - `acr rules`：列出规则 ID、标题与严重度。
- 静态扫描规则 `ACR001`–`ACR006`：子图输出契约、并行写同一状态键、子图作为节点未声明 `output_schema`、缓存与副作用、重试与副作用、节点内隐藏状态读写。每条结果包含规则 ID、严重度、文件行号、证据、最小修复建议与文档链接。
- 轨迹诊断规则 `ACR101`–`ACR105`，覆盖重复工具调用、同一节点重复写、同一步并行写同键、错误后部分写入、最终状态丢键，并输出时间线与证据事件 ID。
- 新增可运行示例 `examples/langgraph-subgraph-leak/`，用 LangGraph #6446 类问题演示父图/子图共享状态键导致的 `InvalidUpdateError` 及 `output_schema` 最小修复。
- 新增 `install.sh`：优先 `uv tool install`，其次 `pipx install`，最后 `python3 -m pip install --user`；支持 `ACR_VERSION`，安装后运行 `acr --version` 验证，不使用 `sudo`。
- 新增 `LICENSE`（MIT）、`SECURITY.md`、本 `CHANGELOG.md`。
- 新增 `.github/workflows/package.yml`：在 Python 3.10/3.12/3.13 上安装 `.[dev,langgraph]`、运行 `pytest`、构建 sdist/wheel 并通过 `twine check`。
- 新增中文 Issue 表单 `.github/ISSUE_TEMPLATE/bug_report.yml` 与 `feature_request.yml`，要求最小复现、版本信息与脱敏轨迹；保留原有 `case.yml`。
- 重写 `skills/agent-composition-review/SKILL.md`，定位收窄到 LangGraph/LangChain 组合故障，流程改为 `acr scan`/`acr trace` → 验证 → 最小修复 → 回归测试 → 报告，并新增规则与修复模式参考文档。

### Changed

- 项目定位从“Agent 架构诊断研究 + 评测档案”调整为“可直接安装使用的组合故障诊断工具”。
- 根 `README.md` 改写为中文主文档：一句话定位、症状清单、60 秒 demo、真实案例、安装方式、命令表、规则表、限制与评测档案链接。
- 许可证由未选择改为 MIT，仓库由私有改为公开。
- 旧评测历史从根 README 移至 `docs/evaluation-history.md`，根 README 保留入口链接。

### Removed

- 根 README 不再以模型评测结果开篇；评测细节改为归档文档，内容不删除、不改写结论。

### Notes

- 旧评测档案（`evals/`、`docs/`、`assets/` 及相关历史记录）继续保留在仓库中，包括负结果与停止规则说明，便于复核。
- **v0.1 不宣称 benchmark 提升。** 本版本是工具与文档的重新定位，不是新的模型能力评测；历史实验结论保持原样，不应被解读为 v0.1 的收益证据。
- `acr scan`/`acr trace` 的规则是可解释的启发式检查：不确定的情况只报 `review` 级别，可能仍有误报，请结合证据人工确认。

[Unreleased]: https://github.com/meiluosi/agent-composition-review/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/meiluosi/agent-composition-review/releases/tag/v0.1.0
