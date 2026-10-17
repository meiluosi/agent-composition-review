# 规则速查：症状 → 证据 → 最小修复 → 误报边界

`acr` 的规则分两类：**ACR0xx** 对构图代码做静态检查（`acr scan`），**ACR1xx** 对运行轨迹做时序检查（`acr trace`）。规则输出是**待验证的发现**，不是判决：先用 finding 里的文件/行号/事件 ID 回到代码与轨迹确认，再动手修。

每条规则的完整页面在 [`docs/rules/`](../../../docs/rules/)，包含可独立打开的示例。

| 规则 | 症状 | 关键证据 | 最小修复 | 误报边界 |
| --- | --- | --- | --- | --- |
| [ACR001](../../../docs/rules/ACR001.md) | 子图单独跑正常，与父图并行后 `InvalidUpdateError`；子图覆盖父图字段 | 子图 `StateGraph(State)` 未声明 `output_schema`，且其状态键与父图共享（尤其父图另有分支写同一键）；finding 指向子图 `compile()` 处 | 给子图声明 `output_schema=ChildOutput`，只包含子图真正写入的键 | 子图本就是唯一写者、或父图刻意要子图回传全量状态时，不构成故障 |
| [ACR002](../../../docs/rules/ACR002.md) | 并行分支在同一 superstep 写同一状态键：`InvalidUpdateError` 或后写覆盖先写 | 同一状态键被两个以上并行节点返回，schema 上该键没有 `Annotated[..., reducer]` | 给该键加 reducer（如 `operator.add`、自定义合并函数），或把写者收敛到单一节点 | 只有一个分支会实际执行、或键语义就是“最后写者胜”时，加 reducer 反而错 |
| [ACR003](../../../docs/rules/ACR003.md) | 子图作为节点使用，输出边界未声明；组合后字段莫名出现/消失 | 子图以 `child.compile()` 直接挂到父图，`StateGraph` 构造没有 `output_schema` | 同 ACR001：声明 output_schema；若确需全量回传，在父图侧显式合并 | 子图与父图状态完全一致且没有并发写者时，风险低，可只记录不修 |
| [ACR004](../../../docs/rules/ACR004.md) | 缓存命中后不重新采样、跳过副作用；重试/回放读到旧结果 | 带副作用的节点/工具被放进图级或节点级 cache；cache key 不含采样参数或幂等键 | 缩小缓存范围；key 纳入输入哈希、模型参数、采样序号；副作用移出缓存 | 纯读、确定性计算或显式允许“同一结果复用”的场景，缓存是正确设计 |
| [ACR005](../../../docs/rules/ACR005.md) | 重试后重复扣款/重复发消息/重复建单 | 节点或工具配置了 retry，且节点内有非幂等外部写；finding 指向 retry 声明与写操作 | 为外部写加幂等键（业务单号/工具调用 ID）；或把重试限制在读取与校验阶段 | 写操作本身幂等（PUT/upsert/带唯一约束）或重试只覆盖纯计算时，无需修改 |
| [ACR006](../../../docs/rules/ACR006.md) | 状态在节点外被改写，父图看不到；回放结果不一致 | 节点内调用 `get_state` / `update_state`，而不是用返回值提交状态 | 改为通过节点返回值提交；确实需要旁路读写时，显式记录并在测试里覆盖 | 框架 API 要求的检查点读取、只读诊断不构成故障 |
| [ACR007](../../../docs/rules/ACR007.md) | 节点返回了字段，下游却读不到；或报 `Must write to at least one of [...]` | 节点 return 的 dict 字面量里有键不在该图 state schema 中（类节点看 `__call__`） | 把键声明进 state schema（多写者加 reducer），或删除/重命名该返回键 | 返回键集无法静态确定（`return state`、`{**state}`、helper）或 schema 不在当前文件时不报 |
| [ACR008](../../../docs/rules/ACR008.md) | 共享 cache 后 B 图返回 A 图结果；缓存命中异常高 | 节点启用 `cache_policy`，但 callable 是 callable object/lambda/partial/未解析参数，cache key 缺少稳定身份 | 用模块级命名函数；或给 cache policy 显式稳定 namespace/key；避免跨图共享匿名 cache | 命名函数、纯计算且不共享 cache、或项目自己保证稳定 key 时不报 |
| [ACR009](../../../docs/rules/ACR009.md) | retry/恢复后第二个子图没真正执行，返回第一个子图结果 | 节点有 `retry_policy`，函数体内调用 2 个以上不同子图的 `.invoke/.ainvoke` | 拆成多个节点；或给每个子图调用独立的 checkpoint/namespace，并加恢复回归 | 只调用一个子图、没有 retry、调用对象不是子图、或新版已修复时，需要按版本降级 |
| [ACR010](../../../docs/rules/ACR010.md) | retry 自环节点与并行分支重叠，报 `InvalidUpdateError` 或丢更新 | 条件边 target 包含 source（自环），节点写已声明但无 reducer 的键 | 给键加 reducer；或 guard 自环，禁止与并行写者重叠 | 无自环、写 reducer 键、动态路由无法静态确认时不报 |
| [ACR106](../../../docs/rules/ACR106.md) | resume/replay 后已完成节点又跑了一次，副作用重复 | trace 中有 `resume`/`replay` 标记，且同一节点在标记前已有 `node_end`、标记后又 `node_start` | 从精确 checkpoint/pending writes 恢复；给副作用加幂等键 | 没有 resume/replay 标记、节点字段缺失、普通重试不看此规则 |
| [ACR101](../../../docs/rules/ACR101.md) | 同一工具在一条轨迹里被重复调用，产生重复副作用或重复计费 | 轨迹中 `(tool_name, normalized_args)` 连续重复，且没有幂等/去重标记 | 在调用前按幂等键去重；区分“重试同一调用”与“重新采样” | 搜索/重读等只读工具被重复调用可能只是效率问题；采样类调用重复是预期行为 |
| [ACR102](../../../docs/rules/ACR102.md) | 同一节点在一次运行中执行多次，状态被重复累加 | 轨迹里同一 node 出现多个 start/end 事件，且每次都写同一键 | 用完成标记/幂等键让重复执行成为 no-op；修掉导致回边的条件 | 循环体节点按设计重复执行（如迭代 refine）时，需要看每次写入是否可累加 |
| [ACR103](../../../docs/rules/ACR103.md) | 并行分支在同一 step 写同一键，结果不确定 | 轨迹中同一 superstep 有多个节点写同一键，且没有 reducer 合并记录 | 加 reducer，或串行化写者；不要在并发分支里写共享单值键 | 若框架已用 reducer 合并且结果正确，finding 应降级为提示 |
| [ACR104](../../../docs/rules/ACR104.md) | 节点报错后仍留下部分写入，重试时状态已污染 | 错误事件之前的写入没有随事务/补偿回滚；重试的输入包含半成品 | 在节点内做事务式提交，或显式记录 failed 状态并在重试前清理 | 框架 checkpoint 语义本就保留“已成功的 step”时，部分写入可能是预期恢复点 |
| [ACR105](../../../docs/rules/ACR105.md) | 最终状态丢了中间节点写过的键，下游读到空值 | 时间线中某键曾出现，最终 state 里缺失；常见于合并/覆盖式返回 | 检查子图 output_schema 与 reducer；用合并而非整体替换提交状态 | 键本来就是临时字段、按设计在结束前删除时，不算丢字段 |

## 使用顺序

1. 先跑 `acr scan <path>` 找出构图层的边界问题，再跑 `acr trace <trace>` 看运行时是否符合预期。
2. 对每条 finding：读 evidence → 回到代码/轨迹确认 → 判断是“真故障 / 设计如此 / 证据不足”。
3. 只对确认的故障做最小修复；修完补一条回归测试（见 [fix-patterns.md](fix-patterns.md)）。
4. 无法确认时报告“证据不足 + 需要什么证据”，不要为了关闭 finding 而改代码。

## 证据纪律

- 规则命中 ≠ 根因；根因需要“触发条件 → 边界证据 → 被破坏的契约 → 业务后果”这条链。
- 静态规则只看代码结构，看不到运行时的实际合并结果；轨迹规则只看发生过的一次运行，不能证明其他输入下同样成立。
- 不确定时用 `acr trace --json` 拿事件 ID，用事实扩大证据，不用猜测补全。
