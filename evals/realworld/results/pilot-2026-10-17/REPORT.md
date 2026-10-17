# 真实 LangGraph 社区案例试点报告（pilot-2026-10-17）

**范围：14 个真实 issue + 3 个内部正常控制图。这是开发型试点，不是保留集 benchmark；样本小，不计算置信区间，不宣称泛化。** 本报告用于发现 v0.2 的规则/能力缺口，所有数字只描述这一批案例。

## 1. 材料与冻结状态

- 盲标：`evals/realworld/labels.json`，`blind: true`；sha256 `d5c48148b6b4c27d…` 与预期一致（脚本每次运行强校验，不匹配即拒绝汇总）。labels 与 fixtures 均未修改。
- 扫描：`acr 0.1.0` 静态 AST 扫描；本工作树未安装 console script，通过 `PYTHONPATH=src python3 -m agent_composition_review` 运行。
- 案例：14 个 issue + 3 个正常控制，全部 `graph.py` 可被 AST 解析。
- 复核：人工复跑 17 个目录的 `acr scan --fail-on never`，输出与存档 `scan.txt` 逐行一致；3 个 controls 均为 0 findings（未安装 console script，运行时为 `PYTHONPATH=src python3 -m agent_composition_review`，`acr 0.1.0`）。
- 口径：`evals/realworld/protocol.md`（冻结于 2026-10-17）；逐条判定依据见 `predictions.json` 的 `notes`。

## 2. 核心数字（只描述本批）

| 指标 | 值 |
| --- | --- |
| 可分析率 | 13/14 = 92.9% |
| 规则可表达率（分母 = 可分析案例） | 1/13 = 7.7% |
| 规则可表达率（分母 = 全部 issue） | 1/14 = 7.1% |
| 命中率（分母 = 可表达案例） | 0/1 = 0.0% |
| 正常控制误报率 | 0/3 = 0.0% |
| 真实案例 finding 相关性 | 0/3 = 0.0%（3 条中误导 1、顺带 2、相关 0） |
| GAP 案例（按盲标 source_type） | 13 例：feature 1, framework 10, usage 2 |
| 判定分布（14 个 issue） | gap 9 / incidental 2 / false_positive 1 / miss 1 / not_applicable 1 |
| 材料不足（硬） | 1/14（`lg-7580`）；另有 3 例版本敏感/低置信材料 |

一句话结论：**唯一的可表达案例 `lg-6446`（ACR001）是 miss；扫描在真实案例上只发出 3 条 finding，0 条与故障相关（1 条误导、2 条顺带）。**

## 3. 四类问题必须分开看

- **用户构图问题（1 例，唯一可表达案例，但 miss）**：`lg-6446` 的 fixture 是标准 ACR001 形态——子图无 `output_schema`、与父图共享键 `b`、并与写 `b` 的 `nodeb` 并行。miss 的原因不是规则不存在，而是 fixture 把子图构建放在 `build_child()` helper 中并返回编译后的 graph；scanner 不跨函数解析返回值，**helper function boundary -> miss**。这是 ACR001 的分析深度/召回缺口。
- **框架内部 bug（GAP 的主体）**：13 个 GAP 中 10 个盲标为 `framework`（另 usage 2、feature 1）。这些是 LangGraph 的 reducer 解包、checkpoint 往返、cache 命名空间、retry/interrupt 恢复、executor 语义等内部行为；**静态构图规则本来就不覆盖，不能算 ACR 的 false-negative 能力问题**。正确修法常常是升级框架或上游 PR，而不是改用户图，ACR 不应把它们包装成用户错误。
- **规则误报（1 例）**：`lg-9311` 的 `findings: ReadOnly[Annotated[list[str], operator.add]]` 本身有 reducer，scan 却报 ACR002 说 "has no reducer"。这是 ReadOnly 限定符未解包导致的**误导性 finding（false_positive/misleading）**——现有 `_annotated_parts` 只解包 `Required`/`NotRequired`，不识别 `ReadOnly`——不是 gap hit。
- **顺带发现（2 例）**：`lg-9276`（resume 重放）和 `lg-9084`（tags 重复）各被报 1 条 ACR001，但这两条来自 fixture 复用同一 `State` 类的副作用，与 issue 故障无关，记为 **incidental/unrelated**，不算 hit。

## 4. 逐案例判定

| case | source_type(盲标) | expected | scan | verdict | 依据（摘要） |
| --- | --- | --- | --- | --- | --- |
| `control-disjoint` | control | NONE | 0 | **control pass** | 正常控制，0 findings（ACR002 误报控制） |
| `control-output-schema` | control | NONE | 0 | **control pass** | 正常控制，0 findings（ACR001/ACR003 误报控制） |
| `control-reducer` | control | NONE | 0 | **control pass** | 正常控制，0 findings（ACR002 误报控制） |
| `lg-2644` | usage | GAP:declared-keys | 0 | **gap** | 节点返回未声明键；材料低置信度（维护者未复现）→ GAP |
| `lg-6446` | user_graph | ACR001 | 0 | **miss** | ACR001 形态，但子图由 helper 返回 compiled graph，跨函数解析缺口 → miss |
| `lg-7580` | usage | GAP:reducer-semantics | 0 | **not_applicable** | 盲标 graph_visible=false，材料是 channel 级而非构图 → not_applicable（材料不足） |
| `lg-8320` | feature | GAP:declared-keys | 0 | **gap** | 节点返回未声明键被静默过滤；现有 11 条规则无覆盖 → GAP |
| `lg-8608` | framework | GAP:other | 0 | **gap** | error_handler 已处理仍重抛原异常 → 框架 executor 语义，GAP |
| `lg-8753` | framework | GAP:retry-cache | 0 | **gap** | 非函数 callable 的 cache namespace 退化为 __dynamic__ → 静态可部分检测，GAP |
| `lg-9005` | framework | GAP:command-send | 0 | **gap** | Command.update 按 MRO 重复应用继承字段 → 运行时语义，GAP |
| `lg-9019` | framework | GAP:reducer-semantics | 0 | **gap** | LastValue 未解包 Overwrite，把 wrapper 存进状态 → 运行时，GAP |
| `lg-9084` | framework | GAP:other | ACR001×1 | **incidental** | scan 的 ACR001 来自共享 State/嵌套链条的副作用，与 tags 重复无关 → 顺带 |
| `lg-9086` | framework | GAP:reducer-semantics | 0 | **gap** | tuple reducer 经 checkpoint 往返退化成 list → 运行时，GAP |
| `lg-9106` | framework | GAP:parallel-interrupt | 0 | **gap** | 嵌套并行 interrupt 已完成 sibling 被重放 → 运行时，GAP |
| `lg-9253` | framework | GAP:retry-cache | 0 | **gap** | retry 未重置 subgraph 计数，第二个子图续用第一个的 checkpoint → 运行时，GAP |
| `lg-9276` | framework | GAP:resume-replay | ACR001×1 | **incidental** | scan 的 ACR001 来自共享 State 的副作用，与 resume 重放故障无关 → 顺带 |
| `lg-9311` | framework | GAP:reducer-semantics | ACR002×1 | **false_positive** | ReadOnly 包住 reducer，ACR002 误判为 no reducer → 误导性 finding |

## 5. 最值得补的缺口（3-5 个）

GAP 分布（13 例）：`command-send` 1, `declared-keys` 2, `other` 2, `parallel-interrupt` 1, `reducer-semantics` 4, `resume-replay` 1, `retry-cache` 2；按盲标 source_type：feature 1, framework 10, usage 2。

1. **ACR001 跨函数解析（P0，唯一可表达案例 miss）**：`lg-6446` 证明「helper 返回 compiled subgraph」会让现有 ACR001 完全失效。这是本次试点里最直接的规则召回缺口，且完全静态可修。
2. **reducer 注解限定符解包（P0，误报）**：`lg-9311` 证明 `ReadOnly` 包住 `Annotated[..., reducer]` 时，ACR002 会误判为「no reducer」（现有实现只解包 `Required`/`NotRequired`）。必须先解包限定符再做 reducer 发现，否则会给用户错误修法建议。
3. **declared-keys 能力（P1，2 例）**：`lg-8320`、`lg-2644` 都是节点返回未声明键、被框架静默过滤；现有 11 条规则无覆盖。这是最适合新增静态规则的缺口之一（节点返回 dict 字面量 vs TypedDict/dataclass 字段）。
4. **cache/retry 身份与命名空间（P1，2 例）**：`lg-8753`（非函数 callable 的 cache namespace 退化为 `__dynamic__`）可做静态身份检查；`lg-9253`（retry 后第二个子图续用第一个的 checkpoint）本质是运行时命名空间/位置身份问题，只能给可疑提示，不能静态断言。
5. **resume/replay 与 parallel interrupt（P1/P2，运行时）**：`lg-9276`、`lg-9106`（以及 `lg-9086` 的 checkpoint 往返）依赖恢复/重放运行时语义与版本，应交给 `acr trace` 或版本感知检测，**不应硬塞进静态规则**。

## 6. v0.2 建议（优先级排序）

- **P0-1｜ACR001 跨函数解析**：跟踪函数返回值与局部变量绑定，解析 `add_node("x", build_child())`、`child = build_child(); add_node("x", child)` 以及 helper 返回 `compile()` 后注册的形态。验收：`lg-6446` 报 ACR001；3 个 controls 仍 0；新增 helper-return 单测，防止误报正常 helper。
- **P0-2｜reducer 限定符解包**：在 reducer 发现前解包 `typing_extensions.ReadOnly`（`Required`/`NotRequired` 已支持，补上回归测试），同步修正 ACR002 与聚合分析。验收：`lg-9311` 不再报 ACR002；新增限定符 × reducer 矩阵单测；该 case 仍按盲标算 GAP（框架行为），不得记为 hit。
- **P1-1｜declared-keys 新规则**：检测节点返回键不在 state schema 中（含 class node 返回另一 schema 的情况）。验收：`lg-8320`、`lg-2644` 命中；controls 0；message 要指出声明位置与会被静默丢弃的键。
- **P1-2｜cache/retry 的静态/运行时分工**：把 cache 键身份（callable 的 `__qualname__`/`__module__` 缺失、partial、跨图共享 cache）做成静态检查（覆盖 `lg-8753`）；retry 与子图 checkpoint 命名空间（`lg-9253`）只做可疑提示或 trace 检测，不在静态规则里下结论。
- **P1-3｜评测口径补强**：在 `summary.json` 里把 miss 细分 `semantic_gap` / `analysis_depth_gap` / `material_gap`（本批：`lg-6446` = analysis_depth_gap，`lg-7580` = material_gap）；记录 fixture 的版本要求（`lg-8608` ≥1.2、`lg-9084` ≥1.2.3），否则跨版本复跑会把不表现行为的 fixture 误判为 miss。
- **P2｜框架内部语义保持边界**：`Command.update` MRO 重复（`lg-9005`）、`Overwrite` 未解包（`lg-9019`）、tuple reducer checkpoint 往返（`lg-9086`）、`error_handler` 重抛（`lg-8608`）、tags 重复（`lg-9084`）属框架/上游问题。可以进 `acr trace` 或版本感知提示，但不进静态构图规则，避免把「该升级 LangGraph」包装成「你的图有问题」。

## 7. 分歧与冻结记录

- **source_type 分歧（1 例）**：`lg-8320` 盲标 `feature`（issue 实际请求 warning/strict 的诊断功能），fixture 元数据写 `framework`。本报告一律用盲标；未修改 labels 或 fixtures。它只影响 GAP 按 source_type 的分布（盲标：feature 1、framework 10），不影响总缺口数。
- **graph_visible 分歧（1 例）**：`lg-7580` 盲标 `graph_visible=false`（issue 复现是 channel 级，没有构图），fixture 元数据写 true（fixture 重建了隐含拓扑）。本报告按盲标把该 case 记为 `not_applicable`（材料不足），不计入可分析率、不算 miss；其缺口 `GAP:reducer-semantics` 仍计入缺口分布并标注 not_analyzable。
- **口径复核**：任务摘要曾把 GAP 的 source_type 记为「framework 9、usage 2、feature 1、user_graph 1」；`labels.json` 的 13 个 GAP 实际为 **framework 10 / usage 2 / feature 1 / user_graph 0**（总分布为 framework 10 / usage 2 / feature 1 / user_graph 1，lg-6446 是 user_graph 但预期 ACR001）。报告与 summary.json 以 labels.json 为准。
- **误报判定**：`lg-9311` 的 ACR002 记 `false_positive/misleading`，不计入 gap hit；`lg-9276`、`lg-9084` 的 ACR001 记 `incidental/unrelated`，也不计入 hit。

## 8. 限制

- 开发型试点：14 个 issue（+3 个内部正常控制），样本小，未计算置信区间，不宣称泛化。
- 本批案例已用于发现缺口，不能当作未见保留集复用；后续规则修改不得把同批案例当 benchmark 宣称。
- 静态分析：本报告只汇总存档的 acr scan 结果，没有运行 fixture，不能证明运行时行为；运行时故障判定依赖 issue 文本、评论与 expected.md。
- 相关性/误导/顺带判定是分析者判断，逐条写在 predictions.json 的 notes 中；没有第二标注者，无一致性统计。
- 版本敏感：lg-8608 需 langgraph>=1.2、lg-9084 需 >=1.2.3，pinned 1.0.3 下 fixture 不表现报告行为；静态扫描不受影响，但复跑 fixture 需要版本矩阵。
- 正常控制是内部 fixture，不是社区里的正常图；0/3 误报率是很弱的证据。
- acr 0.1.0 的静态 AST 规则不等于 acr trace 能力；本报告的 GAP 结论只针对 scan 路径。
- GAP 中多数是框架内部语义（framework 10/13），静态构图规则本来就不覆盖，不能算 ACR 的 false-negative 能力问题。

## 9. 复现

```sh
# 汇总（离线、不联网、不调用模型；会校验 labels sha256）
PYTHONPATH=src python3 scripts/summarize_realworld_eval.py

# 归档扫描（本报告对应的 scan.txt 来源）
for d in evals/realworld/cases/*/; do
  PYTHONPATH=src python3 -m agent_composition_review scan "$d" --fail-on never --json
done
```

输出：`evals/realworld/results/pilot-2026-10-17/predictions.json`、`summary.json`、`REPORT.md`。
