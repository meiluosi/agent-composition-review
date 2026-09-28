# LangGraph 1.0.3 工作流诊断与修复

## 实际结论

冲突发生在父图接收并行分支结果时。`node_b` 只返回 `b`，两个子处理函数分别只返回 `s1` 和 `s2`；但编译后的子图默认输出其完整状态。子图把输入中未修改的旧 `b` 一并返回父图，使父图在同一步收到两个 `b` 写入。不是两个子处理函数直接争写同一字段。

修复仅新增 `ChildOutput`（含 `s1`、`s2`），将子图构造改为 `StateGraph(State, output_schema=ChildOutput)`。完整的 `State` 仍是子图内部状态和默认输入 schema，因此子处理函数可以读取全部四个字段。子图输出只携带自己负责的两个字段。父图、节点函数、注入参数、全部边及并行结构保留。

## 诊断证据

读取材料仅为 task.md、repro.py、evidence/repro-run.txt 及当前 .venv 内已安装的 LangGraph 源码；没有使用技能、联网、评测器或其他候选结果。

实际重新执行原始程序，退出码 1，复现 `InvalidUpdateError: At key 'b': Can receive only one value per step`。

独立调用原始编译子图，以 node_a 完成后的状态作为输入，实际得到：

```python
{'a': 133, 'b': 'old', 's1': 1, 's2': 2}
```

原始父图的 updates 流实际输出：

```python
{'nodea': {'a': 133}}
{'nodeb': {'b': 'old|parent'}}
{'subgraph': {'a': 133, 'b': 'old', 's1': 1, 's2': 2}}
```

随后发生 `b` 冲突。该输出直接证明子图边界把状态快照当成父图更新返回。

检查的已安装源码位置（相对于 `.venv/lib/python3.12/site-packages/`）：

- `langgraph/graph/state.py:241-242`：未传 input_schema、output_schema 时均使用 state_schema。
- `langgraph/graph/state.py:853-883`：由 output_schema 的字段生成编译图 output_channels。
- `langgraph/graph/state.py:962-969`：节点返回字典中的有效键均被提取为更新，不会因为值等于旧值而忽略。
- `langgraph/graph/state.py:1370`：没有特殊注解的字段使用 LastValue。
- `langgraph/channels/last_value.py:55-65`：同一步多个写入值会抛出 InvalidUpdateError。

没有给 `b` 添加 reducer。允许多个值的归并只能掩盖子图错误输出旧 `b` 的问题；“后者覆盖前者”还可能丢失 node_b 的有效结果。限定子图输出消除了该重复写入来源。

## 最小代码差异

```python
class ChildOutput(TypedDict):
    s1: int
    s2: int

# create_graph 内，其他结构不变
child = StateGraph(State, output_schema=ChildOutput)
```

没有硬编码结果、串行化、重试、吞异常或修改环境依赖。

## 真实验证

运行命令（案例根目录）：

```sh
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false .venv/bin/python -B candidates/without-skill/validate.py
```

退出码 0；完整输出保存于 `validation-run.txt`，末行为 `ALL CHECKS PASSED`。

验证内容：

1. 再次复现原始错误，记录完整子图输出和父图更新事件。
2. 修复后默认结果精确等于 `{'a': 133, 'b': 'old|parent', 's1': 1, 's2': 2}`；编译子图的实际输出精确等于 `{'s1': 1, 's2': 2}`。
3. 12 组不同输入均符合默认函数语义。
4. 6 组替换全部四个函数的运行，所有函数都实际读取完整四字段。逐个记录输入，断言 node_a 看到原始状态，node_b/sub_1/sub_2 看到 node_a 更新后的同一完整阶段状态，且子节点之间不会看到对方尚未提交的更新。
5. 每次注入运行以计数器断言四个函数各执行一次，并精确校验四个最终结果和调用者输入未改变。
6. node_b/sub_1/sub_2 共用三方 Barrier，必须全部进入后才能返回，5 秒超时即失败。因此串行父分支或串行子分支无法通过；六次运行均通过。包含三次 invoke 和三次 ainvoke（注入处理函数为同步函数）。
7. 返回与原值相同的合法写入、以及空字典更新，均保留正确最终状态。

## 适用范围与未证明事项

结论覆盖需求中四字段及固定写入职责：node_a 写 a、node_b 写 b、sub_1 写 s1、sub_2 写 s2。若将来子图还应负责其他字段，应同步更新 ChildOutput；本修复不擅自支持违反这些职责的注入函数。空更新时输出 schema 仍会输出子图已有的 s1/s2 值；由于并行父分支仅拥有 b，这不会改变有效最终状态。

测试使用当前安装的 LangGraph 1.0.3 和本地同步处理函数，并验证图的异步调用入口；没有证明原生 async 处理函数、持久化检查点、暂停恢复、失败重试、动态拓扑、跨版本兼容或有外部副作用函数的 exactly-once 语义。Barrier 验证的是这组正常执行中的真实并发进入，不是性能基准。
