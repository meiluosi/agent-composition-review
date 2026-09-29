# 证据、抽象与诊断目标

适用于日志分析、错误恢复、缺失观测和多阶段摘要。先将候选分开，再按用户的目标选择结论，避免所有候选共用无区别的 failure 标签。

## 记录约束

记录事件 ID、来源、动作主体和事件种类（plan/action/observation/artifact/statement）。包装角色与堆栈组件可以不同；原始事件引用不随摘要或展示排序改变。

对每个发现分别记录：

- `status`：confirmed / hypothesis / risk / ruled_out；
- `execution`：observed / planned / unknown / not_applicable；
- `recovery`：recovered / unrecovered / unknown / not_applicable；
- `causal`：untested / intervention_supported / not_applicable。

这些维度不是单一置信度等级。已发生的错误可以已恢复；当前风险可以尚未执行；干预成功也只支持其实际验证范围。

摘要必须保留目标相关的未知和反证。不因后续候选看起来更严重就抹掉历史错误，不因错误发生最早就认定它决定了最终失败。目标不明确时列出不同目标的答案，不能默默替用户决定。

## 可选记录格式

复杂任务可以保存以下结构，简短任务无需机械填表：

```json
{
  "target": "current_contract",
  "requirements": [{"id": "r1", "text": "交付文件应位于约定路径"}],
  "events": [{"id": "e1", "kind": "plan", "source": "trace:7", "actor": "worker", "text": "计划写入另一目录"}],
  "findings": [{"id": "f1", "requirement_id": "r1", "event_ids": ["e1"], "claim": "计划路径不符合要求，实际交付尚未知", "status": "risk", "execution": "planned", "recovery": "not_applicable", "recovery_event_ids": [], "causal": "untested", "intervention_event_ids": []}],
  "selected_ids": ["f1"],
  "verdict": "insufficient"
}
```

`target` 可为 current_contract / earliest_observed_error / decisive_cause / architecture_review / refactor_preservation。`verdict` 为 violation / satisfied / insufficient。`satisfied` 限于要求和材料范围，不证明整个系统无故障。违反契约的归因选择应至少包含一个 confirmed 发现；不能仅凭 hypothesis 或 risk 给出 violation。决定性因果结论无干预材料时可保留为候选，不伪造验证。

运行 `python3 scripts/check_evidence.py RECORD.json`。检查器仅检查引用、枚举和少数不一致组合，如 confirmed + planned、recovered 却无恢复证据引用。它不会读取源码证明事实，也无法判断伪造但结构合法的“观察”。通过只表示记录内部通过这些规则。

**最小验证**：将同一事件改为未执行计划，检查结论是否降为风险；提供成功恢复的记录，检查历史错误与当前状态是否仍被区分；只改变展示次序，检查原始引用是否保持。

依据：[Cousot，抽象解释](https://www.di.ens.fr/~cousot/AI/IntroAbsInt.html)。健全抽象需要定义具体域、抽象域和正确性条件；任意 LLM 摘要没有这种保证。这里采用证据保留约束，不声称已构造 Galois 连接或证明摘要健全。
