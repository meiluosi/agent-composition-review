# 病例索引：症状 → 规则 → 真实案例

这不是规则表，而是**真实案例的检索入口**。遇到相似症状时，先打开案例核对证据边界，再决定要不要套规则。

案例正文在线版：<https://meiluosi.github.io/agent-composition-review/atlas/>
完整索引：[docs/atlas/README.md](../../../docs/atlas/README.md)

## 按错误文本找

| 你看到的错误/现象 | 先看规则 | 案例 | 状态 |
| --- | --- | --- | --- |
| `InvalidUpdateError: At key 'b': Can receive only one value per step` | ACR001 + ACR002 | [LG #6446 子图回传完整状态](../../../docs/atlas/lg-6446.md) | grounded |
| `InvalidUpdateError: At key 'question'`，两个并行子图汇到 sink | ACR001 + ACR002 | [LG #1964 并行子图共享状态](../../../docs/atlas/lg-1964.md) | grounded |
| 节点写了字段，下游读不到；没有报错，字段静默消失 | ACR007 | [LG #8320 未声明键被静默过滤](../../../docs/atlas/lg-8320.md) | grounded |
| 类节点返回另一个 schema 的键；`Must write to at least one of ['foo']` | ACR007 | [LG #2644 类节点写未声明键](../../../docs/atlas/lg-2644.md) | grounded（原报错未复现） |
| `Send` 扇出到子图后报 `Expected node father to update at least one of ['father']` | ACR007（+ ACR003 顺带） | [LG #2581 Send 到子图返回未声明 kids](../../../docs/atlas/lg-2581.md) | grounded |
| resume 后被 **interrupt** 的节点重启，担心副作用重复 | ACR106 | [LG #3875 interrupt 节点重启（负向控制）](../../../docs/atlas/lg-3875.md) | grounded；重启是预期行为 |
| resume/replay 后 **已完成** 节点又跑了一遍 | ACR106 | [LG #9276 resume 重跑已完成节点](../../../docs/atlas/lg-9276.md) | grounded；本地复现正向 |
| 共享 cache 后 B 图返回 A 图结果 | ACR008 | [LG #8753 callable cache 串结果](../../../docs/atlas/lg-8753.md) | experimental；设计源 |
| `retry_policy` 节点调两个子图，恢复后拿到第一个子图结果 | ACR009 | [LG #9253 retry 与子图 checkpoint](../../../docs/atlas/lg-9253.md) | experimental；无独立 exact |
| retry 自环 + 并行分支，`At key 'count_b'` | ACR010 | [LG #2336 retry 自环写非 reducer 键](../../../docs/atlas/lg-2336.md) | experimental；开发集 |
| `cache_policy` 在类方法/命名函数上，却被提示 callable 身份不稳定 | ACR008 | [vanara bound method 误报](../../../docs/atlas/lg-vanara-bound-method.md) | experimental；真实负向控制 |

## 按故障类型找

| 故障类型 | 优先检查 | 案例 |
| --- | --- | --- |
| 子图覆盖父图字段 / 旧值回传 | 子图是否声明 `output_schema`；父图是否并行写同一键 | #6446、#1964 |
| 并行写同一键 | 同一 superstep 的写者；键是否有 reducer | #6446、#1964、#2336 |
| 字段静默丢失 | 节点 return 的键是否在 state schema；类节点 input schema | #8320、#2644、#2581 |
| 重试/恢复后重复副作用 | retry 是否包住非幂等写；已完成节点是否重跑 | #3875、#9276、#9253 |
| 缓存结果串图 | callable 身份、cache namespace、是否跨图共享 cache | #8753、vanara |
| retry 自环 + 并行重叠 | 条件边自环、无 reducer 键 | #2336 |

## 怎么用这些案例

1. **先对症状**：用错误文本/现象找到最接近的案例，不要一上来套最顺手的规则。
2. **再对边界**：案例页都写了“证据边界”（验证命中 / 设计源 / 负向控制 / 仅邻近），不要把 experimental 当 confirmed。
3. **回到自己的代码**：只借用“检查点”和“证据形态”，不要把案例的修复直接复制到不同 schema。
4. **证据不足就停**：案例只能说明“这种结构曾经出过问题”，不能证明你的图一定同因。

关联记录：[第三轮定向验证](../../../evals/realworld3/results/pilot3-2026-10-18/REPORT.md) · [第四轮 lead 追踪](../../../evals/realworld4/LEADS_VERIFIED.md)
