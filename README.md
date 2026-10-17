# Agent Composition Review (ACR)

[![package](https://github.com/meiluosi/agent-composition-review/actions/workflows/package.yml/badge.svg)](https://github.com/meiluosi/agent-composition-review/actions/workflows/package.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Local, no-API-key composition diagnostics for LangGraph/LangChain.

`acr` 帮你定位“每个节点单独跑都正常、拼成图之后才坏”的问题：子图覆盖父图状态、并行写同一键、节点返回未声明字段、resume 后重复副作用。每条 finding 都落到**文件行号或轨迹事件 ID**，并给出最小修复建议。

```text
acr scan     静态分析构图代码
acr trace    分析一次失败轨迹
acr report   把 scan + trace 合并成一页 Markdown
acr demo     本地跑通一个 #6446 类故障（broken → scan → 修复 → 通过）
acr rules    列出全部规则与 grounded/experimental 状态
```

**当前状态（v0.5.1，维护模式）**：功能已冻结在 v0.5.1；优先处理真实误报、兼容性和文档问题，不主动增加新规则。它不是运行时追踪器，不宣称 benchmark 或生产准确率。

## 30 秒开始

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.5.1"
pipx inject agent-composition-review "langgraph>=1.0,<2"   # 仅 acr demo 需要，scan/trace 可跳过

acr --version
acr scan path/to/graph.py
acr demo
```

其他安装方式：

```sh
# uv
uv tool install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.5.1"

# pip
python3 -m pip install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.5.1"

# 源码
git clone https://github.com/meiluosi/agent-composition-review.git
cd agent-composition-review
python3 -m pip install -e ".[langgraph]"
```

`[langgraph]` 只影响 `acr demo` 的示例运行；`scan` / `trace` / `report` 不依赖 LangGraph。`install.sh` 可自动检测 `uv` / `pipx` / `pip`。

## 它抓什么、不抓什么

适合：

- 子图把完整状态当补丁回传，覆盖父图字段；
- 并行分支写同一状态键，没有 reducer；
- 节点返回未声明键，LangGraph 静默过滤，下游读不到；
- 缓存/重试/resume 相关的可疑组合结构；
- 失败轨迹中的重复工具调用、部分写入、丢字段。

不适合：

- 自动采集线上运行（`trace` 需要你提供 trace 文件）；
- command-send、RemoteGraph、动态 `Command` 等运行时语义；
- 证明“线上一定有 bug”或给出准确率承诺；
- 非 Python 构图代码。

**规则命中是线索，不是根因判决。** 先看 evidence，再决定修不修。

## 命令

| 命令 | 作用 | 常用参数 |
| --- | --- | --- |
| `acr scan PATH` | 静态分析构图代码（Python AST） | `--json`；`--sarif`；`-o FILE`；`--fail-on high\|review\|never` |
| `acr trace TRACE` | 分析失败轨迹，输出时间线与事件 ID | `--json`；`--resume-at SEQ`；`--infer-resume` |
| `acr report` | 合并 scan/trace 为一页 Markdown | `--scan FILE --trace FILE -o report.md` |
| `acr demo` | 本地跑 #6446 类示例 | 需要 `[langgraph]` |
| `acr rules` | 规则表 + grounded/experimental 状态 | — |

示例：

```sh
acr scan examples/langgraph-subgraph-leak/broken.py
acr trace run.jsonl --resume-at 4        # debug 流没有 resume 事件时，指向恢复前最后一条事件
acr scan . --sarif -o acr.sarif          # GitHub code scanning
```

`--fail-on` 用于 CI：`high` 只在 high finding 时非零退出，`review` 把 medium 也算失败，`never` 永不因 finding 失败。finding 统一包含 `rule`、`severity`、`status`、`file`、`line`、`evidence`、`fix`、`docs`。

## GitHub Action / SARIF

最小静态检查：

```yaml
- uses: actions/checkout@v4
- uses: meiluosi/agent-composition-review@v0.5.1
  with:
    path: .
    fail-on: high
```

上传到 GitHub code scanning（需要权限）：

```yaml
permissions:
  contents: read
  security-events: write
steps:
  - uses: actions/checkout@v4
  - id: acr
    uses: meiluosi/agent-composition-review@v0.5.1
    with:
      path: .
      fail-on: high
      upload-sarif: true
  - run: echo "ACR findings: ${{ steps.acr.outputs.findings }}"
```

Action 只跑 `acr scan`，不执行被扫描代码、不需要 API key；支持 `findings` / `sarif-file` outputs。完整说明见 [GitHub Action 文档](docs/github-action.md)。

## 规则与证据分层

共 16 条规则：`acr scan` 10 条（ACR001–ACR010），`acr trace` 6 条（ACR101–ACR106）。每条都有独立说明页，finding 的 `docs` 字段是仓库相对路径。

`acr rules` 的 STATUS：

- **grounded**：至少一个真实公开/本地复现样本检查过规则行为 —— 目前是 **ACR001、ACR002、ACR003、ACR007、ACR106**。
- **experimental**：已实现，但只有设计案例、邻近案例或合成 fixture；其余规则都属于这一层，**不要当成已验证能力**。

| 规则 | 抓什么 | 级别 | 状态 |
| --- | --- | --- | --- |
| ACR001 | 子图与父图共享状态键且未声明 `output_schema` | high | grounded |
| ACR002 | 并行分支写同一键且没有 reducer | high | grounded |
| ACR003 | 子图作为节点但未声明 `output_schema` | medium | grounded |
| ACR004 | 副作用节点/图被缓存包裹 | medium | experimental |
| ACR005 | 副作用节点启用 retry | medium | experimental |
| ACR006 | 节点内通过 checkpointer 读写图状态 | low | experimental |
| ACR007 | 节点返回未声明键，写入被静默过滤 | medium | grounded |
| ACR008 | cache_policy 的 callable 身份可能不稳定 | medium | experimental |
| ACR009 | retry_policy 节点内调用多个子图 | medium | experimental |
| ACR010 | 条件自环写非 reducer 键 | medium | experimental |
| ACR101 | 相同参数与结果的工具重复调用 | medium | experimental |
| ACR102 | 同一节点重复执行并重复写同一键 | medium | experimental |
| ACR103 | 同一步并行节点写同一键 | high | experimental |
| ACR104 | 状态已部分写入后抛错 | high | experimental |
| ACR105 | 最终状态缺少输入中存在的键 | medium | experimental |
| ACR106 | resume/replay 后重启已完成节点 | medium | grounded |

级别含义：`high` 很可能是真实组合故障；`medium` 需要人工确认；`low` 是提示。规则说明在 [docs/rules/](docs/rules/)。

## 真实案例图谱

按“症状 → 规则 → 证据 → 最小修复 → 验证边界”整理的 11 个真实案例：

- 在线搜索页：<https://meiluosi.github.io/agent-composition-review/atlas/>
- 仓库索引：[docs/atlas/README.md](docs/atlas/README.md)

其中值得先看的：

- [LG #6446](docs/atlas/lg-6446.md)：子图回传完整状态，父图并行写同一个键；
- [LG #8320](docs/atlas/lg-8320.md)：节点返回未声明键，被静默过滤；
- [LG #2581](docs/atlas/lg-2581.md)：Send 到子图返回未声明键（第二批验证命中）；
- [LG #3875](docs/atlas/lg-3875.md) / [LG #9276](docs/atlas/lg-9276.md)：resume 后 interrupt 节点重启 / 已完成节点重跑；
- [vanara bound method](docs/atlas/lg-vanara-bound-method.md)：真实公开代码上的 ACR008 误报，v0.5.1 已修。

## 真实案例验证（不是 benchmark）

- 第一轮：14 个真实 GitHub issue + 3 个正常控制；暴露了子图跨函数解析和 reducer 误判，v0.1.1 修复。报告：[pilot-2026-10-17/REPORT.md](evals/realworld/results/pilot-2026-10-17/REPORT.md)。
- 第二轮：20 个新案例；ACR007 预期 3 例全部命中，但 ACR008/009 本批 0 样本。报告：[pilot2-2026-10-18/REPORT.md](evals/realworld2/results/pilot2-2026-10-18/REPORT.md)。
- 第三轮：定向找 ACR106/008/009/010；ACR106 有真实 trace 支持，ACR008/009/010 仍无独立 exact。报告：[pilot3-2026-10-18/REPORT.md](evals/realworld3/results/pilot3-2026-10-18/REPORT.md)。
- 第四轮：追代码搜索 lead；ACR008 修掉一个真实 bound-method 误报，ACR009/010 仍无 exact。记录：[LEADS_VERIFIED.md](evals/realworld4/LEADS_VERIFIED.md)。

这些样本已参与规则设计/验证，不能当未见保留集，不能用来计算真实世界召回率或宣称 benchmark 提升。

## 工作原理

- **scan**：Python `ast` 解析，识别 `StateGraph` 构造、`add_node`/`add_edge`、`START` 扇出、`Annotated[...]` reducer、cache/retry 参数和节点内 `get_state`/`update_state`。纯本地，不执行代码。
- **trace**：把 JSON 数组、JSONL、统一事件格式或 LangGraph debug stream 归一化为带 `id`/`seq`/`kind` 的事件流，再检查重复调用、重复写入、并行同键、部分写入、最终状态丢键。输出引用具体事件 ID。
- **report**：只合并与排版，不引入新判断。
- 不需要模型、不需要 API key、不上传代码、不内置遥测。

## 限制

- 仅支持 **Python** 构图代码，基于 **AST 静态分析**；工厂循环、`getattr` 拼名、运行时决定边等可能漏报。
- 面向 **LangGraph 1.x**；旧 API 或不同小版本可能识别失败。
- **不是运行时追踪器**：不自动插桩；`trace` 要你提供轨迹。
- **不自动改代码**：`fix` 是文字建议。
- 静态规则是启发式，可能误报；grounded 也只表示“检测行为有真实样本检查”，不表示生产效果。
- 当前没有外部 design partner / 真实用户反馈；不承诺活跃新功能。

## 仓库结构

```text
src/agent_composition_review/          acr CLI 实现
tests/                                 CLI 与规则测试
examples/langgraph-subgraph-leak/      #6446 类可运行示例
docs/rules/                            16 条规则说明
docs/atlas/                            11 个真实案例 + 静态搜索页
docs/github-action.md                  Action 输入/输出/权限说明
docs/evaluation-history.md             旧评测档案（历史证据，非产品承诺）
skills/agent-composition-review/       Agent Skill 入口与参考
evals/                                 真实案例、协议与评测证据（不参与运行时）
scripts/                               仓库检查、评测与维护脚本
```

产品代码（`src/`、`tests/`、`examples/`、`skills/`）与评测档案（`evals/`、`docs/evaluation-history.md`）刻意分开：前者承诺可运行、可回归；后者只承诺保留证据和限制。

## 给 Agent 使用

运行时 Skill 入口：[skills/agent-composition-review/SKILL.md](skills/agent-composition-review/SKILL.md)。它把流程写成 Agent 可执行步骤：判断症状 → `acr scan` / `acr trace` → 读 finding 并验证 → 最小修复 → 加回归测试 → 输出一页报告，并明确要求“证据不足时说不知道”。规则速查与修复模式在 [references/rules.md](skills/agent-composition-review/references/rules.md) 和 [references/fix-patterns.md](skills/agent-composition-review/references/fix-patterns.md)。

## 贡献

欢迎提交：脱敏后的最小故障复现、正常反例（证明某条 finding 是误报）、安装/兼容问题、规则文档改进。

一次改动最好只对应一个问题：

1. 提交最小复现（构图代码、版本、实际报错/轨迹）或误报样例；
2. 说明期望行为与现有 finding 的差异；
3. 修改最小规则或文档，不顺手重构无关代码；
4. 回归旧案例，补一条能复现旧行为的测试；
5. 在 PR 中说明证据：为什么能命中、误报边界是什么。

误报报告和真实修复案例比 star 更有用。流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。

## 许可证

[MIT](LICENSE)。Copyright (c) 2026 Agent Composition Review contributors。
