# 第二轮真实案例验证协议

冻结日期：2026-10-18。
状态：验证批次，不是统计意义上的保留集。

## 1. 目的

在**未参与规则设计**的公开案例上验证：

- ACR001–ACR009 的规则可表达率、命中率、误导率；
- 3 个正常控制图是否仍然 0 误报；
- 新规则（ACR007 declared-keys、ACR008 cache identity、ACR009 retry+multi-subgraph）在真实案例上是否只有噪声；
- 第一批“多数真实 issue 是框架内部 bug”的观察，在非 GitHub 来源上是否仍然成立。

## 2. 来源与选择

- 15 个 GitHub issue：`langchain-ai/langgraph` 公开 issue，按“用户构图/用法优先、有代码、有具体报错”排序；排除第一批 14 个。
- 5 个社区来源：LangChain Forum、Stack Overflow，优先有代码、可做最小复现。
- 所有案例在规则实现之后入选；`candidates.json` 记录来源、类型猜测和 raw 路径。
- 原始正文缓存到 `raw/`，不进入 Git；公开 only links + metadata + our fixtures。

排除：纯 PR、纯 feature request、只有截图/无代码、无法离线复现的。

## 3. Fixture 规则

每个 `cases/<case_id>/`：

- `case.json`：来源 URL、版本、`source_type`、`graph_visible`、观察行为、结构焦点、材料等级、notes；
- `graph.py` / `broken.py`：我们独立写的最小构图，能 AST 解析；不要求复现框架内部 bug；
- `scan.txt`：实际运行 `acr scan <case_dir> --fail-on never` 的 stdout；
- 可选 `expected.md`。

不复制原帖大段正文/用户代码；只保留复现构图所需结构。材料不足就标 `insufficient`，不硬编造。

## 4. 盲标

在看 `cases/`、`results/`、`scan.txt` 之前完成 `labels.json`：

- `source_type`：`user_graph` / `framework` / `usage` / `feature` / `insufficient`；
- `graph_visible`：能否从材料看出构图；
- `expected_rule`：现有 14 条规则之一，或 `GAP:<category>`，或 `NONE`；
- `expected_finding`：如果规则命中应该指出什么；
- `normal_control`、`material_notes`、`confidence`。

`GAP` 类别沿用第一批：`declared-keys`、`reducer-semantics`、`resume-replay`、`parallel-interrupt`、`command-send`、`retry-cache`、`other`。

## 5. 扫描与判定

只运行 `acr scan <case_dir> --fail-on never --json`，记录每条 finding 的 rule/severity/line/evidence。判定：

- `hit`：命中预期规则且 finding 与故障相关；
- `miss`：预期规则存在但未命中；
- `false_positive`：正常控制图被报，或 finding 对故障给出误导解释；
- `incidental`：finding 真实但与本案例故障无关；
- `gap`：预期为 `GAP:*`，现有规则无覆盖；
- `not_applicable`：材料不足/规则不适用。

## 6. 指标

- 可分析率 = `graph_visible=true` 且 fixture 可解析 / 20；
- 规则可表达率 = `expected_rule` 属于现有 14 条 / 可分析案例；
- 命中率 = `hit` / 可表达案例；
- 误导率 = `false_positive` / 发出的 finding 总数；
- 控制误报率 = 被报控制图 / 3；
- GAP 分布 + 分来源/分 source_type；
- 第一批 vs 第二批的关键差异（例如框架 bug 比例、rule expressibility）。

样本小，不计算置信区间、不宣称泛化。

## 7. 解释边界

- 本批规则已冻结；如果看完结果后修改规则，必须把本批改记为开发集，并另找新案例验证。
- 多数 framework bug 不是静态构图规则的 false negative；报告必须分开。
- 静态分析看不到运行时版本差异；`source_type=framework` 的 GAP 不应算作“用户构图错误未检测”。
- 正常控制图是内部 fixture，不是社区正常图；0/3 只是弱证据。
- 本批社区来源样本少（5 个），只能作为方向性补充，不能代表 Forum/SO 总体。
