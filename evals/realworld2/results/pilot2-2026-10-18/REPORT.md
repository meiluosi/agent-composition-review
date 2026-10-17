# 第二轮真实案例验证报告（pilot2-2026-10-18）

**范围：20 个新案例（15 个 GitHub issue + 3 个 LangChain Forum + 2 个 Stack Overflow）。这是验证批次，不是统计意义上的保留集；样本小，不宣称泛化。ACR007–ACR009 在第一批 14 个 issue 上设计，本批案例未参与规则设计。** 社区来源只有 5 例，论坛/SO 结论仅作方向性补充。

## 1. 材料与冻结状态

- 盲标：[labels.json](../../labels.json)，`blind: true`；sha256 `0510774269aa3032…` 与冻结值一致（脚本每次运行强校验，不匹配即拒绝汇总）。labels 与 fixtures 均未修改。
- 扫描：`acr 0.2.0` 静态 AST 扫描（14 条规则）；本工作树未安装 console script，通过 `PYTHONPATH=src python3 -m agent_composition_review` 运行。
- 复核：人工复跑 20 个 case 的 `acr scan --fail-on never`，输出与存档 `scan.txt` 逐行一致；3 个正常控制沿用第一批内部 controls，复跑仍 0 findings。
- 口径：[protocol.md](../../protocol.md)（冻结于 2026-10-18）；逐条判定依据见 `predictions.json` 的 `notes`。

## 2. 核心数字（只描述本批）

| 指标 | 值 |
| --- | --- |
| 可分析率 | 15/20 = 75.0% |
| 规则可表达率（分母 = 可分析案例） | 9/15 = 60.0% |
| 规则可表达率（分母 = 全部案例） | 9/20 = 45.0% |
| 命中率（分母 = 可表达案例） | 6/9 = 66.7% |
| 正常控制误报率 | 0/3 = 0.0%（沿用第一批 3 个内部 controls） |
| 误导率（false_positive / 全部 finding） | 0/19 = 0.0% |
| finding 相关性 | 9/19 = 47.4%（9 相关 / 1 secondary / 3 off-rule / 6 顺带 / 19 条） |
| 修法建议可能误导 | 2 条（lg2-1950 的 ACR007 fix 文本；检测本身正确，单列不计入 false_positive） |
| GAP 案例（按盲标 expected_rule） | 9 例（6 可分析 / 3 材料不足）：command-send 4 / other 3 / reducer-semantics 2；按盲标 source_type：framework 3, usage 4, user_graph 2 |
| 来源分布 | GitHub 15 / Forum 3 / Stack Overflow 2 |
| source_type（盲标） | framework 3, insufficient 2, usage 5, user_graph 10 |
| 判定分布 | hit 6 / miss 3 / gap 6 / not_applicable 5 / false_positive 0 |
| 材料不足 | 5 例 graph_visible=false；source_type=insufficient 2 例；其中 3 例预期 GAP 记 not_applicable（仍计入缺口分布） |

一句话结论：**可表达案例从第一批的 1 例提升到 9 例，命中 6/9；ACR007 有真实召回（3/3 预期案例）但伴随 3 条 off-rule 和 1 例修法误导；ACR008/ACR009 本批 0 样本、0 命中，未验证；ACR002 预期 3 例全部 miss。**

## 3. 新规则与旧规则逐条结果

### ACR007（declared-keys）：有召回，但不干净

- 预期 3 例（`lg2-2581`、`lg2-1909`、`lg2-1950`）全部命中，召回 3/3；ACR007 共在 4 个 case 触发（case precision 3/4 = 75%），另有 `lg2-1406` 被 off-rule 触发（3 条 finding）。
- 7 条 ACR007 finding：relevant 4 条（1909×1、1950×2、2581×1）= 57.1%；off-rule 3 条（1406）= 42.9%。
- **修法误导（单列）**：`lg2-1950` 的检测是对的（docs 未声明于 OverallState），但 ACR007 的 fix 文本是『把键声明进 state』；该 issue 维护者确认的修法是 `add_node(..., input=QueryOutputState)`。对 class node / 私有 schema 场景，只补 state 键可能不是正确修法。这 2 条 finding 标 `fix_advice_misleading`，但不计入 false_positive（解释本身正确，是修复建议场景不匹配）。
- **off-rule 的来源**：`lg2-1406` 的 3 条 ACR007 不是误报（fixture 中 `num_p`/`query`/`datas` 确实未声明于 OverallState），但它暴露的是 fixture 与盲标的张力：盲标按 issue 原文期望 ACR002（`num_p` 是声明的无 reducer 通道），fixture 把 `num_p` 放在 `DataOutputState`、未合并进 OverallState，导致 ACR002 结构上不可能触发。
- 结论：ACR007 不是只有噪声，但需要 (a) 场景化 fix 文本，(b) 与 ACR001/ACR003 同现时的去重/优先级规则，(c) 用 1406 这类案例做 off-target 回归测试。

### ACR008 / ACR009：本批没有样本，未验证

- 本批 20 个盲标里没有任何 `expected_rule=ACR008` 或 `ACR009` 的样本；scan 也没有发出 ACR008/ACR009 finding（0 条）。`summary.json` 的 `rule_verification.new_rules_without_samples` = ['ACR008', 'ACR009']。
- 因此 **不能宣称 ACR008/ACR009 被验证**，也不能说它们『只有噪声』；准确表述是：**本批没有样本，未验证**。ACR004/ACR005/ACR006/ACR101–ACR105 同样在本批无样本。
- 需要专为 cache identity（callable/lambda/partial + 共享 cache）和 retry_policy + 多子图构建正/负 fixture 后，才能给出覆盖率声明。

### 其他规则

- **ACR001**：预期 2 例（`lg2-1964`、`lg2-6290`）全部命中；共 6 条 finding / 4 个 case，其中 2 个 case 是 target hit，2 个 case（`lg2-5062`、`lg2-5362`）是 incidental。**第一批 P0-1『跨函数解析 helper 返回的 compiled subgraph』已验证生效**：1964/6290 的子图都由 helper 返回后被 ACR001 识别。
- **ACR002**：预期 3 例（`lg2-1406`、`lg2-2336`、`forum-1474`）**全部 miss**，召回 0/3；唯一一条 ACR002 finding 出现在 `lg2-1964`，是 secondary（症状相关但正确修法是 output_schema）。fixture/label 张力见 1406 与 forum-1474：一个因 num_p 未声明、ACR002 无法触发；一个保留了 reducer、规则正确地沉默。
- **ACR003**：预期 1 例（`lg2-2392`）命中；共 5 条 finding / 5 个 case，只有 1 个 target hit，其余 4 个（`forum-2020`、`lg2-2581`、`lg2-4523`、`so-79788210`）是 incidental。ACR003 在本批的 case 精度 1/5；检测本身不是误报，但多数情况下与 issue 故障无关，应默认降级为 review 而非结论。

## 4. 逐案例判定

| case | source_type(盲标) | graph_visible | expected | scan | verdict | 依据（摘要） |
| --- | --- | --- | --- | --- | --- | --- |
| `forum-1474` | user_graph | 是 | ACR002 | 0 | **miss** | ACR002 miss：fixture 保留 reducer 变体，规则正确地沉默 |
| `forum-2020` | usage | 否 | GAP:command-send | ACR003×1 | **not_applicable** | 材料不足；ACR003 顺带（Command.update 越界非 output_schema） |
| `forum-2839` | usage | 是 | GAP:other | 0 | **gap** | GAP:other；HITL/子图用法 |
| `lg2-1406` | user_graph | 是 | ACR002 | ACR007×3 | **miss** | 预期 ACR002 未命中；ACR007×3 是 off-rule（fixture 中 num_p 未声明，ACR002 无法触发）→ miss |
| `lg2-1909` | user_graph | 是 | ACR007 | ACR007×1 | **hit** | ACR007 命中（private_var 未声明） |
| `lg2-1950` | usage | 是 | ACR007 | ACR007×2 | **hit** | ACR007×2 命中；检测正确但 fix 建议可能误导（正确修法是 add_node(input=...)） |
| `lg2-1964` | user_graph | 是 | ACR001 | ACR001×2, ACR002×1 | **hit** | ACR001×2 命中；helper 返回子图已被解析；附带 ACR002 为 secondary |
| `lg2-2336` | user_graph | 是 | ACR002 | 0 | **miss** | ACR002 miss：retry 自环与并行分支重叠不在规则模型内 |
| `lg2-2392` | user_graph | 是 | ACR003 | ACR003×1 | **hit** | ACR003 命中；pydantic child keys unknown，只覆盖『缺 output_schema』一半 |
| `lg2-2581` | user_graph | 是 | ACR007 | ACR003×1, ACR007×1 | **hit** | ACR007 命中（kids 未声明）；ACR003 顺带 |
| `lg2-4523` | usage | 是 | GAP:command-send | ACR003×1 | **gap** | GAP:command-send；ACR003 顺带 |
| `lg2-4879` | framework | 是 | GAP:command-send | 0 | **gap** | GAP:command-send；RemoteGraph 跨部署边界需运行时 |
| `lg2-5062` | framework | 是 | GAP:command-send | ACR001×1 | **gap** | GAP:command-send；ACR001 顺带 |
| `lg2-5225` | framework | 否 | GAP:reducer-semantics | 0 | **not_applicable** | 材料不足；reducer 默认值语义非静态构图可见 |
| `lg2-5362` | usage | 否 | GAP:other | ACR001×1 | **not_applicable** | 材料不足；ACR001 顺带（类型降级非 state 泄漏） |
| `lg2-6290` | user_graph | 是 | ACR001 | ACR001×2 | **hit** | ACR001×2 命中；message 只部分覆盖 reducer 重放后果 |
| `lg2-740` | insufficient | 否 | NONE | 0 | **not_applicable** | 材料不足（NONE）；not_applicable |
| `lg2-766` | user_graph | 是 | GAP:other | 0 | **gap** | GAP:other；duplicate edge（普通边+条件边同一 target）不在 ACR002 模型内 |
| `so-79725144` | user_graph | 是 | GAP:reducer-semantics | 0 | **gap** | GAP:reducer-semantics；reducer 纯度静态不可见 |
| `so-79788210` | insufficient | 否 | NONE | ACR003×1 | **not_applicable** | 材料不足（NONE）；ACR003 顺带 |

## 5. 与第一批对比

| 指标 | 第一批（pilot-2026-10-17） | 第二批（本批） |
| --- | --- | --- |
| 案例 | 14 GitHub issue + 3 controls | 20（15 GitHub + 5 Forum/SO） + 3 复用 controls |
| 规则数 | 11 | 14（新增 ACR007–ACR009） |
| 可分析率 | 13/14 = 92.9% | 15/20 = 75.0% |
| 可表达率（/可分析） | 1/13 = 7.7% | 9/15 = 60.0% |
| 命中率（/可表达） | 0/1 = 0.0% | 6/9 = 66.7% |
| issue 上 finding 数 | 3 | 19 |
| 相关 finding | 0/3 | 9/19 |
| 误导性解释 | 1/3（lg-9311） | 0/19（另有 2 条 fix 建议可能误导，单列） |
| framework GAP 占比 | 10/13 GAP = 76.9% | 3/9 GAP = 33.3% |
| GAP 主类别 | reducer-semantics 4 / declared-keys 2 / retry-cache 2 | command-send 4 / other 3 / reducer-semantics 2 |
| ACR007/008/009 | 不存在 | 007: 3/3 预期命中；008/009: 0 样本未验证 |

解读：

- 可表达率从 7.7% 升到 60%，主要来自 **新增 ACR007（3 个预期案例）** 和 **本批选择偏向 user_graph/usage**，不是旧规则整体变好。
- 旧规则里唯一可确认的进步是 **ACR001 的跨函数解析**：第一批 `lg-6446` 因 helper 边界 miss，本批 1964/6290 的 helper 返回子图被命中。ACR002/ACR003 没有类似进步（ACR002 召回 0/3；ACR003 只有 1 个 target hit）。
- 第一批『多数真实 issue 是框架内部 bug』（framework 10/13 GAP = 76.9%）在本批不成立（framework 3/9 GAP = 33.3%）。但**不能据此说真实世界 framework bug 变少了**：本批选择规则明确『用户构图/用法优先』，且加入了社区来源，样本构成与第一批不同。准确结论是：**在这批偏用户构图的验证样本上，该观察没有复现；framework GAP 仍是 3 个且都是静态规则不该覆盖的运行时/平台语义。**

## 6. 最大缺口与 v0.2 建议（优先级排序）

GAP 分布（9 例）：`GAP:command-send` 4；`GAP:other` 3；`GAP:reducer-semantics` 2（其中 6 例可分析、3 例材料不足）

- **P0｜command-send 运行时边界（本批最大缺口，4/9 GAP）**：`lg2-4523`（Command update+resume 时序）、`lg2-4879`（RemoteGraph 跨部署）、`lg2-5062`（Command.update + list[Send]）、`forum-2020`（Command.PARENT 更新范围）。这些是运行时/平台语义，静态构图规则看不到。建议：不要在 scan 里再加静态规则去『覆盖』它们；在文档/输出中明确标注 `not statically analyzable`，把检测交给 `acr trace` 或版本感知提示。验收：这 4 例不产生新的静态 finding；评估报告不再把 command-send 计入静态规则可表达率。
- **P0/P1｜ACR002 召回模型太窄（0/3）**：规则只建模『同一来源扇出到两个不同分支、都写同一声明键』，漏掉 `lg2-2336` 的 retry 自环与并行分支重叠；`lg2-1406`/`forum-1474` 则暴露 fixture 与盲标的 scope 张力（一个 num_p 未声明导致 ACR002 无法触发，一个保留 reducer 导致规则正确地沉默）。建议：扩展 ACR002 到『同一 superstep 里自环/重入与并行分支重叠写同一无 reducer 键』的 review 级检测；同时单独记录 fixture/label 差异，不要靠改 labels 来『修好』指标。
- **P1｜ACR007 修法场景化（检测 3/3、finding 精度 4/7、1 例 fix 误导）**：class node / 私有 schema 场景应建议 `add_node(input=...)`/output schema，而不是只建议把键补进 state；与 ACR001/ACR003 同现时抑制重复报告；用 `lg2-1406` 做 off-target 回归。验收：1950 的 fix 文本能区分 class-node 与普通 TypedDict；1406 不再把 ACR007 作为唯一主 finding。
- **P1｜ACR003 降级/降噪**：5 条 finding 里 4 条 incidental，只有 1 例 target hit。建议默认 severity 降为 review，或要求父/子 state 有共享键、且有 fan-in/边界症状时才作为主 finding；message 里 `keys: unknown`（pydantic child）不应作为结论。
- **P1｜ACR008/ACR009 先补样本再谈验证**：本批 0 样本 0 命中。建议各建 ≥2 个正例 + 2 个负例 fixture（cache_policy on callable/lambda/partial/shared cache；retry_policy + 两个 subgraph invoke），否则不得在发布说明里声称『已验证』。
- **P2｜ACR001 回归与框架边界**：把 1964/6290 固定为 helper-return 回归测试，防止跨函数解析回退；第一批剩余 P1/P2（resume/replay、parallel interrupt、cache identity、Command MRO）继续走 trace/版本感知，不进静态规则。

## 7. 判定口径、误导与分歧

- **误报 vs 误导 vs 修法误导**：本批 19 条 finding 的**解释**没有一条把普通正常图报成问题，false_positive = 0/19；但 `lg2-1950` 的 2 条 ACR007 的解释正确、**修复建议场景不匹配**，单列 `fix_advice_misleading`，不计入 false_positive。另有 `lg2-1964` 的 1 条 ACR002 是 secondary（建议加 reducer，正确修法是 output_schema），同样不算 hit。
- **ACR007 off-rule 与 1406 的规则**：1406 判定 **miss**（预期 ACR002 未命中），ACR007×3 记 off-rule，不折算 hit；这是『另一条规则命中但预期规则未命中』，不是 hit，也不是 ACR007 误报（fixture 中这些键确实未声明）。
- **framework GAP 的边界**：本批 3 个 framework GAP（`lg2-5225` reducer 默认值、`lg2-4879` RemoteGraph resume、`lg2-5062` Command+Send 组合）属于框架/平台运行时语义，静态构图规则本来就不覆盖，**不能算 ACR 的 false negative**；这也是第一批『framework 不应被包装成用户构图错误』结论在本批的延续。
- **labels/fixtures 不一致（7 例，未修改）**：`forum-2020` blind usage/graph_visible=false vs fixture user_graph/graph_visible=true; `lg2-4523` blind usage vs fixture framework; `lg2-5362` blind usage/graph_visible=false vs fixture framework/graph_visible=true; `lg2-6290` blind user_graph vs fixture framework; `lg2-740` blind insufficient/graph_visible=false vs fixture usage/graph_visible=true; `so-79725144` blind user_graph vs fixture usage; `so-79788210` blind insufficient/graph_visible=false vs fixture user_graph/graph_visible=true。本报告一律以 labels 为准；fixture 元数据只作来源说明。
- **材料不足**：5 例 graph_visible=false（`forum-2020`, `lg2-5225`, `lg2-5362`, `lg2-740`, `so-79788210`），其中 source_type=insufficient 只有 2 例；其余 3 例盲标为 usage/framework 但材料不足以做构图分析，记 not_applicable 并单列。

## 8. 限制

- 验证批次：20 个新案例（15 GitHub + 3 Forum + 2 Stack Overflow），不是统计保留集，样本小，不计算置信区间，不宣称泛化。
- 选择偏差：本批 GitHub 案例按『用户构图/用法优先、有代码、有具体报错』排序，source_type 分布明显偏向 user_graph/usage；因此 framework 占比变化不能解读为真实世界分布变化。
- 社区来源只有 5 例（Forum 3、SO 2）；论坛/SO 的结论只能是方向性的，不能代表 Forum/SO 总体。
- ACR007–ACR009 在第一批 14 个 issue 上设计，本批没有参与规则设计；但本批已被用于评估，后续若据本批改规则，本批必须改记为开发集，不能当未见保留集。
- 静态分析：只汇总存档的 acr scan 结果，没有运行 fixture，不能证明运行时行为；framework 内部 bug 的 GAP 不是静态构图规则的 false negative。
- 本批没有新的正常控制 fixture；控制误报率沿用第一批 3 个内部 controls（0/3），只能证明当前规则集在这 3 张图上不误报，不是独立证据。
- ACR008/ACR009 本批 0 个预期样本、0 条 finding：未验证，既不能说有效，也不能说只有噪声。ACR004/ACR005/ACR006/ACR101–ACR105 同样无本批样本。
- labels 与 fixture 元数据在 7 个 case 上不一致（source_type/graph_visible）；本报告以 labels 为准，未修改任何一侧。
- 部分 fixture 受版本/远端环境限制（lg2-4879、lg2-5062、lg2-5362、so-79788210），静态扫描不受影响，但运行时复现需各自环境。
- 相关性/误导/off-rule 判定是分析者判断，逐条写在 predictions.json 的 notes；没有第二标注者，无一致性统计。

## 9. 复现

```sh
# 汇总（离线、不联网、不调用模型；强校验 labels sha256）
PYTHONPATH=src python3 scripts/summarize_realworld2_eval.py

# 本报告对应的归档扫描
for d in evals/realworld2/cases/*/; do
  PYTHONPATH=src python3 -m agent_composition_review scan "$d" --fail-on never --json
done
```

输出：`evals/realworld2/results/pilot2-2026-10-18/predictions.json`、`summary.json`、`REPORT.md`。
