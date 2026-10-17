# InvalidUpdateError：子图把完整状态当成补丁回传

一个 3 分钟可跑通的 LangGraph 父/子图状态泄漏示例。子图未声明 `output_schema`，把自己不拥有的父图字段 `b`（旧值）连同完整状态一起写回，与并行 sibling 分支的新 `b` 冲突。

- 复现 issue：[langchain-ai/langgraph#6446](https://github.com/langchain-ai/langgraph/issues/6446)
- 回归测试：[evals/cases/test_subgraph_boundary.py](../../evals/cases/test_subgraph_boundary.py)
- 案例说明：[docs/cases.md](../../docs/cases.md)
- 已验证版本：langgraph 1.0.3、langchain-core 1.0.4、Python 3.12

## 症状

运行 `run.py` 的第一段，会看到：

```text
按预期失败：InvalidUpdateError: At key 'b': Can receive only one value per step. Use an Annotated key to handle multiple values.
For troubleshooting, visit: https://docs.langchain.com/oss/python/langgraph/errors/INVALID_CONCURRENT_GRAPH_UPDATE
```

单独运行 `broken.py` 会得到同样的 traceback。

## 最小结构

```text
START ──┬─> sibling            写 b
        └─> child（子图） ────┬─> left   写 s1，读取 a、b
                              └─> right  写 s2，读取 a、b
```

State 有 4 个键：`a`（只读上下文）、`b`（sibling 所有）、`s1`/`s2`（子图所有）。代码见 [broken.py](broken.py)。

## 根因：组件返回“完整快照”，父图当成“更新补丁”

LangGraph 合并节点返回值时，把子图节点（编译后的子图）的**输出**当作对父图状态的更新补丁。子图默认的输出 schema 等于输入 schema，所以即使 `left`/`right` 只返回 `{"s1": ...}`、`{"s2": ...}`，编译后的子图对外输出仍是完整 State：继承来的 `a`、**旧的 `b`**，加上新的 `s1`/`s2`。

于是父图在同一个 superstep 收到两路对 `b` 的写入：

- sibling 分支：`{"b": "old|updated"}`
- child 分支：`{"a": 1, "b": "old", "s1": 4, "s2": -2}`

`b` 是只有一个值的 channel（LastValue），同一 superstep 只能收到一个值，合并时抛出 `InvalidUpdateError: At key 'b'`。

关键不是“子图不该读 `b`”——`left` 计算要用 `len(b)`，它应该读；而是“子图不该回传自己不曾拥有的 `b`”。这就是字段所有权（field ownership）问题：组件的输入上下文和输出补丁不必相同，边界要显式声明。

## 修复：声明 output_schema

最小 diff（完整文件见 [fixed.py](fixed.py)）：

```diff
+class ChildOutput(TypedDict):
+    s1: int
+    s2: int
+
- child = StateGraph(State)
+ child = StateGraph(State, output_schema=ChildOutput)
```

- 输入 schema 仍是完整 `State`：`left`/`right` 继续读取父图的 `a`、`b`；
- 输出 schema 收窄为 `ChildOutput`：子图只把 `s1`/`s2` 写回父图；
- 并行结构不变：`START` 仍同时扇出到 `sibling` 和 `child`。

修复后的结果：

```text
{'a': 1, 'b': 'old|updated', 's1': 4, 's2': -2}
```

## 运行

需要 Python 3.10+ 和 `langgraph>=1.0,<2`；仓库已含 `.venv`（Python 3.12 + langgraph 1.0.3）。

```bash
.venv/bin/python examples/langgraph-subgraph-leak/run.py
```

`run.py` 依次执行 broken 与 fixed：broken 必须抛出 `InvalidUpdateError`（`At key 'b'`），fixed 必须得到期望结果，否则退出码为 1。也可以单独看原始 traceback：

```bash
.venv/bin/python examples/langgraph-subgraph-leak/broken.py
.venv/bin/python examples/langgraph-subgraph-leak/fixed.py
```

## 用 acr scan 找到它

`acr` 的安装方式见[根 README](../../README.md)。装好后在仓库根目录运行：

```bash
acr scan examples/langgraph-subgraph-leak/broken.py
```

实际输出（节选，langgraph 1.0.3 + 本仓库 v0.1；有 `high` finding 时退出码为 1）：

```text
$ acr scan examples/langgraph-subgraph-leak/broken.py
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

  ...
```

同一次扫描还会报 ACR002（Parallel branches write the same state key without a reducer）：子图回传的 `b` 与 sibling 的 `b` 在同一 superstep 竞争。两条 finding 指向同一个根因——ACR001 指认输出边界，ACR002 描述并发写的后果。

规则详情：[docs/rules/ACR001.md](../../docs/rules/ACR001.md)。修复后的文件应无 finding：

```bash
acr scan examples/langgraph-subgraph-leak/fixed.py
# 1 file(s) parsed, 0 skipped - 0 finding(s): high 0, medium 0, low 0
#   No composition issues found.
```

## 用 acr trace 看运行时序列

`trace.jsonl` 是 [broken.py](broken.py) 失败运行时的一条精简事件序列，已规范化为 acr 的统一事件格式（JSONL，每行一个事件）。在仓库根目录运行：

```bash
.venv/bin/acr trace examples/langgraph-subgraph-leak/trace.jsonl
```

实际输出（节选）：

```text
$ acr trace examples/langgraph-subgraph-leak/trace.jsonl
4 event(s) (jsonl) - 1 finding(s): high 1, medium 0, low 0

timeline:
  #001  e1      node_start   node=child  step=1
  #002  e2      node_start   node=sibling  step=1
  #003  e3      node_end     node=sibling  step=1  writes={"b": "old|updated"}
  #004  e4      node_end     node=child  step=1  writes={"a": 1, "b": "old", "s1": 4, "s2": -2}

  HIGH   ACR103  Parallel nodes in one step wrote the same state key
    event seq 3
    Step 1: nodes 'child', 'sibling' both wrote state key 'b'. Concurrent writes to a non-reduced key raise InvalidUpdateError; with a reducer the ordering is implicit.
    evidence:
      - e3: node 'sibling' wrote 'b' in step 1 (seq 3)
      - e4: node 'child' wrote 'b' in step 1 (seq 4)
    fix: Give the key a reducer (e.g. ``Annotated[list, operator.add]``) or route the parallel nodes to write different keys.
    docs: docs/rules/ACR103.md
```

事件字段最少需要 `id`、`kind`（`node_start` / `node_end` / `tool_start` / `tool_end` / `error` / `state`）、`node`、`step`、`writes`；也接受 JSON 数组、带 `events` 数组的统一对象，或 LangGraph `stream_mode="debug"` 的原始事件。ACR103 看到的是“同一步两个节点写同一个键”这一层；本例的根因修法不是给 `b` 加 reducer，而是用 `output_schema` 让子图不再写 `b`——finding 是线索，根因仍需回到代码确认。

## 为什么不用别的“修复”

- 给 `b` 加 reducer（`Annotated[str, ...]`）只会让两个写者“合法并存”，两个 `b` 的语义仍然错误，还掩盖了字段所有权问题。
- 把子图拆成普通节点能绕过问题，但会失去子图封装、嵌套并行和独立测试；`output_schema` 是更小的边界修复。
- 在子图节点里手动 `return {}` 或过滤返回值并不改变输出 schema——编译后的子图仍会输出继承来的完整快照。

## 文件

| 文件 | 作用 |
| --- | --- |
| [broken.py](broken.py) | 复现 `InvalidUpdateError` |
| [fixed.py](fixed.py) | `output_schema` 最小修复 |
| [run.py](run.py) | 一键演示与退出码检查 |
| [trace.jsonl](trace.jsonl) | 失败运行的简化事件序列，供 `acr trace` 演示 |
