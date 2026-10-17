# 未验证 lead 追踪结果

第三轮留下 4 条“代码搜索命中但未抓取”的 lead。这一轮逐条抓取公开文件、读结构、跑 `acr scan`，不执行被扫描代码。

## 结论表

| lead | target | 实际结构 | 结论 |
| --- | --- | --- | --- |
| vanara-ai/vanara-server | ACR008 | `cache_policy` 在 bound method（`self._parse_jd_node`）上 | **ACR008 误报**，已修；真实负向控制 |
| pdhoolia/langgraph-mcp | ACR008 | `cache_policy` 在模块级命名函数上 | 邻近案例，不是 identity 失败 |
| corovcam/Universal-Object-Mapping | ACR008 | `cache_policy` 在命名函数上 | 邻近案例，不是 identity 失败 |
| Theedon/Quizzer | ACR009 | retry 在内部 LLM 节点；调用子图的节点无 retry | 邻近，不是 exact |
| SE-UUlm/sieve | ACR009 | retry 在 `db_step`；子图调用在别处 | 邻近，不是 exact |
| rizkikadafi/odoo-agent | ACR009 | retry 节点只调用 **1** 个子图 | 邻近，不是 exact |
| hahaliu1029/Actus | ACR009 | retry 节点调 LLM；子图调用在另一个 helper | 邻近，不是 exact |
| ACR010 | ACR010 | 没有确认的结构线索 | 无新样本 |

## 1. ACR008：真实公开代码暴露误报

文件：<https://github.com/vanara-ai/vanara-server/blob/main/app/resume_optimizer.py>

代码形态：

```python
workflow.add_node(
    "parse_jd",
    self._parse_jd_node,
    cache_policy=CachePolicy(),
    ...
)
```

修复前 `acr scan` 在 line 218 报 ACR008，理由是“callable could not be resolved to a module-level function”。但 bound method 有稳定的 `module + qualname`：

```python
from langgraph.pregel._call import identifier
# identifier(nodes.run) -> "__main__.Nodes.run", not None
```

LangGraph 的 cache key 用 `identifier(call.func) or "__dynamic__"`；bound method 不属于 `__dynamic__`。所以这是**误报**，不是 cache identity 失败。

修复：

- scanner 收集类方法名；
- `add_node(..., nodes.run, ...)` 这类 `ast.Attribute` 能解析为 method；
- ACR008 不再把有稳定类/方法名的 bound method 当成匿名 callable。

复扫：

```text
before: ACR008 at line 218
after:  0 ACR008 findings
```

边界：这是**真实公开代码上的负向控制**，不是“确认了一个 cache bug”。没有 issue 报告该 repo 出现错误结果；ACR008 仍保持 `experimental`。

## 2. ACR009：只有邻近案例

- **Theedon/Quizzer**：`RetryPolicy` 在 inner `quiz_generator` / `quiz_reviewer`（LLM 节点）；外层调用 subgraph 的 `subgraph_generator` 没有 retry_policy。
- **SE-UUlm/sieve**：retry 在 `db_step`；`product_subgraph.ainvoke` 在另一个 helper，不在 retry 节点内。
- **rizkikadafi/odoo-agent**：`context_collection_wrapper` 有 retry_policy，但只调用一个 `compiled_subgraph`；不够 ACR009 的“2+ 子图”条件。
- **hahaliu1029/Actus**：retry 在 planner（调 LLM）；subgraph 调用在 coordinator helper，没有 retry_policy。

没有一条满足“同一个 retry_policy 节点内调用 2 个以上子图”。ACR009 仍无新 exact 样本。

## 3. ACR010：没有线索

代码搜索命中的是框架源码、fan-in、interrupt 循环，不是“条件自环 + 写已声明无 reducer 键”。没有可验证的新样本。

## 判定

- ACR008：发现并修复一个真实误报；没有正向 exact 失败样本，保持 `experimental`。
- ACR009：0 新 exact，保持 `experimental`。
- ACR010：0 新 exact，保持 `experimental`。
- 本批真实代码负向控制支持 ACR008 的“不要误报”行为，但不支持“能抓到真实 cache identity 故障”的正向结论。
