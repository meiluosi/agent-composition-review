# 三个 worked examples

每个例子都按同一顺序：**错误假设 → 证据 → 最小修复 → 回归断言**。
重点不是背修法，而是看“证据怎样把错误假设推翻”。

## 1. ACR001：子图越界回传，父图并行写同一个键

完整案例：[LG #6446](../../../docs/atlas/lg-6446.md)
可运行示例：[examples/langgraph-subgraph-leak/](../../../examples/langgraph-subgraph-leak/)

### 错误假设

“父图只是并行跑两个分支，报错是因为 `b` 没加 reducer；给 `b` 加个 `operator.add` 就好了。”

这个假设只看到了“并行写同键”，漏掉了**子图为什么在写 `b`**。

### 证据

- 子图用 `StateGraph(State).compile()` 挂到父图，**没有 `output_schema`**；
- 子图与父图共享 `a`、`b`、`s1`、`s2`；
- 父图同时扇出 `sibling`（写 `b`）和 `child`（子图，写 `s1`/`s2`）；
- 子图返回的是**完整状态快照**，其中带着父图旧的 `b`，于是和 `sibling` 的新 `b` 在同一 superstep 冲突；
- `acr scan` 指向 ACR001（子图边界）和 ACR002（并行写同键）两处，而不是只指 `b`。

关键判断：**加 reducer 会让冲突消失，但子图仍会把不属于它的旧 `b` 回传**；这是把边界问题藏起来，不是修边界。

### 最小修复

收窄子图输出，不动并行结构：

```diff
- child = StateGraph(State)
+ child = StateGraph(State, output_schema=ChildOutput)
```

```python
class ChildOutput(TypedDict):
    s1: int
    s2: int
```

### 回归断言

- broken 版本仍能复现 `InvalidUpdateError: At key 'b'`；
- fixed 版本返回 `{"a": 1, "b": "old|updated", "s1": 4, "s2": -2}`；
- 子图仍能读取父图的 `a`/`b` 计算 `s1`/`s2`；
- 并行扇出关系没有被改成串行。

## 2. ACR007：节点返回未声明键，字段被静默过滤

完整案例：[LG #8320](../../../docs/atlas/lg-8320.md)

### 错误假设

“下游节点读不到 `note`，一定是下游逻辑写错了，或者状态没有持久化。”

先别改下游。LangGraph 在节点边界会过滤未声明键，字段可能根本没进 state。

### 证据

- state schema 只声明了 `x`；
- 上游节点 `return {"note": ...}`；
- `note` 不在 schema 中，LangGraph 过滤后下游读到 `None`/缺键；
- `acr scan` 的 ACR007 finding 指向 return 行和 schema 声明行；
- 运行日志里没有任何 `InvalidUpdateError`，因为这是**静默过滤**，不是并发冲突。

### 最小修复

把键声明进 schema（多写者时加 reducer），或删除/重命名返回键：

```python
class State(TypedDict):
    x: int
    note: str
```

类节点如果用了独立 input schema，优先用 `add_node(..., input=...)` 显式映射，不要靠未声明键“碰巧”传下去。

### 回归断言

- broken 版本：下游读不到 `note`，且没有报错；
- fixed 版本：下游能读到 `note`；
- 其他字段值不变；
- 如果 `note` 有多个写者，补一条 reducer/合并顺序测试。

## 3. ACR106：resume 后已完成节点重跑

完整案例：[LG #9276](../../../docs/atlas/lg-9276.md)；负向控制：[LG #3875](../../../docs/atlas/lg-3875.md)

### 错误假设

“resume 就是从 checkpoint 继续，节点重跑是 LangGraph 的正常行为；所有重启都不用管。”

错在把两类重启混在一起：

- **被 interrupt 的节点**在 resume 后重启，是预期行为；
- **已经 `node_end` 且没有 interrupt 的节点**在 resume 后重启，才可能重复副作用。

### 证据

用 debug trace（`stream_mode="debug"`）看事件顺序：

```text
agent  node_end      （已完成，模型调用第 1 次）
gate   node_end      interrupts=[...]（被中断，不是完成）
resume  marker
agent  node_start    （已完成节点又启动，模型调用第 2 次）
gate   node_start
```

`acr trace run.jsonl --resume-at 4` 会指向 ACR106：`agent` 在 resume 前已完成，resume 后又启动。
注意：`--resume-at` 需要指向恢复前最后一条事件；LangGraph 默认 debug 流没有 resume 事件。

### 最小修复

从精确 checkpoint / pending writes 恢复，确保已完成节点不重跑；给副作用加幂等键作为第二道防线。
不要用“关掉 retry”或“resume 前清空状态”掩盖问题。

### 回归断言

- 模型/工具调用次数：完成节点只执行一次；
- 被 interrupt 的 `gate` 仍能在 resume 后正常继续；
- 重复 resume 不会产生第二次外部副作用；
- 用 trace 事件 ID 复跑，确认 ACR106 不再命中。

## 共同原则

- **先复现，再归因**：没有失败样本时，先构造最小图。
- **规则只是线索**：finding 指向的是一处边界，不是自动根因。
- **一次只改一个边界**：改完立刻跑回归。
- **证据不足就写“不知道”**：列出还需要什么日志/输入，不要用框架术语补全因果。
