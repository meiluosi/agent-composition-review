# Skill 协议评测集（10 题）

这组题不测“ACR 规则准不准”，也不调用模型 API。它测的是：Agent 加载 skill 后，是否按协议先复现、引用证据、分级归因、控制作用域、保持诚实。

## 文件

- [`prompts.json`](prompts.json)：10 个 prompt，含期望行为与 red flags。
- [`rubric.md`](rubric.md)：5 个维度 × 0–2 分，总分 100；含关键失败条件。

## 怎么跑

1. 开一个全新会话，只加载 `skills/agent-composition-review/SKILL.md`（不要额外把答案贴进去）。
2. 依次输入 `prompts.json` 里的 prompt。
3. 保存完整 transcript。
4. 按 `rubric.md` 逐题打分；先看有没有关键失败，再算总分。
5. 把 transcript、分数、失败原因存到自己的评测记录里（不要提交真实用户数据）。

## 怎么看结果

- **R 低**：Agent 太急着下结论，需要强化“先复现”；
- **C 低**：证据引用不具体，检查是否允许编造输出；
- **G 低**：没有区分 confirmed / design / insufficient；
- **S 低**：喜欢顺手重构，需要强化“一次一个边界”；
- **H 低**：可能夸大规则能力或忽略 experimental 状态。

这不是 benchmark，不要用这 10 题对外宣称模型排名或真实世界准确率。
