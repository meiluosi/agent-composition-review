# 从结构到回放验收

用于有代码或执行记录的架构审查、修复及重构。短问题不要先造一套 JSON。Python 3 标准库即可运行以下检查；路径均相对本 Skill 目录。

## 四种对象不要混淆

结构中的动作 ID 标识可执行转换；事件 ID 标识一次实际执行；要求 ID 标识判断依据；因果候选和修复位置单独说明。故障出现的动作、暴露故障的后续观察和建议修改的位置可以不同。检查器输出 `action_event_id`，不输出已经证实的根因。

## 建模与命令

先从真实接口、代码和业务要求建立 `model.json`，可改造 [检索结构模板](../assets/retrieval.model.json)。保留前置条件、后置条件、读写状态、失败分支、动作间字段绑定、必经动作和输入域。需求来源写在每条 `requirement`；无法从证据确定的条款先留在报告中，不为了通过检查编造约束。

```sh
python3 scripts/composition.py validate model.json
python3 scripts/composition.py plan model.json > obligations.json
python3 scripts/composition.py generate model.json --limit 1024 > probes.json
python3 scripts/composition.py check model.json trace.json > acceptance.json
```

`plan` 从结构展开每个动作的输入输出、契约、副作用、交接和完成义务。`generate` 对声明的有限 `probes.axes` 做笛卡尔展开，`base` 为共同输入；1024 是防止组合爆炸的默认展开预算，可显式调整。它不自动推导业务输入域或生成任意项目的适配代码。

将 probes 接到被测系统的本地适配器上，执行后再检查 trace。仅生成测试列表不能报告“测试已通过”。适配器需保存真实 payload 和状态，不用模型摘要代替执行记录；外部服务可固定为明确标注的替身。修复前失败、修复后通过，并包含正常及边界反例；重构比较相同输入的业务观察与副作用，不能只看检查器的结论。

## 数据约定

`schema_version: 1`。`types` 为语义类型名到字段路径约束的映射；字段支持 string、number、boolean、object、array、null 及 enum。语义仍要由契约描述，不由名称证明。

`actions` 的每项含 id、input_type、output_type、reads、writes、pre、post；reads/writes 是状态的顶层键。pre/post 的每条含 id、requirement、must，可用 when 表示守卫。`edges` 定义 from/to 动作以及 bindings（output/input 字段路径）。`required_actions` 只列契约要求完成的动作；有合法提前返回时需要适配相应契约，不能强制它走完失败之后的步骤。

表达式为单键对象：`path`（动作事件内路径）、`literal`、`count`、`pluck: {value, field}`、`scale: {value, factor}`。谓词为 `op` 和 `args`，支持 eq/ne/le/ge/contains/subset/unique/and/or。数值单位需显式表达；相等检查不自动应用容差。不得把浮点近似或统计阈值伪装成无条件相等。

trace 含 `complete` 和 `events`。动作事件含 id、kind=action、action、input、output、before、after、parents、source。parents 引用更早的动作事件；source 指向可追溯记录。计划和观察不能冒充 action。当前格式只支持一个顺序状态流，并发需先转换到可解释的顺序回放或另建偏序适配器，不能直接套用相邻快照相等规则。

检查结果为 pass/fail/unknown/not_applicable。缺少整份快照等证据记为 unknown；实际 payload 缺少契约必需字段记为 fail。`complete: true` 却缺少必经动作也会失败。存在 fail 得 violation，无 fail 但有 unknown 得 insufficient；全通过只表示被记录的执行满足所声明的有限契约。返回码 0=满足/正常生成，1=违规或缺证据，2=输入或结构错误。

## 范畴论与工程边界

对象、动作、串联与状态必须有可解释的映射；接口相同不代表关系被保持。字段绑定检查对应一部分组合保持义务，状态和副作用必须进入模型。实际检查有限执行，尚未证明函子规律或任何全局定理。

积保留必要分量，余积保留成功空值/失败等标签；依赖、来源和状态不能在合并时丢失。重试要观察效果计数；复制同一次随机结果不能计作多次独立调用。调用 ID 不同仍不足以证明统计独立。模型迁移需要显式状态适配器；四种交叉组合可能本来不兼容，应按约定拒绝。

结合律比较同一 f/g/h 的括号改变，不能用它证明“合并两次 LLM 调用”为一次提示词后语义等价。随机实验应区分组内自然波动与改动影响；温度为零不等于完全确定。检查器不验证日志真实性、未记录资源、统计独立或因果关系。
