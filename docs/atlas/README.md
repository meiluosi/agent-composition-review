# ACR 真实案例图谱

> **静态站点**：[`index.html`](index.html)（GitHub Pages `/atlas/`）提供按症状关键词、规则 ID、issue 号与证据状态的搜索；它只是索引和筛选器，案例正文、证据与验证边界仍以本目录的 Markdown 页为准。

按“症状 → 规则/命令 → 证据 → 最小修复 → 验证边界”整理的 11 个 LangGraph 真实案例。每个案例都给出原始 issue/公开代码、仓库内可扫描的 fixture / trace、当前 `acr 0.6.0` 的真实输出，以及它到底能被算作哪种证据。

这不是 benchmark，也不是命中率报告。样本来自两轮真实案例验证、一轮定向验证和一轮 lead 追踪；案例页只陈述“这一条规则在这个案例上检测到了什么”，不推断真实世界分布，也不代表 issue 已被上游修复。

## 状态怎么读

| 状态 | 含义 |
| --- | --- |
| **grounded** | 至少一个真实公开案例或本地复现验证了规则的检测行为；页面会注明它来自验证命中、负向控制还是设计源复扫。 |
| **experimental** | 规则已实现，但只有设计源、邻近案例或合成 fixture 证据；没有独立 exact 命中，不能当作“已验证”。 |

`grounded` 也只描述“检测行为”，不表示修复方案已被上游采纳或线上问题已解决。所有 fixture 都是仓库内的最小重构，不是对 issue 原文的逐字转载。

## 索引

| # | 症状关键词 | 规则 | 案例 | 状态 | 证据边界 |
| ---: | --- | --- | --- | --- | --- |
| 1 | `InvalidUpdateError` at key `b`；父子图并行；子图覆盖父图字段 | ACR001（+ ACR002） | [LG #6446 子图回传完整状态](lg-6446.md) | grounded | 开发集复扫 + 本地运行示例 |
| 2 | 两个并行子图汇到 sink；`At key 'question'` | ACR001（+ ACR002） | [LG #1964 并行子图共享状态](lg-1964.md) | grounded | 第二批验证命中 |
| 3 | 节点写了字段但下游读不到；无报错静默丢失 | ACR007 | [LG #8320 未声明的返回键被静默过滤](lg-8320.md) | grounded | ACR007 设计源；静态命中 |
| 4 | 类节点返回另一个 schema 的键；`Must write to at least one of ['foo']` | ACR007 | [LG #2644 类节点写未声明键](lg-2644.md) | grounded | ACR007 设计源；原报错未复现 |
| 5 | `Send` 扇出到子图后报 `Expected node father to update at least one of ['father']` | ACR007（+ ACR003 顺带） | [LG #2581 Send 到子图返回未声明 kids](lg-2581.md) | grounded | 第二批验证命中 |
| 6 | resume 后被 interrupt 的节点重启；担心副作用重复 | ACR106 | [LG #3875 interrupt 节点重启（负向控制）](lg-3875.md) | grounded | 真实公开 trace；0 findings |
| 7 | resume/replay 后已完成节点又跑了一遍 | ACR106 | [LG #9276 resume 重跑已完成节点](lg-9276.md) | grounded | 本地复现正向；非社区附件 |
| 8 | 共享 cache 后 B 图返回 A 图结果；节点像没执行 | ACR008 | [LG #8753 callable cache 串结果](lg-8753.md) | experimental | exact 设计源；无独立命中 |
| 9 | `retry_policy` 节点调用两个子图；恢复后拿到第一个子图结果 | ACR009 | [LG #9253 retry 与子图 checkpoint](lg-9253.md) | experimental | exact 设计源；批次无新样本 |
| 10 | 条件自环 retry + 并行分支；`At key 'count_b'` | ACR010 | [LG #2336 retry 自环写非 reducer 键](lg-2336.md) | experimental | ACR010 开发集；无独立 exact 样本 |
| 11 | `cache_policy` 在类方法上；被误报为匿名 callable | ACR008 | [vanara bound method 误报](lg-vanara-bound-method.md) | experimental | 真实公开代码负向控制；无故障报告 |

## 按症状快速定位

- 报错里有 **`Can receive only one value per step`**：
  - 键是父图共享键、现场有子图 → [LG #6446](lg-6446.md)、[LG #1964](lg-1964.md)（ACR001 / ACR002）
  - 现场有 retry 自环或重入 → [LG #2336](lg-2336.md)（ACR010，experimental）
- 图能跑，但**字段“没写进去”/下游读到旧值** → [LG #8320](lg-8320.md)、[LG #2644](lg-2644.md)、[LG #2581](lg-2581.md)（ACR007）
- **缓存命中后结果不是本次运行的**、节点调用次数异常少 → [LG #8753](lg-8753.md)（ACR008，experimental）
- **`cache_policy` 在类方法/命名函数上却被提示 callable 身份不稳定** → [vanara bound method](lg-vanara-bound-method.md)（ACR008 误报案例）
- **重试/恢复后第二个子图没有真正执行** → [LG #9253](lg-9253.md)（ACR009，experimental）
- **resume / replay 之后节点或副作用重复**：
  - 节点在恢复前被 interrupt 过 → [LG #3875](lg-3875.md)（负向控制：重启是预期行为）
  - 节点在恢复前已经完成 → [LG #9276](lg-9276.md)（ACR106）

## 本地复现命令

克隆仓库并安装 `acr` 后（见[根 README](../../README.md)），在仓库根目录运行：

```sh
# 静态扫描：ACR001 / ACR007 案例
acr scan evals/realworld/cases/lg-6446/graph.py --fail-on never
acr scan evals/realworld2/cases/lg2-1964/graph.py --fail-on never
acr scan evals/realworld/cases/lg-8320/graph.py --fail-on never
acr scan evals/realworld/cases/lg-2644/graph.py --fail-on never
acr scan evals/realworld2/cases/lg2-2581/graph.py --fail-on never

# 实验性规则：ACR008 / ACR009 / ACR010
acr scan evals/realworld/cases/lg-8753/graph.py --fail-on never
acr scan evals/realworld/cases/lg-9253/graph.py --fail-on never
acr scan evals/realworld2/cases/lg2-2336/graph.py --fail-on never

# ACR008 误报回归：bound method 应保持安静
acr scan tests/fixtures/acr008_negative_bound_method.py --fail-on never

# 轨迹：ACR106 负向 / 正向
acr trace evals/realworld3/cases/lg3-3875/trace.jsonl
acr trace evals/realworld3/cases/lg3-9276-repro/trace.jsonl
```

输出行号随 fixture 版本变化；以 `finding` 的 `file:line` 和 `evidence` 为准。

## 证据来源与限制

- **第一批**（2026-10-17）：14 个 GitHub issue + 3 个内部正常控制，开发型试点；报告在 [pilot-2026-10-17/REPORT.md](../../evals/realworld/results/pilot-2026-10-17/REPORT.md)，修复后复扫在 [POSTFIX.md](../../evals/realworld/results/pilot-2026-10-17/POSTFIX.md)。
- **第二批**（2026-10-18）：20 个新案例（15 GitHub + 3 LangChain Forum + 2 Stack Overflow），ACR007 的验证命中来自这一批；报告在 [pilot2-2026-10-18/REPORT.md](../../evals/realworld2/results/pilot2-2026-10-18/REPORT.md)，后续补充在 [POSTFIX.md](../../evals/realworld2/results/pilot2-2026-10-18/POSTFIX.md)。
- **第三轮定向验证**（2026-10-18）：专门找 ACR106/ACR008/ACR009/ACR010 的真实样本；报告在 [pilot3-2026-10-18/REPORT.md](../../evals/realworld3/results/pilot3-2026-10-18/REPORT.md)。
- **第四轮 lead 追踪**（2026-10-18）：追第三轮未验证的代码搜索线索；ACR008 在真实公开代码上暴露并修复了一个 bound method 误报，ACR009/010 仍无 exact；记录在 [LEADS_VERIFIED.md](../../evals/realworld4/LEADS_VERIFIED.md)。
- 每个 `case.json` / `graph.py` / `trace.jsonl` 都是仓库内最小重构，不是 issue 原文；部分 fixture 不在本地运行，只做静态扫描。
- `scan.txt` 是历史版本冻结的归档输出，**不要**拿它和当前 `acr` 输出比较；案例页引用的是 `acr 0.6.0` 的当前输出。
- 这些案例已参与规则设计或验证，不能当“未见保留集”；不能用它们计算或宣称真实世界的召回率、准确率或 benchmark 提升。
- 没有第二标注者；状态与判断是维护者依据仓库证据做的分类，`experimental` 就是明确承认“证据不足”。

## 站点维护

- 站点是纯静态文件：`index.html`（页面 + 内嵌案例 JSON）、`site.js`、`site.css`；没有构建步骤，不依赖外部 CDN、字体或追踪脚本。
- 新增或修改案例时，同步更新 `index.html` 中 `id="case-data"` 的 JSON（字段：`issue`、`slug`、`title`、`summary`、`keywords`、`rules`、`status`、`boundary`、`source`）以及上面的索引表。
- `docs/.nojekyll` 让 GitHub Pages 原样提供文件；`docs/index.html` 把站点根重定向到 `atlas/`。
- 本地查看：`python3 -m http.server -d docs` 后打开 `/atlas/`（直接双击 `index.html` 也能用，但相对链接的体验可能不同）。
