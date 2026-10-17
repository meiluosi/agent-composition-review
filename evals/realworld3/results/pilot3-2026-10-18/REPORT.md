# 第三轮定向验证报告（pilot3-2026-10-18）

**范围：15 个 GitHub 候选 + 13 个社区候选（Forum 11 / Stack Overflow 2）。这是按目标关键词定向收集的能力验证，不是随机抽样，不估计真实世界命中率；样本小，不宣称泛化。**

## 1. 结论先行

| 能力 | 真实样本情况 | 结论 |
| --- | --- | --- |
| **ACR106** | 找到 5 条 GitHub 真实 trace；另有本地复现 #9276 的 trace | **正向 + 负向都跑通**；但正向 trace 是本地复现，不是社区附件 |
| **ACR008** | 7 条 cache identity 邻近案例，0 条 exact | **未验证**；只有 adjacent，不冒充 exact |
| **ACR009** | 0 条新的 retry_policy + 多子图 exact 案例 | **no_sample_found** |
| **ACR010** | 0 条新的条件自环 + 非 reducer exact 案例 | **no_sample_found** |

一句话：**ACR106 得到了真实运行时行为的支持，同时暴露并修复了一个真实误报；ACR008/009/010 这轮没有找到 exact 样本，不能算已验证。**

## 2. ACR106：正向与负向

### 正向：本地复现 #9276

在 `langgraph==1.2.14` 上按 issue #9276 的结构复现：

- 第一段 `stream(..., stream_mode="debug", subgraphs=True)`：`agent` 完成（`node_end`，无 interrupts），`gate` 被 interrupt。
- 第二段 `Command(resume=...)`：`agent` 再次 `node_start`，模型被调用第二次。
- 在两段之间插入 `resume` 标记（LangGraph debug chunk 不自动产生该标记）。
- `acr trace` 命中 **ACR106**，指向 `agent`；被 interrupt 的 `gate` 没有被误报。

文件：`cases/lg3-9276-repro/`（`repro.py`、`trace.jsonl`、`trace.txt`）。
边界：这是**本地复现公开 issue 的 trace**，不是 reporter 附上的原始 trace。

### 负向：真实公开 trace #3875

GitHub issue #3875 的正文里贴了完整的 `stream_mode="debug"` 输出。整理为 trace 后：

- `branch_1` 在 resume 前是 **interrupted**（`task_result` 带 `interrupts`），不是 completed；
- resume 后 `branch_1`/`branch_2` 重启是预期行为；
- `acr trace` 输出 **0 条 ACR106**。

文件：`cases/lg3-3875/`。
**这暴露并修复了一个真实误报**：原实现把带 `interrupts` 的 `node_end` 当成已完成；现在 ACR106 只把无 interrupts 的 `node_end` 当完成。去掉该修复、用同一 trace 复跑会误报 `branch_1`（见报告第 5 节）。

### 仍未验证的部分

- 没有找到**社区直接附带的、带显式 resume/replay 标记**的 debug trace。
- ACR106 要求 trace 里有显式 `resume`/`replay` 事件；LangGraph 默认 debug 输出不产生这个标记。用户需要手动插入或写适配器，这是可用性缺口。

## 3. ACR008：只有邻近案例

7 条新 cache 案例全部标 `adjacent_cache_identity`：

- RedisCache key 的 `:` 拼接碰撞（#8851）；
- Pydantic state 序列化导致缓存 key 不稳定（#5733）；
- cache × checkpointer 交互（#5980）；
- cache key 不含函数代码、namespace 清理、functional API 并发等。

没有一条是 ACR008 的 exact 条件（callable object/lambda/partial + 共享 cache 身份碰撞）。唯一 exact 的 #8753 属第一批，已排除，不能复用。

构建了 #5980 的最小 fixture：`acr scan` **0 条 ACR008**、1 条 ACR004（cache + 副作用）。这只能说明命名函数 + cache_policy 不误报，不能证明 ACR008 正向有效。

## 4. ACR009 / ACR010：没有新样本

- **ACR009**：新候选里没有“`retry_policy` 节点内调用 2+ 子图”的结构；#9253 及其修复 PR #9279 属已排除批次。`no_sample_found`。
- **ACR010**：新候选没有“条件自环 + 已声明无 reducer 键”；#2336 属已排除批次。`no_sample_found`。
- 两者都不能用邻近案例冒充 exact，也不能把“零命中”说成“规则有效”。

## 5. 这轮实际修复的代码

真实 trace 暴露了一个 ACR106 误报：

- `TraceEvent` 增加 `interrupts`；
- 归一化时保留 `task_result.payload.interrupts`；
- ACR106 只把**无 interrupts** 的 `node_end` 当作 completed。

验证：

```sh
# 负向：真实 #3875 trace，带 interrupts -> 0 findings
acr trace evals/realworld3/cases/lg3-3875/trace.jsonl

# 正向：本地复现 #9276 trace -> ACR106
acr trace evals/realworld3/cases/lg3-9276-repro/trace.jsonl
```

同一 #3875 trace 去掉 `interrupts` 字段，会得到 1 条 ACR106 误报；保留字段则 0 条。这是本轮最实在的收获。

## 6. 限制

- 定向收集，不是随机抽样；不能外推流行率。
- ACR106 正向样本是本地复现，不是社区附件；版本固定 1.2.14。
- ACR106 需要显式 resume/replay 标记；默认 debug 输出没有。
- ACR008 只有邻近案例；ACR009/ACR010 没有新 exact 样本，均未验证。
- 没有第二标注者；判定是分析者判断。
- 本批案例已用于发现 ACR106 误报并修正规则，因此对 ACR106 修复而言是开发集，不是独立验证。
