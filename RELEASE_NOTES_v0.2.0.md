# ACR v0.2.0 — declared keys, cache identity, retry subgraphs

v0.2.0 是基于真实案例试点和验证批次的一次规则扩展。14 条规则，新增 3 条静态检查，并把链式构图写法纳入分析。

## 新增规则

- **ACR007 · declared-keys**：节点（含 class-based `__call__`）返回 dict 字面量里不在 graph state schema 中的键。LangGraph 会静默过滤这类写入；典型症状是“字段没写进去”或 `Must write to at least one of [...]`。
- **ACR008 · cache identity**：节点启用 `cache_policy`，但 callable 是 callable object / lambda / partial / 未解析参数。cache key 可能退化成动态 namespace，共享 cache 的图会串结果。
- **ACR009 · retry + multi-subgraph**：节点有 `retry_policy`，函数体内调用多个子图的 `.invoke/.ainvoke`。重试/恢复后，后一个子图可能复用前一个子图的 checkpoint 命名空间。

同时修复了链式构图赋值 `StateGraph(S).add_node(...).add_edge(...).compile()` 此前不进入分析的问题。

## 真实案例验证

两批公开案例，都是开发/验证型试点，不是统计保留集：

- 第一批 14 个 GitHub issue + 3 个控制：发现 13 个 GAP；v0.1.1 修了其中两个 P0。
- 第二批 20 个新案例（15 GitHub + 3 LangChain Forum + 2 Stack Overflow）验证 14 条规则：
  - 可分析 15/20；可表达 9/15 = 60%；可表达案例命中 6/9 = 66.7%；
  - **ACR007 预期 3 例全部命中**，但 1 个 off-rule、finding 精度不干净；
  - **ACR008/ACR009 本批 0 样本、0 命中，未验证**；
  - ACR002 3 个预期案例全部 miss（只建模兄弟扇出，漏 retry 自环重叠）；
  - 最大 GAP 是 command-send（4/9），属运行时/平台语义，不应硬加静态规则。

报告：[第一轮](evals/realworld/results/pilot-2026-10-17/REPORT.md) · [第二轮](evals/realworld2/results/pilot2-2026-10-18/REPORT.md)

## 安装

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.2.0"
pipx inject agent-composition-review langgraph   # acr demo 需要

acr --version
acr demo
```

或：

```sh
uv tool install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.2.0"
```

## 已知限制

- ACR007 的修复建议需要看场景：class node 的私有 schema 优先考虑 `add_node(..., input=...)`，不要盲目把键补进全局 state；v0.2.0 文案已补充这一点。
- ACR008/ACR009 还没有独立正负样本验证，先当 review 级提示使用。
- ACR002 对 retry 自环/并行重叠的召回不足；command-send、resume/replay、parallel interrupt 仍建议走 `acr trace` 或版本感知，不硬加静态规则。
- 真实案例试点样本小、来源有偏；两批之间的 framework GAP 比例变化不能解读为真实分布变化。
- 不宣称 benchmark 提升或检测准确率；规则是启发式检查，请结合证据人工确认。
