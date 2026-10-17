# 真实案例试点协议

冻结日期：2026-10-17。
状态：开发型试点，不是保留集 benchmark。

## 1. 问题

在真实 LangGraph/LangChain 故障上：

1. 有多少案例的材料足以做构图分析？
2. `acr scan` 的现有规则能表达多少？命中多少？
3. 命中里有多少是关于故障本身的，多少只是顺带发现？
4. 正常图上有多少误报？
5. 哪些问题现有规则完全没覆盖？

## 2. 来源与入选

来源优先级：

1. `langchain-ai/langgraph` 的公开 issue，带最小复现代码或明确构图结构；
2. `langchain-ai/langchain` 中与 agent/graph 组合相关的 issue；
3. LangChain Forum / Stack Overflow / Reddit 的公开问题（仅有代码时进入 fixture）。

入选标准：

- 公开可访问、有稳定 URL；
- 描述了一个具体的组合失败：报错、状态丢失/覆盖、重复副作用、并行冲突、恢复后重跑等；
- 能看出 LangGraph 构图或运行结构；
- 能写成最小复现，且不依赖私有服务、账号、数据；
- 不是纯 API 用法问答、功能请求或与组合无关的普通 bug。

排除标准：

- 只有截图/自然语言，无法判断图结构；
- 依赖私有模型、数据库或业务数据，无法离线复现；
- 与 `acr` 当前目标无关（如 UI、序列化格式、部署）。

`candidates.json` 只保存元数据。完整 issue 正文/评论缓存到 `raw/`，不进入 Git。

## 3. Fixture 规则

每个 `cases/<case_id>/` 包含：

- `case.json`：来源 URL、标题、版本、观察到的行为、我们关注的结构、预期规则/缺口、材料等级；
- `graph.py`（或 `broken.py` / `healthy.py`）：**我们独立写的**最小构图代码，注释中链接来源；
- 可选 `trace.jsonl`：仅当 issue 提供了可用的 debug/事件流，且已脱敏；
- 可选 `expected.md`：维护者复核记录。

不复制 issue 的大段代码；只保留复现构图所必需的片段。fixture 必须能单独运行或至少能被 AST 解析，不要求复现框架内部 bug。

## 4. 标注（盲标）

先由标注者在**不看扫描结果**的情况下写 `labels.json`：

- `case_id`、`source`、`source_type`：
  - `user_graph`：用户构图/状态设计问题；
  - `framework`：LangGraph 框架行为/缺陷；
  - `usage`：用法误解或文档问题；
  - `feature`：功能请求；
  - `insufficient`：材料不足。
- `graph_visible`：是否能从材料看出构图。
- `expected_rule`：`ACR001`…`ACR006`、`ACR101`…`ACR105`、`GAP:<category>` 或 `NONE`。
- `expected_finding`：一句话说明“如果规则命中，应该指出什么”。
- `normal_control`：是否正常图。
- `notes`：不确定性和材料限制。

`GAP` 类别首轮预定义：

- `GAP:declared-keys`：节点返回未声明键、静默丢弃；
- `GAP:reducer-semantics`：ReadOnly/Overwrite/元组 reducer 等 reducer 语义；
- `GAP:resume-replay`：checkpoint 恢复后重跑已完成节点/子图；
- `GAP:parallel-interrupt`：并行 interrupt / 多中断恢复；
- `GAP:command-send`：`Command`/`Send` 的更新、路由、嵌套；
- `GAP:retry-cache`：`retry_policy`、cache 命名空间与副作用；
- `GAP:other`：其他。

## 5. 扫描与判定

扫描者只运行：

```sh
acr scan <case_dir> --fail-on never --json
```

对每个 finding 记录：

- `rule`、`severity`、`file:line`；
- `relevant`：是否指向 issue 报告的故障结构（不是“代码里确实有这个模式”就算相关）；
- `verdict`：
  - `hit`：命中预期规则，且 finding 与故障相关；
  - `miss`：预期规则存在但未命中；
  - `false_positive`：正常控制图被报，或 finding 与故障无关且会误导；
  - `not_applicable`：预期规则不适用/材料不足；
  - `gap`：预期是 `GAP:*`，现有规则无覆盖。

一个 case 可以有多个 finding；主判定只看与预期最相关的那条，其余记为 incidental。

## 6. 指标

- **可分析率**：`graph_visible=true` 且 fixture 可解析 / 全部案例。
- **规则可表达率**：`expected_rule` 属于现有 11 条 / 可分析案例。
- **命中率**：`hit` / 可表达案例。
- **误报率**：`false_positive` / 正常控制案例（以及非正常案例中明显误导的 finding）。
- **缺口分布**：按 `GAP:*` 计数，列出典型 issue。
- **材料限制**：`insufficient` / 全部案例。

样本小，不计算置信区间、不宣称泛化。数字只描述这批案例。

## 7. 解释边界

- 真实 issue 大多是**框架内部 bug**，不等于用户构图错误；ACR 的静态规则本来就不覆盖框架内部语义。这个区分本身是试点的重要产出。
- 一个 issue 的“正确修法”可能是升级 LangGraph，不是改用户图；ACR 不应把这类问题包装成用户错误。
- 只看静态代码，不能证明运行时行为；没有 fixture 运行的案例标 `static_only`。
- 本批案例已用于发现缺口，后续规则修改不能把同批案例当作未见保留集。

## 8. 版本与复现

- 记录 `acr --version`、Python、LangGraph 版本；
- 原始 issue 正文缓存不公开，公开 `candidates.json`、fixture、标签、扫描结果和哈希；
- 重新汇总不需要网络，不调用模型。
