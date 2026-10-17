# 更新日志 / Changelog

本项目的所有重要变更记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划中

- 扩展 ACR002 到 retry 自环/并行分支重叠写同一无 reducer 键的 review 级检测（ACR010 先覆盖自环，ACR002 保持兄弟扇出语义）。
- 把 command-send / resume-replay / parallel-interrupt 交给 `acr trace` 或版本感知提示，不硬加静态规则。
- 在更多真实 LangGraph/LangChain 组合故障上校准误报率，并公开可复现的测试样例。

## [0.3.1] - 2026-10-18

### Fixed

- ACR106 不再把带 `interrupts` 的 `node_end` 当成“已完成”。真实公开 trace #3875 暴露了该误报：被 interrupt 的节点在 resume 后重启是预期行为，不应报 ACR106。
- `TraceEvent` 增加 `interrupts` 字段，归一化时保留 `task_result.payload.interrupts`；新增回归测试。

### Added

- 第三轮定向验证报告：`evals/realworld3/results/pilot3-2026-10-18/REPORT.md`。
  - ACR106：本地复现 #9276 的正向 trace 命中；真实公开 trace #3875 作为负向控制 0 误报。
  - ACR008：只有 7 条邻近 cache 案例，0 exact；未验证。
  - ACR009/ACR010：没有新 exact 样本，记 `no_sample_found`。

### Notes

- ACR106 正向 trace 是本地复现公开 issue，不是 reporter 附件；且需要显式 resume/replay 标记（LangGraph 默认 debug 流不产生）。
- 本批用于发现 ACR106 误报并修正规则，因此对 ACR106 修复而言是开发集，不是独立验证。
- ACR008/ACR009/ACR010 仍未验证；不要用邻近案例冒充 exact。

## [0.3.0] - 2026-10-18

### Added

- 新增 ACR010：条件自环节点写无 reducer 键，retry/re-entry 可能与并行分支在同一 superstep 冲突。复扫 `lg2-2336` 命中；该案例已用于设计，属开发集。
- 新增 ACR106（trace）：resume/replay 标记后已完成节点再次启动，用于捕捉重复副作用。目前只有合成 trace fixture，未在真实社区 trace 上验证。
- ACR008/ACR009 各补第二个正例/负例 fixture，覆盖 lambda/class node 与无 retry 场景；仍为合成 fixture，不是真实案例验证。
- 第二轮报告补充 `POSTFIX.md`，记录 ACR010/ACR106 与本批的关系，不修改原始 REPORT 数字。

### Notes

- ACR010 是在第二轮 `lg2-2336` 的 ACR002 miss 之后设计的；不能把同一案例当作 ACR010 的独立验证。
- ACR106 需要 trace 中显式写 `resume`/`replay` 事件；没有标记就看不到。
- ACR008/ACR009 仍无真实案例命中，发布说明不宣称已验证。

## [0.2.0] - 2026-10-18

### Added

- 新增 ACR007：检测节点（含 class-based `__call__`）返回 dict 字面量里不在 graph state schema 中的键；这类写入会被 LangGraph 静默过滤。覆盖真实案例 #8320 与 #2644 的形态。
- 新增 ACR008：检测 `cache_policy` 节点使用 callable object / lambda / partial / 未解析参数，cache key 可能失去稳定身份并与共享 cache 串结果。覆盖真实案例 #8753 的形态。
- 新增 ACR009：检测 `retry_policy` 节点内调用多个子图，重试/恢复后可能复用第一个子图的 checkpoint 命名空间。覆盖真实案例 #9253 的形态。
- 解析链式构图赋值（`StateGraph(S).add_node(...).add_edge(...).compile()`），使这类真实写法进入 ACR001/002/007/009 分析。
- 第二轮真实案例验证：20 个新案例（15 GitHub + 3 Forum + 2 Stack Overflow），报告见 `evals/realworld2/results/pilot2-2026-10-18/REPORT.md`。

### Changed

- ACR007 的修复建议补充 class node/私有 schema 场景：优先考虑 `add_node(..., input=...)`，不要盲目把键补进全局 state。

### Notes

- ACR007 在第二轮 3/3 预期案例命中；但 1406 有 3 条 off-rule finding，1950 的 fix 文本场景不匹配（已在 0.2.0 文案中修正）。
- ACR008/ACR009 在第二轮 **0 个预期样本、0 命中，未验证**；不能宣称已验证。
- ACR002 在第二轮 3 个预期案例全部 miss（只建模兄弟扇出，漏 retry 自环重叠）；command-send 是最大 GAP，属运行时语义。
- 第二轮验证是验证批次，不是统计保留集；选择偏向 user_graph/usage，不能据此推断真实分布。

## [0.1.1] - 2026-10-17

### Fixed

- ACR001 现在能解析 helper 返回 compiled graph 的真实形态（`add_node("child", build_child())`、`child = build_child()`），修复了真实案例 #6446 的漏报。
- ACR002 现在会解包 `ReadOnly`/`Required`/`NotRequired` 限定符后再判断 reducer，修复了 `ReadOnly[Annotated[..., operator.add]]` 被误报为 “no reducer” 的问题。
- 新增对应回归测试：helper 返回带/不带 `output_schema` 的图、`ReadOnly` 包裹 reducer 的负例。

### Added

- 真实案例试点：从 LangGraph GitHub issue 收集 14 个真实故障 + 3 个正常控制图，按冻结协议做盲标、离线扫描与逐例判定；报告见 `evals/realworld/results/pilot-2026-10-17/REPORT.md`。结论用于 v0.2 缺口排序，不宣称泛化。
- 修复后同一批开发案例的复扫记录：`evals/realworld/results/pilot-2026-10-17/POSTFIX.md`（#6446 现在命中，`ReadOnly` 误报消失，控制图仍 0 findings）。

### Notes

- 本次补丁只修复规则召回/误报，不改变“静态构图规则不覆盖框架内部运行时 bug”的边界；真实案例中的 resume/replay、parallel interrupt、cache/retry 身份等缺口仍未覆盖。

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

[Unreleased]: https://github.com/meiluosi/agent-composition-review/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/meiluosi/agent-composition-review/releases/tag/v0.3.1
[0.3.0]: https://github.com/meiluosi/agent-composition-review/releases/tag/v0.3.0
[0.2.0]: https://github.com/meiluosi/agent-composition-review/releases/tag/v0.2.0
[0.1.1]: https://github.com/meiluosi/agent-composition-review/releases/tag/v0.1.1
[0.1.0]: https://github.com/meiluosi/agent-composition-review/releases/tag/v0.1.0
