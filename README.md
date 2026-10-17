# Agent Composition Review (ACR)

[![package](https://github.com/meiluosi/agent-composition-review/actions/workflows/package.yml/badge.svg)](https://github.com/meiluosi/agent-composition-review/actions/workflows/package.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

**Find the boundary where your LangGraph agent breaks.**

`acr` 是一个诊断 LangGraph/LangChain **组合故障**的命令行工具。每个节点单独跑都正常，拼成图之后却报 `InvalidUpdateError`、字段被覆盖、工具被重复调用——`acr` 帮你把故障收敛到具体的文件行号或轨迹事件，并给出最小修复建议。

```text
acr scan     静态分析构图代码，找子图输出越界、并行写冲突、重试/缓存副作用
acr trace    分析一次失败轨迹，找重复调用、部分写入、丢字段
acr report   把 scan + trace 合并成一页 Markdown 报告
acr demo     真实跑通一个 LangGraph #6446 类故障：broken → scan → 修复 → 通过
acr rules    列出全部规则
```

v0.1 是早期版本：MIT 许可、本地运行、不需要 API key。**它不宣称 benchmark 提升，也不是运行时追踪器。** 旧版评测历史见 [docs/evaluation-history.md](docs/evaluation-history.md)，那是历史证据档案，不是本产品的效果承诺。

## 你会遇到的那类故障

- 图单独跑正常，组合后报 `InvalidUpdateError: At key 'x': Can receive only one value per step`。
- 子图把完整状态当补丁回传，覆盖父图其他分支刚写的新值（子图覆盖父图状态）。
- 并行分支（fan-out）写同一个状态键，结果取决于谁后完成（并行同键冲突）。
- 重试、恢复或回放让同一个工具被再次调用，产生重复副作用（重复发消息、重复扣款）。
- 缓存命中后不再重新采样；多智能体合并时丢了字段，下游读到空值。
- 节点报错后留下部分写入，重试时状态已经污染。

这类问题的共同点：**结构合法，组合后语义变了**。单看每个节点都没错，错在边界。

## 60 秒 demo

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.4.0"
pipx inject agent-composition-review "langgraph>=1.0,<2"   # acr demo 需要；scan/trace 可跳过
acr demo
```

`acr demo` 会在本地运行一个 LangGraph #6446 风格的 broken 示例，捕获 `InvalidUpdateError`，用 `acr scan` 找到 ACR001，演示一行 `output_schema` 修复，再跑回归确认通过。它需要可选依赖 `[langgraph]`；只跑 `scan` / `trace` / `report` 不需要安装 LangGraph。

真实运行输出（`acr` 0.4.0，本地环境）：

```text
$ acr demo
acr demo - LangGraph subgraph state leak walkthrough (acr 0.4.0)

1) run the broken graph from examples/langgraph-subgraph-leak/broken.py
   x InvalidUpdateError: At key 'b': Can receive only one value per step. Use an Annotated key to handle multiple values.
     (the child returned its full state including the stale 'b', colliding with the sibling write)

2) static analysis of the broken source
   acr scan found 2 finding(s) before the runtime failure:
   HIGH   ACR001  Compiled subgraph shares state keys with the parent graph
           Compiled subgraph registered as node 'child' has no output_schema, and child state 'State' shares 'a', 'b', 's1', 's2' with parent state 'State'. The child's full state is merged back into the parent, so writes from this node can collide with sibling branches.
           - shared state key(s): 'a', 'b', 's1', 's2'
           - parent state 'State' declared at line 21; key line(s): 'a' at line 22, 'b' at line 23, 's1' at line 24, 's2' at line 25
           - ... 3 more evidence item(s)
           fix: Declare ``output_schema=<SubgraphOutput>`` on the child ``StateGraph`` so only intended keys flow back to the parent, or split the shared key into distinct keys.
           docs: docs/rules/ACR001.md
   HIGH   ACR002  Parallel branches write the same state key without a reducer
           After START, branches 'child', 'sibling' can run in the same superstep and all write state key 'b', which has no reducer. LangGraph raises InvalidUpdateError for concurrent writes to a non-reduced key.
           - source START has unconditional edges to 'child', 'sibling' (edge line(s): 53, 54)
           - state key 'b' declared at line 23 with annotation 'str' and no reducer
           - ... 2 more evidence item(s)
           fix: Give the key a reducer, e.g. ``Annotated[list, operator.add]`` (or ``add_messages`` for messages), or make the branches write disjoint keys / run sequentially.
           docs: docs/rules/ACR002.md

3) run the fixed graph from examples/langgraph-subgraph-leak/fixed.py
   ok: graph.invoke() completed without InvalidUpdateError
   final state: {"a": 1, "b": "old|updated", "s1": 4, "s2": -2}
     (child declares output_schema=ChildOutput, so only s1/s2 are merged back; the parallel fan-out is unchanged)

4) static analysis of the fixed source
   acr scan found 0 finding(s)

Summary: acr scan flagged the shared key and the unreduced parallel write before the graph ran; adding output_schema=ChildOutput removes both findings and the run succeeds.
```

## 真实案例：LangGraph #6446

[LangGraph #6446](https://github.com/langchain-ai/langgraph/issues/6446) 是一个真实的父子图并行冲突：父图同时扇出 `sibling`（写 `b`）和子图 `child`（写 `s1`/`s2`），子图**没有声明 `output_schema`**，于是把完整状态（包括自己从未写过的旧 `b`）当更新回传。父图把它和 `sibling` 的新 `b` 放在同一个 superstep 合并，触发冲突。

最小复现与修复在 [examples/langgraph-subgraph-leak/](examples/langgraph-subgraph-leak/)，用 `run.py` 可一次跑完。下面是本地真实运行输出：

```text
$ python examples/langgraph-subgraph-leak/run.py
================================================================
1) broken.py：子图未声明 output_schema，回传完整状态
================================================================
按预期失败：InvalidUpdateError: At key 'b': Can receive only one value per step. Use an Annotated key to handle multiple values.
For troubleshooting, visit: https://docs.langchain.com/oss/python/langgraph/errors/INVALID_CONCURRENT_GRAPH_UPDATE

================================================================
2) fixed.py：子图声明 output_schema=ChildOutput，只写 s1/s2
================================================================
结果：  {'a': 1, 'b': 'old|updated', 's1': 4, 's2': -2}
期望：  {'a': 1, 'b': 'old|updated', 's1': 4, 's2': -2}

通过：broken 复现 InvalidUpdateError (At key 'b')，fixed 输出正确。
```

`acr scan` 一条命令就把边界指到两处：子图输出越界（ACR001），以及同一 superstep 对 `b` 的竞争（ACR002）。真实输出：

```text
$ acr scan examples/langgraph-subgraph-leak/broken.py
acr scan - examples/langgraph-subgraph-leak/broken.py
1 file(s) parsed, 0 skipped - 2 finding(s): high 2, medium 0, low 0

  HIGH   ACR001  Compiled subgraph shares state keys with the parent graph
    examples/langgraph-subgraph-leak/broken.py:51
    Compiled subgraph registered as node 'child' has no output_schema, and child state 'State' shares 'a', 'b', 's1', 's2' with parent state 'State'. The child's full state is merged back into the parent, so writes from this node can collide with sibling branches.
    evidence:
      - shared state key(s): 'a', 'b', 's1', 's2'
      - parent state 'State' declared at line 21; key line(s): 'a' at line 22, 'b' at line 23, 's1' at line 24, 's2' at line 25
      - child state 'State' declared at line 21; key line(s): 'a' at line 22, 'b' at line 23, 's1' at line 24, 's2' at line 25
      - child graph compiled without output_schema at line 51
      - parent node 'child' registered with add_node at line 51
    fix: Declare ``output_schema=<SubgraphOutput>`` on the child ``StateGraph`` so only intended keys flow back to the parent, or split the shared key into distinct keys.
    docs: docs/rules/ACR001.md

  HIGH   ACR002  Parallel branches write the same state key without a reducer
    examples/langgraph-subgraph-leak/broken.py:53
    After START, branches 'child', 'sibling' can run in the same superstep and all write state key 'b', which has no reducer. LangGraph raises InvalidUpdateError for concurrent writes to a non-reduced key.
    evidence:
      - source START has unconditional edges to 'child', 'sibling' (edge line(s): 53, 54)
      - state key 'b' declared at line 23 with annotation 'str' and no reducer
      - node 'child' writes 'b' (add_node at line 51)
      - node 'sibling' writes 'b' (add_node at line 50)
    fix: Give the key a reducer, e.g. ``Annotated[list, operator.add]`` (or ``add_messages`` for messages), or make the branches write disjoint keys / run sequentially.
    docs: docs/rules/ACR002.md
```

修复后同一条命令返回 0 条 finding：

```text
$ acr scan examples/langgraph-subgraph-leak/fixed.py
acr scan - examples/langgraph-subgraph-leak/fixed.py
1 file(s) parsed, 0 skipped - 0 finding(s): high 0, medium 0, low 0

  No composition issues found.
```

修复只有一行，但方向必须是“收窄子图输出边界”，而不是砍掉并行或让子图少读上下文：

```diff
- child = StateGraph(State)
+ child = StateGraph(State, output_schema=ChildOutput)
```

```python
class ChildOutput(TypedDict):
    s1: int
    s2: int
```

修复后：父图的 `b` 保留为 `old|updated`，子图仍能读取父图的 `a`/`b` 计算 `s1`/`s2`，并行结构不变，回归输出 `{'a': 1, 'b': 'old|updated', 's1': 4, 's2': -2}` 并通过。

## 安装

需要 Python 3.10+。四种方式任选：

```sh
# 1) pipx（推荐，隔离环境）
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.4.0"
pipx inject agent-composition-review "langgraph>=1.0,<2"    # 可选，仅 acr demo 需要

# 2) uv tool
uv tool install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.4.0"

# 3) pip
python3 -m pip install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.4.0"

# 4) 源码
git clone https://github.com/meiluosi/agent-composition-review.git
cd agent-composition-review
python3 -m pip install -e ".[langgraph]"
```

`[langgraph]` 只影响 `acr demo` 的示例运行；`scan` / `trace` / `report` 不依赖 LangGraph 也能用。安装脚本 [install.sh](install.sh) 会自动检测 `uv` / `pipx` / `pip`，但它只安装 CLI 本体；跑 demo 时再按上面的方式补 `langgraph`。

验证安装：

```sh
acr --version
acr rules
acr --help
```

## GitHub Action

把 `acr scan` 放进 CI：

```yaml
- uses: actions/checkout@v4
- uses: meiluosi/agent-composition-review@v0.4.0
  with:
    path: .
    fail-on: high
```

Action 只静态扫描，不执行被扫描代码、不需要 API key。完整输入/退出码/安全说明见 [GitHub Action 文档](docs/github-action.md)。

## 命令

| 命令 | 作用 | 常用参数 |
| --- | --- | --- |
| `acr scan PATH` | 静态分析构图代码（Python AST），输出 finding | `--json`；`--fail-on high\|review\|never` |
| `acr trace TRACE` | 分析失败轨迹，输出时间线与证据事件 ID | `--json`；`--resume-at SEQ`；支持 JSON 数组 / JSONL / 统一事件格式，并兼容 LangGraph debug stream |
| `acr report` | 合并 scan 与 trace 结果为一页 Markdown | `--scan FILE --trace FILE -o report.md` |
| `acr demo` | 本地跑通 #6446 类示例：broken → scan → 修复 → 通过 | 需要 `[langgraph]` |
| `acr rules` | 列出全部规则 ID、标题、严重度与文档链接 | — |

`--fail-on` 用于 CI：`high` 只在 high finding 时非零退出，`review` 把 medium 也算失败，`never` 永不因 finding 失败。所有 finding 都带 `rule`、`severity`、`file`、`line`、`evidence`、`fix`、`docs` 字段。

## 规则

共 16 条。每条都有独立的说明页（症状、为什么发生、输出示例、最小修复、误报边界）；finding 的 `docs` 字段是 `docs/rules/<RULE>.md` 仓库相对路径，安装版可在 [GitHub 的 docs/rules/](https://github.com/meiluosi/agent-composition-review/tree/main/docs/rules) 查看同一个页面。

### 静态检查（`acr scan`）

| 规则 | 症状 | 级别 | 文档 |
| --- | --- | --- | --- |
| ACR001 | 子图与父图共享状态键，且未声明 `output_schema` | high | [ACR001](docs/rules/ACR001.md) |
| ACR002 | 并行分支写同一状态键，且没有 reducer | high | [ACR002](docs/rules/ACR002.md) |
| ACR003 | 子图作为节点使用但未声明 `output_schema` | medium | [ACR003](docs/rules/ACR003.md) |
| ACR004 | 副作用节点/图被缓存包裹 | medium | [ACR004](docs/rules/ACR004.md) |
| ACR005 | 副作用节点启用了 retry | medium | [ACR005](docs/rules/ACR005.md) |
| ACR006 | 节点内通过 checkpointer 读写图状态 | low | [ACR006](docs/rules/ACR006.md) |
| ACR007 | 节点返回了状态 schema 未声明的键，写入被静默过滤 | medium | [ACR007](docs/rules/ACR007.md) |
| ACR008 | 缓存节点用了没有稳定身份的 callable，cache key 可能退化 | medium | [ACR008](docs/rules/ACR008.md) |
| ACR009 | retry_policy 节点内调用多个子图，恢复后可能复用错 checkpoint | medium | [ACR009](docs/rules/ACR009.md) |
| ACR010 | 条件自环写非 reducer 键，重试可能与并行分支重叠 | medium | [ACR010](docs/rules/ACR010.md) |

### 轨迹检查（`acr trace`）

| 规则 | 症状 | 级别 | 文档 |
| --- | --- | --- | --- |
| ACR101 | 相同参数与结果的工具被重复调用 | medium | [ACR101](docs/rules/ACR101.md) |
| ACR102 | 同一节点重复执行并重复写同一状态键 | medium | [ACR102](docs/rules/ACR102.md) |
| ACR103 | 同一步的并行节点写同一状态键 | high | [ACR103](docs/rules/ACR103.md) |
| ACR104 | 状态已部分写入后抛错 | high | [ACR104](docs/rules/ACR104.md) |
| ACR105 | 最终状态缺少输入中存在的键 | medium | [ACR105](docs/rules/ACR105.md) |
| ACR106 | 恢复/重放后重启了已完成的节点 | medium | [ACR106](docs/rules/ACR106.md) |

`acr rules` 现在显示 STATUS 列：

- `grounded`：至少有一个真实公开/本地复现样本检查过规则行为；目前是 ACR001、ACR002、ACR003、ACR007、ACR106。
- `experimental`：已实现，但只有设计案例、邻近案例或合成 fixture；其余规则都属于这一层，不要当成已验证能力。

级别含义：`high` 很可能是真实的组合故障；`medium` 需要人工确认为“故障”还是“设计如此”；`low` 是提示，通常不改变行为。**规则命中不等于根因**，先核对证据再改代码。

## 案例图谱

按症状查真实案例：[docs/atlas/](docs/atlas/README.md)。收录 10 个公开 issue（#6446/#1964/#8320/#2644/#2581/#3875/#9276/#8753/#9253/#2336），每个都写清“症状 → 规则 → 证据 → 最小修复 → 验证边界”，并区分 grounded / experimental。

## 工作原理

- **scan**：用 Python `ast` 解析目标路径下的构图代码，识别 `StateGraph` 构造、`add_node` / `add_edge`、从 `START` 的扇出、状态键的 `Annotated[...]` reducer、`cache` / `retry` 装饰器，以及节点内的 `get_state` / `update_state` 调用。纯本地分析，不执行代码。
- **trace**：把 JSON 数组、JSONL、统一事件格式或 LangGraph debug stream 归一化为带 `id` / `seq` / `kind` 的事件流，再检查同一工具重复调用、同一节点重复写、同一步并行写同键、错误后的部分写入、最终状态丢键。输出会引用具体事件 ID，方便回到原始轨迹核对。
- **report**：只做合并与排版，不引入新的判断。
- 不需要模型、不需要 API key、不上传代码、不内置遥测。

## 限制

- 只支持 **Python** 的构图代码，且基于 **AST 静态分析**。动态构造（工厂函数里循环 `add_node`、`getattr` 拼函数名、运行时才决定边）可能漏报。
- 面向 **LangGraph 1.x**；更老的 API 或不同小版本的 `StateGraph` 参数可能无法识别。
- **不是运行时追踪器**：不会自动插桩或采集线上运行；`trace` 需要你提供轨迹文件。
- **不自动改代码**：`fix` 是文字建议，所有修改由你决定。
- 静态规则是启发式的：装饰器名字、状态键是否重叠等判断都可能误报。v0.1 宁愿把不确定项标成 medium/low，也不替你做结论。
- **v0.1 不宣称 benchmark 提升**，不提供检测准确率或生产效果承诺；旧实验记录见 [评测档案](docs/evaluation-history.md)。

## 评测档案（不是产品承诺）

本项目最初是一份“组合审查方法论”的研究型 Skill，2026 年 9–10 月做过若干轮模型行为实验：外部基准 144 次、开发集消融 96 次、机制检查 72 次、v4 执行修复 48 次、模型交互 100/144 次、自主代码修复 36 次。这些记录保留在 [docs/evaluation-history.md](docs/evaluation-history.md)，包括关键数字、结论和限制。

从 v0.1 起，**产品优先**：主入口是 `acr` CLI，实验历史只作为证据档案维护。那些实验的对象是“模型读完 Skill 文本后的排障表现”，不是 `acr scan` / `acr trace` 的规则准确率；它们不能用来证明本产品有效，也没有被改写成 v0.1 的 benchmark 承诺。想核查原始材料，可读 [评测协议](evals/PROTOCOL.md) 与 [案例清单](evals/cases.json)。

> 一句话：我们保留负结果和未完成记录，不把“没有观察到提升”包装成提升。

### 真实案例试点（2026-10-17）

除了自建案例，我们还从 LangGraph GitHub issue 收集了 **14 个真实故障 + 3 个正常控制图**，按“盲标 → 扫描 → 逐例判定”的协议跑了一轮开发型试点。v0.1.0 的结论不漂亮，但有用：

- 13/14 可以读出构图；v0.1.0 的 11 条规则只能表达其中 **1 个**（#6446）；
- 这个唯一可表达的案例因为子图写在 helper 函数里返回 compiled graph，**扫描漏掉了**；
- 真实案例上只发出 3 条 finding：**0 条与故障相关**，1 条误导（`ReadOnly` 里的 reducer 被误判为没有 reducer），2 条顺带；
- 3 个正常控制图 0 误报。

这不是 benchmark，样本也很小；它直接暴露了两个 P0，并在 **v0.1.1** 修复：**跨函数解析 helper 返回的 compiled subgraph** 和 **reducer 注解限定符解包**。同一批开发案例的复扫结果：`lg-6446` 命中 ACR001 + ACR002，`lg-9311` 的误导 finding 消失，3 个控制图仍 0 findings。其余 12 个案例仍是 GAP，多数是框架内部运行时 bug，静态构图规则不应硬覆盖。

完整报告（含逐例判定、GAP 分布、材料限制）在 [真实案例试点报告](evals/realworld/results/pilot-2026-10-17/REPORT.md)，修复后复扫记录在 [POSTFIX.md](evals/realworld/results/pilot-2026-10-17/POSTFIX.md)。

### 第二轮验证（2026-10-18）

接着又收集了 **20 个未参与规则设计的新案例**（15 个 GitHub issue + 3 个 LangChain Forum + 2 个 Stack Overflow），在 ACR007–ACR009 实现后做验证。规则集为 14 条，本轮随 v0.2.0 发布：

- 可分析 15/20；规则可表达 9/15 = 60%；可表达案例命中 6/9 = 66.7%；
- 19 条 finding 中 0 条误导性解释，但 ACR007 有 2 条修法建议场景不匹配（class node 应优先考虑 `add_node(input=...)`，该问题已在 v0.2.0 的规则文案/文档中修正）；
- **ACR007 预期 3 例全部命中**，但另有 1 个 off-rule（1406）和重复 finding，precision 不干净；
- **ACR008/ACR009 本批 0 样本、0 命中，未验证**；
- ACR002 预期 3 例全部 miss（只建模兄弟扇出，漏 retry 自环重叠）；最大缺口是 command-send（4/9 GAP），属运行时语义，不建议硬加静态规则；
- 3 个正常控制图仍 0 误报。

本批选择偏向 user_graph/usage，不能把“framework GAP 比例下降”解读为真实分布变化。报告：[第二轮验证报告](evals/realworld2/results/pilot2-2026-10-18/REPORT.md)。

**v0.3.0 根据这份报告补了 ACR010/ACR106：**

- ACR010 覆盖条件自环写非 reducer 键，复扫 `lg2-2336` 命中；该案例已用于设计，属开发集，不是独立验证；
- ACR106 在 trace 中识别 `resume`/`replay` 后重启已完成节点；v0.3.1 已用真实公开 trace #3875 做负向控制、本地复现 #9276 做正向样本；仍**没有社区直接附带的 resume trace**；
- ACR008/ACR009 仍无真实案例命中；v0.3.0 只补了合成正负 fixture，不宣称已验证。

补充记录：[POSTFIX.md](evals/realworld2/results/pilot2-2026-10-18/POSTFIX.md)。

### 第三轮定向验证（2026-10-18）

不再估计总体命中率，只针对四个能力找真实样本：

- **ACR106**：本地复现 #9276（langgraph 1.2.14）得到正向 trace，`agent` 在 resume 前完成、resume 后重跑，命中 ACR106；真实公开 trace #3875 作为负向控制，修复后 0 误报。这个负向 trace 还暴露了“被 interrupt 的节点被当成已完成”的真实误报，v0.3.1 已修。
- **ACR008**：找到 7 条 cache identity 邻近案例，**0 条 exact**，不能算验证。
- **ACR009 / ACR010**：没有新的 exact 样本，明确记 `no_sample_found`；#9253 / #2336 属已用于设计的旧案例，不能复用。

报告：[第三轮定向验证报告](evals/realworld3/results/pilot3-2026-10-18/REPORT.md)。ACR106 的正向 trace 是本地复现，不是社区附件；且需要显式 resume/replay 标记，LangGraph 默认 debug 流不产生。

## 给 Agent 使用

同一个仓库还提供运行时 Skill 入口：[skills/agent-composition-review/SKILL.md](skills/agent-composition-review/SKILL.md)。它把上面的流程写成 Agent 可执行的步骤：判断症状 → `acr scan` / `acr trace` → 读 finding 并验证 → 最小修复 → 加回归测试 → 输出一页报告，并明确要求“证据不足时说不知道”。规则速查与修复模式分别在 [references/rules.md](skills/agent-composition-review/references/rules.md) 和 [references/fix-patterns.md](skills/agent-composition-review/references/fix-patterns.md)。

## 贡献

欢迎提交：脱敏后的最小故障复现、正常反例（证明某条 finding 是误报）、安装/兼容问题、规则文档改进。

一次改动最好只对应一个问题：

1. 提交最小复现（构图代码、版本、实际报错/轨迹）或误报样例；
2. 说明期望行为与现有 finding 的差异；
3. 修改最小规则或文档，不顺手重构无关代码；
4. 回归旧案例，补一条能复现旧行为的测试；
5. 在 PR 中说明证据：为什么这条规则能命中、误报边界是什么。

误报报告和真实修复案例比 star 更有用。流程细节见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。

## 许可证

[MIT](LICENSE)。Copyright (c) 2026 Agent Composition Review contributors。

## 仓库结构

```text
src/agent_composition_review/          acr CLI 实现（产品代码）
tests/                                 CLI 与规则测试
examples/langgraph-subgraph-leak/      #6446 类可运行示例（broken/fixed/run）
docs/rules/                            16 条规则的独立说明页
docs/evaluation-history.md             旧评测档案（历史证据，非产品承诺）
docs/launch/                           公开发布清单与渠道文案
skills/agent-composition-review/       Agent Skill 入口与参考
evals/                                 评测历史、协议与原始证据（不参与运行时）
scripts/                               仓库检查、评测与维护脚本
```

产品代码（`src/`、`tests/`、`examples/`、`skills/`）与评测档案（`evals/`、`docs/evaluation-history.md`）刻意分开：前者承诺可运行、可回归；后者只承诺保留证据和限制，不被当作产品效果宣传。

---

**Find the boundary where your LangGraph agent breaks.**
