# 可用结构工具

`python composition.py validate model.json` 检查结构格式与引用。
`python composition.py plan model.json` 列出声明产生的类型、效果、前后条件、交接及完成检查。
`python composition.py generate model.json` 从可选 `probes: {base: 对象, axes: 字段到取值列表}` 展开有限输入。可使用 `--limit N` 指定展开预算。
`python composition.py check model.json trace.json` 检查顺序执行记录。

trace 格式：`{complete: 布尔, events: [...]}`。动作事件含 id、kind="action"、action（模型动作 ID）、input、output、before、after、parents（更早动作事件 ID）及 source。返回 pass/fail/unknown/not_applicable 检查；最终 satisfied 返回 0，violation/insufficient 返回 1，结构/输入错误返回 2。

这份文档只说明接口，不提供本任务结构或修复。建模格式见共同的 MODEL_FORMAT.md；脚本本身也是可读的本地代码。
