# 最小修复模式

五类组合故障的“足够小”修法。原则：只改边界，不改架构；每处修复都对应一条能找到证据的规则；修完补一条能复现旧故障的回归测试。

示例用 LangGraph 1.x 的公开 API；不同小版本字段名可能略有差异，以你安装的版本为准。

## 1. 子图只回传自己负责的字段（output_schema）

症状：子图单独运行正常，组合后父图报 `InvalidUpdateError`，或子图覆盖了父图其他分支写的字段。

```python
from typing import TypedDict
from langgraph.graph import StateGraph

class ParentState(TypedDict):
    question: str
    draft: str
    final: str

class ChildOutput(TypedDict):
    draft: str  # 只列子图真正写入的键

# 子图仍读取完整 ParentState 作为上下文，但只把 ChildOutput 交回父图。
child = StateGraph(ParentState, output_schema=ChildOutput)
child.add_node("write_draft", write_draft)
child.add_edge("write_draft", END)
parent.add_node("draft", child.compile())
```

修复要点：`output_schema` 是**输出边界**，不是输入裁剪；子图对父图上下文的读取能力应保持不变。

## 2. 并行写同一键时显式合并（reducer）

症状：两个并行分支写同一个状态键，框架报“每步只能收到一个值”，或后完成的分支静默覆盖前一个。

```python
import operator
from typing import Annotated, TypedDict

def merge_findings(left: list[str], right: list[str]) -> list[str]:
    # reducer 必须可交换、可重复执行：并行顺序不应改变结果
    return sorted(set(left) | set(right))

class State(TypedDict):
    query: str
    findings: Annotated[list[str], merge_findings]
    total_cost: Annotated[float, operator.add]
```

修复要点：只有“确实需要合并”的键才加 reducer；单值键应串行化写者，而不是硬加一个“最后一次赢”的 reducer。

## 3. 外部副作用使用幂等键（重试/恢复不重复执行）

症状：节点重试、流恢复或人工重放后，重复扣款、重复发消息、重复建单。

```python
def charge_order(state: State) -> dict:
    key = f"{state['order_id']}:charge"          # 幂等键来自业务事实
    charges = state.get("charges", {})
    if key in charges:
        return {}                                # 已执行过：重试变 no-op
    receipt = payment.charge(state["order_id"], idempotency_key=key)
    return {"charges": {**charges, key: receipt}}

def build_charge_node() -> dict:
    return {"charge_order": charge_order}        # 不要在这里直接配无限重试
```

修复要点：幂等键由业务主键生成，不能每次调用重新生成；重试策略只包在幂等操作外层。工具层若不支持幂等键，先在本地去重表里记录“已发起”。

## 4. 缓存键包含采样语义

症状：缓存命中后不再重新采样，投票里其实只有一次真实生成；重试读到旧结果。

```python
import hashlib, json

def cache_key(question: str, model: str, temperature: float, sample_idx: int) -> str:
    payload = {
        "q": question, "model": model,
        "temperature": temperature, "sample_idx": sample_idx,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

def make_llm_call(cache, model, temperature):
    def call(state: State) -> dict:
        key = cache_key(state["question"], model, temperature, state.get("sample_idx", 0))
        if key not in cache:                     # 只有明确要求复用时才命中
            cache[key] = llm.invoke(state["question"])
        return {"answer": cache[key]}
    return call
```

修复要点：需要重新采样时，`sample_idx` / seed / 模型版本必须进 key；“同一结果复用”和“重新生成”是两种语义，不要共用一个缓存入口。

## 5. 定义完成边界与幂等清理

症状：任务报错或取消后仍留下部分写入；重试时看到半成品状态；同一个收尾节点执行两次。

```python
def finalize(state: State) -> dict:
    if state.get("status") == "completed":
        return {}                                # 重复执行不改结果
    try:
        receipt = store.commit(state["draft"])   # 事务式提交
    except Exception:
        store.rollback(state["draft"])           # 清理半成品，再抛给重试层
        raise
    return {"status": "completed", "receipt": receipt}

def cleanup(state: State) -> dict:
    if state.get("status") in {"completed", "failed"}:
        store.discard_temp(state["run_id"])      # 清理幂等
    return {}
```

修复要点：先定“什么算完成”（提交成功？回执落库？消息发出？），再让收尾逻辑可重入；失败路径要么回滚，要么显式标记 `failed`，不要留下“看似完成”的状态。

## 回归测试该断言什么

每处修复至少补一条：

- 复现旧故障的输入仍然能触发原边界（例如构造并发写），但修复后不再报错或不再重复副作用；
- 正常路径结果不变（字段值、并行关系、上下文读取）；
- 重复执行同一节点/同一工具时无第二次外部效果；
- 缓存命中/未命中的两种路径都覆盖；
- 失败后重试只执行一次外部写，且清理后可再次运行。

不要用“所有测试通过”代替“旧故障确实被复现过”；没有复现过的修复无法证明修对了。
