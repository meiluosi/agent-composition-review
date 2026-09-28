# Agent 工程诊断练习

以下是六个互相独立的案例，只使用本文件提供的材料。请逐案回答原始请求，不修改代码、不联网、不向真实用户发送问题；必要的澄清问题写在回答中。每案控制在约 500 个汉字以内，代码位置可以用函数名定位。不要假设存在未展示的日志或实现。

## C1

用户请求：我们的 Agent 报价显示 129 元，但工具实际扣了 12900 元。请定位原因并给出最小修复和验证方法。

```python
from typing import TypedDict

class Quote(TypedDict):
    amount: int
    currency: str

def quote_product(product_id: str) -> Quote:
    # 本服务契约：amount 使用货币最小单位。商品含税价 129 元。
    return {"amount": 12900, "currency": "CNY"}

def render_quote(q: Quote) -> str:
    return f"{q['amount'] / 100:.2f} 元"

def payment_tool(amount: int, currency: str):
    # 下游支付工具契约：amount 使用主单位；ledger 记录最小单位。
    ledger.append({"minor_amount": amount * 100, "currency": currency})

def checkout(q: Quote):
    payment_tool(**q)
```

失败轨迹：`quote_product` 返回 `{amount:12900,currency:CNY}`；`render_quote` 为 `129.00 元`；`checkout` 调用后 ledger 中 `minor_amount=1290000`。不考虑其他币种和折扣逻辑。

## C2

用户请求：为了降低费用，我们改了候选答案服务。上线后答案经常全票通过，但质量没有改善。下面是完整的相关逻辑和一次轨迹，请检查。

```python
memo = {}

def candidate(question, draw_id):
    key = ("model-r3", question)
    if key not in memo:
        memo[key] = model.generate(question, temperature=0.8)
    return memo[key]

def solve(question):
    items = [candidate(question, i) for i in range(7)]
    return majority_vote(items)
```

目标约定：每次 solve 取 7 个新生成候选再投票，不接受重放旧候选。一次运行有 7 次 candidate 调用、1 次 model.generate 调用、6 次完整响应缓存命中。七个候选都指向 artifact-91。没有暴露模型内部推理，只有最终答案与调用元数据。

## C3

用户请求：我们的系统用了 prompt cache，多次生成恰好得到了相同答案。有人说这一定破坏了 self-consistency，需要删掉所有缓存。你怎么看？

服务说明：缓存只复用静态输入前缀的计算，不保存或复用最终生成响应。每次 generate 都进入采样阶段。一次题目的 5 次调用各有不同 generation_id；记录显示 5 次均实际生成，没有响应级缓存或请求合并。最终答案都是 B。采样阶段是否共享随机状态暂未记录；没有未缓存的对照实验，只有这一题的数据。

## C4

用户请求：三个 Agent 的输出字段和单位都一致，为什么合起来仍然不可执行？请判断当前设计，并给一个最小验收方法。

全局任务：为同一个时间段、同一 GPU 资源池选择能并行执行的三个作业，资源池总容量 10 个 GPU，不允许超售。

分发给三个 Agent 的任务都写明 `gpu_count` 表示同时占用的 GPU 数量，共享同一资源池与时段；每个 Agent 只看自己负责的作业，必须如实返回需求，不得降低需求。

结果：A={job:alpha,gpu_count:4}，B={job:beta,gpu_count:4}，C={job:gamma,gpu_count:4}。合并器校验了各输出 schema，并校验每一对作业的总需求不大于 10，然后直接发给调度器。调度器因容量不足拒绝。业务方尚未决定是否允许少选一个作业或改为分批。

## C5

用户请求：我们的 Agent 离线测试挺好，上线答错了。架构是用户输入→检索→总结→回答。你觉得是不是摘要丢了信息？现在还没带代码和日志，先告诉我该怎么查。

当前只有这段描述，没有样例、错误答案、检索结果、原文或组件版本。

## C6

用户请求：请排查这个 Agent 选择计划失败的问题。我们已经记录了相关边界，下面是完整的最小复现。

约定：两个候选计划，预算 100 元，必须选择预算内 quality 最大的计划。各步骤使用人民币元、相同时间范围，输入 schema 已验证。无缓存、无外部调用、无并发。

```python
def select_plan(plans, budget_yuan):
    eligible = [p for p in plans if p['cost_yuan'] <= budget_yuan]
    if not eligible:
        return None
    return min(eligible, key=lambda p: p['quality'])

plans = [
    {'id': 'A', 'cost_yuan': 80, 'quality': 90},
    {'id': 'B', 'cost_yuan': 70, 'quality': 20},
]
```

轨迹：进入 select_plan 的值与上面完全一致，eligible 包含 A/B，返回 B；后续执行器按 B 执行，没有改写结果。
