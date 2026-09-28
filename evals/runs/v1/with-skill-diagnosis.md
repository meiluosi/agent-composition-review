# 诊断与实际验证

结论：已证实的失效边界是“已编译子图的完整状态输出 → 父图的字段更新”。四个函数本身返回互不冲突的增量，但子图默认输出完整 State，把输入携带的旧 b 再次写入父图；它和同一步 node_b 的有效 b 更新冲突。子图输出必须明确限定为它拥有的 s1/s2，读取状态仍需完整。

## 范围与证据

仅阅读 task.md、repro.py、evidence/repro-run.txt、agent-composition-review 技能及其 boundary-contracts/review-output 参考，并检查本目录 .venv 中实际安装的 LangGraph 源码。未联网，未读取其他候选、sources、acceptance 或验证器，未修改依赖。实际运行版本为 LangGraph 1.0.3；运行设置 LANGCHAIN_TRACING_V2=false、LANGSMITH_TRACING=false、PYTHONDONTWRITEBYTECODE=1。

真实执行链是：START → node_a → [node_b ∥ 子图(sub_1 ∥ sub_2)] → END。node_a 的输出先更新父图状态；随后三个处理函数应读取相同阶段的完整四字段快照。没有模型、外部服务、缓存、持久化、重试策略或跨次调用记忆参与本复现。

自写 validate.py 先分别调用原始四函数，确认返回 a=133、b='old|parent'、s1=1、s2=2。随后真实运行原图，收集 updates 流，观察到：

```text
nodea    → {'a': 133}
nodeb    → {'b': 'old|parent'}
subnode1 → {'s1': 1}
subnode2 → {'s2': 2}
subgraph → {'a': 133, 'b': 'old', 's1': 1, 's2': 2}
InvalidUpdateError: At key 'b': Can receive only one value per step.
```

独立构造并调用原始子图也实际返回完整四字段，排除了“某个处理函数意外返回 b”以及“只有子图内部并行就会失败”的假设。完整原始执行输出保存于 validation-run.txt。

本地库源码解释了运行证据：

- `langgraph/graph/state.py:242`：未指定 output_schema 时使用 state_schema；输入 schema 同样默认采用完整 State。
- 同文件 `:853`：编译时从输出 schema 选择 output_channels。
- 同文件 `:962`：父节点对字典返回值逐键提取更新；没有按“与原值相等”过滤，也不会推断字段是否由子节点实际修改。
- 同文件 `:1370`：无 reducer 的普通字段使用 LastValue。
- `langgraph/channels/last_value.py:56`：一次更新含多个值就抛出 InvalidUpdateError。

因此，b 的单写者不变量在子图输出边界被破坏。a 也被子图冗余返回，但 node_a 位于前一个父图步骤，故当前报错落在 b。异常阻止最终状态生成，是功能性故障；上述因果链已有运行与源码双重证据。

## 最小修复

fixed.py 仅新增含 s1/s2 的 ChildOutput TypedDict，并把子图构造改为：

```python
child = StateGraph(State, output_schema=ChildOutput)
```

create_graph 的四个可注入参数、默认处理函数、父图 schema、节点、边和两层并行结构均保留。子图仍以完整 State 作为内部状态和输入；只有返回父图的输出投影为 s1/s2。没有加入丢弃冲突值的 reducer，也没有序列化节点或硬编码业务结果。指定输出字段依赖任务明确给出的字段所有权约定。

修复后真实父图 updates 流为 nodea={'a':133}、nodeb={'b':'old|parent'}、subgraph={'s1':1,'s2':2}；最终状态为：

```python
{'a': 133, 'b': 'old|parent', 's1': 1, 's2': 2}
```

## 自写验证与结果

在案例目录执行：

```sh
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false PYTHONDONTWRITEBYTECODE=1 .venv/bin/python candidates/with-skill/validate.py
```

实际执行成功，输出 `ALL CHECKS PASSED`。判据与覆盖包括：

1. 原图必须复现 b 冲突；原始子图返回完整状态；修复后子图对父图只输出 s1/s2，并产生准确默认结果。
2. 12 组不同整数和字符串输入；四个注入函数均读取所有字段参与计算。断言 node_a 读初始状态，其他三函数读 node_a 更新后的完整状态，且每函数每次只调用一次。
3. node_b、sub_1、sub_2 共同等待三方 Barrier（10 秒超时），三者全部启动后才允许返回。12 组均通过；若父图或子图被改成串行，就无法通过该屏障。验证的是这些实际运行中的并行执行，而非根据边声明推测。
4. 结果与独立公式完全相等，原始输入字典未被修改；覆盖负值、零、空字符串、值不变的更新、空更新和复用已编译图。
5. 子节点抛出的 ValueError 保持传播；修复没有吞异常。
6. 四个异步注入函数通过 ainvoke 正常运行，结果为 {'a':12,'b':'async12','s1':20,'s2':4}。

## 局限

验证针对当前安装的 LangGraph 1.0.3、本任务四字段输入和约定的字段所有权；有限运行不是所有调度与所有输入的形式证明。异步用例验证结果，未额外以异步屏障验证并行程度。未验证 checkpoint、interrupt、取消、重试、分布式运行、外部副作用、不同 LangGraph 版本或注入函数直接原地修改 state 的情况。当前函数无重试配置，一次调用不等于证明故障恢复时端到端 exactly-once。子图输出投影仍会携带其拥有字段的最终值（包括未改变值），这在当前父图没有其他 s1/s2 写者的约定下成立；若未来引入多个写者或改变字段所有权，应重新设计交接契约，不能直接延伸本结论。
