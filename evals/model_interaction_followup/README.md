# 事后补充：JSON 解码扩展绕过事件校验

这是观察正式运行结果后增加的开发检查，**不替代冻结成绩，也不是独立保留集**。四个家族的冻结源码、题面、验收、标签及历史结果保持原样。

发现来自 `model-interaction-2026-10-04` 的 **m08 / Astra / A_tools / trial 1**，结果 key 为 [`2c2982447f7bea923d2c`](../iterations/model-interaction-2026-10-04/results/2c2982447f7bea923d2c/record.json)。执行者提出并验证了此前验收遗漏的反例：

```python
parser.feed(b'{"event":NaN,"event":"done"}\n')
```

冻结的“正常参考”错误返回 done，并进入 DONE；将 NaN 换成 Infinity 或 -Infinity 结果相同。题面要求非法 JSON 触发 StreamError 并进入 FAILED，因此原有正常标签不能支持可靠的误报结论。原始实验分数仍保留，解释必须另行披露该限制。

原因是 Python 的默认 JSON 解码器允许这些非标准数字常量，同时只保留重复字段的最后一个值。前一个非法值因覆盖而逃过后续事件形状检查。[Python 3.12 文档](https://docs.python.org/3.12/library/json.html#infinite-and-nan-number-values)明确说明这类常量属于扩展；[RFC 8259 第 6 节](https://www.rfc-editor.org/rfc/rfc8259#section-6)将其排除在 JSON 数字语法之外。

`stream_reference.py` 是供未来开发使用的完整修订参考，仅通过 `parse_constant` 拒绝这些常量，随后沿原有失败处理进入 FAILED。它保留标准有效帧，也保留原解码器处理重复字段的方式。**重复字段本身应否拒绝是另一个问题**：题面没有明确该策略；[RFC 8259 第 4 节](https://www.rfc-editor.org/rfc/rfc8259#section-4)建议名字唯一，却未把重复名字规定为语法错误。因此，其他执行者仅以重复键为由判断违规的说法存在歧义，不能与本反例混为一谈。

## 复现

从项目根目录执行，无模型调用：

```bash
.venv/bin/python -B -S evals/model_interaction_followup/reproduce.py
.venv/bin/python -B -S -m unittest evals.model_interaction_followup.test_stream_json
```

- `reproduce.py` 从冻结 `cases.json` 读取 m08 原始源码，对比三种常量的原始行为和修订行为；另列仅重复合法字段的控制输入。
- `reproduction.json` 保存该脚本的实际输出及源码 SHA-256。
- 五项 unittest 覆盖原反例、修订后的失败状态、合法 Unicode/字符串内容、重复字段策略保留，以及修订参考通过未经修改的原 12 项验收。

这些有限检查仅覆盖所列行为，不证明解析器对所有输入正确。该修订没有进入本次任何正式模型工作区，也不能把修订后的参考重新计入本轮正常对照。
