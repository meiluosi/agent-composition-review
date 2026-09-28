# v1 历史试用记录

这份记录保留此前已经发生的试用，不把新版本自动算作同样通过。受测指令快照在 [skill-under-test](v1/skill-under-test/SKILL.md)，模型可见参考的后续补充不在该快照中。

## 六个文本场景

每组各一个独立执行者，在新上下文中依次回答六个案例。一组读取技能正文和按需参考，一组明确不加载架构审查技能；两组没有读预先编写的 rubric。然后将答案匿名给第三个执行者评审。

两组都完成六案主要诊断。匿名评审中，一组 24 个观察点通过，另一组 23 个通过、1 个部分；差异是支付验证是否明确使用隔离替身。没有硬性错误，不能由这一处说明差异推出整体优势。匿名条件 A 为使用 Skill，B 为对照；此映射在评分后揭示。

- [原始案例](../scenarios/prompts.md)
- [预设判据](../scenarios/rubric.md)
- [使用 Skill 的回答](v1/scenario-with-skill.md)
- [对照回答](v1/scenario-without-skill.md)
- [匿名评审逐项证据](v1/scenario-blind-assessment.md)

## 公开框架问题

案例 [LangGraph #6446](https://github.com/langchain-ai/langgraph/issues/6446) 在实际 LangGraph 1.0.3 中复现。独立执行者仅看复现、运行报错和业务要求，不读 issue 的结论、主验收器或对方输出。两组均用限定子图 output_schema 的方法修复。

事先冻结的 8 项验收全部通过，并另外将原 issue 的四个处理函数接回修复图核对真实输出。后者属于本地历史执行证据，本仓库不打包原 issue 的代码；当前便携回归在 `evals/cases/` 中独立实现。

- [使用 Skill 的诊断](v1/with-skill-diagnosis.md) / [对照诊断](v1/without-skill-diagnosis.md)
- [使用 Skill 的修复](v1/with-skill-fixed.py) / [对照修复](v1/without-skill-fixed.py)
- [冻结的验收代码](v1/acceptance.py)
- [使用 Skill 的验收记录](v1/with-skill-acceptance.txt) / [对照验收记录](v1/without-skill-acceptance.txt)
- [原处理函数回接记录](v1/with-skill-upstream-callbacks.txt) / [对照回接记录](v1/without-skill-upstream-callbacks.txt)

可在仓库根目录重跑历史 8 项验收：

```sh
.venv/bin/python evals/runs/v1/acceptance.py evals/runs/v1/with-skill-fixed.py
```

## 局限与记录完整性

详细字段与文件校验值见 [record.json](v1/record.json)。首次试用没有记录准确模型标识、推理设置、token 费用与完成耗时，因此保留为 `null`/unknown，不补猜。共享系统环境中，对照组可能仍看到技能目录描述。

均为每组一次运行，没有保留集；只有文本场景进行匿名模型评审，代码修复由确定性测试和主代理核对。模型评审不是人类专家审查；历史匿名评审原文中的“人工判断”措辞指手工设计判据，本次没有实际人类评审。当前只能说明在这些案例上没有明显误导，未证明能力提升。

新发布候选应按 [评测协议](../PROTOCOL.md) 重新收集受影响行为的证据，不沿用这些成绩冒充新版成绩。
