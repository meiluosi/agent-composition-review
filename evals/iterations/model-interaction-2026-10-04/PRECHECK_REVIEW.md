# 正式调用前的材料与执行复核

本记录在正式 144 次尝试之前完成。任务由不同 AI 代理编写，另一位代理审查题面、正常参考和隐藏验收，主维护代理执行整套检查。这是相互独立的代码复核，不是人类专家标注、盲测解题或外部数据集验证。两个模型 canary 单独计数。

## 可执行正负对照

| 家族 | 正常实现 | 组合故障 | 节点内部故障 | 人为变异检出 |
| --- | ---: | --- | --- | ---: |
| lease_queue | 9/9 | 旧 receipt 可错误确认新租约 | 分数租期截断 | 9/9 |
| retrieval_cache | 12/12 | 身份键遗漏与对象别名 | 返回数量多 1 | 10/10 |
| stream_events | 12/12 | 字节分块与终止边界 | 单词计数 | 9/9 |
| tool_results | 12/12 | 旧版本覆盖与非原子更新 | 负数评分 | 10/10 |

12 个变体全部通过同家族共用的公开 smoke；8 个故障变体全部被隐藏验收检出；4 个正常变体全部通过。四组 mutation 共 38 个全部检出。这证明验收能发现这些指定错误，不证明所有错误都能被发现。完整验收覆盖业务状态、输入输出和生命周期，不靠最终回答的关键词评分。

主维护代理于 2026-10-04 执行并通过：

```bash
.venv/bin/python -m unittest evals.model_interaction.test_fixtures evals.model_interaction.test_protocol evals.model_interaction.test_analysis -v
.venv/bin/python -S evals/model_interaction/review_lease_queue.py
.venv/bin/python -S evals/model_interaction/tasks/lease_queue/mutation_audit.py
.venv/bin/python -S evals/model_interaction/tasks/retrieval_cache/mutation_audit.py
.venv/bin/python -S evals/model_interaction/tasks/stream_events/mutation_audit.py
.venv/bin/python -S evals/model_interaction/tasks/tool_results/mutation_audit.py
```

单元测试共 24 项，包含正负对照、标准 dataclass 导入、共享工具一致性、标签隔离、评分器异常输出、工作区保护、服务失败停止调度、不重复调用、缺失数据与部分配对权重等。lease_queue 独立状态参考使用 seed 812 连续执行 300 步，全部通过。测试调度器中的假模型调用不消耗模型额度。

## 复核发现与冻结前修正

- lease_queue 原时间范围承诺过宽。SQLite 浮点表示不能保证任意大整数的到期精度，现明确合法时间与时长范围，补充编码失败后状态保持与 ID 可复用检查。非法 JSON 深度可能产生 RecursionError，题面明确允许编码异常传播。
- retrieval_cache 原“任意有限 JSON”包含 deepcopy 无法支持的极深对象。现限定 payload 路径最多 20 层容器，并验收恰好 20 层；这不是在看到正式成绩后放宽评分。独立检查还覆盖特殊字符串过滤条件的 6 种插入顺序、2**80 的整数时钟和来源持有对象后的修改。
- stream_events 的极深 JSON 帧曾泄漏 RecursionError，且没有进入失败态。已修正正常实现和其他变体中的共同部分，增加反例与 mutation。另有 200 种合法字节切分的独立探索检查通过；该探索检查只作补充，持续验证以仓库中的固定验收为准。
- tool_results 对 list/dict 形式的非法 call_id 曾抛出错误的异常类型。三个变体的共同验证逻辑已一致修正，验收增加该反例。
- 验收加载器注册 app 模块，兼容合法 dataclass/self-import；捕获候选日志，逐项记录异常与异常退出。评分器用独立目录和 Python `-S` 执行，拒绝缺失、重复、跳过的检查及非零退出。
- 分析器将未运行标 pending；缺失源码/基础设施故障不计为正常业务回归；不完整配对先平均每题重复，再平均家族。非对象 JSON 回答按无效输出与缺失结论记录；正常源码缺失也参与保护门槛。四家族重采样仅作描述性敏感性分析。

以上修正均发生在正式材料冻结前；历史实验没有重写。

## 模型与工具连通性

使用 codex-cli 0.160.0、两个请求模型的 high 设置，各完成一次独立加法修复 canary。两次均实际修改源码、调用 Python smoke、有效返回 JSON，且独立行为验收通过。

| 请求模型 | 耗时秒 | input tokens | cached input（已包含在 input） | output tokens |
| --- | ---: | ---: | ---: | ---: |
| gpt-6-astra | 41.882 | 54,732 | 42,880 | 317 |
| gpt-6-luna | 33.688 | 51,505 | 38,912 | 563 |

记录与产物保存在 preflight 目录；原始调用日志保留在本机忽略目录，以哈希关联。没有重跑 canary。两次 canary 发生在 Python helper 增加 `-S`、评分器清除 PYTHONPATH/PYTHONHOME，以及记录补充运行环境字段之前；随后用最终评分器离线重验两份既有源码，均通过。原始 canary 记录保持不变。这是一次可用性检查，不是对最终全部配置重新执行的模型验证，正式首批 6 次仍承担执行完整性检查。

## 尚存限制

这些材料是新编写的开发任务，不能称作专业基准或独立保留集。正常参考只通过有限验收与额外反例审查；例如租约并发只覆盖有限线程调度，检索缓存只承诺顺序调用，事件流不是完整 SSE 实现。隐藏验收不反馈给执行模型，但 shell 可读标准库；本方案不宣称对恶意执行者实现完整文件读取隔离。正式结果还须回读命令审计，并披露超时、服务失败、无效输出与用量缺失。

模型指导包与当前 v4 完全一致，默认安装版本不因本次准备工作改变。正式调用前冻结题目、验收、指导、调度、分析代码及本记录；结果只按[预先方案](ANALYSIS_PLAN.md)作开发方向决策。
